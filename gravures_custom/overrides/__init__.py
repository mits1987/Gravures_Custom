import frappe
import subprocess
import tempfile
import os
import base64
import re
from urllib.parse import urljoin
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

import sys
print(f"DEBUG: gravures_custom.overrides module LOADED from {__file__}", file=sys.stderr)

# ---------------------------------------------------------------------------
# Circuit breaker — shared with kreativ_attendance.openwa_health
# ---------------------------------------------------------------------------

def _check_whatsapp_circuit_breaker():
    """Check if OpenWA circuit breaker is tripped.

    Mirrors the breaker in kreativ_attendance which sets openwa_failure_streak
    in cache. If >= 3 consecutive failures, reject the request with a friendly
    message instead of hitting the (presumed-down) gateway.
    """
    site = frappe.local.site or "default"
    streak = int(frappe.cache().get_value(f"openwa:streak:{site}") or 0)
    if streak >= 3:
        frappe.throw(
            "WhatsApp service is temporarily unavailable (circuit breaker open). "
            "Please try again later. If the issue persists, check OpenWA health."
        )


def _fetch_profile_pictures(base_url, session_id, headers, contact_ids, max_workers=10):
    """Fetch profile picture URLs for multiple contacts in parallel.

    Args:
        base_url: OpenWA base URL
        session_id: WhatsApp session ID
        headers: Auth headers with X-API-Key
        contact_ids: List of WhatsApp contact IDs (e.g., '919876543210@c.us')
        max_workers: Max parallel requests

    Returns:
        Dict mapping contact_id -> profile_picture_url (or None if not found)
    """
    results = {}
    base_url = base_url.rstrip("/")

    def fetch_one(cid):
        if "@g.us" in cid:  # Groups don't have profile pictures via this endpoint
            return (cid, None)
        try:
            r = requests.get(
                "{0}/api/sessions/{1}/contacts/{2}/profile-picture".format(
                    base_url, session_id, cid
                ),
                headers=headers, timeout=8,
            )
            if r.ok:
                data = r.json()
                return (cid, data.get("url"))
        except Exception:
            pass
        return (cid, None)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_one, cid) for cid in contact_ids]
        for f in as_completed(futures):
            cid, url = f.result()
            results[cid] = url

    return results


@frappe.whitelist()
def download_pdf(doctype, name, format=None, doc=None, no_letterhead=0, letterhead=None, settings=None, _lang=None):
    html = frappe.get_print(
        doctype, name, print_format=format, doc=doc,
        no_letterhead=no_letterhead, letterhead=letterhead, as_pdf=False,
    )
    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html)
        html_path = f.name
    pdf_path = tempfile.mktemp(suffix='.pdf')
    chrome_path = frappe.conf.get("chrome_path") or "/home/mitesh/frappe-bench-v16/chromium/chrome-linux/headless_shell"
    cmd = [chrome_path, '--headless', '--no-sandbox', '--disable-gpu',
           '--no-pdf-header-footer', '--print-to-pdf=' + pdf_path, html_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        with open(pdf_path, 'rb') as f:
            pdf = f.read()
    except subprocess.CalledProcessError as e:
        frappe.log_error(f"Chrome PDF generation failed: {e.stderr}")
        frappe.throw(f"PDF generation failed. Check error logs.")
    finally:
        os.unlink(html_path)
        if os.path.exists(pdf_path):
            os.unlink(pdf_path)
    frappe.local.response.filename = f"{name}.pdf"
    frappe.local.response.filecontent = pdf
    frappe.local.response.type = "download"


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _chrome_path():
    return frappe.conf.get("chrome_path") or "/home/mitesh/frappe-bench-v16/chromium/chrome-linux/headless_shell"


def _generate_pdf_bytes(doctype, name, print_format=None, no_letterhead=0, letterhead=None):
    """Render any Document as PDF bytes via Chromium headless.
    Inlines `/files/...` images as base64 to avoid Chromium's auth-less
    image fetches (which return 401 / break the logo etc.)."""
    html = frappe.get_print(
        doctype, name, print_format=print_format, doc=None,
        no_letterhead=no_letterhead, letterhead=letterhead, as_pdf=False,
    )

    # Strip the printview "Print | Get PDF" action banner that gets
    # embedded when generating PDF output — keeps the PDF clean.
    html = re.sub(r'<div\s+class="action-banner[^"]*"[^>]*>.*?</div>', '', html, flags=re.DOTALL)

    # Find all <img src="/files/..."> (relative paths) and inline them.
    # Also handles letter head image injected by Frappe at runtime.
    base_url = frappe.utils.get_url()
    img_re = re.compile(r'(<img[^>]*\ssrc=["\'])([^"\']*\.(?:png|jpe?g|gif|svg|webp|bmp))(["\'])', re.IGNORECASE)
    seen = {}
    session = requests.Session()

    def inline_img(match):
        full_url = urljoin(base_url + "/", match.group(2))
        if full_url in seen:
            return match.group(1) + seen[full_url] + match.group(3)
        try:
            r = session.get(full_url, timeout=10)
            if r.status_code == 200 and r.content:
                b64 = base64.b64encode(r.content).decode("utf-8")
                mime = "image/png"
                ct = r.headers.get("Content-Type", "")
                if ct.startswith("image/"):
                    mime = ct
                data_uri = "data:{0};base64,{1}".format(mime, b64)
                seen[full_url] = data_uri
                return match.group(1) + data_uri + match.group(3)
        except Exception:
            pass
        return match.group(0)

    # Replace relative `/files/...` in HTML
    html = img_re.sub(inline_img, html)
    # Also handle literal server URLs (e.g. https://162.kreativgravures.com/files/...)
    abs_re = re.compile(r'(<img[^>]*\ssrc=["\'])(https?://[^"\']*\.(?:png|jpe?g|gif|svg|webp|bmp))(["\'])', re.IGNORECASE)
    abs_seen = {}
    def inline_abs_img(m):
        u = m.group(2)
        if u in abs_seen:
            return m.group(1) + abs_seen[u] + m.group(3)
        try:
            r = session.get(u, timeout=10)
            if r.status_code == 200 and r.content:
                b64 = base64.b64encode(r.content).decode("utf-8")
                mime = "image/png"
                ct = r.headers.get("Content-Type", "")
                if ct.startswith("image/"):
                    mime = ct
                d = "data:{0};base64,{1}".format(mime, b64)
                abs_seen[u] = d
                return m.group(1) + d + m.group(3)
        except Exception:
            pass
        return m.group(0)
    html = abs_re.sub(inline_abs_img, html)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html)
        html_path = f.name
    pdf_path = tempfile.mktemp(suffix='.pdf')
    try:
        cmd = [_chrome_path(), '--headless', '--no-sandbox', '--disable-gpu',
               '--force-device-scale-factor=2',
               '--no-pdf-header-footer', '--print-to-pdf=' + pdf_path, html_path]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
        with open(pdf_path, 'rb') as f:
            return f.read()
    except subprocess.CalledProcessError as e:
        frappe.log_error(f"Chrome PDF generation failed: {e.stderr}")
        frappe.throw("PDF generation failed. Check error logs.")
    finally:
        os.unlink(html_path)
        if os.path.exists(pdf_path):
            os.unlink(pdf_path)


def _send_document_via_whatsapp(doc_b64, filename, caption, chat_id_override=None):
    """Send a base64 PDF/document to the configured WhatsApp chat.

    If chat_id_override is provided, send to that chat instead of the
    default from OpenWA Settings.
    """
    _check_whatsapp_circuit_breaker()
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.enabled:
        frappe.throw("WhatsApp is disabled in OpenWA Settings.")
    base_url = (settings.base_url or "").rstrip("/")
    if not base_url:
        frappe.throw("OpenWA Base URL not set.")
    chat_id = chat_id_override or settings.chat_id
    if not chat_id:
        frappe.throw("No Chat ID in OpenWA Settings.")
    api_key = settings.get_password("api_key") or ""
    if not api_key:
        frappe.throw("No API Key in OpenWA Settings.")
    session_id = settings.session_id or "default"

    url = "{0}/api/sessions/{1}/messages/send-document".format(base_url, session_id)
    payload = {"chatId": chat_id, "base64": doc_b64, "mimetype": "application/pdf",
               "filename": filename, "caption": caption}

    try:
        r = requests.post(url, json=payload, headers={"X-API-Key": api_key}, timeout=60)
    except requests.exceptions.ConnectionError:
        frappe.throw("Cannot connect to OpenWA. Is it running?")
    except requests.exceptions.Timeout:
        frappe.throw("OpenWA timed out.")
    except Exception:
        frappe.log_error(title="WhatsApp document send exception",
                         message=frappe.get_traceback())
        frappe.throw("Unexpected error sending WhatsApp. Check Error Log.")

    if r.ok:
        try:
            return {"success": True, "message": "Sent!", "messageId": r.json().get("messageId")}
        except Exception:
            return {"success": True, "message": "Sent!"}
    error_msg = ""
    try:
        error_msg = r.json().get("message", r.text[:200])
    except Exception:
        error_msg = r.text[:200]
    frappe.log_error(title="WhatsApp send failed: {0}".format(r.status_code),
                     message="URL: {0}\nResponse: {1}".format(url, error_msg))
    frappe.throw("WhatsApp failed (HTTP {0}): {1}".format(r.status_code, error_msg))


