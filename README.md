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
| **Employee Shift** | One record per paired IN/OUT. Shows worked hours, overtime, status (Paired / Anomaly / Missing Check-Out / Break punch). |
| **Employee Shift Lock** | Created when a Salary Slip is submitted. Locks all shifts for that employee-month to prevent silent edits after payroll. |
| **Employee Standard Hours** | Per-employee standard shift hours (9h, 12h, or 30h for monthly). Used for overtime calculation. |
| **Employee Shift Summary** | Script Report — monthly view of all Employee Shift records. Filters: Year, Month, Employee. |

### Color-Coded List View (Employee Shift)

Rows are visually coded in the list view:

| Color | Meaning |
|---|---|
| 🟢 Green | Paired — normal IN/OUT pair, no issues |
| 🟠 Orange | Anomaly — missing check-out or previous-month carryover (needs review) |
| 🔴 Light red | Break punch — employee selected "Break Out" instead of "Check Out" |
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

#### Fixing anomalies (missing check-out)
1. Open Employee Shift list → find orange/red rows
2. **Click the check_in or check_out time** (underlined) → opens the Employee Checkin record
3. Add missing punch or correct `log_type`
4. Save → shifts auto-rebuild in 2-5 seconds → orange turns green

#### Month-end / Salary lock
1. Process Salary Slip in HRMS
2. `on_submit` hook auto-creates Employee Shift Lock for each employee-month
3. All affected Employee Shift records get `locked=1` (greyed out)
4. Future edits blocked; recalc refuses locked periods

#### Correction after payroll (unlock)
1. Find the Employee Shift Lock record for that employee-month
2. Set `is_unlocked=1`, enter reason (audit trail)
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

### Reports

**Employee Shift Summary** (Reports → Employee Shift Summary)
- Filters: Year (Int), Month (Select 1-12), Employee (optional Link)
- Columns: Employee ID, Name, Department, Date, IN, OUT, Worked Hours, Overtime, Status, Locked
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