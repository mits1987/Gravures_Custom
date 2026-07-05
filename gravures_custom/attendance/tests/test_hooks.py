"""Tests for the hooks module."""
import unittest
from unittest.mock import patch, MagicMock


class TestHooks(unittest.TestCase):

    def test_on_checkin_updated_enqueues_recalc(self):
        from gravures_custom.attendance.hooks import on_checkin_updated

        fake_frappe = MagicMock()

        captured = {}

        def fake_enqueue(method, **kwargs):
            captured["method"] = method
            captured["kwargs"] = kwargs

        # Use a plain class with explicit attribute so _gravures_attendance_recalc_triggered is False
        class FakeDoc:
            name = "EC-TEST-123"
        fake_doc = FakeDoc()

        with patch("gravures_custom.attendance.hooks.frappe", fake_frappe):
            with patch("gravures_custom.attendance.hooks.enqueue", fake_enqueue):
                on_checkin_updated(fake_doc, method="on_update")

        self.assertEqual(captured["method"], "gravures_custom.attendance.service.recalculate_for_checkin")
        self.assertEqual(captured["kwargs"]["checkin_name"], "EC-TEST-123")
        self.assertTrue(captured["kwargs"]["now"] is False, "should be queued, not run synchronously")
        self.assertTrue(captured["kwargs"].get("enqueue_after_commit") is True,
                        "must enqueue AFTER commit so the worker sees fresh values")

    def test_on_checkin_updated_falls_back_to_sync_if_enqueue_fails(self):
        from gravures_custom.attendance.hooks import on_checkin_updated

        fake_frappe = MagicMock()

        run_called = {}

        def fake_sync(checkin_name):
            run_called["name"] = checkin_name

        def fake_enqueue_fail(*args, **kwargs):
            raise Exception("no worker")

        class FakeDoc:
            name = "EC-FALLBACK-9"
        fake_doc = FakeDoc()

        with patch("gravures_custom.attendance.hooks.frappe", fake_frappe):
            with patch("gravures_custom.attendance.hooks.enqueue", fake_enqueue_fail):
                with patch("gravures_custom.attendance.hooks.recalculate_for_checkin", fake_sync):
                    on_checkin_updated(fake_doc, method="on_update")

        self.assertEqual(run_called["name"], "EC-FALLBACK-9")

    def test_on_checkin_updated_doesnt_recurse(self):
        """When a recalc has already been triggered on a doc instance, the hook should short-circuit."""
        from gravures_custom.attendance.hooks import on_checkin_updated

        enqueue_called = {}

        def fake_enqueue(*args, **kwargs):
            enqueue_called["yes"] = True

        # Build a doc with the sentinel already set
        class FakeDoc:
            name = "EC-X"
            _gravures_attendance_recalc_triggered = True
            # MagicMock won't define dunder attrs but getattr works:

        # Note: getattr needs to find the attribute — use a real plain object
        # The hook calls setattr too, must allow it
        import json
        fake_doc = type("FakeDoc", (), {"name": "EC-X", "_gravures_attendance_recalc_triggered": True})

        # Patch frappe at module level but skip the reentrance handler
        fake_frappe = MagicMock()
        with patch("gravures_custom.attendance.hooks.frappe", fake_frappe):
            with patch("gravures_custom.attendance.hooks.enqueue", fake_enqueue):
                on_checkin_updated(fake_doc)

        self.assertNotIn("yes", enqueue_called)
