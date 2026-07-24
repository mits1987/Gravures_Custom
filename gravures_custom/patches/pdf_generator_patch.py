import subprocess
import tempfile
import os


def chrome_get_pdf(html, options=None, output=None):
    """Use Chrome subprocess if site is configured for Chrome, otherwise fall back."""
    import frappe
    from frappe.utils.pdf import get_pdf as original_get_pdf

    # Only activate if explicitly enabled via site_config
    if frappe.conf.get("pdf_generator") != "chrome":
        return original_get_pdf(html, options, output)

    # Get Chrome path from site config (or fallback to the known location)
    chrome_path = frappe.conf.get("chrome_path") or "/home/mitesh/frappe-bench-v16/chromium/chrome-linux/headless_shell"

    # Create temporary files
    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html)
        html_path = f.name

    pdf_path = tempfile.mktemp(suffix='.pdf')

    # Build Chrome command (same flags you already tested)
    cmd = [
        chrome_path,
        '--headless',
        '--no-sandbox',
        '--disable-gpu',
        '--print-to-pdf-no-header',
        '--no-pdf-header-footer',      # removes default header/footer
        '--print-to-pdf=' + pdf_path,
        html_path
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        with open(pdf_path, 'rb') as f:
            pdf = f.read()
    except Exception as e:
        frappe.log_error(f"Chrome PDF generation failed: {e}")
        # Fallback to original method if Chrome fails
        pdf = original_get_pdf(html, options, output)
    finally:
        # Clean up temp files
        os.unlink(html_path)
        if os.path.exists(pdf_path):
            os.unlink(pdf_path)

    if output:
        with open(output, 'wb') as f:
            f.write(pdf)
        return output
    return pdf


# Deferred patch: apply at runtime when frappe.conf is available
def apply_patch():
    import frappe
    from frappe.utils import pdf as pdf_module
    if frappe.conf.get("pdf_generator") == "chrome":
        pdf_module.get_pdf = chrome_get_pdf
