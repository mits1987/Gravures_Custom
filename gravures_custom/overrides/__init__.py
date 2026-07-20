import frappe
import subprocess
import tempfile
import os
import base64
import re
from urllib.parse import urljoin
import requests
from frappe.utils.file_manager import get_file_path as get_file_path_util

# ---------------------------------------------------------------------------
# Circuit breaker / OpenWAClient / Queue — delegate to kreativ_notification
# ---------------------------------------------------------------------------

from kreativ_notification.notification.openwa_client import (
    check_circuit_breaker as _check_circuit_breaker,
    increment_circuit_breaker as _increment_circuit_breaker,
    reset_circuit_breaker as _reset_circuit_breaker,
    OpenWAClient,
)

from kreativ_notification.notification.send import (
    send_document_via_whatsapp,
    send_image_via_whatsapp,
    send_text_via_whatsapp,
)

from kreativ_notification.notification.send_log import create_log as log_whatsapp_send
from kreativ_notification.notification.send_log import update_log_status as update_whatsapp_log_status

from kreativ_notification.notification.dispatcher import dispatch as dispatch_whatsapp
from kreativ_notification.notification.pdf_utils import generate_pdf_bytes, screenshot_html


def _check_whatsapp_circuit_breaker():
    """Backward-compat shim."""
    _check_circuit_breaker()


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Legacy OpenWAClient shims (for backward compat)
# ---------------------------------------------------------------------------

def _get_openwa_config() -> tuple[str, str, str]:
    """Return (base_url, api_key, session_id) — any may be blank."""
    try:
        settings = frappe.get_cached_doc("OpenWA Settings")
        base_url = (settings.get("base_url") or "").strip().rstrip("/")
        api_key = settings.get_password("api_key", raise_exception=False) or ""
        session_id = (settings.get("session_id") or "default").strip()
        return base_url, api_key, session_id
    except Exception:
        return "", "", "default"


# ---------------------------------------------------------------------------
# Dashboard senders — delegate to kreativ_notification dispatcher
# ---------------------------------------------------------------------------

@frappe.whitelist()
def send_print_pdf_whatsapp(doctype, name, print_format=None, chat_id=None):
    """Generate PDF and enqueue WhatsApp send via unified dispatcher."""
    if not doctype or not name:
        frappe.throw("doctype and name are required")

    settings = frappe.get_cached_doc("OpenWA Settings")
    recipient = chat_id or settings.chat_id
    filename = "{0}_{1}.pdf".format(doctype.replace(" ", "_"), name)

    pdf_bytes = generate_pdf_bytes(doctype, name, print_format=print_format)
    base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

    return dispatch_whatsapp(
        recipient=recipient,
        text=filename,
        file_b64=base64_pdf,
        filename=filename,
        mimetype="application/pdf",
        message_type="Print PDF",
        source_doctype=doctype,
        source_docname=name,
        source_print_format=print_format or "",
    )


@frappe.whitelist()
def _send_document_via_whatsapp(doc_b64, filename, caption, chat_id_override=None,
                                    source_doctype=None, source_docname=None, source_print_format=None):
    """Legacy shim: delegate to kreativ_notification.send."""
    return send_document_via_whatsapp(
        doc_b64, filename, caption,
        chat_id_override=chat_id_override,
        source_doctype=source_doctype,
        source_docname=source_docname,
        source_print_format=source_print_format,
    )


@frappe.whitelist()
def _send_image_via_whatsapp(image_b64, filename, caption, chat_id_override=None):
    """Legacy shim: delegate to kreativ_notification.send."""
    return send_image_via_whatsapp(
        image_b64, filename, caption,
        chat_id_override=chat_id_override,
    )


