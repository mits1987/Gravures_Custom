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
