import frappe
import subprocess
import tempfile
import os

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
        '--no-pdf-header-footer',          # ← ADD THIS LINE
        '--print-to-pdf=' + pdf_path,
        html_path
    ]

    try:
        # 5. Run Chrome
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        
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
