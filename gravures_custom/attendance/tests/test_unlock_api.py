"""Tests for unlock_period whitelisted API."""
import unittest
from unittest.mock import patch


class TestUnlockPeriod(unittest.TestCase):

    def test_unlock_period_requires_employee(self):
        from gravures_custom.attendance.api import unlock_period
        import frappe
        with self.assertRaises(Exception) as ctx:
            unlock_period(emp="", year=2026, month=5, reason="")
        msg = str(ctx.exception).lower()
        self.assertTrue("employee" in msg or "required" in msg)

    def test_unlock_period_requires_reason(self):
        from gravures_custom.attendance.api import unlock_period
        with self.assertRaises(Exception) as ctx:
            unlock_period(emp="HR-EMP-X", year=2026, month=5, reason="")
        msg = str(ctx.exception).lower()
        self.assertTrue("reason" in msg, f"expected reason check, got: {msg}")

    def test_unlock_period_requires_month_and_year(self):
        from gravures_custom.attendance.api import unlock_period
        with self.assertRaises(Exception) as ctx:
            unlock_period(emp="HR-EMP-X", year=None, month=None, reason="test")
        msg = str(ctx.exception).lower()
        self.assertTrue("year" in msg or "month" in msg or "required" in msg)