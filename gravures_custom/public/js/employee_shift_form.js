// Formatter for Employee Shift — color-renders the status field and worked_hours.
// Bound via the doctype's __init__.py get_formatter_dict/indicator functions.

frappe.ui.form.on('Employee Shift', {
    refresh(frm) {
        const s = frm.doc.status;
        const r = (frm.doc.anomaly_reason || '').toLowerCase();
        const wrap = frm.fields_dict.status.$wrapper;

        const colors = {
            'Paired': '#29cd41',
            'Missing Check-Out': '#ff9b00',
            'Anomaly': '#ff4d4d',
            'Manual': '#5b9bd5',
        };
        const key = (s === 'Anomaly' && r === 'break_punch') ? 'Anomaly' : s;
        const color = colors[key] || 'inherit';

        // Highlight the status pill itself
        wrap.find('.field-area').first().attr('style', `font-weight:bold;color:${color};font-size:1.05em;`);

        // Widen worked hours / overtime if exhausted
        if (s === 'Paired' && frm.doc.overtime_seconds > 0) {
            const wt = (frm.doc.overtime_hours || '').split(':');
            const minutes = parseInt(wt[0],10)*60 + parseInt(wt[1]||'0',10);
            frm.fields_dict.overtime_hours.$wrapper.attr('style',
                minutes > 300
                    ? 'background:#fff4e6;padding:4px 8px;font-weight:bold;'  // > 5h overtime
                    : 'background:#e6f9ed;padding:4px 8px;'
            );
        }
    }
});
