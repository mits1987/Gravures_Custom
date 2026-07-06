// Employee Shift list view — row coloring + clickable check-in/out times.
// Registered via hooks.py doctype_list_js.
//
// Clicking a Check-In or Check-Out TIME opens the underlying Employee Checkin
// record, so a wrong punch (IN selected instead of OUT, or vice versa) can be
// corrected right there. Shifts rebuild automatically a few seconds after the
// checkin is saved.

frappe.listview_settings['Employee Shift'] = frappe.listview_settings['Employee Shift'] || {};

// Ensure the fields we need are always fetched for the list rows
frappe.listview_settings['Employee Shift'].add_fields = [
    'status', 'locked', 'anomaly_reason', 'manual_correction',
    'check_in_record', 'check_out_record',
];

// ============================================================================
// INDICATOR — status pill color on each row
// ============================================================================

frappe.listview_settings['Employee Shift'].get_indicator = function(doc) {
    if (doc.locked) {
        return [__('Locked'), 'grey', 'locked'];
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

            var doc = (listview.data || []).find(function(d) { return d.name === docname; }) || {};

            // --- Row background coloring ---
            if (doc.locked) {
                row.css('background-color', '#d3d3d3');
            } else if (doc.status === 'Anomaly' || doc.status === 'Missing Check-Out') {
                row.css('background-color', '#fff4e6');
            } else if (doc.manual_correction) {
                row.css('background-color', '#e6eef9');
            } else if (doc.status === 'Paired') {
                row.css('background-color', '#e6f9ed');
            }

            // --- Clickable times → open the source Employee Checkin record ---
            make_time_open_checkin(row, 'check_in', doc.check_in_record);
            make_time_open_checkin(row, 'check_out', doc.check_out_record);
        });
    }, 300);
};

// ============================================================================
// HELPERS
// ============================================================================

/** Make a Datetime cell open the linked Employee Checkin (to fix wrong punches) */
function make_time_open_checkin(row, fieldname, checkin_name) {
    var cell = row.find('[data-fieldname="' + fieldname + '"]');
    if (!cell.length) return;
    if (!checkin_name) return;   // anomaly rows without a source checkin: leave as plain text

    var text = cell.text().trim();
    if (!text) return;

    cell.css({
        'cursor': 'pointer',
        'color': '#2d7ff9',
        'text-decoration': 'underline'
    }).attr('title', __('Open Employee Checkin to correct this punch'));

    cell.off('click.timeclick').on('click.timeclick', function(e) {
        e.stopPropagation();
        e.preventDefault();
        frappe.set_route('Form', 'Employee Checkin', checkin_name);
    });
}
