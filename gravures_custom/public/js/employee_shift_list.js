// List view client script for Employee Shift.
// Highlights Paired (green), Anomaly (red), Missing Check-Out (orange)
// and registers a "Recalculate Month" bulk action.

frappe.listview_settings['Employee Shift'] = {
    onload(listview) {
        // Row color
        listview.page.fields_dict.employee.get_value = function() { return ''; };
    },

    refresh(listview) {
        // Highlight list rows by status
        listview.wrapper.find('.list-row').each(function() {
            const row = $(this);
            const indicator = row.find('.indicator-pill');
            const text = indicator.text().trim();
            if (text === 'Anomaly' || text.includes('Break punch')) {
                row.css('background-color', '#ffe6e6');  // light red
            } else if (text === 'Missing Check-Out' || text.includes('Unpaired')) {
                row.css('background-color', '#fff4e6');  // light orange
            } else if (text === 'Paired') {
                row.css('background-color', '#e6f9ed');  // light green
            } else if (text === 'Manual') {
                row.css('background-color', '#e6eef9');  // light blue
            }
        });

        // Add "Recalculate Month" button at the top
        if (!listview.page.btn_container.find('.btn-recalc-month').length) {
            listview.page.add_inner_button(
                __('Recalculate Month'),
                function() {
                    const today = new Date();
                    const dlg = new frappe.ui.Dialog({
                        title: __('Recalculate Employee Shifts'),
                        fields: [
                            { fieldname: 'year', label: __('Year'), fieldtype: 'Int', default: today.getFullYear(), reqd: 1 },
                            { fieldname: 'month', label: __('Month (1-12)'), fieldtype: 'Int', default: today.getMonth() + 1, reqd: 1 }
                        ],
                        primary_action_label: __('Recalculate'),
                        primary_action: (values) => {
                            frappe.call({
                                method: 'gravures_custom.attendance.api.recalculate_year_month',
                                args: { year: values.year, month: values.month },
                                freeze: true,
                                freeze_message: __('Recalculating shifts...'),
                                callback: (r) => {
                                    dlg.hide();
                                    frappe.show_alert({
                                        message: __(
                                            'Recalc done. Employees: ' + r.employees +
                                            ', Paired: ' + r.paired +
                                            ', Anomalies: ' + r.anomalies +
                                            ', Deleted: ' + r.deleted
                                        ),
                                        indicator: 'green'
                                    });
                                    listview.refresh();
                                }
                            });
                        }
                    });
                    dlg.show();
                },
                'Tools'
            ).addClass('btn-recalc-month');
        }
    },
};
