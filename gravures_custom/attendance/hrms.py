"""Bridge from Employee Shift into standard HRMS doctypes.

sync_month_to_hrms(year, month) does two things, both idempotent:

1. Attendance — one submitted record per employee per shift_date
   (status Present, working_hours, in_time/out_time). This is what makes
   HRMS payroll work: Salary Slip payment days, absence deduction and all
   stock attendance reports key off Attendance. Existing records for the
   same employee+date are skipped, so re-running is safe.

2. Additional Salary — one submitted "Overtime" row per employee for the
   month (amount = total OT hours x Employee Standard Hours.overtime_rate).
   Employees with no overtime or no rate are skipped and reported.

After syncing, use a standard Payroll Entry -> Create Salary Slips.
"""
from datetime import date, timedelta

import frappe

OT_COMPONENT = "Overtime"


def _period(year: int, month: int):
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def sync_month_to_hrms(year: int, month: int, employee: str = None) -> dict:
    start, end = _period(year, month)

    filters = [
        ["shift_date", ">=", start],
        ["shift_date", "<", end],
        ["status", "in", ["Paired", "Manual"]],
    ]
    if employee:
        filters.append(["employee", "=", employee])

    shifts = frappe.get_all(
        "Employee Shift",
        filters=filters,
        fields=["employee", "shift_date", "check_in", "check_out",
                "worked_seconds", "overtime_seconds"],
        order_by="employee, check_in",
    )
    if not shifts:
        return {"period": f"{year}-{month:02d}", "message": "No paired shifts found for this period."}

    att = _create_attendance(shifts)
    ot = _create_overtime(shifts, end)

    frappe.db.commit()
    return {
        "period": f"{year}-{month:02d}",
        "attendance_created": att["created"],
        "attendance_skipped_existing": att["skipped"],
        "attendance_errors": att["errors"],
        "overtime_created": ot["created"],
        "overtime_skipped_existing": ot["skipped"],
        "overtime_no_rate": ot["no_rate"],
        "overtime_errors": ot["errors"],
    }


def _create_attendance(shifts) -> dict:
    # Aggregate per employee-day: several IN/OUT pairs on one date become one
    # Attendance with summed hours, first IN and last OUT.
    by_day = {}
    for s in shifts:
        d = by_day.setdefault((s.employee, s.shift_date), {
            "worked": 0, "in": s.check_in, "out": s.check_out,
        })
        d["worked"] += s.worked_seconds or 0
        if s.check_in and (not d["in"] or s.check_in < d["in"]):
            d["in"] = s.check_in
        if s.check_out and (not d["out"] or s.check_out > d["out"]):
            d["out"] = s.check_out

    created, skipped, errors = 0, 0, []
    for (emp, day), d in sorted(by_day.items()):
        if frappe.db.exists("Attendance", {
            "employee": emp, "attendance_date": day, "docstatus": ["<", 2],
        }):
            skipped += 1
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Attendance",
                "employee": emp,
                "attendance_date": day,
                "status": "Present",
                "working_hours": round(d["worked"] / 3600.0, 2),
                "in_time": d["in"],
                "out_time": d["out"],
            })
            doc.insert(ignore_permissions=True)
            doc.submit()
            created += 1
        except Exception as e:
            errors.append(f"{emp} {day}: {e}")
    return {"created": created, "skipped": skipped, "errors": errors}


def _create_overtime(shifts, period_end: date) -> dict:
    ot_totals = {}
    for s in shifts:
        ot_totals[s.employee] = ot_totals.get(s.employee, 0) + (s.overtime_seconds or 0)

    rates = {
        r["employee"]: r["overtime_rate"]
        for r in frappe.get_all(
            "Employee Standard Hours",
            fields=["employee", "overtime_rate"],
        )
    }

    payroll_date = period_end - timedelta(days=1)  # last day of month
    _ensure_ot_component()

    created, skipped, no_rate, errors = 0, 0, [], []
    for emp, ot_seconds in sorted(ot_totals.items()):
        if not ot_seconds:
            continue
        rate = rates.get(emp) or 0
        if not rate:
            no_rate.append(emp)
            continue
        if frappe.db.exists("Additional Salary", {
            "employee": emp,
            "salary_component": OT_COMPONENT,
            "payroll_date": payroll_date,
            "docstatus": ["<", 2],
        }):
            skipped += 1
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Additional Salary",
                "employee": emp,
                "company": frappe.db.get_value("Employee", emp, "company"),
                "salary_component": OT_COMPONENT,
                "payroll_date": payroll_date,
                "amount": round(ot_seconds / 3600.0 * rate, 2),
                "overwrite_salary_structure_amount": 0,
            })
            doc.insert(ignore_permissions=True)
            doc.submit()
            created += 1
        except Exception as e:
            errors.append(f"{emp}: {e}")
    return {"created": created, "skipped": skipped, "no_rate": no_rate, "errors": errors}


def _ensure_ot_component():
    if not frappe.db.exists("Salary Component", OT_COMPONENT):
        frappe.get_doc({
            "doctype": "Salary Component",
            "salary_component": OT_COMPONENT,
            "salary_component_abbr": "OT",
            "type": "Earning",
        }).insert(ignore_permissions=True)
