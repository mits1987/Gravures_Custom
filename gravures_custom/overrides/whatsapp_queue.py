"""WhatsApp OpenWA Client + Background Queue (Items 15+16).

Consolidated OpenWA API client shared by gravures_custom and kreativ_attendance.
All API calls go through :class:`OpenWAClient` instead of inline requests.

Background queue: whitelisted endpoints enqueue work to ``frappe.enqueue``
(queue='long', timeout=1500) and return immediately with {"status": "queued"}.
The background worker runs the actual send and updates WhatsApp Send Log.

Circuit breaker (shared cache key ``openwa:streak:{site}``):
- After 3 consecutive failures, subsequent requests fail fast with a friendly message.
- Cleared on first success after a failure.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime
import requests
import base64
from typing import Any

# ---------------------------------------------------------------------------
# Circuit breaker — single source of truth for both apps
# ---------------------------------------------------------------------------

CIRCUIT_BREAKER_THRESHOLD = 3


def _breaker_key(suffix: str) -> str:
    """Per-site cache key so sites don't share breaker state."""
    site = frappe.local.site or "default"
    return f"openwa:{suffix}:{site}"


def check_circuit_breaker() -> None:
    """Fail-fast if OpenWA has been failing consecutively >= threshold."""
    streak = int(frappe.cache().get_value(_breaker_key("streak")) or 0)
    if streak >= CIRCUIT_BREAKER_THRESHOLD:
        frappe.throw(
            _("WhatsApp service is temporarily unavailable (circuit breaker open). "
              "Please try again later. If the issue persists, check OpenWA health.")
        )


def increment_circuit_breaker() -> int:
    streak = _get_failure_streak() + 1
    frappe.cache().set_value(_breaker_key("streak"), streak, expires_in_sec=86400)
    # Record when the breaker first tripped (for health check's hard ceiling auto-reset)
    if streak == CIRCUIT_BREAKER_THRESHOLD:
        frappe.cache().set_value(
            _breaker_key("tripped"), str(now_datetime()), expires_in_sec=86400)
    return streak


def reset_circuit_breaker() -> None:
    frappe.cache().delete_value(_breaker_key("streak"))
    frappe.cache().delete_value(_breaker_key("tripped"))
    frappe.cache().delete_value(_breaker_key("probe"))


def _get_failure_streak() -> int:
    return int(frappe.cache().get_value(_breaker_key("streak")) or 0)


# ---------------------------------------------------------------------------
# OpenWA config — shared helper
# ---------------------------------------------------------------------------

def get_openwa_config() -> tuple[str, str, str]:
    """Return (base_url, api_key, session_id) — any may be blank."""
    try:
        settings = frappe.get_cached_doc("OpenWA Settings")
        base_url = (settings.get("base_url") or "").strip().rstrip("/")
        api_key = settings.get_password("api_key", raise_exception=False) or ""
        session_id = (settings.get("session_id") or "default").strip()
        return base_url, api_key, session_id
    except frappe.DoesNotExistError:
        return "", "", ""


# ---------------------------------------------------------------------------
# OpenWAClient — single code path for all OpenWA HTTP calls
# ---------------------------------------------------------------------------

