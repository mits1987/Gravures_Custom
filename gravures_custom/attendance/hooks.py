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

    try:
        enqueue(
            "gravures_custom.attendance.service.recalculate_for_checkin",
            queue="default",
            timeout=120,
            checkin_name=doc.name,
            now=False,
            enqueue_after_commit=True,
        )
    except Exception:
        # If enqueue fails (no worker available), run inline
        recalculate_for_checkin(doc.name)
