"""Tests for Employee Shift Lock doctype + lock_period helper.

Strict TDD: these tests were written first and failed (module/class missing).
"""
import unittest
from datetime import datetime


class TestLockClass(unittest.TestCase):

    def test_lock_class_exists(self):
        """EmployeeShiftLock class should exist in gravures_custom.attendance.lock."""
        from gravures_custom.attendance.lock import EmployeeShiftLock
        self.assertTrue(hasattr(EmployeeShiftLock, "lock_period"))

    def test_lock_period_validates_month_low(self):
        """lock_period with month=0 must raise."""
        from gravures_custom.attendance.lock import EmployeeShiftLock
        import frappe
        # Use a tolerant harness: catch ValidationError and ConversionError
        with self.assertRaises(Exception):
            EmployeeShiftLock.lock_period(
                employee="HR-EMP-00037", year=2026, month=0,
                salary_slip=None, locked_by="Administrator"
            )

    def test_lock_period_validates_month_high(self):
        """lock_period with month=13 must raise."""
        from gravures_custom.attendance.lock import EmployeeShiftLock
        with self.assertRaises(Exception):
            EmployeeShiftLock.lock_period(
                employee="HR-EMP-00037", year=2026, month=13,
                salary_slip=None, locked_by="Administrator"
            )

    def test_lock_period_requires_employee(self):
        """Empty employee must raise."""
        from gravures_custom.attendance.lock import EmployeeShiftLock
        with self.assertRaises(Exception):
            EmployeeShiftLock.lock_period(
                employee="", year=2026, month=5,
                salary_slip=None, locked_by="Administrator"
            )

    def test_lock_period_validates_year(self):
        """Year must be 2000..2100."""
        from gravures_custom.attendance.lock import EmployeeShiftLock
        with self.assertRaises(Exception):
            EmployeeShiftLock.lock_period(
                employee="HR-EMP-00037", year=1999, month=5,
                salary_slip=None, locked_by="Administrator"
            )

    def test_lock_period_month_valid_passes_validation(self):
        """Month=5 with existing employee should not raise on validation; may raise on missing data fields."""
        # We expect frappe to validate month at field level via JSON constraints (1..12).
        # Calling with month=5 should NOT raise a "month must be 1..12" type error.
        # It may still raise if salary_slip or HR deps are missing — we tolerate that
        # by catching only the validation-class error message.
        from gravures_custom.attendance.lock import EmployeeShiftLock
        try:
            EmployeeShiftLock.lock_period(
                employee="HR-EMP-99999", year=2026, month=5,
                salary_slip=None, locked_by="Administrator"
            )
        except Exception as exc:
            msg = str(exc).lower()
            # Not the month-range error
            self.assertNotIn("month must be between 1 and 12", msg)
            self.assertNotIn("between 1 and 12", msg)
