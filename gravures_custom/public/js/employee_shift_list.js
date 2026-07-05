// Employee Shift list view — row coloring + clickable check-in/out times.
// Registered via hooks.py doctype_list_js.

frappe.listview_settings['Employee Shift'] = frappe.listview_settings['Employee Shift'] || {};

// ============================================================================
// INDICATOR — status pill color on each row
// ============================================================================

frappe.listview_settings['Employee Shift'].get_indicator = function(doc) {
    if (doc.locked) {
        return [__('Locked'), 'grey', 'locked'];
    }
    if (doc.anomaly_reason === 'break_punch') {
        return [__('Break punch'), 'red', 'anomaly'];
    }
    if (['missing_checkout', 'previous_month_carryover'].includes(doc.anomaly_reason)) {
        var label = doc.status === 'Missing Check-Out'
            ? __('Missing Check-Out')
            : __('Unpaired');
        return [label, 'orange', 'anomaly'];
    }
    if (doc.manual_correction) {
        return [__('Manual'), 'blue', 'manual_correction'];
    }
    return [__('Paired'), 'green', 'ok'];
};

// ============================================================================
// REFRESH — row background colors + clickable check-in/out times
// ============================================================================

frappe.listview_settings['Employee Shift'].refresh = function(listview) {
    // Wait a beat for Frappe to finish rendering rows
    setTimeout(function() {
        listview.wrapper.find('.list-row').each(function() {
            var row = $(this);
            var docname = row.attr('data-name');
            if (!docname) return;

            // --- Row background coloring ---
            var statusEl = row.find('[data-fieldname="status"]');
            var lockedEl = row.find('[data-fieldname="locked"]');
            var status = statusEl.length ? statusEl.text().trim() : '';
            var locked = lockedEl.data('value');

            if (locked === '1') {
                row.css('background-color', '#d3d3d3');
            } else if (status === 'Anomaly' || status === 'Break punch') {
                row.css('background-color', '#ffe6e6');
            } else if (status === 'Missing Check-Out') {
                row.css('background-color', '#fff4e6');
            } else if (status === 'Paired') {
                row.css('background-color', '#e6f9ed');
            } else if (status === 'Manual') {
                row.css('background-color', '#e6eef9');
            }

            // --- Make check-in time clickable → opens Employee Shift ---
            make_time_clickable(row, docname, 'check_in');

            // --- Make check-out time clickable → opens Employee Shift ---
            make_time_clickable(row, docname, 'check_out');

            // --- Convert check_in_record / check_out_record link cells ---
            // These are Link→Employee Checkin fields. By default clicking them
            // navigates to the Employee Checkin doc. Instead, make them also
            // navigate to the Employee Shift record.
            make_record_link_redirect(row, docname, 'check_in_record');
            make_record_link_redirect(row, docname, 'check_out_record');
        });
    }, 300);
};

// ============================================================================
// HELPERS
// ============================================================================

/** Make a Datetime cell act as a link to the Employee Shift form */
function make_time_clickable(row, docname, fieldname) {
    var cell = row.find('[data-fieldname="' + fieldname + '"]');
    if (!cell.length) return;

    var text = cell.text().trim();
    if (!text) return;

    cell.css({
        'cursor': 'pointer',
        'color': '#2d7ff9',
        'text-decoration': 'underline'
    }).attr('title', __('Open Employee Shift'));

    // Remove any existing click handler before adding new one
    cell.off('click.timeclick').on('click.timeclick', function(e) {
        e.stopPropagation();
        frappe.set_route('Form', 'Employee Shift', docname);
    });
}

/** Redirect a Link cell to open the Employee Shift instead of the linked doc */
function make_record_link_redirect(row, docname, fieldname) {
    var cell = row.find('[data-fieldname="' + fieldname + '"]');
    if (!cell.length) return;

    cell.off('click.recordredirect').on('click.recordredirect', function(e) {
        e.stopPropagation();
        frappe.set_route('Form', 'Employee Shift', docname);
    });
}