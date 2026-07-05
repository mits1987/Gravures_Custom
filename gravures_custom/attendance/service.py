"""Glue between the pure pairing logic and Frappe DB + doctypes.

Public entrypoints:
    recalculate_period(year, month, employee=None) -> dict
    recalculate_employee_for_period(emp_id, year, month) -> dict
    recalculate_from_checkin_checkin(checkin_name) -> dict  (called on save/edit)
"""
from datetime import datetime, timedelta
import frappe
from frappe.utils import get_datetime

from gravures_custom.attendance.pairing import (
    pair_checkins,
    format_hhmm,
    seconds_from_hours,
)


DEFAULT_STANDARD_SECONDS = 8 * 3600


def build_standard_hours_map() -> dict:
    """Return {employee_doc_name: standard_seconds} from Employee Standard Hours.

    Key is the Employee document name (HR-EMP-XXXXX), not the ZKTeco code,
    because the translation to codes happens at lookup time in recalculate_period.
    """
    rows = frappe.db.get_all(
        "Employee Standard Hours",
        fields=["employee", "standard_hours"],
    )
    return {r["employee"]: seconds_from_hours(r["standard_hours"]) for r in rows}


def default_standard_seconds(standard_map: dict, employee: str) -> int:
    """Return per-employee standard, or 8h default if not set."""
    return standard_map.get(employee) or DEFAULT_STANDARD_SECONDS


def fetch_checkins_for_period(start: datetime, end: datetime, employee: str = None) -> dict:
    """Return {employee: [sorted list of checkin dicts]} for the inclusive [start, end) window."""
    filters = {"time": ["between", [start, end]]}
    if employee:
        filters["employee"] = employee

    records = frappe.db.get_all(
        "Employee Checkin",
        filters=filters,
        fields=["name", "employee", "employee_name", "time", "log_type", "device_id"],
        order_by="employee, time",
    )

    # Add helper field for raw type detection (the device_id encodes original punch state).
    # In real data, 'device_id' looks like "Auto add (ZKTeco-26897)" — original "Break"
    # punches have it preserved in device_id text per the user's script.
    for r in records:
        dev = r.get("device_id") or ""
        if "BREAK" in dev.upper():
            r["_raw_log_type"] = "Break In" if r["log_type"] == "IN" else "Break Out"
        else:
            r["_raw_log_type"] = ""

    grouped = {}
    for r in records:
        grouped.setdefault(r["employee"], []).append(r)
    return grouped


def _serialize_checkin_for_pairing(checkins):
    """Convert DB rows to format expected by pure pair_checkins()."""
    out = []
    for c in checkins:
        ct = c["time"]
        if isinstance(ct, str):
            ct = get_datetime(ct)
        out.append({
            "time": ct,
            "log_type": c["log_type"],
            "log_type_raw": c.get("_raw_log_type", ""),
            "checkin_name": c["name"],
            "employee": c.get("employee"),
            "employee_name": c.get("employee_name"),
            "device_id": c.get("device_id"),
        })
    return out


def delete_existing_shifts(year: int, month: int, employee: str = None) -> int:
    """Wipe Employee Shift rows for the period before recalc. Returns count deleted.

    Uses >= / < comparison with a ±2 day window to catch cross-midnight shifts.
    """
    period_start = datetime(year, month, 1)
    period_end = (
        datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    )
    # Extend window ±2 days to match fetch_checkins_for_period window
    fetch_start = period_start - timedelta(days=2)
    fetch_end = period_end + timedelta(days=2)

    filters = [
        ["shift_date", ">=", fetch_start.date()],
        ["shift_date", "<", fetch_end.date()],
    ]
    if employee:
        filters = [["employee", "=", employee]] + filters

    names = frappe.db.get_all("Employee Shift", filters=filters, pluck="name")
    if not names:
        return 0
    for n in names:
        frappe.delete_doc("Employee Shift", n, ignore_permissions=True)
    frappe.db.commit()
    return len(names)