@frappe.whitelist()
def send_print_pdf_whatsapp(doctype, name, print_format=None, chat_id=None):
    """Generate PDF for the given document and send it to WhatsApp.
    Called from the Print Preview page WhatsApp button.

    If chat_id is provided, send to that specific WhatsApp chat
    instead of the default from OpenWA Settings."""
    if not doctype or not name:
        frappe.throw("doctype and name are required")
    pdf_bytes = _generate_pdf_bytes(doctype, name, print_format=print_format)
    if not pdf_bytes or len(pdf_bytes) < 1024:
        frappe.throw("Generated PDF is empty.")
    b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    filename = "{0}_{1}.pdf".format(doctype.replace(" ", "_"), name)
    doc_title = "{0} {1}".format(doctype, name)
    caption = doc_title
    return _send_document_via_whatsapp(b64, filename, caption, chat_id_override=chat_id)


@frappe.whitelist()
def get_whatsapp_chats(search=None):
    """Fetch contacts and groups from OpenWA for the contact picker.

    Uses the /contacts endpoint (which works) instead of the broken
    /chats endpoint. Builds a combined list of individual contacts
    and (when available) group chats from recent messages.

    Responses are cached for 5 minutes.

    Args:
        search: Optional search string to filter contacts by name/number

    Response shape:
    {
        "chats": [ {"id": "...", "name": "...", "isGroup": false, "timestamp": 0}, ... ],
        "groups": [ {"id": "...", "name": "..."}, ... ]
    }
    """
    cache_key = "openwa_chats_list"
    if search:
        cache_key = "openwa_chats_search_{}".format(frappe.safe_encode(search).decode()[:50])
    cached = frappe.cache().get_value(cache_key)
    if cached:
        return cached

    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.enabled:
        frappe.throw("WhatsApp is disabled in OpenWA Settings.")
    base_url = (settings.base_url or "").rstrip("/")
    if not base_url:
        frappe.throw("OpenWA Base URL not set.")
    api_key = settings.get_password("api_key") or ""
    session_id = settings.session_id or "default"

    headers = {"X-API-Key": api_key}
    result = {"chats": [], "groups": []}

    # Fetch contacts (replaces the broken /chats endpoint)
    # The /contacts endpoint returns all known WhatsApp contacts with
    # name, pushName, and their WhatsApp ID.
    try:
        r = requests.get(
            "{0}/api/sessions/{1}/contacts?limit=200".format(base_url.rstrip("/"), session_id),
            headers=headers, timeout=15,
        )
        if r.ok:
            contacts = r.json() if isinstance(r.json(), list) else []
            seen = set()
            search_lower = (search or "").lower()
            for c in contacts:
                cid = c.get("id", "")
                if not cid or cid in seen:
                    continue
                # Client-side filter if search provided (OpenWA doesn't support search param)
                if search_lower:
                    # Check all possible name fields - contact may have name as phone format
                    # but number field without spaces/+, so check all
                    name_fields = [
                        c.get("name"),
                        c.get("pushName"),
                        c.get("formattedName"),
                        c.get("shortName"),
                        c.get("verifiedName"),
                        c.get("notifyName"),
                        c.get("number"),
                        cid,
                    ]
                    name_fields = [f for f in name_fields if f]
                    match = any(search_lower in f.lower() for f in name_fields)
                    if not match:
                        continue
                seen.add(cid)
                # Determine if this is a group or individual contact
                is_group = "@g.us" in cid
                name = (
                    c.get("name")
                    or c.get("pushName")
                    or c.get("number")
                    or cid
                )
                if is_group:
                    result["groups"].append({
                        "id": cid,
                        "name": name,
                    })
                else:
                    result["chats"].append({
                        "id": cid,
                        "name": name,
                        "isGroup": False,
                        "lastMessage": "",
                        "timestamp": 0,
                        "profilePicture": None,  # placeholder, will populate below
                    })
    except Exception:
        frappe.log_error(title="OpenWA contacts fetch failed", message=frappe.get_traceback())

    # Fetch profile pictures for individual contacts (parallel, cached)
    if result["chats"]:
        contact_ids = [c["id"] for c in result["chats"]]
        pic_cache_key = "openwa_profile_pics"
        pic_cache = frappe.cache().get_value(pic_cache_key) or {}

        to_fetch = [cid for cid in contact_ids if cid not in pic_cache]
        if to_fetch:
            new_pics = _fetch_profile_pictures(base_url, session_id, headers, to_fetch)
            pic_cache.update(new_pics)
            frappe.cache().set_value(pic_cache_key, pic_cache, expires_in_sec=3600)

        # Apply cached profile pictures
        for c in result["chats"]:
            c["profilePicture"] = pic_cache.get(c["id"])

    # Try to get groups from /groups endpoint (may fail silently)
    try:
        r = requests.get(
            "{0}/api/sessions/{1}/groups?limit=50".format(base_url.rstrip("/"), session_id),
            headers=headers, timeout=15,
        )
        if r.ok:
            groups = r.json() if isinstance(r.json(), list) else (
                r.json().get("data", []) if isinstance(r.json(), dict) else []
            )
            result["groups"] = [
                {"id": g.get("id", ""), "name": g.get("name") or g.get("id", "")}
                for g in groups
            ]
    except Exception:
        # Groups endpoint may be broken like /chats — silently ignore
        pass

    # If groups endpoint failed, try to extract group info from recent messages
    if not result["groups"]:
        try:
            r = requests.get(
                "{0}/api/sessions/{1}/messages?limit=100".format(base_url.rstrip("/"), session_id),
                headers=headers, timeout=15,
            )
            if r.ok:
                body = r.json()
                msgs = body.get("messages", []) if isinstance(body, dict) else body
                group_map = {}
                for m in msgs:
                    cid = m.get("chatId", "")
                    if "@g.us" in cid and cid not in group_map:
                        group_map[cid] = {
                            "id": cid,
                            "name": m.get("chatName") or m.get("author", "").split("@")[0] or cid,
                        }
                result["groups"] = list(group_map.values())
        except Exception:
            pass

    # Cache for 5 minutes (shorter for search results)
    expires = 60 if search else 300
    frappe.cache().set_value(cache_key, result, expires_in_sec=expires)
    return result


import sys
print(f"DEBUG: About to define search_whatsapp_contacts", file=sys.stderr)

@frappe.whitelist()
def search_whatsapp_contacts(query):
    """Server-side search for WhatsApp contacts.

    Called from the Print Preview WhatsApp contact picker when user
    types in the search box or clicks the search button.

    Note: frappe.call passes args as dict, so query may be {'query': '...'} or '...'
    """
    # Handle both string and dict (from frappe.call)
    if isinstance(query, dict):
        query = query.get('query', '')
    if not query or len(query.strip()) < 1:
        return {"chats": [], "groups": []}
    return get_whatsapp_chats(search=query.strip())


def _fetch_profile_pictures(base_url, session_id, headers, contact_ids):
    """Fetch profile picture URLs for multiple contacts in parallel.

    Returns dict: {contact_id: profile_picture_url_or_None}
    """
    import concurrent.futures

    def fetch_one(cid):
        try:
            r = requests.get(
                "{0}/api/sessions/{1}/contacts/{2}/profile-picture".format(
                    base_url.rstrip("/"), session_id, cid
                ),
                headers=headers, timeout=10,
            )
            if r.ok:
                data = r.json()
                # OpenWA returns {"url": "..."} - extract the URL
                return cid, data.get("url")
        except Exception:
            pass
        return cid, None

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for cid, url in executor.map(fetch_one, contact_ids):
            results[cid] = url
    return results


def _screenshot_html(html_content, width=1000):
    """Render HTML to PNG via Chromium, return raw PNG bytes. Crops trailing whitespace.
    Renders at 2x device pixel ratio for HD quality."""
    from PIL import Image
    import io
    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html_content)
        html_path = f.name
    png_path = tempfile.mktemp(suffix='.png')
    try:
        cmd = [_chrome_path(), '--headless', '--no-sandbox', '--disable-gpu',
               '--force-device-scale-factor=2',
               '--window-size={0},2000'.format(width),
               '--screenshot=' + png_path, html_path]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        img = Image.open(png_path)
        w, h = img.size
        pixels = img.load()
        last_content_row = 0
        for y in range(h - 1, -1, -1):
            for x in range(0, w, 50):
                if pixels[x, y] != (255, 255, 255):
                    last_content_row = y
                    break
            if last_content_row:
                break
        if last_content_row and last_content_row < h - 1:
            img = img.crop((0, 0, w, min(last_content_row + 15, h)))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()
    finally:
        os.unlink(html_path)
        if os.path.exists(png_path):
            os.unlink(png_path)


def _send_image_via_whatsapp(image_b64, filename, caption):
    """Send a base64 image to the configured WhatsApp chat."""
    _check_whatsapp_circuit_breaker()
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.enabled:
        frappe.throw("WhatsApp is disabled in OpenWA Settings.")
    base_url = (settings.base_url or "").rstrip("/")
    if not base_url:
        frappe.throw("OpenWA Base URL not set.")
    chat_id = settings.chat_id
    if not chat_id:
        frappe.throw("No Chat ID in OpenWA Settings.")
    api_key = settings.get_password("api_key") or ""
    if not api_key:
        frappe.throw("No API Key in OpenWA Settings.")
    session_id = settings.session_id or "default"

    url = "{0}/api/sessions/{1}/messages/send-document".format(base_url, session_id)
    payload = {"chatId": chat_id, "base64": image_b64, "mimetype": "image/png",
               "filename": filename, "caption": caption}

    try:
        r = requests.post(url, json=payload, headers={"X-API-Key": api_key}, timeout=30)
    except requests.exceptions.ConnectionError:
        frappe.throw("Cannot connect to OpenWA at {0}. Is it running?".format(base_url))
    except requests.exceptions.Timeout:
        frappe.throw("OpenWA timed out after 30 seconds.")
    except Exception:
        frappe.log_error(title="WhatsApp send exception", message=frappe.get_traceback())
        frappe.throw("Unexpected error sending WhatsApp. Check Error Log.")

    if r.ok:
        try:
            return {"success": True, "message": "Sent!", "messageId": r.json().get("messageId")}
        except Exception:
            return {"success": True, "message": "Sent!"}
    else:
        error_msg = ""
        try:
            error_msg = r.json().get("message", r.text[:200])
        except Exception:
            error_msg = r.text[:200]
        frappe.log_error(title="WhatsApp send failed: {0}".format(r.status_code),
                         message="URL: {0}\nResponse: {1}".format(url, error_msg))
        frappe.throw("WhatsApp failed (HTTP {0}): {1}".format(r.status_code, error_msg))


