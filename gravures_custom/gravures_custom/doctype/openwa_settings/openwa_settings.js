frappe.ui.form.on('OpenWA Settings', {
	refresh(frm) {
		// Auto-fetch status on load if base config exists
		if (frm.doc.base_url && frm.doc.api_key) {
			fetchSessionStatus(frm);
		}

		frm.add_custom_button(__('🔄 Refresh Status'), function() {
			fetchSessionStatus(frm);
		});

		frm.add_custom_button(__('📱 Fetch QR Code'), function() {
			fetchQR(frm);
		});

		frm.add_custom_button(__('▶ Start Session'), function() {
			frappe.call({
				method: 'gravures_custom.overrides.start_openwa_session',
				freeze: true,
				freeze_message: __('Starting WhatsApp session...'),
				callback: function(r) {
					if (r.message && r.message.status === 'ok') {
						frappe.show_alert({ message: '✅ ' + r.message.message, indicator: 'green' });
						fetchSessionStatus(frm);
					} else {
						const msg = (r.message && r.message.message) || 'Start failed';
						frappe.msgprint({ message: '❌ ' + msg, indicator: 'red' });
					}
				}
			});
		});

		frm.add_custom_button(__('⏹ Stop Session'), function() {
			frappe.call({
				method: 'gravures_custom.overrides.stop_openwa_session',
				freeze: true,
				freeze_message: __('Stopping WhatsApp session...'),
				callback: function(r) {
					if (r.message && r.message.status === 'ok') {
						frappe.show_alert({ message: '✅ ' + r.message.message, indicator: 'green' });
						setTimeout(() => fetchSessionStatus(frm), 2000);
					} else {
						const msg = (r.message && r.message.message) || 'Stop failed';
						frappe.msgprint({ message: '❌ ' + msg, indicator: 'red' });
					}
				}
			});
		});

		frm.add_custom_button(__('🆕 Create New Session'), function() {
			frappe.confirm(
				'<b>Create a brand new WhatsApp session?</b><br><br>' +
				'This will:<br>' +
				'1. Create a new session on OpenWA<br>' +
				'2. Update the Session ID in these settings<br>' +
				'3. Start the session so you can scan the QR<br><br>' +
				'The <b>old session will be abandoned</b> (not deleted). Continue?',
				function() {
					frappe.call({
						method: 'gravures_custom.overrides.create_new_openwa_session',
						freeze: true,
						freeze_message: __('Creating new session...'),
						callback: function(r) {
							if (r.message && r.message.status === 'ok') {
								frappe.msgprint({ message: '✅ ' + r.message.message, indicator: 'green' });
								frm.reload_doc();
							} else {
								const msg = (r.message && r.message.message) || 'Creation failed';
								frappe.msgprint({ message: '❌ ' + msg, indicator: 'red' });
							}
						}
					});
				}
			);
		});

		frm.add_custom_button(__('✉ Send Test'), function() {
			frappe.call({
				method: 'gravures_custom.overrides.send_test_whatsapp',
				freeze: true,
				freeze_message: __('Sending...'),
				callback: function(r) {
					if (r.message && r.message.status === 'success') {
						frappe.msgprint({ message: '✅ ' + r.message.message, indicator: 'green' });
					} else {
						const msg = (r.message && r.message.message) || 'Send failed';
						frappe.msgprint({ message: '❌ ' + msg, indicator: 'red' });
					}
				}
			});
		});

		frm.add_custom_button(__('🌐 OpenWA Dashboard'), function() {
			if (frm.doc.base_url) {
				window.open(frm.doc.base_url, '_blank');
			} else {
				frappe.msgprint('Set Base URL first');
			}
		});
	}
});


function fetchSessionStatus(frm) {
	if (!frm.doc.base_url || !frm.doc.api_key) {
		frappe.msgprint({ message: 'Set Base URL and API Key first, then Save.', indicator: 'orange' });
		return;
	}

	frappe.call({
		method: 'gravures_custom.overrides.get_openwa_session_status',
		freeze: true,
		freeze_message: __('Fetching session status...'),
		callback: function(r) {
			if (!r.message) return;

			const data = r.message;

			// Handle errors
			if (data.status === 'error') {
				frm.set_value('session_status', 'error');
				showQrArea(frm, 'error', '❌ ' + (data.message || 'Connection error'));
				return;
			}

			if (data.status === 'not_found') {
				frm.set_value('session_status', 'error');
				showQrArea(frm, 'info',
					'Session not found on OpenWA server.' +
					'<br><br><b>Options:</b>' +
					'<br>1. Click <b>🆕 Create New Session</b> to create one' +
					'<br>2. Or enter the Session ID manually and click <b>▶ Start Session</b>'
				);
				return;
			}

			// Update status field
			frm.set_value('session_status', data.status);

			// Update phone / pushname if available
			if (data.phone || data.pushname) {
				if (data.phone) frm.set_value('session_phone', data.phone);
				if (data.pushname) frm.set_value('session_pushname', data.pushname);
				frm.refresh_field('session_phone');
				frm.refresh_field('session_pushname');
			}

			// Choose what to show in the QR section based on status
			switch (data.status) {
				case 'qr_ready':
					// QR code is available — fetch and display it
					fetchQR(frm);
					break;

				case 'disconnected':
				case 'created':
					// Needs start — offer to start
					showQrArea(frm, 'info',
						'Session status: <b>' + data.status + '</b>' +
						'<br><br>Click <b>▶ Start Session</b> to activate, then scan the QR code.'
					);
					break;

				case 'ready':
				case 'connected':
					// Connected — show success info
					showQrArea(frm, 'connected',
						'<b>✅ Session Connected</b><br>' +
						'Phone: ' + (data.phone || '—') + '<br>' +
						'Name: ' + (data.pushname || '—') + '<br>' +
						'Session: ' + (data.session_name || data.session_id || '—')
					);
					break;

				default:
					// unknown or any other status — show QR anyway if possible
					showQrArea(frm, 'info',
						'Session status: <b>' + data.status + '</b>' +
						'<br><br>Click <b>📱 Fetch QR Code</b> to try fetching the QR, or <b>▶ Start Session</b>.'
					);
			}
		}
	});
}


