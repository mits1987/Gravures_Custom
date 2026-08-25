"""
Hourly recovery job: finds Sales Invoices with IRN set but missing e-Invoice Log,
fetches data from the e-Invoice portal, and recreates the log.

This guards against the async frappe.enqueue in log_e_invoice() silently losing jobs.
"""

import json

import frappe
from frappe.utils import now_datetime

# Guard: only run if india_compliance is installed
try:
    import india_compliance  # noqa: F401
    HAS_IC = True
except ImportError:
    HAS_IC = False


def recover_missing_e_invoice_logs():
    """Scheduler entry — hourly. Safe to call on sites without india_compliance."""
    if not HAS_IC:
        return

    # Find SIs with IRN set but no matching e-Invoice Log
    missing = frappe.db.sql(
        """
        SELECT si.name, si.irn, si.company_gstin, si.docstatus
        FROM `tabSales Invoice` si
        LEFT JOIN `tabe-Invoice Log` eil ON eil.name = si.irn
        WHERE si.irn IS NOT NULL AND si.irn != ''
          AND si.docstatus = 1
          AND eil.name IS NULL
        ORDER BY si.modified DESC
        LIMIT 50
        """,
        as_dict=True,
    )

    if not missing:
        return

    frappe.logger().warning(
        f"e-Invoice recovery: found {len(missing)} Sales Invoices with missing logs"
    )

    recovered = 0
    for si in missing:
        try:
            _recover_one(si)
            recovered += 1
        except Exception:
            frappe.log_error(
                title=f"e-Invoice recovery failed for {si.name}",
                message=frappe.get_traceback(),
            )

    if recovered:
        frappe.logger().info(
            f"e-Invoice recovery: recovered {recovered}/{len(missing)} logs"
        )


def _recover_one(si):
    """Fetch e-Invoice data from the NIC portal and create the missing log."""
    import jwt as pyjwt
    from india_compliance.gst_india.api_classes.e_invoice import EInvoiceAPI

    api = EInvoiceAPI.create(company_gstin=si.company_gstin)
    result = api.get_e_invoice_by_irn(si.irn)

    if not result or result.get("ErrorDetails"):
        frappe.throw(
            f"Portal returned error for IRN {si.irn}: {result}"
        )

    # Parse SignedInvoice → invoice_data (same logic as generate_e_invoice)
    invoice_data = None
    if result.SignedInvoice:
        decoded_invoice = json.loads(
            pyjwt.decode(result.SignedInvoice, options={"verify_signature": False})["data"]
        )
        invoice_data = frappe.as_json(decoded_invoice, indent=4)

    log_data = {
        "irn": si.irn,
        "reference_doctype": "Sales Invoice",
        "reference_name": si.name,
        "acknowledgement_number": result.AckNo,
        "acknowledged_on": result.AckDt,
        "signed_invoice": result.SignedInvoice,
        "signed_qr_code": result.SignedQRCode,
        "invoice_data": invoice_data,
    }

    # Create the e-Invoice Log (same as _log_e_invoice)
    log = frappe.new_doc("e-Invoice Log")
    log.update(log_data)
    log.save(ignore_permissions=True)
    frappe.db.commit()


@frappe.whitelist()
def trigger_e_invoice_recovery():
    """Manual trigger from desk — shows result summary."""
    recover_missing_e_invoice_logs()
    return {"status": "ok", "message": "Recovery job completed. Check Error Log for details."}
