# Copyright (c) 2026, kreativ-gravures
# License: MIT
"""Employee Shift Summary — Script Report.

Two views (filter "View"):
  Detail  — one row per shift: IN, OUT, worked, overtime, status.
  Summary — one row per employee: present days, total hours, overtime
            (same numbers as the old pandas script's monthly summary sheet).
"""
from datetime import date

import frappe

from gravures_custom.attendance.pairing import format_hhmm

# statuses that count as actually-worked time
WORKED_STATUSES = ("Paired", "Manual")


def execute(filters=None):
    f = frappe._dict(filters or {})
    year = int(f.year)
    month = int(f.month)
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    conditions = [
        ["shift_date", ">=", start],
        ["shift_date", "<", end],
    ]
    if f.get("employee"):
        conditions.append(["employee", "=", f.employee])

    shifts = frappe.get_all(
        "Employee Shift",
        filters=conditions,
        fields=[
            "name", "employee", "employee_name", "department", "shift_date",
            "check_in", "check_out", "worked_seconds", "overtime_seconds",
            "worked_hours", "overtime_hours", "status", "locked",
        ],
        order_by="employee, check_in, shift_date",
    )

    if f.get("view") == "Summary":
        return _summary(shifts)
    return _detail(shifts)


def _detail(shifts):
    columns = [
        {"fieldname": "employee", "label": "Employee", "fieldtype": "Link", "options": "Employee", "width": 100},
        {"fieldname": "employee_name", "label": "Name", "fieldtype": "Data", "width": 160},
        {"fieldname": "department", "label": "Department", "fieldtype": "Data", "width": 120},
        {"fieldname": "shift_date", "label": "Date", "fieldtype": "Date", "width": 100},
        {"fieldname": "check_in", "label": "Check-In", "fieldtype": "Datetime", "width": 150},
        {"fieldname": "check_out", "label": "Check-Out", "fieldtype": "Datetime", "width": 150},
        {"fieldname": "worked_hours", "label": "Worked (HH:MM)", "fieldtype": "Data", "width": 110},
        {"fieldname": "overtime_hours", "label": "Overtime (HH:MM)", "fieldtype": "Data", "width": 110},
        {"fieldname": "status", "label": "Status", "fieldtype": "Data", "width": 120},
        {"fieldname": "locked", "label": "Locked", "fieldtype": "Check", "width": 70},
        {"fieldname": "name", "label": "Shift", "fieldtype": "Link", "options": "Employee Shift", "width": 120},
    ]
    return columns, shifts


def _summary(shifts):
    per_emp = {}
    for s in shifts:
        e = per_emp.setdefault(s.employee, {
            "employee": s.employee,
            "employee_name": s.employee_name,
            "department": s.department,
            "worked": 0,
            "overtime": 0,
            "days": set(),
            "anomalies": 0,
        })
        if s.status in WORKED_STATUSES:
            e["worked"] += s.worked_seconds or 0
            e["overtime"] += s.overtime_seconds or 0
            e["days"].add(s.shift_date)
        else:
            e["anomalies"] += 1

    rows = []
    for e in sorted(per_emp.values(), key=lambda x: x["employee"]):
        rows.append({
            "employee": e["employee"],
            "employee_name": e["employee_name"],
            "department": e["department"],
            "present_days": len(e["days"]),
            "total_hours": format_hhmm(e["worked"]),
            "overtime": format_hhmm(e["overtime"]),
            "anomalies": e["anomalies"],
            # raw seconds for consumers that need totals (dashboard);
            # not in columns, so the report UI ignores them
            "_worked_seconds": e["worked"],
            "_overtime_seconds": e["overtime"],
        })

    columns = [
        {"fieldname": "employee", "label": "Employee", "fieldtype": "Link", "options": "Employee", "width": 100},
        {"fieldname": "employee_name", "label": "Name", "fieldtype": "Data", "width": 180},
        {"fieldname": "department", "label": "Department", "fieldtype": "Data", "width": 130},
        {"fieldname": "present_days", "label": "Present Days", "fieldtype": "Int", "width": 110},
        {"fieldname": "total_hours", "label": "Total Hours (HH:MM)", "fieldtype": "Data", "width": 140},
        {"fieldname": "overtime", "label": "Overtime (HH:MM)", "fieldtype": "Data", "width": 140},
        {"fieldname": "anomalies", "label": "Unresolved Anomalies", "fieldtype": "Int", "width": 150},
    ]
    return columns, rows
