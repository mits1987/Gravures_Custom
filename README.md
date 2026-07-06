# Gravures Custom

Custom ERPNext functionality for Kreativ Gravures' manufacturing and HR operations.

## Attendance System

A custom attendance engine built for flexible/rotational shifts. Employees punch IN/OUT on EasyTime Pro (ZKTeco) biometric devices, data syncs to ERPNext, and the system auto-pairs punches into shift records with worked hours and overtime calculation.

### Architecture

```
EasyTime Pro (ZKTeco) ──sync──→ Employee Checkin ──recalc──→ Employee Shift ──lock──→ Salary
         │                           │                            │
         │                           ▼                            ▼
         │                    ZKTeco Config                   Reports:
         │                    (sync frequency,                 Employee Shift Summary
         │                     last sync timestamp)
         │
         ▼
    Employee Fields:
    - employee = ZKTeco code (e.g. "97")
    - attendance_device_id = ZKTeco code
    - department = synced from EasyTime Pro
```

### Key Doctypes

| Doctype | Purpose |
|---|---|
| **Employee Shift** | One record per paired IN/OUT. Shows worked hours, overtime, status (Paired / Anomaly / Missing Check-Out / Manual). |
| **Employee Shift Lock** | Created when a Salary Slip is submitted. Locks all shifts for that employee-month to prevent silent edits after payroll. |
| **Employee Standard Hours** | Per-employee standard shift hours (9h, 12h, or 30h for monthly) used for overtime calculation, plus the hourly Overtime Rate used for overtime pay. |
| **Employee Shift Summary** | Script Report — Detail view (one row per shift) or Summary view (per employee: present days, total hours, overtime — same numbers as the old Excel script). Includes the "Sync to HRMS" and "Create Payroll Entry" buttons. |

### Color-Coded List View (Employee Shift)

Rows are visually coded in the list view:

| Color | Meaning |
|---|---|
| 🟢 Green | Paired — normal IN/OUT pair, no issues |
| 🟠 Orange | Anomaly — missing check-out or previous-month carryover (needs review) |
| 🔵 Blue | Manual correction applied |
| ⬜ Grey | Locked — payroll has been processed for this period |

### Shift Categories (3 types)

| Category | Hours | Overtime |
|---|---|---|
| 9-Hour Shift | 9h | After 9h |
| 12-Hour Shift | 12h | After 12h |
| Monthly (No OT) | 30h (daily cap) | None (monthly salary) |

### Workflows

#### Daily attendance
1. Employee punches IN/OUT on biometric device
2. ZKTeco Config syncs every 5 min (configurable: 10s-600s)
3. New Employee Checkin records are created
4. Auto-recalc runs in background → Employee Shifts rebuilt

#### Fixing anomalies (missing check-out / wrong punch type)
1. Open Employee Shift list → find orange/red rows
2. **Click the check_in or check_out time** (underlined) → opens the Employee Checkin record
3. Add missing punch or correct `log_type` (e.g. employee pressed IN instead of OUT)
4. Save → shifts auto-rebuild in 2-5 seconds → orange turns green

#### Month-end payroll (the whole flow inside ERPNext)
1. Open the **Attendance Dashboard** (`/app/attendance-dashboard`) — pick any month/year to see the final summary (per employee: present days, total hours, overtime — same as the old Excel script). Orange rows / the red "Open Anomalies" card show what still needs fixing
2. Click **Sync to HRMS (Attendance + OT)** — creates submitted `Attendance` records (payment days for payroll) and one `Additional Salary` "Overtime" entry per employee (amount = OT hours × Overtime Rate from Employee Standard Hours). Idempotent: existing records are skipped, safe to re-run
3. Click **Create Payroll Entry** → standard HRMS flow → **Create Salary Slips**
4. Submitting each Salary Slip auto-creates an Employee Shift Lock: all that employee-month's shifts get `locked=1` (greyed out), further edits and recalcs are blocked

The same buttons also exist on the Employee Shift Summary report.

#### Correction after payroll (unlock)
1. Open the Employee Shift Lock record for that employee-month
2. Tick `Unlocked` and enter an Unlock Reason (audit trail), then Save
3. All affected shifts become unlocked
4. Fix checkins, recalc, re-process salary

### Auto-Sync Details

**Sync frequency:** Default 300 seconds (5 minutes). Change in ZKTeco Config → `seconds` field.

What syncs from EasyTime Pro:
- Employee punches (attendance records) → Employee Checkin
- Employee list → Employee master (name, code, code)
- Department → Employee.department
- Biometric ID → Employee.attendance_device_id
- Face templates (NOT photos — API doesn't provide images)

### Reports & Dashboard

**Attendance Dashboard** (`/app/attendance-dashboard`)
- Month + Year selectors (view any past month)
- Cards: Employees, Present Days, Total Hours, Overtime, Open Anomalies (click → filtered shift list)
- Per-employee summary table with totals row — the old script's summary sheet, live
- Buttons: **Sync to HRMS (Attendance + OT)** (primary), Create Payroll Entry, Open Shift List, Open Full Report

**Employee Shift Summary** (Reports → Employee Shift Summary)
- Filters: Year (Int), Month (Select 1-12), Employee (optional Link), View (Detail / Summary)
- Detail columns: Employee ID, Name, Department, Date, IN, OUT, Worked Hours, Overtime, Status, Locked
- Summary columns: Employee ID, Name, Department, Present Days, Total Hours, Overtime, Unresolved Anomalies
- Buttons: **Sync to HRMS (Attendance + OT)**, **Create Payroll Entry**
- Data source: `tabEmployee Shift`

### Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/mits1987/Gravures_Custom --branch main
bench --site your-site install-app gravures_custom
```

### Dependencies

- Frappe v16+
- ERPNext v16+
- HRMS v16+
- ZKTeco/EasyTime Pro biometric device with network-accessible REST API