function showQrArea(frm, type, message) {
	let html = '';
	if (type === 'connected') {
		html = `<div style="padding:20px;background:#e8f5e9;border-radius:8px;border:1px solid #c8e6c9;text-align:center;font-size:14px;">
			${message}
			<p style="margin-top:12px;">
				<button class="btn btn-sm btn-primary" onclick="cur_frm && cur_frm.trigger('refresh')">🔄 Refresh</button>
			</p>
		</div>`;
	} else if (type === 'error') {
		html = `<div style="padding:20px;background:#ffebee;border-radius:8px;border:1px solid #ffcdd2;text-align:center;font-size:14px;">
			<div style="color:#d32f2f;font-size:18px;margin-bottom:8px;">⚠ Error</div>
			${message}
			<p style="margin-top:12px;">
				<button class="btn btn-sm btn-primary" onclick="cur_frm && cur_frm.trigger('refresh')">🔄 Retry</button>
			</p>
		</div>`;
	} else {
		html = `<div style="padding:20px;background:#e3f2fd;border-radius:8px;border:1px solid #bbdefb;text-align:center;font-size:14px;">
			${message}
			<p style="margin-top:12px;">
				<button class="btn btn-sm btn-success" onclick="fetchQR(cur_frm)">📱 Fetch QR Code</button>
				<button class="btn btn-sm btn-primary" onclick="cur_frm && cur_frm.trigger('refresh')">🔄 Refresh</button>
			</p>
		</div>`;
	}
	frm.set_df_property('session_qr', 'options', html);
}


function fetchQR(frm) {
	if (!frm.doc.base_url || !frm.doc.api_key) {
		frappe.msgprint({ message: 'Set Base URL and API Key first, then Save.', indicator: 'orange' });
		return;
	}

	frappe.call({
		method: 'gravures_custom.overrides.get_openwa_session_qr',
		freeze: true,
		freeze_message: __('Fetching QR code...'),
		callback: function(r) {
			if (!r.message) return;

			const data = r.message;

			if (data.status === 'ok' && data.qr) {
				const statusLabel = data.session_status || 'qr_ready';
				frm.set_value('session_status', statusLabel);

				const qrHtml = `
					<div style="text-align:center;padding:20px;">
						<div style="background:white;display:inline-block;padding:16px;border-radius:12px;border:2px solid #25D366;box-shadow:0 4px 16px rgba(37,211,102,0.2);">
							<img src="${data.qr}" style="max-width:300px;display:block;" />
						</div>
						<p style="margin-top:16px;color:#555;font-size:14px;line-height:1.6;">
							<b>📱 Scan this QR code with WhatsApp</b><br>
							<span style="font-size:13px;">
								Open WhatsApp → <b>Settings</b> (<code>⋯</code> or <code>⚙</code>) → <b>Linked Devices</b> → <b>Link a Device</b>
							</span>
						</p>
						<div style="margin-top:8px;">
							<span style="background:#25D366;color:white;padding:5px 16px;border-radius:20px;font-size:13px;font-weight:bold;">
								Status: ${statusLabel.toUpperCase()}
							</span>
						</div>
						<p style="margin-top:16px;">
							<button class="btn btn-sm btn-primary" onclick="cur_frm && cur_frm.trigger('refresh')">🔄 Refresh Status</button>
						</p>
					</div>
				`;
				frm.set_df_property('session_qr', 'options', qrHtml);

				// Also update phone/pushname if returned
				if (data.phone || data.pushname) {
					if (data.phone) frm.set_value('session_phone', data.phone);
					if (data.pushname) frm.set_value('session_pushname', data.pushname);
					frm.refresh_field('session_phone');
					frm.refresh_field('session_pushname');
				}
			} else {
				const msg = data.message || 'Failed to load QR code';
				showQrArea(frm, 'error', '❌ ' + msg);
			}
		}
	});
}
