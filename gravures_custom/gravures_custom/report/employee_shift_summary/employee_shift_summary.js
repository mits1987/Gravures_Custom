// Copyright (c) 2026, kreativ-gravures
// License: MIT

frappe.query_reports["Employee Shift Summary"] = {
	"filters": [
		{
			"fieldname": "month",
			"label": __("Month"),
			"fieldtype": "Select",
			"options": "1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n12",
			"default": String(new Date().getMonth() + 1),
			"reqd": 1
		},
		{
			"fieldname": "year",
			"label": __("Year"),
			"fieldtype": "Int",
			"default": new Date().getFullYear(),
			"reqd": 1
		},
		{
			"fieldname": "employee",
			"label": __("Employee"),
			"fieldtype": "Link",
			"options": "Employee"
		},
		{
			"fieldname": "view",
			"label": __("View"),
			"fieldtype": "Select",
			"options": "Detail\nSummary",
			"default": "Summary",
			"reqd": 1
		}
	],

	onload: function(report) {
		// Step 1: push the reviewed month into HRMS (Attendance + Overtime)
		report.page.add_inner_button(__('Sync to HRMS (Attendance + OT)'), function() {
			var f = report.get_values();
			frappe.confirm(
				__('Create submitted Attendance records and Overtime salary entries for {0}-{1}?<br><br>Run this only after all shifts for the month are reviewed (no orange/red rows).', [f.year, f.month]),
				function() {
					frappe.call({
						method: 'gravures_custom.attendance.api.sync_month_to_hrms',
						args: { year: f.year, month: f.month, employee: f.employee || null },
						freeze: true,
						freeze_message: __('Creating Attendance and Overtime entries...'),
						callback: function(r) {
							var m = r.message || {};
							var html = '<b>' + __('HRMS sync complete for {0}', [m.period]) + '</b><br>'
								+ __('Attendance created: {0} (skipped existing: {1})', [m.attendance_created, m.attendance_skipped_existing]) + '<br>'
								+ __('Overtime entries created: {0} (skipped existing: {1})', [m.overtime_created, m.overtime_skipped_existing]);
							if ((m.overtime_no_rate || []).length) {
								html += '<br>' + __('No overtime rate set (skipped): {0}', [m.overtime_no_rate.join(', ')]);
							}
							var errs = (m.attendance_errors || []).concat(m.overtime_errors || []);
							if (errs.length) {
								html += '<br><br><b>' + __('Errors') + ':</b><br>' + errs.join('<br>');
							}
							frappe.msgprint({ title: __('Sync to HRMS'), message: html, indicator: errs.length ? 'orange' : 'green' });
						}
					});
				}
			);
		});

		// Step 2: standard HRMS payroll — creates the salary slips
		report.page.add_inner_button(__('Create Payroll Entry'), function() {
			frappe.new_doc('Payroll Entry');
		});
	}
};