class OpenWAClient:
    """Consolidated OpenWA gateway client.

    Usage::

        client = OpenWAClient()
        result = client.send_chat_id(chat_id, text="Hello")
        result = client.send_document(chat_id, pdf_b64, "invoice.pdf")
    """

    def __init__(self):
        self.base_url, self.api_key, self.session_id = get_openwa_config()

    # -- validation ------------------------------------------------

    def _ensure_configured(self) -> None:
        if not self.base_url:
            frappe.throw(_("OpenWA Base URL is not configured."))
        if not self.api_key:
            frappe.throw(_("OpenWA API Key is not configured."))

    # -- low-level POST --------------------------------------------

    def _post(self, endpoint: str, payload: dict, timeout: int = 30) -> dict[str, Any]:
        """POST to ``/api/sessions/{session_id}/messages/{endpoint}``.

        Returns ``{"success": True, "data": ...}`` on success, or
        ``{"success": False, "error": "..."}`` on any failure (never throws).
        """
        self._ensure_configured()
        url = "{0}/api/sessions/{1}/messages/{2}".format(
            self.base_url, self.session_id, endpoint,
        )
        headers = {"X-API-Key": self.api_key}
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if r.ok:
                return {"success": True, "data": r.json() if r.content else {}}
            _log_error("OpenWA HTTP {0}".format(r.status_code),
                       "URL: {0}\nStatus: {1}\nResponse: {2}".format(url, r.status_code, r.text[:500]))
            return {"success": False, "error": "HTTP {0}: {1}".format(r.status_code, r.text[:200])}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Cannot connect to OpenWA at {0}. Is it running?".format(self.base_url)}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "OpenWA timed out after {0}s".format(timeout)}
        except Exception:
            _log_error("OpenWA exception", frappe.get_traceback())
            return {"success": False, "error": "Unexpected error — check Error Log."}

    # -- high-level send methods -----------------------------------

    def send_text(self, chat_id: str, text: str) -> dict:
        return self._post("send-text", {"chatId": chat_id, "text": text})

    def send_document(self, chat_id: str, base64_data: str, filename: str,
                      mimetype: str = "application/pdf", caption: str = "") -> dict:
        return self._post("send-document", {
            "chatId": chat_id,
            "base64": base64_data,
            "mimetype": mimetype,
            "filename": filename,
            "caption": caption,
        })

    def send_image(self, chat_id: str, base64_data: str, filename: str, caption: str = "") -> dict:
        return self.send_document(chat_id, base64_data, filename,
                                  mimetype="image/png", caption=caption)

    # -- session / contacts ----------------------------------------

    def get_contacts(self, limit: int = 200) -> list:
        """Fetch all known WhatsApp contacts."""
        self._ensure_configured()
        url = "{0}/api/sessions/{1}/contacts?limit={2}".format(self.base_url, self.session_id, limit)
        try:
            r = requests.get(url, headers={"X-API-Key": self.api_key}, timeout=15)
            if r.ok:
                return r.json() if isinstance(r.json(), list) else []
        except Exception:
            pass
        return []

    def get_session_status(self) -> dict:
        """Return current session state from OpenWA."""
        self._ensure_configured()
        url = "{0}/api/sessions/{1}".format(self.base_url, self.session_id)
        try:
            r = requests.get(url, headers={"X-API-Key": self.api_key}, timeout=10)
            if r.status_code == 404:
                return {"status": "not_found", "message": "Session not found on OpenWA."}
            if r.ok:
                data = r.json()
                return {
                    "status": data.get("status", "unknown"),
                    "phone": data.get("phone"),
                    "pushname": data.get("pushname"),
                    "last_active": data.get("lastActive"),
                    "session_id": self.session_id,
                }
            return {"status": "error", "message": "HTTP {0}".format(r.status_code)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_session_qr(self) -> dict:
        """Return QR code (base64) for scanning."""
        self._ensure_configured()
        url = "{0}/api/sessions/{1}/qr".format(self.base_url, self.session_id)
        try:
            r = requests.get(url, headers={"X-API-Key": self.api_key}, timeout=10)
            if r.ok:
                data = r.json()
                return {"status": "ok", "qr": data.get("qrCode", ""),
                        "session_status": data.get("status", "qr_ready")}
            return {"status": "error", "message": "HTTP {0}: {1}".format(r.status_code, r.text[:200])}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def start_session(self) -> dict:
        self._ensure_configured()
        url = "{0}/api/sessions/{1}/start".format(self.base_url, self.session_id)
        try:
            r = requests.post(url, headers={"X-API-Key": self.api_key}, timeout=15)
            if r.ok:
                return {"status": "ok", "message": "Session start requested."}
            return {"status": "error", "message": "HTTP {0}".format(r.status_code)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def stop_session(self) -> dict:
        self._ensure_configured()
        url = "{0}/api/sessions/{1}/stop".format(self.base_url, self.session_id)
        try:
            r = requests.post(url, headers={"X-API-Key": self.api_key}, timeout=10)
            if r.ok:
                return {"status": "ok", "message": "Session stopped."}
            return {"status": "error", "message": "HTTP {0}".format(r.status_code)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def create_session(self, name: str = "") -> dict:
        """Create a new session on OpenWA and update Frappe settings."""
        self._ensure_configured()
        name = name or frappe.local.site or "default"
        url = "{0}/api/sessions".format(self.base_url)
        try:
            r = requests.post(url,
                              headers={"Content-Type": "application/json", "X-API-Key": self.api_key},
                              json={"name": name}, timeout=15)
            if r.status_code != 201:
                return {"status": "error", "message": "Create returned {0}: {1}".format(r.status_code, r.text[:300])}
            data = r.json()
            new_id = data.get("id")
            if not new_id:
                return {"status": "error", "message": "No session ID returned."}
            # Update Frappe settings
            settings = frappe.get_single("OpenWA Settings")
            settings.db_set("session_id", new_id, commit=True)
            # Start to get QR
            self.session_id = new_id
            self.start_session()
            return {"status": "ok", "new_session_id": new_id, "session_name": data.get("name")}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def fetch_profile_picture(self, contact_id: str) -> str | None:
        """Fetch profile picture URL for one contact; returns URL or None."""
        if "@g.us" in contact_id:
            return None
        url = "{0}/api/sessions/{1}/contacts/{2}/profile-picture".format(
            self.base_url, self.session_id, contact_id)
        try:
            r = requests.get(url, headers={"X-API-Key": self.api_key}, timeout=10)
            if r.ok:
                return r.json().get("url")
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Background queue worker (Item 16)
# ---------------------------------------------------------------------------

# Queue configuration
WHATSAPP_QUEUE = "long"
WHATSAPP_TIMEOUT = 1500  # 25 min — covers PDF gen + OpenWA network


def enqueue_whatsapp_send(action_type: str, log_name: str | None = None, **kwargs) -> dict:
    """Enqueue a WhatsApp send as a background job.

    Returns immediately with ``{"status": "queued", "log_name": ..., ...}``.
    The background worker runs the actual send.
    """
    # Circuit breaker check stays synchronous (fail-fast before queueing)
    check_circuit_breaker()

    from gravures_custom.overrides import _log_whatsapp_send

    if not log_name:
        log_name = _log_whatsapp_send(
            source_doctype=kwargs.pop("source_doctype", "System"),
            source_docname=kwargs.pop("source_docname", ""),
            recipient=kwargs.pop("recipient", ""),
            message_type=kwargs.pop("message_type", "Custom"),
            meta=kwargs.pop("meta", {}),
        )
        frappe.db.commit()

    job = frappe.enqueue(
        "gravures_custom.overrides.whatsapp_queue._execute_whatsapp_send",
        queue=WHATSAPP_QUEUE,
        timeout=WHATSAPP_TIMEOUT,
        job_id=log_name or None,
        job_args={"action_type": action_type, "log_name": log_name, **kwargs},
    )
    return {
        "status": "queued",
        "job_id": job.id,
        "log_name": log_name,
        "message": _("WhatsApp send queued. Check WhatsApp Send Log for status."),
    }


def _execute_whatsapp_send(job_args: dict) -> dict:
    """Background worker — runs via frappe.enqueue."""
    action = job_args.pop("action_type", None)
    log_name = job_args.pop("log_name", None)

    _update_log(log_name, "Processing")

    try:
        if action == "send_pdf":
            result = _bg_send_pdf(job_args)
        elif action == "send_screenshot":
            result = _bg_send_screenshot(job_args)
        elif action == "send_test":
            result = _bg_send_test()
        elif action == "send_manual":
            result = _bg_send_manual(job_args)
        else:
            result = {"success": False, "error": "Unknown action: {0}".format(action)}

        if result.get("success"):
            reset_circuit_breaker()
            _update_log(log_name, "Sent")
        else:
            increment_circuit_breaker()
            _update_log(log_name, "Failed", result.get("error"))
        return result

    except Exception as e:
        increment_circuit_breaker()
        _update_log(log_name, "Failed", str(e))
        frappe.log_error(title="WhatsApp bg worker error", message=frappe.get_traceback())
        return {"success": False, "error": str(e)}


def _bg_send_pdf(args: dict) -> dict:
    """Generate PDF and send via WhatsApp."""
    from gravures_custom.overrides import _generate_pdf_bytes
    pdf_bytes = _generate_pdf_bytes(args["doctype"], args["name"], args.get("print_format"))
    if not pdf_bytes or len(pdf_bytes) < 1024:
        return {"success": False, "error": "Generated PDF is empty."}
    b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    filename = "{0}_{1}.pdf".format(args["doctype"].replace(" ", "_"), args["name"])
    chat_id = args.get("chat_id")
    if not chat_id:
        chat_id = frappe.get_cached_doc("OpenWA Settings").chat_id
    client = OpenWAClient()
    return client.send_document(chat_id, b64, filename, caption=filename)


def _bg_send_screenshot(args: dict) -> dict:
    """Render HTML to PNG and send via WhatsApp."""
    from gravures_custom.overrides import _screenshot_html_playwright
    png = _screenshot_html_playwright(args["html"], width=args.get("width", 1000))
    b64 = base64.b64encode(png).decode("utf-8")
    if len(b64) < 1024:
        return {"success": False, "error": "Generated screenshot is empty."}
    chat_id = args.get("chat_id") or frappe.get_cached_doc("OpenWA Settings").chat_id
    client = OpenWAClient()
    return client.send_image(chat_id, b64, args["filename"], args.get("caption", ""))


def _bg_send_test() -> dict:
    """Send a basic test text message."""
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.chat_id:
        return {"success": False, "error": "No Recipient Chat ID in OpenWA Settings."}
    client = OpenWAClient()
    return client.send_text(settings.chat_id, "Test message from Gravures ERPNext")


def _bg_send_manual(args: dict) -> dict:
    """Manual send — dispatches to document or image based on message_type."""
    client = OpenWAClient()
    chat_id = args["chat_id_override"]
    file_b64 = args.get("file_b64", "")
    filename = args.get("filename", "document")
    message_type = args.get("message_type", "Custom")

    if message_type in ("Print PDF", "Dispatch PDF"):
        return client.send_document(chat_id, file_b64, filename, caption=filename)
    elif message_type == "Screenshot":
        return client.send_image(chat_id, file_b64, filename, args.get("caption", ""))
    else:
        # "Custom" — try as document, fall back to text if no file data
        if file_b64:
            return client.send_document(chat_id, file_b64, filename)
        return client.send_text(chat_id, args.get("text", ""))


def _update_log(log_name: str | None, status: str, error: str | None = None) -> None:
    """Update WhatsApp Send Log status; silently ignores missing log_name."""
    if not log_name:
        return
    try:
        from kreativ_notification.notification.send_log import update_log_status
        update_log_status(log_name, status, error)
    except Exception:
        pass


def _log_error(title: str, message: str) -> None:
    """Log to Frappe Error Log."""
    try:
        frappe.log_error(title=title, message=message)
    except Exception:
        pass
