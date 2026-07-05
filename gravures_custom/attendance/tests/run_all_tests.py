"""Test runner that initializes Frappe then runs unittest. Use:
    bench --site <site> execute gravures_custom.attendance.tests.run_all_tests.run"""

import unittest
import frappe

from gravures_custom.attendance.tests import test_pairing


def run():
    """Run all attendance unit tests inside the bench context."""
    suite = unittest.TestLoader().loadTestsFromModule(test_pairing)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()
