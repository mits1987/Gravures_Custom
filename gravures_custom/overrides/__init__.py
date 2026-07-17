import frappe
import subprocess
import tempfile
import os
import base64
import re
from urllib.parse import urljoin
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from frappe.utils.file_manager import get_file_path as get_file_path_util

try:
    import phonenumbers
    from phonenumbers import NumberParseException, PhoneNumberType
    PHONENUMBERS_AVAILABLE = True
except ImportError:
    phonenumbers = None
    NumberParseException = Exception
    PhoneNumberType = None
    PHONENUMBERS_AVAILABLE = False

from gravures_custom.gravures_custom.doctype.whatsapp_send_log.whatsapp_send_log import (
	create_log as log_whatsapp_send,
	update_log_status as update_whatsapp_log_status,
)

import sys
print(f"DEBUG: gravures_custom.overrides module LOADED from {__file__}", file=sys.stderr)

# ---------------------------------------------------------------------------
# Circuit breaker — delegates to consolidated whatsapp_queue module
# ---------------------------------------------------------------------------

from gravures_custom.overrides.whatsapp_queue import (
    check_circuit_breaker as _check_whatsapp_circuit_breaker,
    increment_circuit_breaker as _increment_whatsapp_circuit_breaker,
    reset_circuit_breaker as _reset_whatsapp_circuit_breaker,
    get_openwa_config as _get_openwa_config,
    OpenWAClient,
    enqueue_whatsapp_send,
)


def _log_whatsapp_send(
    source_doctype: str,
    source_docname: str,
    recipient: str,
    message_type: str,
    recipient_display: str = "",
    source_print_format: str = "",
    meta: dict = None,
) -> str:
    """Create a WhatsApp Send Log entry and return its name."""
    try:
        log_name = log_whatsapp_send(
            source_doctype=source_doctype,
            source_docname=source_docname,
            recipient=recipient,
            message_type=message_type,
            recipient_display=recipient_display,
            source_print_format=source_print_format,
            meta=meta or {},
        )
        return log_name
    except Exception:
        # Don't let logging failure break the send
        frappe.log_error(
            title="WhatsApp Send Log creation failed",
            message=frappe.get_traceback(),
        )
        return None