def _dispatch_screenshot(html, filename, caption, width=1000):
    """Render HTML to PNG and send via WhatsApp. Shared by all dispatch endpoints."""
    png = _screenshot_html(html, width=width)
    b64 = base64.b64encode(png).decode("utf-8")
    if len(b64) < 1024:
        frappe.throw("Generated screenshot is empty.")
    return _send_image_via_whatsapp(b64, filename, caption)


def _format_date_range(from_date, to_date):
    """Format date range for screenshot captions:
       - same day → "July 11, 2026"
       - same month, first-to-last → "July 2026"
       - same month, partial → "July 5-15, 2026"
       - same year, multi-month → "July 29 - August 12, 2026"
       - cross-year → "December 28, 2025 - January 5, 2026"
    """
    import calendar as _cal
    try:
        fy, fm, fd = [int(x) for x in from_date.split("-")]
        ty, tm, td = [int(x) for x in to_date.split("-")]
        # Same day
        if fy == ty and fm == tm and fd == td:
            return "{0} {1}, {2}".format(_cal.month_name[fm], fd, fy)
        # Same month, full month (1 to last day)
        last_day = _cal.monthrange(fy, fm)[1]
        if fy == ty and fm == tm and fd == 1 and td == last_day:
            return "{0} {1}".format(_cal.month_name[fm], fy)
        # Same month, partial
        if fy == ty and fm == tm:
            return "{0} {1}-{2}, {3}".format(_cal.month_name[fm], fd, td, fy)
        # Same year, different months
        if fy == ty:
            return "{0} {1} - {2} {3}, {4}".format(
                _cal.month_name[fm], fd, _cal.month_name[tm], td, fy)
        # Cross year
        return "{0} {1}, {2} - {3} {4}, {5}".format(
            _cal.month_name[fm], fd, fy, _cal.month_name[tm], td, ty)
    except Exception:
        return "{0} to {1}".format(from_date, to_date)


def _format_month(month):
    """Format 'YYYY-MM' as e.g. 'July 2026'."""
    import calendar as _cal
    try:
        y, m = month.split("-")
        return "{0} {1}".format(_cal.month_name[int(m)], y)
    except Exception:
        return month


def _build_html_table(columns, rows, title, subtitle=""):
    """Generic HTML table builder for screenshots."""
    ths = "".join('<th style="background:#eef6f9;color:#333;padding:8px 12px;border:1px solid #ddd;font-size:13px;">{0}</th>'.format(c) for c in columns)
    rows_html = ""
    for row in rows:
        cells = ""
        for val in row:
            cells += "<td style='padding:6px 12px;border:1px solid #eee;font-size:13px;'>{0}</td>".format(val)
        rows_html += "<tr>{0}</tr>".format(cells)
    table = '<table style="border-collapse:collapse;width:100%;font-size:13px;"><thead><tr>{0}</tr></thead><tbody>{1}</tbody></table>'.format(ths, rows_html)
    return """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{margin:0;padding:20px;font-family:Arial,sans-serif;background:#fff;}}</style>
</head><body>
<h3 style="text-align:center;margin:0 0 10px 0;color:#333;">{0}</h3>
{1}
</body></html>""".format(title, table)


def _run_report(report_name, filters):
    """Run a Frappe report and return (columns, rows) as plain lists."""
    result = frappe.call(
        "frappe.desk.query_report.run",
        report_name=report_name, filters=filters)
    if not result:
        return [], []
    raw_data = result.get("result", [])
    raw_cols = result.get("columns", [])
    col_labels = [c.get("label", c.get("fieldname", "")) if isinstance(c, dict) else str(c) for c in raw_cols]
    plain_rows = []
    for r in raw_data:
        if isinstance(r, dict):
            plain_rows.append([str(r.get(c.get("fieldname", ""), "")) if isinstance(c, dict) else "" for c in raw_cols])
        elif isinstance(r, (list, tuple)):
            plain_rows.append([str(v) for v in r])
    return col_labels, plain_rows


# ---------------------------------------------------------------------------
# Block-specific WhatsApp senders
# ---------------------------------------------------------------------------

