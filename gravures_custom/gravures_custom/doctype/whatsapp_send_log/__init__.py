# -*- coding: utf-8 -*-
# Copyright (c) 2026, Kreativ Gravures and contributors
# For license information, please see license.txt

from .whatsapp_send_log import (
	WhatsAppSendLog,
	create_log,
	update_log_status,
	get_logs_for_document,
)

__all__ = [
	"WhatsAppSendLog",
	"create_log",
	"update_log_status",
	"get_logs_for_document",
]