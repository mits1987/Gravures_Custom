# Copyright (c) 2026, kreativ-gravures
# License: MIT

import frappe
from frappe.model.document import Document


class EmployeeShift(Document):
    pass


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
