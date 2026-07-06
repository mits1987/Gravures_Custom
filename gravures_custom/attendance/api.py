"""Whitelisted (client-callable) API endpoints for the attendance module.

Exposed via /api/method/gravures_custom.attendance.api.<name>
"""
import frappe
from frappe import _

from gravures_custom.attendance.service import (
    recalculate_period,
    recalculate_employee_for_period,
)
# Alias: this module defines a whitelisted function with the same name below.
# Without the alias the endpoint would shadow the import and call itself
# (infinite recursion).
from gravures_custom.attendance.service import (
    recalculate_for_checkin as _service_recalculate_for_checkin,
)

ALLOWED_ROLES = ("System Manager", "HR Manager", "HR User")


@frappe.whitelist()
def recalculate_year_month(year: int = None, month: int = None) -> dict:
    """Recalculate Employee Shift records for the given month/year.

    Args:
        year: e.g. 2026
        month: e.g. 5 (May)

    Returns dict with counts of paired/anomalies/employees affected.
    """
    frappe.only_for(ALLOWED_ROLES)
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
    frappe.only_for(ALLOWED_ROLES)
    if not emp or not year or not month:
        frappe.throw(_("Employee, year and month are all required"))
    return recalculate_employee_for_period(emp_id=emp, year=int(year), month=int(month))


@frappe.whitelist()
def recalculate_for_checkin(checkin_name: str = None) -> dict:
    """Manual trigger — same as what runs when an Employee Checkin is saved.

    Wipes & rebuilds the affected employee's shifts for current AND previous month.
    """
    frappe.only_for(ALLOWED_ROLES)
    if not checkin_name:
        frappe.throw(_("checkin_name is required"))
    return _service_recalculate_for_checkin(checkin_name)


@frappe.whitelist()
def unlock_period(emp: str = None, year: int = None, month: int = None, reason: str = "") -> dict:
    """Unlock an Employee Shift Lock period so corrections can be made.

    Requires: emp, year, month, and a non-empty reason for audit trail.
    Clears the `locked` flag and `lock_period` on all affected Employee Shift records.
    """
    frappe.only_for(ALLOWED_ROLES)
    if not emp:
        frappe.throw(_("Employee is required"))
    if not year or not month:
        frappe.throw(_("Both year and month are required"))
    if not reason or not reason.strip():
        frappe.throw(_("An unlock reason is required for audit trail"))

    year = int(year)
    month = int(month)

    from frappe.utils import now_datetime
    from gravures_custom.attendance.lock import release_shift_flags

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
    lock_doc.save(ignore_permissions=True)

    released = release_shift_flags(emp, year, month)

    frappe.db.commit()
    return {
        "status": "unlocked",
        "lock": lock,
        "shifts_released": released,
        "period": f"{year}-{month:02d}",
    }


@frappe.whitelist()
def sync_month_to_hrms(year: int = None, month: int = None, employee: str = None) -> dict:
    """Create HRMS Attendance records + Overtime Additional Salary for a month.

    Run this once shifts for the month are reviewed (all rows green). Then use
    a standard HRMS Payroll Entry -> Create Salary Slips: payment days come from
    the Attendance records, overtime pay from the Additional Salary rows.
    """
    frappe.only_for(ALLOWED_ROLES)
    if not year or not month:
        frappe.throw(_("Both year and month are required"))
    from gravures_custom.attendance.hrms import sync_month_to_hrms as _sync
    return _sync(int(year), int(month), employee=employee or None)
