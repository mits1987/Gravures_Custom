import frappe
import subprocess
import tempfile
import os
import requests


@frappe.whitelist()
def download_pdf(doctype, name, format=None, doc=None, no_letterhead=0, letterhead=None, settings=None, _lang=None):
    # 1. Generate the print HTML
    html = frappe.get_print(
        doctype,
        name,
        print_format=format,
        doc=doc,
        no_letterhead=no_letterhead,
        letterhead=letterhead,
        as_pdf=False,
    )

    # 2. Create temporary files
    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html)
        html_path = f.name

    pdf_path = tempfile.mktemp(suffix='.pdf')

    # 3. Get Chrome path from site config (or fallback to the installed one)
    chrome_path = frappe.conf.get("chrome_path") or "/home/mitesh/frappe-bench-v16/chromium/chrome-linux/headless_shell"

    # 4. Build the command
    cmd = [
        chrome_path,
        '--headless',
        '--no-sandbox',
        '--disable-gpu',
        '--no-pdf-header-footer',
        '--print-to-pdf=' + pdf_path,
        html_path
    ]

    try:
        # 5. Run Chrome
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)

        # 6. Read the generated PDF
        with open(pdf_path, 'rb') as f:
            pdf = f.read()
    except subprocess.CalledProcessError as e:
        frappe.log_error(f"Chrome PDF generation failed: {e.stderr}")
        frappe.throw(f"PDF generation failed. Check error logs.")
    finally:
        # 7. Clean up temporary files
        os.unlink(html_path)
        if os.path.exists(pdf_path):
            os.unlink(pdf_path)

    # 8. Send the PDF as a download
    frappe.local.response.filename = f"{name}.pdf"
    frappe.local.response.filecontent = pdf
    frappe.local.response.type = "download"


@frappe.whitelist()
def send_whatsapp_image(image_base64, filename="image.png", caption=""):
    """Send a base64-encoded image via WhatsApp using OpenWA."""
    frappe.logger().info("send_whatsapp_image called: filename={0}, caption={1}, img_len={2}".format(
        filename, caption, len(image_base64) if image_base64 else 0))

    # --- Validate image data ---
    if not image_base64:
        frappe.throw("No image data provided.")

    # Basic sanity check: base64 should be at least 1KB for a meaningful image
    if len(image_base64) < 1024:
        frappe.throw("Image data too small — screenshot may have failed. Try again.")

    # --- Load OpenWA Settings ---
    try:
        settings = frappe.get_doc("OpenWA Settings")
    except frappe.DoesNotExistError:
        frappe.throw("OpenWA Settings not found. Please install kreativ_attendance app and configure OpenWA Settings.")

    if not settings.enabled:
        frappe.throw("WhatsApp is disabled. Enable it in OpenWA Settings.")

    if not settings.base_url:
        frappe.throw("OpenWA Base URL is not set. Configure it in OpenWA Settings.")

    chat_id = settings.chat_id
    if not chat_id:
        frappe.throw("No Recipient Chat ID set in OpenWA Settings.")

    session_id = settings.session_id or "default"

    # --- Build OpenWA API request ---
    url = "{0}/api/sessions/{1}/messages/send-document".format(
        settings.base_url.rstrip("/"),
        session_id,
    )
    # Read api_key directly — get_password() fails when value was inserted
    # as plain text via SQL instead of through Frappe's Password field.
    api_key = frappe.db.get_single_value("OpenWA Settings", "api_key") or ""
    if not api_key:
        frappe.throw("OpenWA API Key is not set. Configure it in OpenWA Settings.")

    payload = {
        "chatId": chat_id,
        "base64": image_base64,
        "mimetype": "image/png",
        "filename": filename or "dispatch_report.png",
        "caption": caption or "Daily Dispatch Report",
    }

    # --- Send via OpenWA ---
    try:
        r = requests.post(
            url,
            json=payload,
            headers={"X-API-Key": api_key},
            timeout=30,
        )
    except requests.exceptions.ConnectionError:
        frappe.throw(
            "Cannot connect to OpenWA at {0}. "
            "Is the OpenWA service running?".format(settings.base_url)
        )
    except requests.exceptions.Timeout:
        frappe.throw("OpenWA request timed out after 30 seconds. Try again.")
    except Exception:
        frappe.log_error(title="WhatsApp send exception", message=frappe.get_traceback())
        frappe.throw("Unexpected error connecting to OpenWA. Check Error Log.")

    if r.ok:
        try:
            resp = r.json()
            return {"success": True, "message": "Image sent to WhatsApp", "messageId": resp.get("messageId")}
        except Exception:
            return {"success": True, "message": "Image sent to WhatsApp"}
    else:
        # Parse error response for useful message
        error_msg = ""
        try:
            err = r.json()
            error_msg = err.get("message", r.text[:200])
        except Exception:
            error_msg = r.text[:200]

        frappe.log_error(
            title="WhatsApp send failed: HTTP {0}".format(r.status_code),
            message="URL: {0}\nChatID: {1}\nResponse: {2}".format(url, chat_id, error_msg),
        )
        frappe.throw(
            "WhatsApp send failed (HTTP {0}): {1}".format(r.status_code, error_msg)
        )