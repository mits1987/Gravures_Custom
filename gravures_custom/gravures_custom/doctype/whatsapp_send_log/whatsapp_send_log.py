# -*- coding: utf-8 -*-
# Copyright (c) 2026, Kreativ Gravures and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document


class WhatsAppSendLog(Document):
	pass


def create_log(
	source_doctype: str,
	source_docname: str,
	recipient: str,
	message_type: str = "Print PDF",
	status: str = "Queued",
	source_print_format: str | None = None,
	recipient_display: str | None = None,
	error_message: str | None = None,
	sent_by: str | None = None,
	file_url: str | None = None,
	meta: dict | None = None,
) -> str:
	"""
	Create a WhatsApp Send Log entry.

	Returns the name of the created log entry.
	"""
	from datetime import datetime

	log = frappe.get_doc({
		"doctype": "WhatsApp Send Log",
		"source_doctype": source_doctype,
		"source_docname": source_docname,
		"source_print_format": source_print_format or "",
		"recipient": recipient,
		"recipient_display": recipient_display or "",
		"message_type": message_type,
		"status": status,
		"error_message": error_message or "",
		"sent_by": sent_by or frappe.session.user,
		"sent_at": datetime.now(),
		"file_url": file_url or "",
		"meta": frappe.as_json(meta or {}),
	})
	log.insert(ignore_permissions=True)
	return log.name


def update_log_status(log_name: str, status: str, error_message: str | None = None):
	"""Update the status of a WhatsApp Send Log entry."""
	frappe.db.set_value("WhatsApp Send Log", log_name, {
		"status": status,
		"error_message": error_message or "",
	}, update_modified=False)
	frappe.db.commit()


def get_logs_for_document(source_doctype: str, source_docname: str, limit: int = 50):
	"""Get WhatsApp Send Log entries for a specific source document."""
	return frappe.get_all(
		"WhatsApp Send Log",
		filters={
			"source_doctype": source_doctype,
			"source_docname": source_docname,
		},
		fields=[
			"name", "recipient", "recipient_display", "message_type",
			"status", "error_message", "sent_by", "sent_at", "file_url", "meta"
		],
		order_by="sent_at desc",
		limit=limit,
	)