@frappe.whitelist()
def send_dispatch_whatsapp(from_date=None, to_date=None):
    """Dispatch by JobType daily screenshot."""
    from_date = from_date or frappe.utils.today()
    to_date = to_date or frappe.utils.today()
    if from_date > to_date:
        frappe.throw("From Date cannot be after To Date")

    data = frappe.db.sql("""
        SELECT so.custom_jobtype AS job_type, so.custom_closed AS dispatch_date,
               SUM(so.custom_no_of_cylinders) AS total_cylinders,
               SUM(so.custom_cylinder_tmm) AS total_tmm
        FROM `tabSales Order` so
        WHERE so.custom_closed IS NOT NULL AND so.custom_closed != ''
          AND so.custom_closed BETWEEN %s AND %s
        GROUP BY so.custom_jobtype, so.custom_closed
        ORDER BY so.custom_jobtype ASC
    """, (from_date, to_date), as_dict=True)

    main_types = ["CFAB", "FABG", "SUPG", "CSUP", "DCRC"]
    excluded_types = ["AIIR", "SFOC", "CFOC"]
    main_rows = [[r.job_type, str(r.dispatch_date), str(int(r.total_cylinders or 0)), "{:.2f}".format(r.total_tmm or 0)] for r in data if r.job_type in main_types]
    excluded_rows = [[r.job_type, str(r.dispatch_date), str(int(r.total_cylinders or 0)), "{:.2f}".format(r.total_tmm or 0)] for r in data if r.job_type in excluded_types]

    total_cyl = sum(int(r.total_cylinders or 0) for r in data)
    total_tmm = sum(float(r.total_tmm or 0) for r in data)
    main_cyl = sum(int(r.total_cylinders or 0) for r in data if r.job_type in main_types)
    main_tmm = sum(float(r.total_tmm or 0) for r in data if r.job_type in main_types)
    excl_cyl = sum(int(r.total_cylinders or 0) for r in data if r.job_type in excluded_types)
    excl_tmm = sum(float(r.total_tmm or 0) for r in data if r.job_type in excluded_types)

    date_label = _format_date_range(from_date, to_date)
    caption = "DAILY DISPATCH ({0})\n\n".format(date_label)
    caption += "Main: {0} cyl | {1} TMM\n".format(main_cyl, "{:.2f}".format(main_tmm))
    caption += "Excluded: {0} cyl | {1} TMM".format(excl_cyl, "{:.2f}".format(excl_tmm))

    cols = ["Job Type", "Date", "Cylinders", "TMM"]
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{margin:0;padding:20px;font-family:Arial,sans-serif;background:#fff;}}</style>
</head><body>
<h3 style="text-align:center;margin:0 0 15px 0;color:#333;">DAILY DISPATCH ({0})</h3>
<div style="display:flex;gap:2rem;flex-wrap:wrap;">
  <div style="flex:1;"><strong>Main Job Types</strong>{1}</div>
  <div style="flex:1;"><strong>Excluded Job Types</strong>{2}</div>
</div>
</body></html>""".format(date_label,
        _build_table(cols, main_rows, shift_total=True) if main_rows else '<p style="text-align:center;color:#999;">No data</p>',
        _build_table(cols, excluded_rows, shift_total=True) if excluded_rows else '<p style="text-align:center;color:#999;">No data</p>')

    return _dispatch_screenshot(html, "Dispatch_{0}.png".format(from_date), caption)


def _build_table(cols, rows, shift_total=False):
    ths = "".join('<th style="background:#eef6f9;color:#333;padding:8px 12px;border:1px solid #ddd;font-size:13px;">{0}</th>'.format(c) for c in cols)
    rows_html = ""
    total_cyl = 0
    total_tmm = 0.0
    has_cyl = any('cylind' in str(c).lower() for c in cols)
    has_tmm = any('tmm' in str(c).lower() for c in cols)
    cyl_idx = next((i for i, c in enumerate(cols) if 'cylind' in str(c).lower()), -1)
    tmm_idx = next((i for i, c in enumerate(cols) if 'tmm' in str(c).lower()), -1)
    for row in rows:
        cells = "".join("<td style='padding:6px 12px;border:1px solid #eee;font-size:13px;'>{0}</td>".format(v) for v in row)
        rows_html += "<tr>{0}</tr>".format(cells)
        if cyl_idx >= 0 and cyl_idx < len(row):
            try:
                total_cyl += int(float(str(row[cyl_idx]).replace(",", "") or 0))
            except (ValueError, TypeError):
                pass
        if tmm_idx >= 0 and tmm_idx < len(row):
            try:
                total_tmm += float(str(row[tmm_idx]).replace(",", "") or 0)
            except (ValueError, TypeError):
                pass

    if has_cyl or has_tmm:
        if shift_total:
            total_cells = "<td style='padding:6px 12px;border:1px solid #eee;font-size:13px;'></td>"
            total_cells += "<td style='padding:6px 12px;border:1px solid #eee;font-size:13px;'>Total</td>"
        else:
            total_cells = "<td style='padding:6px 12px;border:1px solid #eee;font-size:13px;'>Total</td>"
        if has_cyl:
            total_cells += "<td style='padding:6px 12px;border:1px solid #eee;font-size:13px;'>{0}</td>".format(total_cyl)
        if has_tmm:
            total_cells += "<td style='padding:6px 12px;border:1px solid #eee;font-size:13px;'>{0:.2f}</td>".format(total_tmm)
        rows_html += "<tr style='font-weight:bold;background:#eef6f9;'>{0}</tr>".format(total_cells)

    return '<table style="border-collapse:collapse;width:100%;font-size:13px;"><thead><tr>{0}</tr></thead><tbody>{1}</tbody></table>'.format(ths, rows_html)


@frappe.whitelist()
def send_engraving_whatsapp(from_date=None, to_date=None):
    """Engraving details — matches block: grouped by machine, vertical tables, TMM Total row."""
    from_date = from_date or frappe.utils.today()
    to_date = to_date or frappe.utils.today()

    result = frappe.call("frappe.desk.query_report.run",
        report_name="Engraving Details By Date",
        filters={"from_date": from_date, "to_date": to_date}) or {}
    data = result.get("result", [])
    cols = result.get("columns", [])

    # Block column order — including Machine for grouping (hidden in display)
    desired_order = ["Sales Order", "Date", "Customer", "Job Name", "TMM",
                     "Color", "Circum", "Length", "Stylus", "Screen/Angel",
                     "Shadow", "Highlight", "Channel", "Machine", "Remarks"]
    ordered_cols = []
    for label in desired_order:
        c = next((x for x in cols if x.get('label') == label), None)
        if c:
            ordered_cols.append(c)
    for col in cols:
        if not any(c.get('label') == col.get('label') for c in ordered_cols):
            ordered_cols.append(col)

    machine_col = next((c for c in ordered_cols if c.get('label') == "Machine"), None)
    tmm_col = next((c for c in ordered_cols if c.get('label') == "TMM"), None)
    display_cols = [c for c in ordered_cols if c.get('label') != "Machine"]

    # Group rows by machine
    groups = {}
    for row in data:
        machine = row.get(machine_col.get('fieldname')) if machine_col else "Unspecified"
        if not machine:
            machine = "Unspecified"
        groups.setdefault(machine, []).append(row)
    machine_names = sorted(groups.keys())

    th_style = "background:#eef6f9;color:#333;padding:5px 8px;border:1px solid #ddd;font-size:10px;white-space:nowrap;"
    td_style = "padding:3px 8px;border:1px solid #eee;font-size:10px;white-space:nowrap;"

    tables_html = ""
    grand_tmm = 0.0
    for machine in machine_names:
        machine_rows = groups[machine]
        machine_tmm = 0.0
        ths = "".join(
            '<th style="{0}">{1}</th>'.format(th_style, c.get('label'))
            for c in display_cols)
        rows_html = ""
        for row in machine_rows:
            cells = ""
            for c in display_cols:
                val = row.get(c.get('fieldname'), "") or ""
                if c.get('label') == "TMM" and val != "":
                    val = "{:.2f}".format(float(val))
                cells += "<td style='{0}'>{1}</td>".format(td_style, val)
            rows_html += "<tr>{0}</tr>".format(cells)
            if tmm_col:
                tmm_val = float(row.get(tmm_col.get('fieldname')) or 0)
                machine_tmm += tmm_val

        # Total row
        total_cells = ""
        tmm_idx_disp = next((i for i, c in enumerate(display_cols) if c.get('label') == "TMM"), -1)
        for i, c in enumerate(display_cols):
            if i == tmm_idx_disp:
                total_cells += "<td style='{0}'><b>{1:.2f}</b></td>".format(td_style, machine_tmm)
            elif i == 0:
                total_cells += "<td style='{0}'><b>Total</b></td>".format(td_style)
            else:
                total_cells += "<td style='{0}'></td>".format(td_style)
        rows_html += "<tr style='font-weight:bold;background:#eef6f9;'>{0}</tr>".format(total_cells)
        grand_tmm += machine_tmm

        tables_html += """<div style="margin-bottom:15px;">
<h5 style="text-align:center; margin:8px 0;">{0}</h5>
<table style="border-collapse:collapse;width:100%;font-size:12px;">
<thead><tr>{1}</tr></thead>
<tbody>{2}</tbody>
</table></div>""".format(machine, ths, rows_html)

    # Caption — per machine + grand total with date range
    date_label = _format_date_range(from_date, to_date)
    caption = "ENGRAVING DETAILS ({0})\n\n".format(date_label)
    for machine in machine_names:
        m_tmm = sum(float(r.get(tmm_col.get('fieldname')) or 0) for r in groups[machine])
        caption += "{0}: {1:.2f} TMM\n".format(machine, m_tmm)
    caption += "Total TMM: *{0:.2f}*".format(grand_tmm)

    date_label = _format_date_range(from_date, to_date)
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{margin:0;padding:20px;font-family:Arial,sans-serif;background:#fff;}}</style>
</head><body>
<h3 style="text-align:center;margin:0 0 15px 0;color:#333;">ENGRAVING DETAILS ({0})</h3>
{1}
</body></html>""".format(date_label, tables_html) if machine_names else """<!DOCTYPE html><html><body><p>No data</p></body></html>"""

    return _dispatch_screenshot(html, "Engraving_{0}.png".format(from_date), caption, width=2400)


@frappe.whitelist()
def send_engraving_monthly_whatsapp(from_date=None, to_date=None):
    """Engraving MONTHLY SUMMARY — matches block's bottom section: Machine | Total TMM only."""
    from_date = from_date or frappe.utils.today()
    to_date = to_date or frappe.utils.today()

    result = frappe.call("frappe.desk.query_report.run",
        report_name="Engraving Details By Date",
        filters={"from_date": from_date, "to_date": to_date}) or {}
    data = result.get("result", [])
    cols = result.get("columns", [])

    machine_col = next((c for c in cols if c.get('label') == "Machine"), None)
    tmm_col = next((c for c in cols if c.get('label') == "TMM"), None)
    if not machine_col or not tmm_col:
        frappe.throw("Required columns (Machine, TMM) not found.")

    machine_totals = {}
    for row in data:
        machine = row.get(machine_col.get('fieldname')) or "Unspecified"
        tmm = float(row.get(tmm_col.get('fieldname')) or 0)
        machine_totals[machine] = machine_totals.get(machine, 0) + tmm

    machines = sorted(machine_totals.keys())
    grand_total = sum(machine_totals[m] for m in machines)

    th_style = "background:#eef6f9;color:#333;padding:6px 10px;border:1px solid #ddd;font-size:13px;"
    td_style = "padding:6px 10px;border:1px solid #eee;font-size:13px;"

    rows_html = ""
    for m in machines:
        rows_html += "<tr><td style='{0}'>{1}</td><td style='{0}'>{2:.2f}</td></tr>".format(td_style, m, machine_totals[m])
    rows_html += "<tr style='font-weight:bold;background:#eef6f9;'><td style='{0}'>Total</td><td style='{0}'>{1:.2f}</td></tr>".format(td_style, grand_total)

    caption = "ENGRAVING MONTHLY ({0})\n\n".format(_format_date_range(from_date, to_date))
    for m in machines:
        caption += "{0}: {1:.2f} TMM\n".format(m, machine_totals[m])
    caption += "Total TMM: *{0:.2f}*".format(grand_total)

    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{margin:0;padding:20px;font-family:Arial,sans-serif;background:#fff;}}
table{{border-collapse:collapse;width:auto;}}
</style></head><body>
<h3 style="text-align:center;margin:0 0 15px 0;color:#333;">ENGRAVING MONTHLY ({0})</h3>
<table>
<thead><tr><th style="{1}">Machine</th><th style="{1}">Total TMM</th></tr></thead>
<tbody>{2}</tbody>
</table>
</body></html>""".format(_format_date_range(from_date, to_date), th_style, rows_html)

    return _dispatch_screenshot(html, "EngravingMonthly_{0}.png".format(from_date), caption, width=600)


@frappe.whitelist()
def send_proofing_whatsapp(from_date=None, to_date=None):
    """Proofing details — matches block: Date column hidden, QTY/Length/Circum renamed, Total QTY+TMM row."""
    from_date = from_date or frappe.utils.today()
    to_date = to_date or frappe.utils.today()

    result = frappe.call("frappe.desk.query_report.run",
        report_name="Proofing Details By Date",
        filters={"from_date": from_date, "to_date": to_date}) or {}
    data = result.get("result", [])
    cols = result.get("columns", [])

    # Block logic: drop Date column; rename No of Cylinders→QTY, Final Height→Circum, Full Width→Length
    cols = [c for c in cols if c.get('label') != "Date"]
    for c in cols:
        if c.get('label') == "No of Cylinders":
            c['label'] = "QTY"
        elif c.get('label') == "Final Height":
            c['label'] = "Circum"
        elif c.get('label') == "Full Width":
            c['label'] = "Length"

    qty_idx = next((i for i, c in enumerate(cols) if c.get('label') == "QTY"), -1)
    tmm_idx = next((i for i, c in enumerate(cols) if c.get('label') == "TMM"), -1)

    th_style = "background:#eef6f9;color:#333;padding:6px 10px;border:1px solid #ddd;font-size:12px;"
    td_style = "padding:4px 10px;border:1px solid #eee;font-size:12px;"

    ths = "".join('<th style="{0}">{1}</th>'.format(th_style, c.get('label')) for c in cols)
    rows_html = ""
    total_qty = 0
    total_tmm = 0.0
    for row in data:
        cells = ""
        for i, c in enumerate(cols):
            val = row.get(c.get('fieldname'), "") or ""
            if c.get('label') == "TMM" and val != "":
                val = "{:.2f}".format(float(val))
            cells += "<td style='{0}'>{1}</td>".format(td_style, val)
        rows_html += "<tr>{0}</tr>".format(cells)
        if qty_idx >= 0:
            v = row.get(cols[qty_idx].get('fieldname'))
            if v is not None and v != "":
                total_qty += int(float(v) or 0)
        if tmm_idx >= 0:
            v = row.get(cols[tmm_idx].get('fieldname'))
            if v is not None and v != "":
                total_tmm += float(v or 0)

    # Total row — Total in first cell, QTY total / TMM total in their columns, blanks elsewhere
    total_cells = ""
    for i, c in enumerate(cols):
        if i == qty_idx:
            total_cells += "<td style='{0}'><b>{1}</b></td>".format(td_style, total_qty)
        elif i == tmm_idx:
            total_cells += "<td style='{0}'><b>{1:.2f}</b></td>".format(td_style, total_tmm)
        elif i == 0:
            total_cells += "<td style='{0}'><b>Total</b></td>".format(td_style)
        else:
            total_cells += "<td style='{0}'></td>".format(td_style)
    rows_html += "<tr style='font-weight:bold;background:#eef6f9;'>{0}</tr>".format(total_cells)

    date_label = _format_date_range(from_date, to_date)
    caption = "PROOFING ({0})\nQTY: *{1}*\nTotal TMM: *{2:.2f}*".format(date_label, total_qty, total_tmm)

    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{margin:0;padding:20px;font-family:Arial,sans-serif;background:#fff;}}</style>
</head><body>
<h3 style="text-align:center;margin:0 0 15px 0;color:#333;">PROOFING DETAILS ({0})</h3>
<table style="border-collapse:collapse;width:100%;font-size:12px;">
<thead><tr>{1}</tr></thead>
<tbody>{2}</tbody>
</table>
</body></html>""".format(date_label, ths, rows_html)

    return _dispatch_screenshot(html, "Proofing_{0}.png".format(from_date), caption)


@frappe.whitelist()
def send_dispatch_customer_whatsapp(from_date=None, to_date=None):
    """Customer dispatch screenshot — matches block: per-customer rows + single Total row."""
    from_date = from_date or frappe.utils.today()
    to_date = to_date or frappe.utils.today()

    result = frappe.call("frappe.desk.query_report.run",
        report_name="Dispatch Monthly Customer",
        filters={"from_date": from_date, "to_date": to_date}) or {}
    data = result.get("result", [])
    cols = result.get("columns", [])

    th_style = "background:#eef6f9;color:#333;padding:6px 10px;border:1px solid #ddd;font-size:12px;"
    td_style = "padding:4px 10px;border:1px solid #eee;font-size:12px;"

    ths = "".join('<th style="{0}">{1}</th>'.format(th_style, c.get('label')) for c in cols)
    rows_html = ""
    total_cyl = 0
    total_tmm = 0.0
    cyl_idx = next((i for i, c in enumerate(cols) if 'cylind' in str(c.get('label','')).lower()), -1)
    tmm_idx = next((i for i, c in enumerate(cols) if 'tmm' in str(c.get('label','')).lower()), -1)
    for row in data:
        cells = ""
        for c in cols:
            val = row.get(c.get('fieldname'), "") or ""
            if c.get('fieldname') == "total_tmm" and val != "":
                val = "{:.2f}".format(float(val))
            elif c.get('fieldname') == "total_cylinders" and val != "":
                val = int(float(val))
            cells += "<td style='{0}'>{1}</td>".format(td_style, val)
        rows_html += "<tr>{0}</tr>".format(cells)
        if cyl_idx >= 0:
            v = row.get(cols[cyl_idx].get('fieldname'))
            if v is not None and v != "":
                total_cyl += int(float(v) or 0)
        if tmm_idx >= 0:
            v = row.get(cols[tmm_idx].get('fieldname'))
            if v is not None and v != "":
                total_tmm += float(v or 0)

    # Total row — Total | CYL | TMM (3-column output: first column Total, second/third = sums)
    total_cells = "<td style='{0}'><b>Total</b></td>".format(td_style)
    if cyl_idx >= 0:
        total_cells += "<td style='{0}'><b>{1}</b></td>".format(td_style, total_cyl)
    if tmm_idx >= 0:
        total_cells += "<td style='{0}'><b>{1:.2f}</b></td>".format(td_style, total_tmm)
    rows_html += "<tr style='font-weight:bold;background:#eef6f9;'>{0}</tr>".format(total_cells)

    # Caption — Customers is the only "summary" metric, total already in screenshot
    date_label = _format_date_range(from_date, to_date)
    caption = "CUSTOMER DISPATCH ({0})\nCustomers: {1}".format(date_label, len(data))

    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{margin:0;padding:20px;font-family:Arial,sans-serif;background:#fff;}}</style>
</head><body>
<h3 style="text-align:center;margin:0 0 15px 0;color:#333;">CUSTOMER DISPATCH ({0})</h3>
<table style="border-collapse:collapse;width:100%;font-size:12px;">
<thead><tr>{1}</tr></thead>
<tbody>{2}</tbody>
</table>
</body></html>""".format(date_label, ths, rows_html)

    return _dispatch_screenshot(html, "CustomerDispatch_{0}.png".format(from_date), caption)


@frappe.whitelist()
def send_dispatch_monthly_whatsapp(from_date=None, to_date=None):
    """Monthly dispatch by job type — aggregated (no per-day rows)."""
    from_date = from_date or frappe.utils.today()
    to_date = to_date or frappe.utils.today()
    if from_date > to_date:
        frappe.throw("From Date cannot be after To Date")

    data = frappe.db.sql("""
        SELECT so.custom_jobtype AS job_type,
               SUM(so.custom_no_of_cylinders) AS total_cylinders,
               SUM(so.custom_cylinder_tmm) AS total_tmm
        FROM `tabSales Order` so
        WHERE so.custom_closed IS NOT NULL AND so.custom_closed != ''
          AND so.custom_closed BETWEEN %s AND %s
        GROUP BY so.custom_jobtype
        ORDER BY so.custom_jobtype ASC
    """, (from_date, to_date), as_dict=True)

    main_types = ["CFAB", "FABG", "SUPG", "CSUP", "DCRC"]
    excluded_types = ["AIIR", "SFOC", "CFOC"]
    main_rows = [[r.job_type, str(int(r.total_cylinders or 0)), "{:.2f}".format(r.total_tmm or 0)] for r in data if r.job_type in main_types]
    excluded_rows = [[r.job_type, str(int(r.total_cylinders or 0)), "{:.2f}".format(r.total_tmm or 0)] for r in data if r.job_type in excluded_types]

    total_cyl = sum(int(r.total_cylinders or 0) for r in data)
    total_tmm = sum(float(r.total_tmm or 0) for r in data)
    main_cyl = sum(int(r.total_cylinders or 0) for r in data if r.job_type in main_types)
    main_tmm = sum(float(r.total_tmm or 0) for r in data if r.job_type in main_types)
    excl_cyl = sum(int(r.total_cylinders or 0) for r in data if r.job_type in excluded_types)
    excl_tmm = sum(float(r.total_tmm or 0) for r in data if r.job_type in excluded_types)

    date_label = _format_date_range(from_date, to_date)
    caption = "MONTHLY DISPATCH ({0})\n\n".format(date_label)
    caption += "Main: {0} cyl | {1} TMM\n".format(main_cyl, "{:.2f}".format(main_tmm))
    caption += "Excluded: {0} cyl | {1} TMM".format(excl_cyl, "{:.2f}".format(excl_tmm))

    cols = ["Job Type", "Cylinders", "TMM"]
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{margin:0;padding:20px;font-family:Arial,sans-serif;background:#fff;}}</style>
</head><body>
<h3 style="text-align:center;margin:0 0 15px 0;color:#333;">MONTHLY DISPATCH ({0})</h3>
<div style="display:flex;gap:2rem;flex-wrap:wrap;">
  <div style="flex:1;"><strong>Main Job Types</strong>{1}</div>
  <div style="flex:1;"><strong>Excluded Job Types</strong>{2}</div>
</div>
</body></html>""".format(date_label,
        _build_table(cols, main_rows, shift_total=False) if main_rows else '<p style="text-align:center;color:#999;">No data</p>',
        _build_table(cols, excluded_rows, shift_total=False) if excluded_rows else '<p style="text-align:center;color:#999;">No data</p>')

    return _dispatch_screenshot(html, "MonthlyDispatch_{0}.png".format(from_date), caption)


@frappe.whitelist()
def send_dispatch_yearly_whatsapp(from_date=None, to_date=None):
    """Yearly dispatch screenshot — matches block: grouped by job type, Main vs Excluded."""
    from_date = from_date or frappe.utils.today()
    to_date = to_date or frappe.utils.today()

    data = frappe.db.sql("""
        SELECT so.custom_jobtype AS job_type,
               SUM(so.custom_no_of_cylinders) AS total_cylinders,
               SUM(so.custom_cylinder_tmm) AS total_tmm
        FROM `tabSales Order` so
        WHERE so.custom_closed IS NOT NULL AND so.custom_closed != ''
          AND so.custom_closed BETWEEN %s AND %s
        GROUP BY so.custom_jobtype
        ORDER BY so.custom_jobtype ASC
    """, (from_date, to_date), as_dict=True)

    main_types = ["CFAB", "FABG", "SUPG", "CSUP", "DCRC"]
    excluded_types = ["AIIR", "SFOC", "CFOC"]

    main_rows = [[r.job_type, str(int(r.total_cylinders or 0)),
                  "{:.2f}".format(r.total_tmm or 0)]
                 for r in data if r.job_type in main_types]
    excluded_rows = [[r.job_type, str(int(r.total_cylinders or 0)),
                      "{:.2f}".format(r.total_tmm or 0)]
                     for r in data if r.job_type in excluded_types]

    main_cyl = sum(int(r.total_cylinders or 0) for r in data if r.job_type in main_types)
    main_tmm = sum(float(r.total_tmm or 0) for r in data if r.job_type in main_types)
    excl_cyl = sum(int(r.total_cylinders or 0) for r in data if r.job_type in excluded_types)
    excl_tmm = sum(float(r.total_tmm or 0) for r in data if r.job_type in excluded_types)
    total_cyl = main_cyl + excl_cyl
    total_tmm = main_tmm + excl_tmm

    date_label = _format_date_range(from_date, to_date)
    caption = "DISPATCH YEARLY ({0})\nMain: {1} cyl | {2} TMM\nExcluded: {3} cyl | {4} TMM".format(
        date_label, main_cyl, "{:.2f}".format(main_tmm), excl_cyl, "{:.2f}".format(excl_tmm))

    cols = ["Job Type", "Cylinders", "TMM"]
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{margin:0;padding:20px;font-family:Arial,sans-serif;background:#fff;}}</style>
</head><body>
<h3 style="text-align:center;margin:0 0 15px 0;color:#333;">DISPATCH YEARLY ({0})</h3>
<div style="display:flex;gap:2rem;flex-wrap:wrap;">
  <div style="flex:1;"><strong>Main Types</strong>{1}</div>
  <div style="flex:1;"><strong>Excluded Types</strong>{2}</div>
</div>
</body></html>""".format(date_label,
        _build_table(cols, main_rows, shift_total=False) if main_rows else '<p style="text-align:center;color:#999;">No data</p>',
        _build_table(cols, excluded_rows, shift_total=False) if excluded_rows else '<p style="text-align:center;color:#999;">No data</p>')

    return _dispatch_screenshot(html, "DispatchYearly_{0}.png".format(from_date), caption)


@frappe.whitelist()
def send_job_status_whatsapp(from_date=None, to_date=None):
    """Sales order status — matches block: Workflow State/Count/TMM/Percentage (count-based), no TOTAL row, with top summary line."""
    from_date = from_date or "2025-10-01"
    to_date = to_date or frappe.utils.today()

    # Block uses frappe.client.get_list with same filters
    conditions = [
        ["docstatus", "in", [0, 1]],
        ["transaction_date", ">=", from_date],
        ["transaction_date", "<=", to_date],
        ["workflow_state", "!=", "Ready for Billing"]
    ]
    sales_orders = frappe.call("frappe.client.get_list",
        doctype="Sales Order",
        fields=["workflow_state", "custom_cylinder_tmm"],
        filters=conditions,
        limit_page_length=10000) or []

    # Group by workflow_state
    state_map = {}
    for row in sales_orders:
        state = row.get("workflow_state") or "(blank)"
        tmm = float(row.get("custom_cylinder_tmm") or 0)
        if state not in state_map:
            state_map[state] = {"count": 0, "tmm": 0}
        state_map[state]["count"] += 1
        state_map[state]["tmm"] += tmm

    states = sorted(state_map.keys())
    total_count = sum(state_map[s]["count"] for s in states)
    total_tmm = sum(state_map[s]["tmm"] for s in states)

    th_style = "background:#eef6f9;color:#333;padding:5px 8px;border:1px solid #ddd;font-size:10px;white-space:nowrap;"
    td_style = "padding:3px 8px;border:1px solid #eee;font-size:10px;white-space:nowrap;"
    summary_style = "margin-bottom:10px;font-weight:bold;"

    rows_html = ""
    for state in states:
        cnt = state_map[state]["count"]
        tmm = state_map[state]["tmm"]
        pct = ((cnt / total_count) * 100) if total_count else 0
        rows_html += "<tr><td style='{0}'>{1}</td><td style='{0}'>{2}</td><td style='{0}'>{3:.2f}</td><td style='{0}'>{4:.1f}%</td></tr>".format(
            td_style, state, cnt, tmm, pct)

    date_label = _format_date_range(from_date, to_date)
    caption = "SALES ORDER STATUS ({0})\nOrders: {1}\nTotal TMM: *{2:.2f}*".format(date_label, total_count, total_tmm)

    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{margin:0;padding:20px;font-family:Arial,sans-serif;background:#fff;}}</style>
</head><body>
<h3 style="text-align:center;margin:0 0 15px 0;color:#333;">SALES ORDER STATUS ({0})</h3>
<div style="{1}">Total Sales Orders (excluding "Ready for Billing"): {2}<br>Total TMM: {3:.2f}</div>
<table style="border-collapse:collapse;width:100%;font-size:12px;">
<thead><tr>
<th style="{3}">Workflow State</th>
<th style="{3}">Count</th>
<th style="{4}">Total TMM</th>
<th style="{4}">Percentage</th>
</tr></thead>
<tbody>{5}</tbody>
</table>
</body></html>""".format(date_label, summary_style, total_count, total_tmm, th_style, rows_html)

    return _dispatch_screenshot(html, "JobStatus_{0}.png".format(from_date), caption)


@frappe.whitelist()
def send_monthly_report_whatsapp(month=None):
    """Kreativ monthly summary — matches block: pivoted proofing/engraving/dispatch tables."""
    if not month:
        frappe.throw("Month is required (YYYY-MM)")
    from_date = "{0}-01".format(month)
    last_day = frappe.utils.get_last_day(from_date).day
    to_date = "{0}-{1:02d}".format(month, last_day)
    filters = {"from_date": from_date, "to_date": to_date}

    # Title suffix — month name from selected filter, e.g. "2026-07" -> "July 2026"
    month_label = _format_month(month)
    title_text = "MONTHLY SUMMARY ({0})".format(month_label)

    # Fetch raw report data
    proof_result = frappe.call("frappe.desk.query_report.run",
        report_name="Proofing Details By Date", filters=filters) or {}
    engr_result = frappe.call("frappe.desk.query_report.run",
        report_name="Engraving Details By Date", filters=filters) or {}
    disp_result = frappe.call("frappe.desk.query_report.run",
        report_name="Dispatch By Custom_Closed", filters=filters) or {}

    proof_data = proof_result.get("result", [])
    engr_data = engr_result.get("result", [])
    disp_data = disp_result.get("result", [])

    th = "background:#eef6f9;color:#333;padding:6px 8px;border:1px solid #ddd;font-size:12px;"
    td = "padding:4px 8px;border:1px solid #eee;font-size:12px;"

    # --- PROOFING: group by date → Date, QTY, TMM ---
    pgroups = {}
    for row in proof_data:
        dt = str(row.get("date", "")) if isinstance(row, dict) else ""
        qty = float(row.get("no_of_cylinders", 0) or 0) if isinstance(row, dict) else 0
        tmm = float(row.get("tmm", 0) or 0) if isinstance(row, dict) else 0
        if dt:
            if dt not in pgroups:
                pgroups[dt] = {"qty": 0, "tmm": 0}
            pgroups[dt]["qty"] += qty
            pgroups[dt]["tmm"] += tmm

    p_dates = sorted(pgroups.keys())
    p_total_qty = sum(g["qty"] for g in pgroups.values())
    p_total_tmm = sum(g["tmm"] for g in pgroups.values())
    p_avg_tmm = p_total_tmm / len(p_dates) if p_dates else 0

    p_rows_html = ""
    for dt in p_dates:
        g = pgroups[dt]
        p_rows_html += "<tr><td style='{0}'>{1}</td><td style='{0}'>{2}</td><td style='{0}'>{3:.2f}</td></tr>".format(td, dt, int(g["qty"]), g["tmm"])
    p_rows_html += "<tr style='font-weight:bold;background:#eef6f9;'><td style='{0}'>Total</td><td style='{0}'>{1}</td><td style='{0}'>{2:.2f}</td></tr>".format(td, int(p_total_qty), p_total_tmm)
    p_rows_html += "<tr style='background:#f9f9f9;'><td style='{0}'>Average</td><td style='{0}'></td><td style='{0}'>{1:.2f}</td></tr>".format(td, p_avg_tmm)

    proofing_html = """<table style="border-collapse:collapse;width:100%;font-size:12px;">
<thead><tr><th style="{0}">Date</th><th style="{0}">QTY</th><th style="{0}">TMM</th></tr></thead>
<tbody>{1}</tbody></table>""".format(th, p_rows_html) if p_dates else '<p style="text-align:center;color:#999;">No data</p>'

    # --- ENGRAVING: group by date+machine → pivot columns per machine ---
    emgroups = {}
    all_machines = set()
    for row in engr_data:
        if not isinstance(row, dict):
            continue
        dt = str(row.get("date", ""))
        machine = row.get("machine", "Unspecified") or "Unspecified"
        tmm = float(row.get("tmm", 0) or 0)
        if dt:
            if dt not in emgroups:
                emgroups[dt] = {}
            emgroups[dt][machine] = emgroups[dt].get(machine, 0) + tmm
            all_machines.add(machine)

    e_dates = sorted(emgroups.keys())
    e_machines = sorted(all_machines)
    e_grand = sum(sum(emgroups[d].get(m, 0) for m in e_machines) for d in e_dates)
    e_avg = e_grand / len(e_dates) if e_dates else 0

    e_mach_th = "".join('<th style="{0}">{1}</th>'.format(th, m) for m in e_machines)
    e_rows_html = ""
    for dt in e_dates:
        cells = "<td style='{0}'>{1}</td>".format(td, dt)
        for m in e_machines:
            val = emgroups[dt].get(m, 0)
            cells += "<td style='{0}'>{1:.2f}</td>".format(td, val)
        date_total = sum(emgroups[dt].get(m, 0) for m in e_machines)
        cells += "<td style='{0}'>{1:.2f}</td>".format(td, date_total)
        e_rows_html += "<tr>{0}</tr>".format(cells)

    e_total_cells = "<td style='{0}'>Total</td>".format(td)
    for m in e_machines:
        mtotal = sum(emgroups[d].get(m, 0) for d in e_dates)
        e_total_cells += "<td style='{0}'>{1:.2f}</td>".format(td, mtotal)
    e_total_cells += "<td style='{0}'>{1:.2f}</td>".format(td, e_grand)
    e_rows_html += "<tr style='font-weight:bold;background:#eef6f9;'>{0}</tr>".format(e_total_cells)

    e_avg_cells = "<td style='{0}'>Average</td>".format(td)
    for m in e_machines:
        e_avg_cells += "<td style='{0}'></td>".format(td)
    e_avg_cells += "<td style='{0}'>{1:.2f}</td>".format(td, e_avg)
    e_rows_html += "<tr style='background:#f9f9f9;'>{0}</tr>".format(e_avg_cells)

    engraving_html = """<table style="border-collapse:collapse;width:100%;font-size:12px;">
<thead><tr><th style="{0}">Date</th>{1}<th style="{0}">Total TMM</th></tr></thead>
<tbody>{2}</tbody></table>""".format(th, e_mach_th, e_rows_html) if e_dates else '<p style="text-align:center;color:#999;">No data</p>'

    # --- DISPATCH: pivot by date × job type, split Main/Excluded ---
    main_types = ["CFAB", "FABG", "SUPG", "CSUP", "DCRC"]
    excluded_types = ["AIIR", "SFOC", "CFOC"]

    def build_dispatch_pivot(data, job_types):
        # Group by date × job_type
        dj = {}
        present_types = set()
        for row in data:
            if not isinstance(row, dict):
                continue
            dt = str(row.get("date", ""))
            jt = row.get("job_type", "")
            cyl = float(row.get("total_cylinders", 0) or 0)
            tmm = float(row.get("total_tmm", 0) or 0)
            if dt and jt in job_types:
                if dt not in dj:
                    dj[dt] = {}
                if jt not in dj[dt]:
                    dj[dt][jt] = {"cyl": 0, "tmm": 0}
                dj[dt][jt]["cyl"] += cyl
                dj[dt][jt]["tmm"] += tmm
                present_types.add(jt)

        used_types = sorted(present_types)
        dates = sorted(dj.keys())
        if not dates:
            return '<p style="text-align:center;color:#999;">No data</p>'

        hdr1 = '<th style="{0}">Date</th>'.format(th)
        hdr2 = '<th style="{0}"></th>'.format(th)
        for jt in used_types:
            hdr1 += '<th colspan="2" style="{0}">{1}</th>'.format(th, jt)
            hdr2 += '<th style="{0}">QTY</th><th style="{0}">TMM</th>'.format(th)
        hdr1 += '<th style="{0}">Total QTY</th><th style="{0}">Total TMM</th>'.format(th)
        hdr2 += '<th style="{0}"></th><th style="{0}"></th>'.format(th)

        rows_html = ""
        gt_cyl = 0
        for dt in dates:
            cells = "<td style='{0}'>{1}</td>".format(td, dt)
            d_cyl = 0
            for jt in used_types:
                vals = dj[dt].get(jt, {"cyl": 0, "tmm": 0})
                d_cyl += vals["cyl"]
                cells += "<td style='{0}'>{1}</td><td style='{0}'>{2:.2f}</td>".format(td, int(vals["cyl"]), vals["tmm"])
            d_tmm = sum(dj[dt].get(jt, {}).get("tmm", 0) for jt in used_types)
            gt_cyl += d_cyl
            cells += "<td style='{0}'>{1}</td><td style='{0}'>{2:.2f}</td>".format(td, int(d_cyl), d_tmm)
            rows_html += "<tr>{0}</tr>".format(cells)

        total_cells = "<td style='{0}'>Total</td>".format(td)
        gt_tmm = 0
        for jt in used_types:
            jt_cyl = sum(dj[d].get(jt, {}).get("cyl", 0) for d in dates)
            jt_tmm = sum(dj[d].get(jt, {}).get("tmm", 0) for d in dates)
            gt_tmm += jt_tmm
            total_cells += "<td style='{0}'>{1}</td><td style='{0}'>{2:.2f}</td>".format(td, int(jt_cyl), jt_tmm)
        total_cells += "<td style='{0}'>{1}</td><td style='{0}'>{2:.2f}</td>".format(td, int(gt_cyl), gt_tmm)
        rows_html += "<tr style='font-weight:bold;background:#eef6f9;'>{0}</tr>".format(total_cells)

        avg_cells = "<td style='{0}'>Average</td>".format(td)
        for jt in used_types:
            avg_cells += "<td style='{0}'></td><td style='{0}'></td>".format(td)
        avg_tmm = gt_tmm / len(dates) if dates else 0
        avg_cells += "<td style='{0}'></td><td style='{0}'>{1:.2f}</td>".format(td, avg_tmm)
        rows_html += "<tr style='background:#f9f9f9;'>{0}</tr>".format(avg_cells)

        return """<table style="border-collapse:collapse;width:100%;font-size:12px;">
<thead><tr>{0}</tr><tr>{1}</tr></thead>
<tbody>{2}</tbody></table>""".format(hdr1, hdr2, rows_html)

    main_dispatch = build_dispatch_pivot(disp_data, main_types)
    excl_dispatch = build_dispatch_pivot(disp_data, excluded_types)

    dispatch_html = '<div><h6 style="text-align:center;">Main Types</h6>{0}</div><div style="margin-top:10px;"><h6 style="text-align:center;">Excluded Types</h6>{1}</div>'.format(
        main_dispatch, excl_dispatch)

    # Caption totals — must match what's shown in the screenshot tables
    main_d_cyl = sum(float(r.get("total_cylinders", 0) or 0) for r in disp_data
                     if isinstance(r, dict) and r.get("job_type") in main_types)
    main_d_tmm = sum(float(r.get("total_tmm", 0) or 0) for r in disp_data
                     if isinstance(r, dict) and r.get("job_type") in main_types)
    excl_d_cyl = sum(float(r.get("total_cylinders", 0) or 0) for r in disp_data
                     if isinstance(r, dict) and r.get("job_type") in excluded_types)
    excl_d_tmm = sum(float(r.get("total_tmm", 0) or 0) for r in disp_data
                     if isinstance(r, dict) and r.get("job_type") in excluded_types)
    # p_total_qty already aggregated in proofing section (matches screenshot's Total row)
    # e_grand is engraving TMM total (matches screenshot's Total row)

    title_bold = "*{0}*".format(title_text)
    caption = "{0}\n\n".format(title_bold)
    caption += "Proofing: {0} cyl | {1} TMM\n".format(int(p_total_qty), "{:.2f}".format(p_total_tmm))
    caption += "Engraving: {1} TMM\n".format(0, "{:.2f}".format(e_grand))
    caption += "Dispatch Main: {0} cyl | {1} TMM\n".format(int(main_d_cyl), "{:.2f}".format(main_d_tmm))
    caption += "Dispatch Excluded: {0} cyl | {1} TMM".format(int(excl_d_cyl), "{:.2f}".format(excl_d_tmm))

    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{margin:0;padding:20px;font-family:Arial,sans-serif;background:#fff;}}</style>
</head><body>
<h3 style="text-align:center;margin:0 0 15px 0;color:#333;">{3}</h3>
<div style="margin-bottom:15px;"><h5 style="text-align:center;">PROOFING</h5>{0}</div>
<div style="margin-bottom:15px;"><h5 style="text-align:center;">ENGRAVING</h5>{1}</div>
<div style="margin-bottom:15px;"><h5 style="text-align:center;">DISPATCH</h5>{2}</div>
</body></html>""".format(proofing_html, engraving_html, dispatch_html, title_text)

    return _dispatch_screenshot(html, "Monthly_{0}.png".format(month), caption)


# ---------------------------------------------------------------------------
# OpenWA Settings Whitelisted Methods (for Gravures Custom - kreativ216)
# ---------------------------------------------------------------------------

def _get_openwa_settings(require_chat=False, require_enabled=False):
    """Get OpenWA Settings single doc, throws if not configured.

    Set require_chat=True for send operations that need a recipient chat_id.
    Set require_enabled=True for send operations that require the feature enabled.
    """
    settings = frappe.get_single("OpenWA Settings")
    if require_enabled and not settings.enabled:
        frappe.throw("WhatsApp is disabled in OpenWA Settings.")
    if not settings.base_url:
        frappe.throw("OpenWA Base URL is not set. Configure it in OpenWA Settings.")
    api_key = settings.get_password("api_key", raise_exception=False)
    if not api_key:
        frappe.throw("OpenWA API Key is not set. Configure it in OpenWA Settings.")
    if require_chat and not settings.chat_id:
        frappe.throw("No Recipient Chat ID set in OpenWA Settings.")
    return settings


def _openwa_headers(api_key):
    return {"X-API-Key": api_key}


@frappe.whitelist()
def get_openwa_session_status():
    """Fetch current session status from OpenWA gateway."""
    frappe.only_for(("System Manager", "HR Manager"))
    settings = _get_openwa_settings()
    base_url = settings.base_url.rstrip("/")
    api_key = settings.get_password("api_key", raise_exception=False)
    session_id = settings.session_id or "default"

    try:
        r = requests.get(f"{base_url}/api/sessions/{session_id}",
                         headers=_openwa_headers(api_key), timeout=10)
        if r.status_code == 404:
            return {"status": "not_found", "message": "Session not found on OpenWA"}
        if r.status_code != 200:
            return {"status": "error", "message": f"Session API returned {r.status_code}: {r.text[:200]}"}
        data = r.json()
        return {
            "status": data.get("status", "unknown"),
            "phone": data.get("phone"),
            "pushname": data.get("pushName") or data.get("pushname"),
            "lastActive": data.get("lastActive"),
            "session_id": data.get("id"),
            "session_name": data.get("name"),
        }
    except Exception as e:
        frappe.log_error(f"OpenWA session status error: {e}")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def get_openwa_session_qr():
    """Get QR code image for the session (base64 data URL)."""
    frappe.only_for(("System Manager", "HR Manager"))
    settings = _get_openwa_settings()
    base_url = settings.base_url.rstrip("/")
    api_key = settings.get_password("api_key", raise_exception=False)
    session_id = settings.session_id or "default"

    try:
        r = requests.get(f"{base_url}/api/sessions/{session_id}/qr",
                         headers=_openwa_headers(api_key), timeout=10)
        if r.status_code == 404:
            return {"status": "error", "message": "Session not found on OpenWA"}
        if r.status_code != 200:
            return {"status": "error", "message": f"QR API returned {r.status_code}: {r.text[:200]}"}

        data = r.json()
        qr_code = data.get("qrCode", "")
        return {
            "status": "ok",
            "qr": qr_code,
            "session_status": data.get("status", "qr_ready"),
        }
    except Exception as e:
        frappe.log_error(f"OpenWA QR error: {e}")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def start_openwa_session():
    """Start/Restart the WhatsApp session."""
    frappe.only_for(("System Manager", "HR Manager"))
    settings = _get_openwa_settings()
    base_url = settings.base_url.rstrip("/")
    api_key = settings.get_password("api_key", raise_exception=False)
    session_id = settings.session_id or "default"

    try:
        r = requests.post(f"{base_url}/api/sessions/{session_id}/start",
                          headers=_openwa_headers(api_key), timeout=15)
        if r.status_code in (200, 201):
            data = r.json()
            return {"status": "ok", "message": f"Session start requested. New status: {data.get('status', 'unknown')}"}
        return {"status": "error", "message": f"Start returned {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        frappe.log_error(f"OpenWA start session error: {e}")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def stop_openwa_session():
    """Stop the WhatsApp session."""
    frappe.only_for(("System Manager", "HR Manager"))
    settings = _get_openwa_settings()
    base_url = settings.base_url.rstrip("/")
    api_key = settings.get_password("api_key", raise_exception=False)
    session_id = settings.session_id or "default"

    try:
        r = requests.post(f"{base_url}/api/sessions/{session_id}/stop",
                          headers=_openwa_headers(api_key), timeout=10)
        if r.status_code in (200, 204):
            return {"status": "ok", "message": "Session stopped successfully"}
        return {"status": "error", "message": f"Stop returned {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        frappe.log_error(f"OpenWA stop session error: {e}")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def create_new_openwa_session():
    """Create a brand new WhatsApp session on OpenWA and update settings."""
    frappe.only_for(("System Manager", "HR Manager"))
    settings = _get_openwa_settings()
    base_url = settings.base_url.rstrip("/")
    api_key = settings.get_password("api_key", raise_exception=False)

    site_name = frappe.local.site or "default"
    try:
        # 1. Create session
        r = requests.post(f"{base_url}/api/sessions",
                          headers={"Content-Type": "application/json", **_openwa_headers(api_key)},
                          json={"name": site_name}, timeout=15)
        if r.status_code != 201:
            return {"status": "error", "message": f"Create session returned {r.status_code}: {r.text[:300]}"}

        new_session = r.json()
        new_session_id = new_session.get("id")
        if not new_session_id:
            return {"status": "error", "message": "No session ID returned from OpenWA"}

        # 2. Update Frappe settings
        settings.db_set("session_id", new_session_id, commit=True)

        # 3. Start it to get QR
        requests.post(f"{base_url}/api/sessions/{new_session_id}/start",
                      headers=_openwa_headers(api_key), timeout=10)

        return {
            "status": "ok",
            "message": f"New session created: {new_session.get('name', '')} (id: {new_session_id}). Updated settings. Scan QR to link WhatsApp.",
            "new_session_id": new_session_id,
        }
    except Exception as e:
        frappe.log_error(f"OpenWA create session error: {e}")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def send_test_whatsapp():
    """Send a test message via OpenWA to verify connectivity."""
    frappe.only_for(("System Manager", "HR Manager"))
    settings = _get_openwa_settings(require_chat=True)
    api_key = settings.get_password("api_key", raise_exception=False)
    session_id = settings.session_id or "default"
    base_url = settings.base_url.rstrip("/")
    target_chat = settings.chat_id

    if not target_chat:
        frappe.throw("No Recipient Chat ID set in OpenWA Settings")

    test_message = "✅ WhatsApp notification is working!\n\nSite: {site}\nSession: {session}\n\nIf you see this, everything is configured correctly.".format(
        site=frappe.local.site or "unknown", session=session_id)

    try:
        payload = {
            "chatId": target_chat,
            "contentType": "string",
            "content": test_message
        }
        r = requests.post(f"{base_url}/api/sessions/{session_id}/send-message",
                          headers={"Content-Type": "application/json", "X-API-Key": api_key},
                          json=payload, timeout=30)
        if r.status_code in (200, 201):
            return {"status": "success", "message": f"Test message sent to {target_chat}"}
        return {"status": "error", "message": f"Send failed: {r.status_code} {r.text[:200]}"}
    except Exception as e:
        frappe.log_error(f"OpenWA send test error: {e}")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def send_whatsapp_via_openwa(message=None, chat_id=None, file_data=None, file_type=None, filename=None):
    """Generic WhatsApp send via OpenWA (text, image, or document)."""
    frappe.only_for(("System Manager", "HR Manager"))
    settings = _get_openwa_settings()
    
    api_key = settings.get_password("api_key", raise_exception=False)
    session_id = settings.session_id or "default"
    base_url = settings.base_url.rstrip("/")
    target_chat = chat_id or settings.chat_id
    
    if not target_chat:
        frappe.throw("No Chat ID specified and no default in settings")

    headers = _openwa_headers(api_key)
    
    try:
        if file_data and file_type:
            # Send image/document
            if file_type in ["image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"]:
                # Send as image
                import base64
                b64_data = base64.b64encode(file_data).decode("utf-8") if isinstance(file_data, bytes) else file_data
                payload = {
                    "chatId": target_chat,
                    "contentType": "image",
                    "media": b64_data,
                    "caption": message or ""
                }
            else:
                # Send as document
                import base64
                b64_data = base64.b64encode(file_data).decode("utf-8") if isinstance(file_data, bytes) else file_data
                payload = {
                    "chatId": target_chat,
                    "contentType": "document",
                    "media": b64_data,
                    "filename": filename or "document.pdf",
                    "caption": message or ""
                }
            
            r = requests.post(f"{base_url}/api/sessions/{session_id}/send-media",
                             headers={**headers, "Content-Type": "application/json"},
                             json=payload, timeout=30)
            
            if r.status_code in (200, 201):
                return {"status": "success", "message": f"File sent to {target_chat}"}
            return {"status": "error", "message": f"Send failed: {r.status_code} {r.text[:200]}"}
        else:
            # Send text message
            payload = {
                "chatId": target_chat,
                "contentType": "string",
                "content": message
            }
            r = requests.post(f"{base_url}/api/sessions/{session_id}/send-message",
                             headers={**headers, "Content-Type": "application/json"},
                             json=payload, timeout=30)
            if r.status_code in (200, 201):
                return {"status": "success", "message": f"Message sent to {target_chat}"}
            return {"status": "error", "message": f"Send failed: {r.status_code} {r.text[:200]}"}
            
    except Exception as e:
        frappe.log_error(f"OpenWA send error: {e}")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def switch_theme(theme):
    """Override for frappe.core.doctype.user.user.switch_theme.
    Accepts 'Kreativ' in addition to the standard Light/Dark/Automatic."""
    allowed = ["Light", "Dark", "Automatic", "Kreativ"]
    if theme in allowed:
        frappe.db.set_value("User", frappe.session.user, "desk_theme", theme)