def create_shift_record(employee, paired_shift) -> str:
    """Insert one Employee Shift. Returns name."""
    doc = frappe.get_doc({
        "doctype": "Employee Shift",
        "employee": employee,
        "shift_date": paired_shift["shift_date"],
        "check_in": paired_shift["check_in_time"],
        "check_out": paired_shift.get("check_out_time"),
        "worked_hours": format_hhmm(paired_shift["total_seconds"]),
        "overtime_hours": format_hhmm(paired_shift["overtime_seconds"]),
        "worked_seconds": paired_shift["total_seconds"],
        "overtime_seconds": paired_shift["overtime_seconds"],
        "standard_hours": paired_shift.get("standard_hours", 8.0),
        "check_in_record": paired_shift.get("check_in_name"),
        "check_out_record": paired_shift.get("check_out_name"),
        "status": "Paired" if paired_shift.get("check_out_time") else "Missing Check-Out",
        "anomaly_reason": "" if paired_shift.get("check_out_time") else "missing_checkout",
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def has_active_lock(employee: str, year: int, month: int) -> bool:
    """Return True if there is a non-unlocked Employee Shift Lock for this (emp, year, month).

    An active lock has unlocked_at == None. Used by recalculate_period / recalculate_for_checkin
    to refuse re-pairing if the period is payroll-finalized.
    """
    name = frappe.db.get_value(
        "Employee Shift Lock",
        [
            ["employee", "=", employee],
            ["period_year", "=", int(year)],
            ["period_month", "=", int(month)],
            ["unlocked_at", "is", "not set"],
        ],
        "name",
    )
    return bool(name)


def recalculate_period(year: int, month: int, employee: str = None) -> dict:
    """
    Wipe & rebuild Employee Shift records for the given month.
    Includes cross-month carryover (extends window by ±2 days).

    Returns {employees_processed, paired, anomalies, deleted}.
    """
    period_start = datetime(year, month, 1)
    if month == 12:
        period_end = datetime(year + 1, 1, 1)
    else:
        period_end = datetime(year, month + 1, 1)
    # Extend window by 2 days on each side to catch cross-month pairs
    fetch_start = period_start - timedelta(days=2)
    fetch_end = period_end + timedelta(days=2)

    standard_map = build_standard_hours_map()
    grouped = fetch_checkins_for_period(fetch_start, fetch_end, employee=employee)

    # PRE-FLIGHT: refuse to delete/rewrite when Employee Shift Lock is active
    target_emp_list = [employee] if employee else list(grouped.keys())
    for emp_check in target_emp_list:
        if has_active_lock(emp_check, year, month):
            lock_name = frappe.db.get_value(
                "Employee Shift Lock",
                [
                    ["employee", "=", emp_check],
                    ["period_year", "=", int(year)],
                    ["period_month", "=", int(month)],
                    ["unlocked_at", "is", "not set"],
                ],
                "name",
            )
            frappe.throw(
                f"Cannot recalculate: Employee Shift Lock '{lock_name}' for "
                f"{emp_check} / {year}-{month:02d} is active. "
                "Click 'Unlock Period' on the lock record with a reason to allow edits."
            )

    deleted = delete_existing_shifts(year, month, employee=employee)

    total_paired = 0
    total_anomalies = 0
    emps_processed = 0

    for emp, checkins in grouped.items():
        emps_processed += 1
        standard = default_standard_seconds(standard_map, emp)
        sorted_ck = sorted(checkins, key=lambda c: c["time"])
        prepared = _serialize_checkin_for_pairing(sorted_ck)
        paired, anomalies = pair_checkins(
            prepared,
            standard_seconds=standard,
            period_year=year,
            period_month=month,
        )

        for p in paired:
            p["standard_hours"] = standard / 3600.0
            create_shift_record(emp, p)
            total_paired += 1

        # Anomalies: persist as separate Employee Shift with status = "Anomaly"
        for a in anomalies:
            doc = frappe.get_doc({
                "doctype": "Employee Shift",
                "employee": emp,
                "shift_date": a["time"].date() if hasattr(a["time"], "date") else a["time"],
                "check_in": a["time"] if a.get("log_type") == "IN" else None,
                "check_out": a["time"] if a.get("log_type") == "OUT" else None,
                "worked_hours": "",
                "overtime_hours": "",
                "standard_hours": standard / 3600.0 if standard else 8,
                "status": "Anomaly",
                "anomaly_reason": _map_anomaly_reason(a.get("reason", "")),
                "check_in_record": a.get("checkin_name") if a.get("log_type") == "IN" else None,
                "check_out_record": a.get("checkin_name") if a.get("log_type") == "OUT" else None,
            })
            doc.insert(ignore_permissions=True)
            total_anomalies += 1

    frappe.db.commit()
    return {
        "employees": emps_processed,
        "paired": total_paired,
        "anomalies": total_anomalies,
        "deleted": deleted,
        "period": f"{year}-{month:02d}",
    }


def _map_anomaly_reason(reason: str) -> str:
    """Map internal pairing reason → doctype Anomaly Reason select values."""
    r = reason.lower()
    if "break" in r:
        return "break_punch"
    if "carryover" in r:
        return "previous_month_carryover"
    if "unpaired" in r or "missing" in r:
        return "missing_checkout"
    return ""


def recalculate_employee_for_period(emp_id: str, year: int, month: int) -> dict:
    return recalculate_period(year, month, employee=emp_id)


def recalculate_for_checkin(checkin_name: str) -> dict:
    """Called after Employee Checkin save / edit. Wipes & rebuilds the affected employee's
    shifts for the month containing that checkin (and previous month, because of carryovers)."""
    c = frappe.get_doc("Employee Checkin", checkin_name)
    t = c.time
    year, month = t.year, t.month
    result = recalculate_employee_for_period(c.employee, year, month)
    # also re-process previous month for carryover that may now belong to it
    if month > 1:
        prev = recalculate_employee_for_period(c.employee, year, month - 1)
    else:
        prev = recalculate_employee_for_period(c.employee, year - 1, 12)
    return {"current": result, "previous": prev}
