import frappe

# Upstream HRMS ships Employee Checkin with allow_copy disabled, which hides
# Frappe's native Duplicate action. The Duplicate button added by
# kreativ_notification (Client Script + duplicate_employee_checkin API) needs
# allow_copy set for the Form view to render it.
#
# This is applied as a DocType property at the database level rather than by
# editing apps/hrms/.../employee_checkin.json, so `bench update` can keep
# pulling upstream HRMS without local modifications blocking the pull.
# The value is idempotent and survives bench migrate.


def ensure():
	"""Set allow_copy on Employee Checkin if the doctype exists and it is off."""
	if not frappe.db.exists("DocType", "Employee Checkin"):
		return

	if frappe.db.get_value("DocType", "Employee Checkin", "allow_copy"):
		return

	frappe.db.set_value("DocType", "Employee Checkin", "allow_copy", 1)
	frappe.clear_cache(doctype="DocType")
	frappe.logger().info("ensure_employee_checkin_allow_copy: enabled allow_copy on Employee Checkin")
