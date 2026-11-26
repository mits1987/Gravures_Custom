"""Repair Print Designer formats whose ``print_designer_settings`` was left empty.

``print_designer.pdf.get_print_format_template`` and ``print_designer.pdf.pdf_body_html``
both call ``json.loads(print_format.print_designer_settings)`` without a None guard, so a
NULL value raises::

    TypeError: the JSON object must be str, bytes or bytearray, not NoneType

and the PDF download fails with HTTP 500. Filling the empty field with an empty JSON
object keeps the record renderable instead of exploding.

Only the empty/NULL value is written - existing settings are never touched.
"""

import json

import frappe


def execute():
	repaired = []

	formats = frappe.get_all(
		"Print Format",
		filters={"print_designer": 1},
		fields=["name", "print_designer_settings"],
		ignore_permissions=True,
	)

	for fmt in formats:
		settings = fmt.print_designer_settings
		if settings:
			continue

		frappe.db.set_value(
			"Print Format",
			fmt.name,
			"print_designer_settings",
			json.dumps({}),
			update_modified=False,
		)
		repaired.append(fmt.name)

	if repaired:
		frappe.clear_cache(doctype="Print Format")
		frappe.logger("gravures_custom").info(
			"Repaired empty print_designer_settings for: {}".format(", ".join(repaired))
		)

	return {"repaired": repaired}
