"""Tests for Employee Shift schema: new locked + lock_period fields added.

We don't call python-side bar the test runs against the live factory's
Employee Shift doctype, asserting on field metadata directly.
"""
import unittest


class TestShiftLockFields(unittest.TestCase):

    def test_locked_field_exists_default_zero(self):
        meta = __import__("frappe").get_meta("Employee Shift")
        f = meta.get_field("locked")
        assert f is not None, "Employee Shift should have a 'locked' field"
        assert f.fieldtype == "Check"
        # default 0
        assert str(f.default) in ("0", "False", "false", None) or f.default == 0, \
            f"locked default must be 0; got {f.default!r}"

    def test_lock_period_field_exists(self):
        meta = __import__("frappe").get_meta("Employee Shift")
        f = meta.get_field("lock_period")
        assert f is not None, "Employee Shift should have a 'lock_period' field"
        assert f.fieldtype == "Link"
        # Must reference Employee Shift Lock
        assert f.options == "Employee Shift Lock", \
            f"lock_period.options should be 'Employee Shift Lock'; got {f.options!r}"

    def test_existing_fields_not_disrupted(self):
        """Adding the lock fields must not break existing schema."""
        meta = __import__("frappe").get_meta("Employee Shift")
        for required in ("employee", "shift_date", "check_in", "status"):
            assert meta.get_field(required) is not None, \
                f"existing field '{required}' was lost during schema change"
