/**
 * Guard: re-fetch document in print preview if frm.doc is missing.
 * Prevents "doc: ''" crashes in get_html_and_style (e.g. e-Waybill formats).
 */
frappe.provide("gravures_custom.print_guard");

const _orig_get_print_html = frappe.ui.form.PrintView.prototype.get_print_html;
frappe.ui.form.PrintView.prototype.get_print_html = function (callback) {
    if (!this.frm.doc) {
        frappe.model.with_doc(this.frm.doctype, this.frm.docname, () => {
            this.frm.doc = frappe.get_doc(this.frm.doctype, this.frm.docname);
            if (this.frm.doc) {
                _orig_get_print_html.call(this, callback);
            } else {
                frappe.msgprint(__("Could not load document. Please refresh."));
            }
        });
        return;
    }
    _orig_get_print_html.call(this, callback);
};