def _dispatch_screenshot(html, filename, caption, width=1000,
                            source_doctype="Dispatch", source_docname="",
                            message_type="Screenshot", meta=None):
    """Render HTML to PNG and enqueue WhatsApp send via unified dispatcher."""
    settings = frappe.get_cached_doc("OpenWA Settings")
    recipient = settings.chat_id

    png_bytes = screenshot_html(html, width=width)
    image_b64 = base64.b64encode(png_bytes).decode("utf-8")

    return dispatch_whatsapp(
        recipient=recipient,
        text=caption,
        file_b64=image_b64,
        filename=filename,
        mimetype="image/png",
        message_type=message_type,
        source_doctype=source_doctype,
        source_docname=source_docname,
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Chat/Contact fetchers (keep - they query OpenWA directly, not via queue)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_whatsapp_chats(search=None):
    """Fetch recent chats from OpenWA for the contact picker."""
    # --- Permission check: whitelist admin roles, block restricted roles ---
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

    restricted_roles = {
        "Employee Self Service",
        "Employee",
        "Graphics User",
        "Proofing User",
        "Engraving User",
    }

    user_roles = set(frappe.get_roles())

    if user_roles & admin_roles:
        pass  # allowed
    elif user_roles & restricted_roles:
        frappe.throw(
            "You are not permitted to access WhatsApp chats.",
            frappe.PermissionError,
        )

    cache_key = "openwa_chats_list"
    if search:
        cache_key = "openwa_chats_search_{0}".format(frappe.safe_encode(search).decode()[:50])
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

    try:
        r = requests.get(
            "{0}/api/sessions/{1}/chats".format(base_url.rstrip("/"), session_id),
            headers=headers, timeout=15,
        )
        if r.ok:
            chats = r.json() if isinstance(r.json(), list) else []
            seen = set()
            search_lower = (search or "").lower()

            chats.sort(key=lambda c: c.get("timestamp") or 0, reverse=True)

            for c in chats:
                cid = c.get("id", "")
                if not cid or cid in seen:
                    continue
                seen.add(cid)

                is_group = c.get("isGroup", False) or "@g.us" in cid
                name = c.get("name") or cid
                last_message = c.get("lastMessage") or ""
                unread = c.get("unreadCount") or 0
                ts = c.get("timestamp") or 0

                if search_lower:
                    name_match = search_lower in name.lower()
                    id_match = search_lower in cid.lower()
                    msg_match = last_message and search_lower in last_message.lower()
                    if not (name_match or id_match or msg_match):
                        continue

                item = {
                    "id": cid,
                    "name": name,
                    "isGroup": is_group,
                    "lastMessage": last_message,
                    "timestamp": ts,
                    "unreadCount": unread,
                }

                if is_group:
                    result["groups"].append(item)
                else:
                    result["chats"].append(item)
    except Exception:
        frappe.log_error(title="OpenWA chats fetch failed", message=frappe.get_traceback())

    expires = 60 if search else 300
    frappe.cache().set_value(cache_key, result, expires_in_sec=expires)
    return result


@frappe.whitelist()
def search_whatsapp_contacts(query):
    """Server-side search for WhatsApp contacts."""
    if isinstance(query, dict):
        query = query.get('query', '')
    if not query or len(query.strip()) < 1:
        return {"chats": [], "groups": []}
    return get_whatsapp_chats(search=query.strip())


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

def _format_date_range(from_date, to_date):
    """Format date range for screenshot captions."""
    import calendar as _cal
    try:
        fy, fm, fd = [int(x) for x in from_date.split("-")]
        ty, tm, td = [int(x) for x in to_date.split("-")]
        if fy == ty and fm == tm and fd == td:
            return "{0} {1}, {2}".format(_cal.month_name[fm], fd, fy)
        last_day = _cal.monthrange(fy, fm)[1]
        if fy == ty and fm == tm and fd == 1 and td == last_day:
            return "{0} {1}".format(_cal.month_name[fm], fy)
        if fy == ty and fm == tm:
            return "{0} {1}-{2}, {3}".format(_cal.month_name[fm], fd, td, fy)
        if fy == ty:
            return "{0} {1} - {2} {3}, {4}".format(
                _cal.month_name[fm], fd, _cal.month_name[tm], td, fy)
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
<style>body{margin:0;padding:20px;font-family:Arial,sans-serif;background:#fff;}</style>
</head><body>
<h3 style="text-align:center;margin:0 0 10px 0;color:#333;">{0}</h3>
{1}
</body></html>""".format(title, table)


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
# Block-specific WhatsApp senders (dashboard "Send to WhatsApp" buttons)
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


@frappe.whitelist()
def send_engraving_whatsapp(from_date=None, to_date=None):
    """Engraving details — grouped by machine, vertical tables, TMM Total row."""
    from_date = from_date or frappe.utils.today()
    to_date = to_date or frappe.utils.today()

    result = frappe.call("frappe.desk.query_report.run",
        report_name="Engraving Details By Date",
        filters={"from_date": from_date, "to_date": to_date}) or {}
    data = result.get("result", [])
    cols = result.get("columns", [])

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

        tmm_idx_disp = next((i for i, c in enumerate(display_cols) if c.get('label') == "TMM"), -1)
        total_cells = ""
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
        message_type="Print PDF",
        meta={"from_date": from_date, "to_date": to_date, "caption": caption},
    )


@frappe.whitelist()
def send_engraving_monthly_whatsapp(from_date=None, to_date=None):
    """Engraving MONTHLY SUMMARY — Machine | Total TMM only."""
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
        message_type="Print PDF",
        meta={"from_date": from_date, "to_date": to_date, "caption": caption},
    )


@frappe.whitelist()
def send_proofing_whatsapp(from_date=None, to_date=None):
    """Proofing details — Date column hidden, QTY/Length/Circum renamed, Total QTY+TMM row."""
    from_date = from_date or frappe.utils.today()
    to_date = to_date or frappe.utils.today()

    result = frappe.call("frappe.desk.query_report.run",
        report_name="Proofing Details By Date",
        filters={"from_date": from_date, "to_date": to_date}) or {}
    data = result.get("result", [])
    cols = result.get("columns", [])

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
        message_type="Print PDF",
        meta={"from_date": from_date, "to_date": to_date, "caption": caption},
    )


@frappe.whitelist()
def send_dispatch_customer_whatsapp(from_date=None, to_date=None):
    """Customer dispatch screenshot — per-customer rows + single Total row."""
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

    total_cells = "<td style='{0}'><b>Total</b></td>".format(td_style)
    if cyl_idx >= 0:
        total_cells += "<td style='{0}'><b>{1}</b></td>".format(td_style, total_cyl)
    if tmm_idx >= 0:
        total_cells += "<td style='{0}'><b>{1:.2f}</b></td>".format(td_style, total_tmm)
    rows_html += "<tr style='font-weight:bold;background:#eef6f9;'>{0}</tr>".format(total_cells)

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
        message_type="Dispatch PDF",
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
        message_type="Print PDF",
        meta={"from_date": from_date, "to_date": to_date, "caption": caption},
    )


@frappe.whitelist()
def send_dispatch_yearly_whatsapp(from_date=None, to_date=None):
    """Yearly dispatch screenshot — grouped by job type, Main vs Excluded."""
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
        message_type="Print PDF",
        meta={"from_date": from_date, "to_date": to_date, "caption": caption},
    )


@frappe.whitelist()
def send_job_status_whatsapp(from_date=None, to_date=None):
    """Sales order status — Workflow State/Count/TMM/Percentage (count-based)."""
    from_date = from_date or "2025-10-01"
    to_date = to_date or frappe.utils.today()

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
        message_type="Print PDF",
        meta={"from_date": from_date, "to_date": to_date, "caption": caption},
    )


@frappe.whitelist()
def send_monthly_report_whatsapp(month=None):
    """Kreativ monthly summary — pivoted proofing/engraving/dispatch tables."""
    if not month:
        frappe.throw("Month is required (YYYY-MM)")
    from_date = "{0}-01".format(month)
    last_day = frappe.utils.get_last_day(from_date).day
    to_date = "{0}-{1:02d}".format(month, last_day)
    filters = {"from_date": from_date, "to_date": to_date}

    month_label = _format_month(month)
    title_text = "MONTHLY SUMMARY ({0})".format(month_label)

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

    # --- PROOFING: group by date -> Date, QTY, TMM ---
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

    # --- ENGRAVING: group by date+machine -> pivot columns per machine ---
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

    main_d_cyl = sum(float(r.get("total_cylinders", 0) or 0) for r in disp_data
                     if isinstance(r, dict) and r.get("job_type") in main_types)
    main_d_tmm = sum(float(r.get("total_tmm", 0) or 0) for r in disp_data
                     if isinstance(r, dict) and r.get("job_type") in main_types)
    excl_d_cyl = sum(float(r.get("total_cylinders", 0) or 0) for r in disp_data
                     if isinstance(r, dict) and r.get("job_type") in excluded_types)
    excl_d_tmm = sum(float(r.get("total_tmm", 0) or 0) for r in disp_data
                     if isinstance(r, dict) and r.get("job_type") in excluded_types)

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
        message_type="Print PDF",
        meta={"month": month, "from_date": from_date, "to_date": to_date, "caption": caption},
    )


# ---------------------------------------------------------------------------
# OpenWA Settings Whitelisted Methods
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_openwa_session_status():
    """Fetch current session status from OpenWA gateway."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.get_session_status()


@frappe.whitelist()
def get_openwa_session_qr():
    """Get QR code image for the session."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.get_session_qr()


@frappe.whitelist()
def start_openwa_session():
    """Start/Restart the WhatsApp session."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.start_session()


@frappe.whitelist()
def stop_openwa_session():
    """Stop the WhatsApp session."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.stop_session()


@frappe.whitelist()
def create_new_openwa_session():
    """Create a new session on OpenWA and update settings."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.create_session()


@frappe.whitelist()
def send_test_whatsapp():
    """Enqueue a test WhatsApp message."""
    frappe.only_for(("System Manager", "HR Manager"))
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.chat_id:
        frappe.throw("No Recipient Chat ID set in OpenWA Settings")

    return dispatch_whatsapp(
        recipient=settings.chat_id,
        text="✅ Test message from ERPNext — your WhatsApp channel is working.",
        message_type="Test",
        source_doctype="System",
        source_docname="test",
        priority="Urgent",
    )


@frappe.whitelist()
def send_whatsapp_via_openwa(message=None, chat_id=None, file_data=None, file_type=None, filename=None):
    """Generic WhatsApp send via dispatcher."""
    frappe.only_for(("System Manager", "HR Manager"))
    settings = frappe.get_cached_doc("OpenWA Settings")
    target_chat = chat_id or settings.chat_id

    if not target_chat:
        frappe.throw("No Chat ID specified and no default in settings")

    file_b64 = ""
    if file_data:
        if isinstance(file_data, bytes):
            file_b64 = base64.b64encode(file_data).decode("utf-8")
        else:
            file_b64 = file_data

    if file_type and file_data:
        if file_type in ["image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"]:
            msg_type = "Screenshot"
        else:
            msg_type = "Print PDF"
    else:
        msg_type = "Custom"

    return dispatch_whatsapp(
        recipient=target_chat,
        text=message or "",
        file_b64=file_b64,
        filename=filename or "document",
        mimetype=file_type or "application/pdf",
        message_type=msg_type,
        source_doctype="System",
        source_docname="",
    )


@frappe.whitelist()
def switch_theme(theme):
    """Override for frappe.core.doctype.user.user.switch_theme."""
    allowed = ["Light", "Dark", "Automatic", "Kreativ"]
    if theme in allowed:
        frappe.db.set_value("User", frappe.session.user, "desk_theme", theme)