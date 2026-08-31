"""
Recovery: recreate missing e-Invoice Logs and e-Waybill Logs.
Called via: bench --site kreativ216 execute gravures_custom.api.e_invoice_recovery.recover_all_missing_logs
"""
import json

import frappe


def recover_all_missing_logs():
    import jwt as pyjwt
    from india_compliance.gst_india.api_classes.nic.e_invoice import EInvoiceAPI

    broken = frappe.db.sql("""
        SELECT si.name, si.irn, si.ewaybill, si.company_gstin,
               eil.name AS has_einv_log,
               ewl.name AS has_ewb_log
        FROM `tabSales Invoice` si
        LEFT JOIN `tabe-Invoice Log` eil ON eil.name = si.irn
        LEFT JOIN `tabe-Waybill Log` ewl ON ewl.e_waybill_number = si.ewaybill
        WHERE si.docstatus = 1
          AND (
            (si.irn IS NOT NULL AND si.irn != '' AND eil.name IS NULL)
            OR
            (si.ewaybill IS NOT NULL AND si.ewaybill != '' AND ewl.name IS NULL)
          )
        ORDER BY si.creation DESC
    """, as_dict=True)

    if not broken:
        frappe.logger().info("e-Invoice/e-Waybill recovery: no missing logs found")
        return {"recovered_einv": 0, "recovered_ewb": 0, "total": 0}

    frappe.logger().warning(
        f"e-Invoice/e-Waybill recovery: found {len(broken)} invoices with missing logs"
    )

    recovered_einv = 0
    recovered_ewb = 0

    for si in broken:
        # ── e-Invoice Log recovery ──
        if si.irn and not si.has_einv_log:
            try:
                api = EInvoiceAPI.create(company_gstin=si.company_gstin)
                result = api.get_e_invoice_by_irn(si.irn)

                if result and not result.get("ErrorDetails"):
                    invoice_data = None
                    if result.SignedInvoice:
                        decoded = json.loads(
                            pyjwt.decode(result.SignedInvoice, options={"verify_signature": False})["data"]
                        )
                        invoice_data = frappe.as_json(decoded, indent=4)

                    log = frappe.new_doc("e-Invoice Log")
                    log.update({
                        "irn": si.irn,
                        "reference_doctype": "Sales Invoice",
                        "reference_name": si.name,
                        "acknowledgement_number": result.AckNo,
                        "acknowledged_on": result.AckDt,
                        "signed_invoice": result.SignedInvoice,
                        "signed_qr_code": result.SignedQRCode,
                        "invoice_data": invoice_data,
                    })
                    log.save(ignore_permissions=True)
                    frappe.db.commit()
                    recovered_einv += 1
                    frappe.logger().info(f"e-Invoice recovery: created log for {si.name}")
            except Exception:
                frappe.log_error(
                    title=f"e-Invoice recovery failed for {si.name}",
                    message=frappe.get_traceback(),
                )

        # ── e-Waybill Log recovery ──
        if si.ewaybill and not si.has_ewb_log:
            try:
                if si.irn:
                    api = EInvoiceAPI.create(company_gstin=si.company_gstin)
                    result = api.get_e_waybill_by_irn(si.irn)
                else:
                    from india_compliance.gst_india.api_classes.nic.e_waybill import EWaybillAPI
                    api = EWaybillAPI.create(company_gstin=si.company_gstin)
                    result = api.get_e_waybill(si.ewaybill)

                if result and not result.get("ErrorDetails"):
                    ewb_data = frappe.as_json(
                        {k: v for k, v in (result if isinstance(result, dict) else result.__dict__).items()
                         if v is not None and k not in ("ErrorDetails",)},
                        indent=4,
                    )
                    log = frappe.new_doc("e-Waybill Log")
                    log.update({
                        "e_waybill_number": si.ewaybill,
                        "data": ewb_data,
                        "created_on": result.get("EwbDt") or result.get("ewayBillDate") or result.get("createdDate"),
                        "valid_upto": result.get("EwbValidTill") or result.get("validUpto"),
                        "reference_doctype": "Sales Invoice",
                        "reference_name": si.name,
                        "is_cancelled": 0,
                    })
                    log.save(ignore_permissions=True)
                    frappe.db.commit()
                    recovered_ewb += 1
                    frappe.logger().info(f"e-Waybill recovery: created log with data for {si.name}")
            except Exception:
                frappe.log_error(
                    title=f"e-Waybill recovery failed for {si.name}",
                    message=frappe.get_traceback(),
                )

    return {"recovered_einv": recovered_einv, "recovered_ewb": recovered_ewb, "total": len(broken)}


@frappe.whitelist()
def trigger_e_invoice_recovery():
    """Manual trigger from desk — shows result summary."""
    result = recover_all_missing_logs()
    return {"status": "ok", "message": f"Recovery completed. e-Invoice: {result['recovered_einv']}, e-Waybill: {result['recovered_ewb']} recovered out of {result['total']} broken."}
