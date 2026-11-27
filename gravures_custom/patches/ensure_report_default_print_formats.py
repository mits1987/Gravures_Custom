"""Give reports a stored default print format.

Frappe's report print preview (``frappe/printing/page/report_print/report_print.js``)
preselects the format named by ``report_doc.default_print_format``:

	default: [print_format, this.report.report_doc.default_print_format].find((name) =>
		names.includes(name)) || ""

But the ``Report`` doctype has no ``default_print_format`` field in v16. ERPNext's
report JSON still declares it (``"default_print_format": "Accounts Receivable
Standard"`` in ``erpnext/accounts/report/accounts_receivable/accounts_receivable.json``)
but Frappe drops the key on import because no such field exists — so the
preview falls back to the generic grid template. The field is therefore added
here as a Custom Field, which makes Frappe's own code path work without
patching core.

Standard Report records are deleted and re-imported from their JSON on every
``bench migrate`` (``frappe/modules/import_file.py`` -> ``delete_old_doc``), and
the value is not in that doctype's ``ignore_values`` list, so the stored default
is wiped on every migrate. This runs from the ``after_migrate`` hook so the
default is re-applied afterwards.
"""

import frappe


# report name -> print format preselected in the report print preview
REPORT_DEFAULT_PRINT_FORMATS = {
	"Accounts Receivable": "Customer Bill-wise Pending",
}


def ensure_custom_field():
	"""Add `default_print_format` to the Report doctype."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(
		{
			"Report": [
				{
					"fieldname": "default_print_format",
					"label": "Default Print Format",
					"fieldtype": "Link",
					"options": "Print Format",
					"insert_after": "letter_head",
					"no_copy": 1,
				}
			]
		},
		update=True,
	)


def execute():
	"""Apply the defaults. Never raises — a failure here must not break `bench migrate`.

	This runs from the ``after_migrate`` hook, so any exception would abort the
	whole migration (and therefore `bench update`). Every problem is logged and
	the hook returns quietly instead.
	"""
	logger = frappe.logger("gravures_custom")

	try:
		ensure_custom_field()
	except Exception:
		logger.error("Could not create Report.default_print_format custom field")
		logger.error(frappe.get_traceback())
		return []

	applied = []
	skipped = []

	for report_name, print_format in REPORT_DEFAULT_PRINT_FORMATS.items():
		try:
			if not frappe.db.exists("Report", report_name):
				skipped.append("{} (report not found)".format(report_name))
				continue

			if not frappe.db.exists("Print Format", print_format):
				skipped.append("{} (print format '{}' not found)".format(report_name, print_format))
				continue

			if frappe.db.get_value("Report", report_name, "default_print_format") != print_format:
				frappe.db.set_value(
					"Report", report_name, "default_print_format", print_format, update_modified=False
				)
				applied.append(report_name)
		except Exception:
			logger.error("Could not set default print format for report '{}'".format(report_name))
			logger.error(frappe.get_traceback())

	if applied:
		try:
			frappe.clear_cache(doctype="Report")
		except Exception:
			logger.warning("Could not clear Report cache after applying default print formats")

	for note in skipped:
		logger.warning("Report default print format not applied - {}".format(note))

	return applied
