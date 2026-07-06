"""Document-event hooks for Employee Checkin.

on_update() fires every time a checkin is created OR modified. We must
rebuild the affected employee's shifts so color indicators and search
results stay correct, but we must NOT recompute inside the recompute
itself (would cause infinite recursion if `recalculate_period` ever
creates new checkins).

The recalc service creates `Employee Shift` records, not new `Employee
Checkin` records, so recursion is not an actual concern today. We use a
thread-local flag as defensive belt-and-suspenders in case the service
ever changes.
"""
import frappe
from frappe.utils.background_jobs import enqueue

from gravures_custom.attendance.service import recalculate_for_checkin


_REENTRANCY_KEY = "_gravures_attendance_checkin_recalc_in_progress"


def on_checkin_updated(doc, method=None):
    """Triggered after any Employee Checkin save/edit.

    Rebuilds shifts for the affected employee for the relevant month and
    its previous month (because overnight shifts cross midnight boundaries).

    Reentrance guard: we stash a thread-local sentinel on the doc itself
    rather than on frappe.flags (which is shared and we don't want to
    pollute). The recalc service creates Employee Shift records, not new
    Employee Checkin records, so recursion is not actually possible today,
    but this guard makes the contract future-proof.
    """
    if getattr(doc, "_gravures_attendance_recalc_triggered", False):
        return
    setattr(doc, "_gravures_attendance_recalc_triggered", True)

    # Deduplicate per employee-month: a 5-minute device sync inserts many
    # checkins for the same employee, and each full-month rebuild is
    # idempotent — one queued job per (employee, month) is enough. Without
    # this, N punches enqueue N identical rebuilds that can also race each
    # other on parallel workers (duplicate Employee Shift rows).
    from frappe.utils import get_datetime
    t = get_datetime(doc.time)
    try:
        enqueue(
            "gravures_custom.attendance.service.recalculate_for_checkin",
            queue="default",
            timeout=120,
            checkin_name=doc.name,
            now=False,
            enqueue_after_commit=True,
            deduplicate=True,
            job_id=f"gravures-shift-recalc-{doc.employee}-{t.year}-{t.month:02d}",
        )
    except Exception:
        # If enqueue fails (no worker available), run inline
        recalculate_for_checkin(doc.name)


def on_salary_slip_submit(doc, method=None):
    """Triggered when a Salary Slip is submitted/finalized.

    Creates an Employee Shift Lock for the employee-month range, then
    locks all Employee Shift records in that period so they cannot be
    silently edited after payroll sign-off.

    Idempotent: if a lock already exists (unlocked_at is NULL), it's a no-op.
    """
    if not doc.employee or not doc.start_date:
        return

    from gravures_custom.attendance.lock import EmployeeShiftLock
    from frappe.utils import getdate
    import datetime

    start_date = getdate(doc.start_date)
    year = start_date.year
    month = start_date.month

    # Check if lock already exists (idempotency)
    existing = frappe.db.get_value(
        "Employee Shift Lock",
        [
            ["employee", "=", doc.employee],
            ["period_year", "=", year],
            ["period_month", "=", month],
            ["unlocked_at", "is", "not set"],
        ],
        "name",
    )
    if existing:
        return  # already locked

    # Create the lock
    lock_doc = EmployeeShiftLock.lock_period(
        employee=doc.employee,
        year=year,
        month=month,
        salary_slip=doc.name,
        locked_by=frappe.session.user,
        reason=f"Salary Slip {doc.name} submitted for {year}-{month:02d}",
    )

    # Apply lock flag to all Employee Shift records in this period
    period_start = datetime.date(year, month, 1)
    if month == 12:
        period_end = datetime.date(year + 1, 1, 1)
    else:
        period_end = datetime.date(year, month + 1, 1)

    shifts = frappe.get_all(
        "Employee Shift",
        filters=[
            ["employee", "=", doc.employee],
            ["shift_date", ">=", period_start],
            ["shift_date", "<", period_end],
        ],
        pluck="name",
    )
    for shift_name in shifts:
        frappe.db.set_value(
            "Employee Shift",
            shift_name,
            {"locked": 1, "lock_period": lock_doc.name},
            update_modified=False,
        )

    frappe.db.commit()
