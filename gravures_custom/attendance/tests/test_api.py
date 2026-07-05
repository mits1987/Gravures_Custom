"""Tests for the whitelisted API endpoints (recalculate_year_month, recalculate_employee,
recalculate_for_checkin).

We mock frappe.call to capture calls without actually running the recalc.
"""
import unittest
from unittest.mock import patch, MagicMock


class TestWhitelistedEndpoints(unittest.TestCase):

    def test_recalculate_year_month_calls_service(self):
        """The whitelisted endpoint should delegate to service.recalculate_period."""
        from gravures_custom.attendance.api import recalculate_year_month

        fake_frappe = MagicMock()
        with patch("gravures_custom.attendance.api.frappe", fake_frappe):
            with patch(
                "gravures_custom.attendance.api.recalculate_period",
                return_value={"paired": 100, "anomalies": 5, "employees": 30, "deleted": 0, "period": "2026-05"},
            ) as mock_service:
                result = recalculate_year_month(year=2026, month=5)

        mock_service.assert_called_once_with(year=2026, month=5, employee=None)
        self.assertEqual(result["period"], "2026-05")

    def test_recalculate_year_month_validates_missing_year(self):
        from gravures_custom.attendance.api import recalculate_year_month
        fake_frappe = MagicMock()
        fake_frappe.throw = MagicMock(side_effect=Exception("ValidationError"))
        with patch("gravures_custom.attendance.api.frappe", fake_frappe):
            with self.assertRaises(Exception):
                recalculate_year_month(year=None, month=5)

    def test_recalculate_employee_calls_service(self):
        from gravures_custom.attendance.api import recalculate_employee
        with patch(
            "gravures_custom.attendance.api.recalculate_employee_for_period",
            return_value={"paired": 5, "anomalies": 0, "employees": 1, "deleted": 2, "period": "2026-05"},
        ) as mock:
            result = recalculate_employee(emp="HR-EMP-00037", year=2026, month=5)
        mock.assert_called_once_with(emp_id="HR-EMP-00037", year=2026, month=5)
        self.assertEqual(result["paired"], 5)


if __name__ == "__main__":
    unittest.main()
