"""Employee Shift Lock: per-employee-per-month snapshot when Salary Slip is finalized.

Lock is created in gravures_custom.attendance.lock module. The companion
doctype JSON lives in gravures_custom/gravures_custom/doctype/employee_shift_lock .
"""
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, get_datetime


class EmployeeShiftLock(Document):
    pass

    @staticmethod
    def lock_period(employee, year, month, salary_slip, locked_by, reason="Salary Slip submitted"):
        """Create a lock for (employee, year, month).

        Required: employee, year, month.
        Optional: salary_slip (Link), reason (Text), locked_by (default "Administrator").

        Snapshots total worked_seconds / overtime_seconds / shift_count for the
        period so future corrections can be compared.

        Raises:
            frappe.ValidationError if month is out of 1..12
            frappe.ValidationError if year is out of 2000..2100
            frappe.ValidationError if employee is empty
        """
        # ---- validation (pure-Python, runs before we touch the DB) ----
        if not employee or not str(employee).strip():
            frappe.throw("employee is required to lock a period")
        if not isinstance(year, int) or year < 2000 or year > 2100:
            frappe.throw("year must be an integer between 2000 and 2100")
        if not isinstance(month, int) or month < 1 or month > 12:
            frappe.throw("month must be between 1 and 12")

        # ---- compute snapshot totals from Employee Shift ----
        period_start = get_datetime(f"{year:04d}-{month:02d}-01")
        if month == 12:
            period_end = get_datetime(f"{year+1}-01-01")
        else:
            period_end = get_datetime(f"{year:04d}-{(month+1):02d}-01")

        shifts_in_period = frappe.get_all(
            "Employee Shift",
            filters=[
                ["employee", "=", employee],
                ["shift_date", ">=", str(period_start.date())],
                ["shift_date", "<", str(period_end.date())],
            ],
            fields=["worked_seconds", "overtime_seconds"],
        )
        worked = sum((s.get("worked_seconds") or 0) for s in shifts_in_period)
        ot = sum((s.get("overtime_seconds") or 0) for s in shifts_in_period)
        cnt = len(shifts_in_period)

        # ---- create the lock document ----
        doc = frappe.get_doc({
            "doctype": "Employee Shift Lock",
            "employee": employee,
            "period_year": year,
            "period_month": month,
            "salary_slip": salary_slip,
            "reason": reason or "Salary Slip submitted",
            "locked_at": now_datetime(),
            "locked_by": locked_by or frappe.session.user if frappe.session else "Administrator",
            "snapshot_total_worked_seconds": int(worked),
            "snapshot_total_overtime_seconds": int(ot),
            "snapshot_shift_count": int(cnt),
        })
        doc.flags.ignore_mandatory = True
        doc.insert(ignore_permissions=True)
        return doc
