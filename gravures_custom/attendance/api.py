"""Whitelisted (client-callable) API endpoints for the attendance module.

Exposed via /api/method/gravures_custom.attendance.api.<name>
"""
import frappe
from frappe import _

from gravures_custom.attendance.service import (
    recalculate_period,
    recalculate_employee_for_period,
    recalculate_for_checkin,
)


@frappe.whitelist()
def recalculate_year_month(year: int = None, month: int = None) -> dict:
    """Recalculate Employee Shift records for the given month/year.

    Args:
        year: e.g. 2026
        month: e.g. 5 (May)

    Returns dict with counts of paired/anomalies/employees affected.
    """
    if not year or not month:
        frappe.throw(_("Both year and month are required"))
    year = int(year)
    month = int(month)
    if not (1 <= month <= 12):
        frappe.throw(_("Month must be between 1 and 12"))
    return recalculate_period(year=year, month=month, employee=None)


@frappe.whitelist()
def recalculate_employee(emp: str = None, year: int = None, month: int = None) -> dict:
    """Recalculate Employee Shift records for ONE employee in a given period."""
    if not emp or not year or not month:
        frappe.throw(_("Employee, year and month are all required"))
    return recalculate_employee_for_period(emp_id=emp, year=int(year), month=int(month))


@frappe.whitelist()
def recalculate_for_checkin(checkin_name: str = None) -> dict:
    """Hook entrypoint — triggered when an Employee Checkin is saved/edited.

    Wipes & rebuilds the affected employee's shifts for current AND previous month.
    """
    if not checkin_name:
        frappe.throw(_("checkin_name is required"))
    return recalculate_for_checkin(checkin_name)


@frappe.whitelist()
def unlock_period(emp: str = None, year: int = None, month: int = None, reason: str = "") -> dict:
    """Unlock an Employee Shift Lock period so corrections can be made.

    Requires: emp, year, month, and a non-empty reason for audit trail.
    Clears the `locked` flag and `lock_period` on all affected Employee Shift records.
    """
    if not emp:
        frappe.throw(_("Employee is required"))
    if not year or not month:
        frappe.throw(_("Both year and month are required"))
    if not reason or not reason.strip():
        frappe.throw(_("An unlock reason is required for audit trail"))

    year = int(year)
    month = int(month)

    from frappe.utils import now_datetime

    lock = frappe.db.get_value(
        "Employee Shift Lock",
        [
            ["employee", "=", emp],
            ["period_year", "=", year],
            ["period_month", "=", month],
            ["unlocked_at", "is", "not set"],
        ],
        "name",
    )
    if not lock:
        frappe.throw(_("No active lock found for {0} in {1}-{2}. Already unlocked?").format(emp, year, month))

    # Unlock the lock record
    lock_doc = frappe.get_doc("Employee Shift Lock", lock)
    lock_doc.is_unlocked = 1
    lock_doc.unlocked_at = now_datetime()
    lock_doc.unlocked_by = frappe.session.user
    lock_doc.unlock_reason = reason.strip()
    lock_doc.flags.ignore_lock_guard = True  # our own guard, not needed here
    lock_doc.save(ignore_permissions=True)

    # Bulk-free Employee Shifts for this period
    import datetime
    period_start = datetime.date(year, month, 1)
    if month == 12:
        period_end = datetime.date(year + 1, 1, 1)
    else:
        period_end = datetime.date(year, month + 1, 1)

    shifts = frappe.get_all(
        "Employee Shift",
        filters={
            "employee": emp,
            "shift_date": [">=", period_start],
            "locked": 1,
        },
        pluck="name",
    )
    for shift_name in shifts:
        frappe.db.set_value(
            "Employee Shift",
            shift_name,
            {"locked": 0, "lock_period": None},
            update_modified=False,
        )

    frappe.db.commit()
    return {
        "status": "unlocked",
        "lock": lock,
        "shifts_released": len(shifts),
        "period": f"{year}-{month:02d}",
    }
