// Attendance Dashboard — the month-end control panel.
// Month/year selectors + per-employee summary table (same numbers as the old
// Excel script: present days, total hours, overtime), with one-click path to
// salary: Sync to HRMS -> Create Payroll Entry.

frappe.pages['attendance-dashboard'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Attendance Dashboard'),
		single_column: true,
	});

	var now = frappe.datetime.now_date(); // "YYYY-MM-DD"
	var state = {
		year: parseInt(now.split('-')[0], 10),
		month: parseInt(now.split('-')[1], 10),
	};

	var month_field = page.add_field({
		fieldname: 'month',
		label: __('Month'),
		fieldtype: 'Select',
		options: [
			{value: '1', label: __('January')}, {value: '2', label: __('February')},
			{value: '3', label: __('March')}, {value: '4', label: __('April')},
			{value: '5', label: __('May')}, {value: '6', label: __('June')},
			{value: '7', label: __('July')}, {value: '8', label: __('August')},
			{value: '9', label: __('September')}, {value: '10', label: __('October')},
			{value: '11', label: __('November')}, {value: '12', label: __('December')},
		],
		default: String(state.month),
		change: function() { state.month = parseInt(month_field.get_value(), 10); load(); },
	});

	var year_field = page.add_field({
		fieldname: 'year',
		label: __('Year'),
		fieldtype: 'Int',
		default: state.year,
		change: function() {
			var y = parseInt(year_field.get_value(), 10);
			if (y && y > 2000 && y < 2100 && y !== state.year) { state.year = y; load(); }
		},
	});

	page.set_primary_action(__('Sync to HRMS (Attendance + OT)'), function() {
		frappe.confirm(
			__('Create submitted Attendance records and Overtime salary entries for {0}-{1}?<br><br>Run this only after all anomalies for the month are resolved.', [state.year, state.month]),
			function() {
				frappe.call({
					method: 'gravures_custom.attendance.api.sync_month_to_hrms',
					args: { year: state.year, month: state.month },
					freeze: true,
					freeze_message: __('Creating Attendance and Overtime entries...'),
					callback: function(r) {
						var m = r.message || {};
						var errs = (m.attendance_errors || []).concat(m.overtime_errors || []);
						var html = __('Attendance created: {0} (skipped existing: {1})', [m.attendance_created, m.attendance_skipped_existing]) + '<br>'
							+ __('Overtime entries created: {0} (skipped existing: {1})', [m.overtime_created, m.overtime_skipped_existing]);
						if ((m.overtime_no_rate || []).length) {
							html += '<br>' + __('No overtime rate set (skipped): {0}', [m.overtime_no_rate.join(', ')]);
						}
						if (errs.length) {
							html += '<br><br><b>' + __('Errors') + ':</b><br>' + errs.join('<br>');
						}
						frappe.msgprint({ title: __('Sync to HRMS'), message: html, indicator: errs.length ? 'orange' : 'green' });
					}
				});
			}
		);
	});

	page.add_inner_button(__('Create Payroll Entry'), function() {
		frappe.new_doc('Payroll Entry');
	});
	page.add_inner_button(__('Open Shift List'), function() {
		frappe.set_route('List', 'Employee Shift', 'List');
	});
	page.add_inner_button(__('Open Full Report'), function() {
		frappe.set_route('query-report', 'Employee Shift Summary');
	});

	var $body = $('<div class="attendance-dashboard" style="padding: 15px 0;"></div>').appendTo(page.main);

	function card(label, value, color, onclick) {
		var c = $(
			'<div style="flex:1; min-width:150px; background: var(--card-bg, #fff); border:1px solid var(--border-color, #e2e2e2);' +
			' border-radius:8px; padding:14px 18px;' + (onclick ? ' cursor:pointer;' : '') + '">' +
			'<div style="font-size:12px; color: var(--text-muted, #6c7680);">' + label + '</div>' +
			'<div style="font-size:22px; font-weight:600; color:' + (color || 'inherit') + ';">' + value + '</div>' +
			'</div>'
		);
		if (onclick) c.on('click', onclick);
		return c;
	}

	function load() {
		$body.html('<div class="text-muted" style="padding:30px;">' + __('Loading...') + '</div>');
		frappe.call({
			method: 'gravures_custom.attendance.api.month_summary',
			args: { year: state.year, month: state.month },
			callback: function(r) { render(r.message || {}); },
		});
	}

	function render(data) {
		$body.empty();
		var t = data.totals || {};
		var rows = data.rows || [];

		// --- summary cards ---
		var cards = $('<div style="display:flex; gap:12px; flex-wrap:wrap; margin-bottom:18px;"></div>').appendTo($body);
		cards.append(card(__('Employees'), t.employees || 0));
		cards.append(card(__('Present Days (total)'), t.present_days || 0));
		cards.append(card(__('Total Hours'), t.total_hours || '0:00'));
		cards.append(card(__('Overtime'), t.overtime || '0:00', '#ff9b00'));
		cards.append(card(
			__('Open Anomalies'), t.anomalies || 0,
			t.anomalies ? '#ff4d4d' : '#29cd41',
			function() {
				frappe.route_options = { status: ['in', ['Anomaly', 'Missing Check-Out']] };
				frappe.set_route('List', 'Employee Shift', 'List');
			}
		));

		if (t.anomalies) {
			$body.append(
				'<div class="alert alert-warning" style="margin-bottom:14px;">' +
				__('There are {0} unresolved anomaly record(s) in {1}. Fix them before syncing to HRMS — anomaly rows are excluded from hours and payroll.', [t.anomalies, data.period]) +
				'</div>'
			);
		}

		// --- per-employee table (same as the old script's summary sheet) ---
		if (!rows.length) {
			$body.append('<div class="text-muted" style="padding:30px;">' + __('No shift data for {0}.', [data.period]) + '</div>');
			return;
		}

		var html = '<table class="table table-bordered" style="background: var(--card-bg, #fff);">' +
			'<thead><tr>' +
			'<th>' + __('Employee ID') + '</th>' +
			'<th>' + __('Name') + '</th>' +
			'<th>' + __('Department') + '</th>' +
			'<th style="text-align:right">' + __('Present Days') + '</th>' +
			'<th style="text-align:right">' + __('Total Hours') + '</th>' +
			'<th style="text-align:right">' + __('Overtime') + '</th>' +
			'<th style="text-align:right">' + __('Anomalies') + '</th>' +
			'</tr></thead><tbody>';

		rows.forEach(function(r) {
			html += '<tr' + (r.anomalies ? ' style="background:#fff4e6;"' : '') + '>' +
				'<td><a href="/app/employee/' + encodeURIComponent(r.employee) + '">' + frappe.utils.escape_html(r.employee) + '</a></td>' +
				'<td>' + frappe.utils.escape_html(r.employee_name || '') + '</td>' +
				'<td>' + frappe.utils.escape_html(r.department || '') + '</td>' +
				'<td style="text-align:right">' + r.present_days + '</td>' +
				'<td style="text-align:right">' + r.total_hours + '</td>' +
				'<td style="text-align:right">' + r.overtime + '</td>' +
				'<td style="text-align:right">' + (r.anomalies || 0) + '</td>' +
				'</tr>';
		});

		html += '</tbody><tfoot><tr style="font-weight:600;">' +
			'<td colspan="3">' + __('Total') + '</td>' +
			'<td style="text-align:right">' + (t.present_days || 0) + '</td>' +
			'<td style="text-align:right">' + (t.total_hours || '0:00') + '</td>' +
			'<td style="text-align:right">' + (t.overtime || '0:00') + '</td>' +
			'<td style="text-align:right">' + (t.anomalies || 0) + '</td>' +
			'</tr></tfoot></table>';

		$body.append(html);
	}

	load();
};
