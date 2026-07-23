import frappe


# ---------------------------------------------------------------------------
# Theme override
# ---------------------------------------------------------------------------

@frappe.whitelist()
def switch_theme(theme):
    """Override for frappe.core.doctype.user.user.switch_theme."""
    allowed = ["Light", "Dark", "Automatic", "Kreativ"]
    if theme in allowed:
        frappe.db.set_value("User", frappe.session.user, "desk_theme", theme)


# ---------------------------------------------------------------------------
# Utility helpers (kept for potential use by reports / custom HTML blocks)
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
