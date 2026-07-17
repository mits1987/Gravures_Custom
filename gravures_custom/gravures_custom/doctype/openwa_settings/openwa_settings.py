# Copyright (c) 2024, Kreativ Gravures
# License: MIT

import frappe
from frappe.model.document import Document


class OpenWASettings(Document):
	pass


@frappe.whitelist()
def get_session_status():
	"""Fetch current session status from OpenWA gateway."""
	frappe.only_for(("System Manager", "HR Manager"))
	from gravures_custom.overrides import _get_openwa_config
	base_url, api_key, session_id = _get_openwa_config()

	if not base_url or not api_key:
		return {"status": "error", "message": "Base URL and/or API Key not configured"}

	import requests
	try:
		r = requests.get(f"{base_url}/api/sessions/{session_id}",
						 headers={"X-API-Key": api_key}, timeout=10)
		if r.status_code == 404:
			return {"status": "not_found", "message": "Session not found on OpenWA (may have been deleted)."}
		if r.status_code != 200:
			return {"status": "error", "message": f"Session API returned {r.status_code}: {r.text[:200]}"}

		data = r.json()
		return {
			"status": data.get("status", "unknown"),
			"phone": data.get("phone"),
			"pushname": data.get("pushname"),
			"last_active": data.get("lastActive"),
			"session_id": data.get("id"),
			"session_name": data.get("name"),
		}
	except Exception as e:
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def get_session_qr():
	"""Get QR code image for the session (base64 data URL)."""
	frappe.only_for(("System Manager", "HR Manager"))
	from gravures_custom.overrides import _get_openwa_config
	base_url, api_key, session_id = _get_openwa_config()

	if not base_url or not api_key:
		return {"status": "error", "message": "Base URL and/or API Key not configured"}

	import requests
	try:
		r = requests.get(f"{base_url}/api/sessions/{session_id}/qr",
						 headers={"X-API-Key": api_key}, timeout=10)
		if r.status_code == 404:
			return {"status": "error", "message": "Session not found on OpenWA"}
		if r.status_code != 200:
			return {"status": "error", "message": f"QR API returned {r.status_code}: {r.text[:200]}"}

		data = r.json()
		qr_code = data.get("qrCode", "")
		return {
			"status": "ok",
			"qr": qr_code,
			"session_status": data.get("status", "qr_ready"),
		}
	except Exception as e:
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def start_session():
	"""Start/Restart the WhatsApp session."""
	frappe.only_for(("System Manager", "HR Manager"))
	from gravures_custom.overrides import _get_openwa_config
	base_url, api_key, session_id = _get_openwa_config()

	if not base_url or not api_key:
		return {"status": "error", "message": "Base URL and/or API Key not configured"}

	import requests
	try:
		r = requests.post(f"{base_url}/api/sessions/{session_id}/start",
						  headers={"X-API-Key": api_key}, timeout=15)
		if r.status_code in (200, 201):
			data = r.json()
			return {"status": "ok", "message": f"Session start requested. New status: {data.get('status', 'unknown')}"}
		return {"status": "error", "message": f"Start returned {r.status_code}: {r.text[:200]}"}
	except Exception as e:
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def stop_session():
	"""Stop the WhatsApp session."""
	frappe.only_for(("System Manager", "HR Manager"))
	from gravures_custom.overrides import _get_openwa_config
	base_url, api_key, session_id = _get_openwa_config()

	if not base_url or not api_key:
		return {"status": "error", "message": "Base URL and/or API Key not configured"}

	import requests
	try:
		r = requests.post(f"{base_url}/api/sessions/{session_id}/stop",
						  headers={"X-API-Key": api_key}, timeout=10)
		if r.status_code in (200, 204):
			return {"status": "ok", "message": "Session stopped successfully"}
		return {"status": "error", "message": f"Stop returned {r.status_code}: {r.text[:200]}"}
	except Exception as e:
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def create_new_session():
	"""Create a brand new WhatsApp session on OpenWA and update settings."""
	frappe.only_for(("System Manager", "HR Manager"))
	from gravures_custom.overrides import _get_openwa_config
	base_url, api_key, _ = _get_openwa_config()

	if not base_url or not api_key:
		return {"status": "error", "message": "Base URL and/or API Key not configured"}

	site_name = frappe.local.site or "default"
	import requests
	try:
		# 1. Create session
		r = requests.post(f"{base_url}/api/sessions",
						  headers={"Content-Type": "application/json", "X-API-Key": api_key},
						  json={"name": site_name}, timeout=15)
		if r.status_code != 201:
			return {"status": "error", "message": f"Create session returned {r.status_code}: {r.text[:300]}"}

		new_session = r.json()
		new_session_id = new_session.get("id")
		if not new_session_id:
			return {"status": "error", "message": "No session ID returned from OpenWA"}

		# 2. Update Frappe settings
		settings = frappe.get_single("OpenWA Settings")
		settings.db_set("session_id", new_session_id, commit=True)

		# 3. Start it to get QR
		requests.post(f"{base_url}/api/sessions/{new_session_id}/start",
					  headers={"X-API-Key": api_key}, timeout=10)

		return {
			"status": "ok",
			"message": f"New session created: {new_session.get('name', '')} (id: {new_session_id}). Updated settings. Scan QR to link WhatsApp.",
			"new_session_id": new_session_id,
		}
	except Exception as e:
		return {"status": "error", "message": str(e)}