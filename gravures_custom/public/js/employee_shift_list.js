// Auto-loaded by Frappe for Employee Shift list view
// Place in public/js/ folder — Frappe picks it up automatically

frappe.listview_settings['Employee Shift'] = frappe.listview_settings['Employee Shift'] || {};

frappe.listview_settings['Employee Shift'].get_indicator = function(doc) {
    if (doc.locked) {
        return [__('Locked'), 'grey', 'locked'];
    }
    if (doc.anomaly_reason === 'break_punch') {
        return [__('Break punch'), 'red', 'anomaly'];
    }
    if (['missing_checkout', 'previous_month_carryover'].includes(doc.anomaly_reason)) {
        var label = doc.status === 'Missing Check-Out' ? __('Missing Check-Out') : __('Unpaired');
        return [label, 'orange', 'anomaly'];
    }
    if (doc.manual_correction) {
        return [__('Manual'), 'blue', 'manual_correction'];
    }
    return [__('Paired'), 'green', 'ok'];
};

frappe.listview_settings['Employee Shift'].refresh = function(listview) {
    setTimeout(function() {
        listview.wrapper.find('.list-row').each(function() {
            var row = $(this);
            var statusEl = row.find('[data-fieldname="status"]');
            var lockedEl = row.find('[data-fieldname="locked"]');
            var status = statusEl.length ? statusEl.text().trim() : '';
            var locked = lockedEl.data('value');
            
            if (locked === '1') {
                row.css('background-color', '#d3d3d3');
                return;
            }
            if (!status) return;
            if (status === 'Anomaly' || status === 'Break punch') {
                row.css('background-color', '#ffe6e6');
            } else if (status === 'Missing Check-Out') {
                row.css('background-color', '#fff4e6');
            } else if (status === 'Paired') {
                row.css('background-color', '#e6f9ed');
            } else if (status === 'Manual') {
                row.css('background-color', '#e6eef9');
            }
        });
    }, 500);
};