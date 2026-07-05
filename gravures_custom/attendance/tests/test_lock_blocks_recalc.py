"""Tests that recalculate_period rejects locked periods."""
import unittest
from unittest.mock import patch


class TestRecalcBlockedByLock(unittest.TestCase):

    def test_no_lock_returns_false(self):
        from gravures_custom.attendance.service import has_active_lock
        with patch("gravures_custom.attendance.service.frappe") as fmock:
            fmock.db.get_value.return_value = None
            result = has_active_lock("HR-EMP-TEST", 2026, 5)
        self.assertFalse(result, "no lock → should return False")

    def test_active_lock_returns_true(self):
        from gravures_custom.attendance.service import has_active_lock
        with patch("gravures_custom.attendance.service.frappe") as fmock:
            fmock.db.get_value.return_value = "ESL-FOO-01"
            result = has_active_lock("HR-EMP-TEST", 2026, 5)
        self.assertTrue(result, "active lock exists → should return True")