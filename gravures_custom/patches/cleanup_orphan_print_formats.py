"""Remove Print Format records whose upstream template no longer ships with any app.

When ERPNext removes a print format from its repository, the database row survives
``bench migrate`` and keeps showing up in the print format dropdown, but rendering it
raises ``TemplateNotFoundError`` (or a legacy-Jinja error). ERPNext's own cleanup patch
(``replace_legacy_print_formats_with_classic``) only knows a hard-coded list of names,
so anything else it dropped upstream is left orphaned forever.

Only upstream-owned records are touched: ``standard == "Yes"`` and no template shipped
by any installed app. Custom formats (``standard == "No"``), builder-beta formats,
print-designer formats and report-style formats are never removed.
"""

import os
import re

import frappe


def scrub(name):
	slug = name.lower().replace(" ", "_").replace("-", "_")
	return re.sub(r"[^a-z0-9_]+", "_", slug).strip("_")


def shipped_template_exists(slug):
	"""True when any installed app still ships a template folder for this slug."""
	for app in frappe.get_installed_apps():
		app_path = frappe.get_app_path(app)
		if not os.path.isdir(app_path):
			continue

		candidates = [os.path.join(app_path, "print_format", slug)]
		try:
			entries = os.listdir(app_path)
		except OSError:
			entries = []

		for entry in entries:
			module_path = os.path.join(app_path, entry)
			if os.path.isdir(module_path):
				candidates.append(os.path.join(module_path, "print_format", slug))

		for candidate in candidates:
			if os.path.isdir(candidate):
				return True

	return False


def is_referenced(name):
	"""True when the format is still referenced as somebody's default."""
	if frappe.db.get_value("DocType", {"default_print_format": name}, "name"):
		return True

	if frappe.db.get_value("Property Setter", {"property": "default_print_format", "value": name}, "name"):
		return True

	return bool(frappe.db.get_value("Payment Request", {"print_format": name}, "name"))


def execute():
	removed = []
	disabled = []

	# ONLY upstream-owned formats are eligible. "standard" is the authoritative marker:
	# "Yes" = shipped by an app, "No" = created by a user inside the site.
	# Every format with standard == "No" is user-owned and is never touched here.
	formats = frappe.get_all(
		"Print Format",
		filters={"standard": "Yes"},
		fields=[
			"name",
			"doc_type",
			"standard",
			"disabled",
			"custom_format",
			"print_format_builder_beta",
			"print_designer",
			"print_format_type",
		],
		ignore_permissions=True,
	)

	for fmt in formats:
		name = fmt.name

		# hard guard: never touch a user-created format
		if fmt.standard != "Yes":
			continue

		# report style formats have no doc_type and live as report templates
		if not fmt.doc_type:
			continue

		# builder-beta formats keep their layout in the database
		if fmt.print_format_builder_beta:
			continue

		# print-designer formats keep their layout in the database too
		if fmt.print_designer:
			continue

		if shipped_template_exists(scrub(name)):
			continue

		if is_referenced(name):
			frappe.db.set_value("Print Format", name, "disabled", 1)
			disabled.append(name)
			continue

		frappe.delete_doc("Print Format", name, force=True, ignore_permissions=True)
		removed.append(name)

	custom_preserved = frappe.db.count("Print Format", {"standard": "No"})

	if removed or disabled:
		frappe.clear_cache(doctype="Print Format")
		frappe.logger("gravures_custom").info(
			"Removed orphan print formats: {}. Disabled referenced orphans: {}. "
			"User-created formats preserved: {}".format(
				", ".join(removed) or "-",
				", ".join(disabled) or "-",
				custom_preserved,
			)
		)

	return {
		"removed": removed,
		"disabled": disabled,
		"custom_formats_preserved": custom_preserved,
	}
