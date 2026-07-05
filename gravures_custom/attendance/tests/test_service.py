"""Tests for the Frappe-side service: how the pure pairing logic interacts with DB.

We test by mocking `frappe` and the DB cursor so the test runs in plain Python.
The actual production code under test is recalc.py.
"""
import unittest
from datetime import datetime
from unittest.mock import patch

from gravures_custom.attendance.service import (
    build_standard_hours_map,
    default_standard_seconds,
)


class TestService(unittest.TestCase):

    def test_build_standard_hours_map_defaults_empty(self):
        """If no Employee Standard Hours records exist, return empty map."""
        import frappe
        with patch.object(frappe.db, "get_all", return_value=[]):
            result = build_standard_hours_map()
        self.assertEqual(result, {})

    def test_build_standard_hours_map_reads_records(self):
        """Standard hours per employee should be loaded correctly."""
        import frappe
        fake_rows = [
            {"employee": "1", "standard_hours": 9.0},
            {"employee": "42", "standard_hours": 8.5},
        ]
        with patch.object(frappe.db, "get_all", return_value=fake_rows):
            result = build_standard_hours_map()
        self.assertEqual(result["1"], 9.0 * 3600)
        self.assertEqual(result["42"], 8.5 * 3600)

    def test_default_seconds_helper(self):
        self.assertEqual(default_standard_seconds({}, "X"), 8 * 3600)  # default = 8h
        self.assertEqual(default_standard_seconds({"X": None}, "X"), 8 * 3600)
        self.assertEqual(default_standard_seconds({"X": 7 * 3600}, "X"), 7 * 3600)
        # different employee does not inherit X's value
        self.assertEqual(default_standard_seconds({"X": 7 * 3600}, "Y"), 8 * 3600)
