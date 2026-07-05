# Copyright (c) 2026, kreativ-gravures
# License: MIT

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime
from gravures_custom.attendance.lock import EmployeeShiftLock as LockImpl


class EmployeeShiftLock(LockImpl, Document):
    """Controller for Employee Shift Lock doctype.

    Inherits from Document for Frappe's ORM lifecycle, and delegates
    the static `lock_period` method to the shared implementation in
    gravures_custom.attendance.lock.
    """
    pass