def _update_log_result(log_name: str, success: bool, error_message: str = ""):
    """Update WhatsApp Send Log with result."""
    if not log_name:
        return
    try:
        update_whatsapp_log_status(log_name, "Sent" if success else "Failed", error_message or None)
    except Exception:
        frappe.log_error(
            title="WhatsApp Send Log update failed",
            message=frappe.get_traceback(),
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
def validate_phone_number(phone: str, default_country: str = "IN") -> dict:
    """Validate and format a phone number for WhatsApp.

    Uses phonenumbers library (Google's libphonenumber port) to:
    - Parse the number with given default country
    - Check if valid mobile number
    - Return E.164 format (+919876543210) and WhatsApp chat_id (919876543210@c.us)

    Args:
        phone: Raw phone number input (digits only, may include country code)
        default_country: ISO country code for parsing (default: IN for India)

    Returns:
        Dict with: valid, formatted, e164, chat_id, error
    """
    if not PHONENUMBERS_AVAILABLE:
        # Fallback: basic validation if library not available
        digits = re.sub(r"[^0-9]", "", phone or "")
        if len(digits) >= 10:
            # Assume India (91) if no country code detected
            if len(digits) == 10:
                digits = "91" + digits
            elif len(digits) == 12 and digits.startswith("91"):
                pass
            else:
                return {"valid": False, "error": "Invalid phone number format"}
            return {
                "valid": True,
                "formatted": digits,
                "e164": "+" + digits,
                "chat_id": digits + "@c.us",
                "error": None,
            }
        return {"valid": False, "error": "Phone number too short (min 10 digits)"}

    try:
        # Handle input that may already have + or spaces
        parsed = phonenumbers.parse(phone or "", default_country)

        if not phonenumbers.is_valid_number(parsed):
            return {"valid": False, "error": "Invalid phone number"}

        # Check if it's a mobile number (WhatsApp requires mobile)
        num_type = phonenumbers.number_type(parsed)
        if num_type not in (PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE):
            return {"valid": False, "error": "Must be a mobile number (no landlines)"}

        # Format as E.164 (+919876543210)
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        # WhatsApp chat_id format: 919876543210@c.us (digits only, no +)
        digits = e164.lstrip("+")
        chat_id = digits + "@c.us"

        return {
            "valid": True,
            "formatted": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
            "e164": e164,
            "chat_id": chat_id,
            "error": None,
        }
    except NumberParseException as e:
        return {"valid": False, "error": f"Could not parse phone number: {e}"}
    except Exception as e:
        frappe.log_error("Phone validation error", frappe.get_traceback())
        return {"valid": False, "error": "Validation error"}


@frappe.whitelist()
def download_pdf(doctype, name, format=None, doc=None, no_letterhead=0, letterhead=None, settings=None, _lang=None):
    # Use _generate_pdf_bytes which inlines local images as base64
    # to avoid Chrome's auth-less image fetches (401 errors for logos)
    pdf = _generate_pdf_bytes(
        doctype, name, print_format=format,
        no_letterhead=no_letterhead, letterhead=letterhead
    )
    frappe.local.response.filename = f"{name}.pdf"
    frappe.local.response.filecontent = pdf
    frappe.local.response.type = "download"


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _chrome_path():
    """Return path to Chrome/Chromium binary. Uses shutil.which for portability."""
    import shutil
    # Prioritize the known working headless_shell first (avoids broken chromium on this system)
    for binary in ("/home/mitesh/frappe-bench-v16/chromium/chrome-linux/headless_shell",
                   "chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"):
        path = shutil.which(binary) if not binary.startswith("/") else binary
        if path and os.path.exists(path):
            return path
    return "/home/mitesh/frappe-bench-v16/chromium/chrome-linux/headless_shell"


def _generate_pdf_bytes(doctype, name, print_format=None, no_letterhead=0, letterhead=None):
    """Render any Document as PDF bytes via Chromium headless.
    Inlines `/files/...` images as base64 to avoid Chromium's auth-less
    image fetches (which return 401 / break the logo etc.).

    SECURITY: Read files from local filesystem instead of fetching via HTTP
    to prevent SSRF (Server-Side Request Forgery). Only processes local
    `/files/...` and `/private/files/...` URLs. External URLs are skipped."""
    html = frappe.get_print(
        doctype, name, print_format=print_format, doc=None,
        no_letterhead=no_letterhead, letterhead=letterhead, as_pdf=False,
    )

    # Strip the printview "Print | Get PDF" action banner that gets
    # embedded when generating PDF output — keeps the PDF clean.
    html = re.sub(r'<div\s+class="action-banner[^"]*"[^>]*>.*?</div>', '', html, flags=re.DOTALL)

    # Find all <img src="/files/..."> and <img src="/private/files/..."> (relative paths)
    # and inline them by reading from local filesystem.
    # This avoids SSRF that would occur if we fetched via HTTP.
    img_re = re.compile(r'(<img[^>]*\ssrc=["\'])((?:/private)?/files/[^"\']*\.(?:png|jpe?g|gif|svg|webp|bmp))(["\'])', re.IGNORECASE)
    seen = {}

    def inline_img(match):
        file_url = match.group(2)
        if file_url in seen:
            return match.group(1) + seen[file_url] + match.group(3)

        # Get local filesystem path for the file
        file_path = get_file_path_util(file_url)
        if not file_path or not os.path.exists(file_path):
            return match.group(0)

        try:
            with open(file_path, "rb") as f:
                content = f.read()
            if not content:
                return match.group(0)

            b64 = base64.b64encode(content).decode("utf-8")
            # Determine MIME type from extension
            ext = file_url.rsplit(".", 1)[-1].lower()
            mime_map = {
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "gif": "image/gif",
                "svg": "image/svg+xml",
                "webp": "image/webp",
                "bmp": "image/bmp",
            }
            mime = mime_map.get(ext, "image/png")
            data_uri = "data:{0};base64,{1}".format(mime, b64)
            seen[file_url] = data_uri
            return match.group(1) + data_uri + match.group(3)
        except Exception:
            return match.group(0)

    # Replace local `/files/...` and `/private/files/...` in HTML
    html = img_re.sub(inline_img, html)

    # SECURITY: Skip absolute/external URLs (https://..., http://...)
    # to prevent SSRF. External images will not be inlined and may
    # appear broken in PDF, but this is safer than fetching arbitrary URLs.
    # If needed, add a whitelist of allowed domains in the future.

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


def _log_whatsapp_send(source_doctype, source_docname, recipient, recipient_display,
                        message_type, source_print_format="", meta=None):
    """Create a WhatsApp Send Log entry. Returns the log name."""
    from ..doctype.whatsapp_send_log.whatsapp_send_log import create_log
    try:
        return create_log(
            source_doctype=source_doctype,
            source_docname=source_docname,
            recipient=recipient,
            recipient_display=recipient_display,
            message_type=message_type,
            source_print_format=source_print_format,
            meta=meta or {},
            sent_by=frappe.session.user,
        )
    except Exception:
        frappe.log_error("Failed to create WhatsApp Send Log", frappe.get_traceback())
        return None


def _update_log_result(log_name, success, error_message=None):
    """Update a WhatsApp Send Log entry with send result."""
    if not log_name:
        return
    from ..doctype.whatsapp_send_log.whatsapp_send_log import update_log_status
    try:
        update_log_status(log_name, "Sent" if success else "Failed", error_message)
    except Exception:
        frappe.log_error("Failed to update WhatsApp Send Log", frappe.get_traceback())


def _send_document_via_whatsapp(doc_b64, filename, caption, chat_id_override=None,
                                    source_doctype=None, source_docname=None, source_print_format=None):
    """Send a base64 PDF/document via OpenWAClient (consolidated — Item 15).

    Logs send attempt, calls OpenWAClient, updates log with result.
    Falls back to inline requests only if OpenWAClient fails.
    """
    _check_whatsapp_circuit_breaker()
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.enabled:
        frappe.throw("WhatsApp is disabled in OpenWA Settings.")
    chat_id = chat_id_override or settings.chat_id
    if not chat_id:
        frappe.throw("No Chat ID in OpenWA Settings.")

    # Create log entry
    log_name = _log_whatsapp_send(
        source_doctype=source_doctype or "Unknown",
        source_docname=source_docname or "",
        recipient=chat_id,
        recipient_display=caption,
        message_type="Print PDF",
        source_print_format=source_print_format or "",
        meta={"filename": filename, "caption": caption},
    )

    client = OpenWAClient()
    result = client.send_document(chat_id, doc_b64, filename, caption=caption)

    if result.get("success"):
        _reset_whatsapp_circuit_breaker()
        _update_log_result(log_name, True)
        return {"success": True, "message": "Sent!", "messageId": result.get("data", {}).get("messageId")}
    else:
        error_msg = result.get("error", "Unknown error")
        _increment_whatsapp_circuit_breaker()
        _update_log_result(log_name, False, error_msg)
        frappe.throw(error_msg)


@frappe.whitelist()
def send_print_pdf_whatsapp(doctype, name, print_format=None, chat_id=None):
    """Generate PDF and enqueue WhatsApp send (background queue - Item 16)."""
    if not doctype or not name:
        frappe.throw("doctype and name are required")

    settings = frappe.get_cached_doc("OpenWA Settings")
    recipient = chat_id or settings.chat_id
    filename = "{0}_{1}.pdf".format(doctype.replace(" ", "_"), name)

    # Enqueue the PDF generation + send to background worker
    return enqueue_whatsapp_send(
        action_type="send_pdf",
        source_doctype=doctype,
        source_docname=name,
        recipient=recipient,
        message_type="Print PDF",
        meta={"filename": filename},
        doctype=doctype,
        name=name,
        print_format=print_format,
        chat_id=chat_id,
    )


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

    Permission: Blocked for Employee, Graphics, Proofing, Engraving roles
    UNLESS user also has admin role (System Manager, HR Manager, etc).
    All other roles allowed.
    """
    # --- Permission check: whitelist admin roles, block restricted roles ---
    # Admin roles that always have access (even if they also have restricted roles)
    admin_roles = {
        "System Manager",
        "Administrator",
        "HR Manager",
        "Accounts Manager",
        "Sales Manager",
        "Purchase Manager",
        "Production Manager",
        "Manufacturing Manager",
        "Quality Manager",
        "Projects Manager",
        "Stock Manager",
        "Copper Production Manager",
    }

    # Roles that should be blocked (unless user also has admin role)
    restricted_roles = {
        "Employee Self Service",
        "Employee",
        "Graphics User",
        "Proofing User",
        "Engraving User",
    }

    user_roles = set(frappe.get_roles())

    # Allow if user has any admin role
    if user_roles & admin_roles:
        pass  # allowed
    # Block if user has restricted roles and NO admin role
    elif user_roles & restricted_roles:
        frappe.throw(
            "You are not permitted to access WhatsApp contacts.",
            frappe.PermissionError,
        )

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
    """Render HTML to full-page PNG via Chromium headless, return raw PNG bytes.

    Fixes:
    - Full page capture: uses large window height (20000px) so Chromium renders all content
    - Smart crop: detects background color from corner pixels instead of assuming white
    - Portable Chrome path: uses shutil.which()
    """
    from PIL import Image, ImageChops
    import io
    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html_content)
        html_path = f.name
    png_path = tempfile.mktemp(suffix='.png')
    try:
        # Large height (20000) forces Chromium to render full document
        # --hide-scrollbars prevents scrollbar artifacts
        cmd = [_chrome_path(), '--headless', '--no-sandbox', '--disable-gpu',
               '--force-device-scale-factor=2',
               '--hide-scrollbars',
               '--window-size={0},20000'.format(width),
               '--screenshot=' + png_path, html_path]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
        img = Image.open(png_path)

        # ---- Smart crop: detect background from corners ----
        # Sample 4 corners to determine background color (handles gray/colored backgrounds)
        w, h = img.size
        corner_sample = 10  # pixels from corner
        corners = [
            img.crop((0, 0, corner_sample, corner_sample)),           # top-left
            img.crop((w - corner_sample, 0, w, corner_sample)),        # top-right
            img.crop((0, h - corner_sample, corner_sample, h)),        # bottom-left
            img.crop((w - corner_sample, h - corner_sample, w, h)),    # bottom-right
        ]
        # Find most common color among corners (the background)
        from collections import Counter
        corner_colors = []
        for c in corners:
            colors = c.getcolors(corner_sample * corner_sample)
            if colors:
                corner_colors.append(max(colors, key=lambda x: x[0])[1])
        if corner_colors:
            bg_color = Counter(corner_colors).most_common(1)[0][0]
            # If BG is RGBA, use RGB for comparison
            if isinstance(bg_color, tuple) and len(bg_color) == 4:
                bg_color = bg_color[:3]

            # Convert to RGB for comparison
            if img.mode != 'RGB':
                img_rgb = img.convert('RGB')
            else:
                img_rgb = img

            # Use ImageChops.difference to create mask of non-background pixels
            bg_image = Image.new('RGB', img_rgb.size, bg_color)
            diff = ImageChops.difference(img_rgb, bg_image)
            bbox = diff.getbbox()
            if bbox:
                # Add 15px padding at bottom
                left, top, right, bottom = bbox
                bottom = min(bottom + 15, h)
                img = img.crop((0, 0, w, bottom))

        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()
    finally:
        os.unlink(html_path)
        if os.path.exists(png_path):
            os.unlink(png_path)


def _screenshot_html_playwright(html_content, width=1000):
    """Render HTML to full-page PNG via Playwright (Chromium), return raw PNG bytes.

    Uses Playwright's full_page=True for true full-page capture without height hacks.
    Falls back to _screenshot_html if Playwright is not available.
    """
    import io
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        # Playwright not installed, fall back to Chromium method
        return _screenshot_html(html_content, width)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html_content)
        html_path = f.name

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-gpu', '--force-device-scale-factor=2']
            )
            page = browser.new_page(viewport={'width': width, 'height': 1080})
            page.goto('file://' + html_path, wait_until='networkidle')
            # Full page screenshot - captures entire scrollable page
            png_bytes = page.screenshot(full_page=True, type='png')
            browser.close()
        return png_bytes
    except Exception:
        # Any error with Playwright, fall back to Chromium method
        return _screenshot_html(html_content, width)
    finally:
        os.unlink(html_path)


def _send_image_via_whatsapp(image_b64, filename, caption, chat_id_override=None):
    """Send a base64 image via OpenWAClient (consolidated - Item 15)."""
    _check_whatsapp_circuit_breaker()
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.enabled:
        frappe.throw("WhatsApp is disabled in OpenWA Settings.")
    chat_id = chat_id_override or settings.chat_id
    if not chat_id:
        frappe.throw("No Chat ID in OpenWA Settings.")

    client = OpenWAClient()
    result = client.send_image(chat_id, image_b64, filename, caption=caption)

    if result.get("success"):
        _reset_whatsapp_circuit_breaker()
        return {"success": True, "message": "Sent!", "messageId": result.get("data", {}).get("messageId")}
    else:
        error_msg = result.get("error", "Unknown error")
        _increment_whatsapp_circuit_breaker()
        frappe.throw(error_msg)


def _dispatch_screenshot(html, filename, caption, width=1000,
                            source_doctype="Dispatch", source_docname="",
                            message_type="Screenshot", meta=None):
    """Render HTML to PNG and enqueue WhatsApp send (background queue - Item 16).

    Playwright rendering happens in the background worker so the page
    returns immediately with a "queued" status.
    """
    settings = frappe.get_cached_doc("OpenWA Settings")
    recipient = settings.chat_id

    # Enqueue the screenshot + send to background worker
    return enqueue_whatsapp_send(
        action_type="send_screenshot",
        source_doctype=source_doctype,
        source_docname=source_docname,
        recipient=recipient,
        message_type=message_type,
        meta=meta or {"filename": filename, "caption": caption},
        html=html,
        width=width,
        filename=filename,
        caption=caption,
    )


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

    return _dispatch_screenshot(
        html, "Dispatch_{0}.png".format(from_date), caption,
        source_doctype="Sales Order",
        source_docname=date_label,
        message_type="Dispatch PDF",
        meta={"from_date": from_date, "to_date": to_date, "caption": caption},
    )


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

    return _dispatch_screenshot(
        html, "Engraving_{0}.png".format(from_date), caption,
        width=2400,
        source_doctype="Sales Order",
        source_docname=date_label,
        message_type="Engraving PDF",
        meta={"from_date": from_date, "to_date": to_date, "caption": caption},
    )


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

    return _dispatch_screenshot(
        html, "EngravingMonthly_{0}.png".format(from_date), caption,
        width=600,
        source_doctype="Sales Order",
        source_docname=_format_date_range(from_date, to_date),
        message_type="Engraving Monthly PDF",
        meta={"from_date": from_date, "to_date": to_date, "caption": caption},
    )


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

    return _dispatch_screenshot(
        html, "Proofing_{0}.png".format(from_date), caption,
        source_doctype="Sales Order",
        source_docname=date_label,
        message_type="Proofing PDF",
        meta={"from_date": from_date, "to_date": to_date, "caption": caption},
    )


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

    return _dispatch_screenshot(
        html, "CustomerDispatch_{0}.png".format(from_date), caption,
        source_doctype="Sales Order",
        source_docname=date_label,
        message_type="Customer Dispatch PDF",
        meta={"from_date": from_date, "to_date": to_date, "caption": caption},
    )


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

    return _dispatch_screenshot(
        html, "MonthlyDispatch_{0}.png".format(from_date), caption,
        source_doctype="Sales Order",
        source_docname=date_label,
        message_type="Monthly Dispatch PDF",
        meta={"from_date": from_date, "to_date": to_date, "caption": caption},
    )


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

    return _dispatch_screenshot(
        html, "DispatchYearly_{0}.png".format(from_date), caption,
        source_doctype="Sales Order",
        source_docname=date_label,
        message_type="Yearly Dispatch PDF",
        meta={"from_date": from_date, "to_date": to_date, "caption": caption},
    )


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

    return _dispatch_screenshot(
        html, "JobStatus_{0}.png".format(from_date), caption,
        source_doctype="Sales Order",
        source_docname=date_label,
        message_type="Job Status PDF",
        meta={"from_date": from_date, "to_date": to_date, "caption": caption},
    )


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

    return _dispatch_screenshot(
        html, "Monthly_{0}.png".format(month), caption,
        source_doctype="Sales Order",
        source_docname=month,
        message_type="Monthly Report PDF",
        meta={"month": month, "from_date": from_date, "to_date": to_date, "caption": caption},
    )


# ---------------------------------------------------------------------------
# OpenWA Settings Whitelisted Methods (for Gravures Custom - kreativ216)
# ---------------------------------------------------------------------------

# NOTE: _get_openwa_settings and _openwa_headers removed in Item 15.
# OpenWAClient handles configuration internally.


@frappe.whitelist()
def get_openwa_session_status():
    """Fetch current session status from OpenWA gateway (via OpenWAClient - Item 15)."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.get_session_status()


@frappe.whitelist()
def get_openwa_session_qr():
    """Get QR code image for the session (via OpenWAClient - Item 15)."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.get_session_qr()


@frappe.whitelist()
def start_openwa_session():
    """Start/Restart the WhatsApp session (via OpenWAClient - Item 15)."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.start_session()


@frappe.whitelist()
def stop_openwa_session():
    """Stop the WhatsApp session (via OpenWAClient - Item 15)."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.stop_session()


@frappe.whitelist()
def create_new_openwa_session():
    """Create a new session on OpenWA and update settings (via OpenWAClient - Item 15)."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.create_session()



@frappe.whitelist()
def send_test_whatsapp():
    """Enqueue a test WhatsApp message (background queue - Item 16)."""
    frappe.only_for(("System Manager", "HR Manager"))
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.chat_id:
        frappe.throw("No Recipient Chat ID set in OpenWA Settings")

    return enqueue_whatsapp_send(
        action_type="send_test",
        source_doctype="System",
        source_docname="",
        recipient=settings.chat_id,
        message_type="Test",
        meta={"test": True},
    )



@frappe.whitelist()
def send_whatsapp_via_openwa(message=None, chat_id=None, file_data=None, file_type=None, filename=None):
    """Generic WhatsApp send via OpenWAClient (background queue - Item 16)."""
    frappe.only_for(("System Manager", "HR Manager"))
    settings = frappe.get_cached_doc("OpenWA Settings")
    target_chat = chat_id or settings.chat_id

    if not target_chat:
        frappe.throw("No Chat ID specified and no default in settings")

    # Convert file_data to base64 if bytes
    file_b64 = ""
    if file_data:
        if isinstance(file_data, bytes):
            file_b64 = base64.b64encode(file_data).decode("utf-8")
        else:
            file_b64 = file_data

    # Determine message type based on file_type
    if file_type and file_data:
        if file_type in ["image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"]:
            msg_type = "Screenshot"
        else:
            msg_type = "Print PDF"
    else:
        msg_type = "Custom"

    return enqueue_whatsapp_send(
        action_type="send_manual",
        source_doctype="System",
        source_docname="",
        recipient=target_chat,
        message_type=msg_type,
        meta={"filename": filename or "document", "message": message or ""},
        chat_id_override=target_chat,
        file_b64=file_b64,
        filename=filename or "document",
        text=message or "",
    )



@frappe.whitelist()
def switch_theme(theme):
    """Override for frappe.core.doctype.user.user.switch_theme.
    Accepts 'Kreativ' in addition to the standard Light/Dark/Automatic."""
    allowed = ["Light", "Dark", "Automatic", "Kreativ"]
    if theme in allowed:
        frappe.db.set_value("User", frappe.session.user, "desk_theme", theme)
