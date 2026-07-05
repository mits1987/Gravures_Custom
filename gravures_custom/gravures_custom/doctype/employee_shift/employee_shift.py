# Copyright (c) 2026, kreativ-gravures
# License: MIT

import frappe
from frappe.model.document import Document


class EmployeeShift(Document):
    def validate(self):
        """Block edits to shifts whose period is locked.

        Frappe hooks used:
          - validate() runs on every save (both insert and update), so a single
            guard covers on_update + on_submit edits too.
        """
        self._guard_lock()

    def on_trash(self):
        self._guard_lock()

    def _guard_lock(self):
        """Raise ValidationError if this shift belongs to a locked period.

        HR must run `unlock_period()` first to make corrections after payroll.
        """
        if not getattr(self, "locked", None):
            return
        # bypass flag check (so HR's unlock/relock can operate)
        if getattr(self.flags, "ignore_lock_guard", None):
            return
        refr = "unknown"
        if self.lock_period:
            refr = str(self.lock_period)
        frappe.throw(
            f"This shift is locked by Employee Shift Lock '{refr}'. "
            "Click 'Unlock Period' on the lock record to allow edits. "
            "An audit-reason note is required."
        )


def get_indicator(doc):
    """Color indicators per user request:
        - RED:    Break punch anomaly (incorrect type selected)
        - ORANGE: Unpaired / carryover (needs review)
        - GREEN:  Clean paired shift
        - BLUE:   Manual correction applied
    """
    if doc.manual_correction:
        return ("Manual", "blue", "manual_correction")
    if doc.anomaly_reason == "break_punch":
        return ("Break punch - needs correction", "red", "anomaly")
    if doc.anomaly_reason in ("missing_checkout", "previous_month_carryover"):
        return ("Unpaired - review needed", "orange", "anomaly")
    return ("Paired", "green", "ok")
