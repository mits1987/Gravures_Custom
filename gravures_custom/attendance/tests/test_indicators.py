"""Tests for the Employee Shift get_indicator function (color logic)."""
import importlib.util
import sys
import types
import unittest


def _load_employee_shift_module():
    """Load gravures_custom.doctype.employee_shift.employee_shift.py by file path.

    The file is loaded by Frappe's doctype loader, which means Python's
    normal import system can't find it via `gravures_custom.doctype...
    Instead we load it directly with importlib.util. We stub out the
    `frappe` import that the controller needs.
    """
    path = "/home/mitesh/frappe-bench-v16/apps/gravures_custom/gravures_custom/gravures_custom/doctype/employee_shift/employee_shift.py"

    fake_frappe = types.ModuleType("frappe")
    fake_model = types.ModuleType("frappe.model")
    fake_document = types.ModuleType("frappe.model.document")
    fake_document.Document = type("Document", (), {})

    sys.modules.setdefault("frappe", fake_frappe)
    sys.modules.setdefault("frappe.model", fake_model)
    sys.modules.setdefault("frappe.model.document", fake_document)

    spec = importlib.util.spec_from_file_location("es_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Stub:
    """Tiny stub that lets us set attributes via ctor."""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        # Default: locked=False (all tests except lock-specific ones)
        if "locked" not in kw:
            self.locked = 0


class TestGetIndicator(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_employee_shift_module()

    def test_paired_is_green(self):
        result = self.mod.get_indicator(_Stub(status="Paired", anomaly_reason="", manual_correction=0))
        label, color, _ = result
        self.assertEqual(color, "green")
        self.assertTrue(label.startswith("Paired") or label == "Paired")

    def test_missing_checkout_is_orange(self):
        result = self.mod.get_indicator(_Stub(status="Missing Check-Out", anomaly_reason="missing_checkout", manual_correction=0))
        label, color, _ = result
        self.assertEqual(color, "orange")
        self.assertIn("Unpaired", label)

    def test_break_punch_is_red(self):
        """Break punches (e.g. employee selected Break Out instead of Check Out) should be RED."""
        result = self.mod.get_indicator(_Stub(status="Anomaly", anomaly_reason="break_punch", manual_correction=0))
        label, color, _ = result
        self.assertEqual(color, "red")
        self.assertIn("Break punch", label)

    def test_manual_correction_is_blue(self):
        result = self.mod.get_indicator(_Stub(status="Manual", anomaly_reason="", manual_correction=1))
        label, color, _ = result
        self.assertEqual(color, "blue")

    def test_previous_month_carryover_is_orange(self):
        result = self.mod.get_indicator(_Stub(status="Anomaly", anomaly_reason="previous_month_carryover", manual_correction=0))
        label, color, _ = result
        self.assertEqual(color, "orange")

    def test_manual_correction_takes_precedence_over_other_flags(self):
        """If user manually fixed something, show that as blue regardless of original anomaly."""
        result = self.mod.get_indicator(_Stub(status="Anomaly", anomaly_reason="break_punch", manual_correction=1))
        label, color, _ = result
        self.assertEqual(color, "blue")



    def test_locked_is_grey(self):
        """Locked shifts should return grey indicator regardless of other status."""
        result = self.mod.get_indicator(_Stub(status="Paired", anomaly_reason="", manual_correction=0, locked=1))
        label, color, _ = result
        self.assertEqual(color, "grey")
        self.assertIn("Locked", label)

    def test_locked_takes_priority_break_punch(self):
        """Even if anomaly_reason is break_punch, locked makes it grey."""
        result = self.mod.get_indicator(_Stub(status="Anomaly", anomaly_reason="break_punch", manual_correction=0, locked=1))
        label, color, _ = result
        self.assertEqual(color, "grey")
if __name__ == "__main__":
    unittest.main()
