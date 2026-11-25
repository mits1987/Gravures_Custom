import frappe

# Doctypes ERPNext's Sales Order validate chain queries. Missing read/select on any of
# these makes an SO save fail with "PermissionError: Not permitted" for under-provisioned
# custom roles (e.g. Engraving/Graphics/Proofing User).
# - Sales Taxes and Charges Template: ERPNext 16.51.1+ calls require_permission(..., "select")
# - The rest: get_item_details / party account / exchange-rate / price-list lookups need read
SO_LOOKUP_PERMS = {
    "Sales Taxes and Charges Template": "select",
    "Item": "read",
    "Account": "read",
    "Currency": "read",
    "Currency Exchange": "read",
    "Price List": "read",
    "Lead": "read",
}


def _role_has_perm(role, doctype, ptype):
    """True if role already has `ptype` on `doctype` via standard DocPerm or Custom DocPerm."""
    if frappe.db.exists("DocPerm", {"parent": doctype, "role": role, ptype: 1}):
        return True
    if frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, ptype: 1}):
        return True
    return False


def _grant(role, doctype):
    """Ensure role has select+read on doctype via Custom DocPerm (idempotent)."""
    existing = frappe.db.get_value(
        "Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}, "name"
    )
    if existing:
        row = frappe.get_doc("Custom DocPerm", existing)
        changed = False
        if not row.select:
            row.select = 1
            changed = True
        if not row.read:
            row.read = 1
            changed = True
        if changed:
            row.save(ignore_permissions=True)
    else:
        row = frappe.new_doc("Custom DocPerm")
        row.update(
            {
                "parent": doctype,
                "parenttype": "DocType",
                "parentfield": "permissions",
                "role": role,
                "permlevel": 0,
                "if_owner": 0,
                "select": 1,
                "read": 1,
                "write": 0,
                "create": 0,
                "delete": 0,
                "submit": 0,
                "cancel": 0,
                "amend": 0,
                "report": 1,
                "export": 1,
                "print": 1,
                "email": 1,
            }
        )
        row.insert(ignore_permissions=True)
    frappe.clear_cache(doctype=doctype)


def ensure():
    """Grant missing lookup perms to any role that can write/create a Sales Order.

    Runs on every migrate so newly-created custom roles (or perms wiped by an upgrade)
    are healed automatically instead of surfacing as "Not permitted" in production.
    """
    so_write_roles = frappe.get_all(
        "Custom DocPerm",
        filters={"parent": "Sales Order"},
        or_filters=[{"write": 1}, {"create": 1}],
        pluck="role",
    )
    so_write_roles = list(set(so_write_roles))

    granted = []
    for role in so_write_roles:
        for doctype, ptype in SO_LOOKUP_PERMS.items():
            if not frappe.db.exists("DocType", doctype):
                continue
            if _role_has_perm(role, doctype, ptype):
                continue
            _grant(role, doctype)
            granted.append(f"{role}/{doctype}")

    if granted:
        frappe.db.commit()
        frappe.logger().info(
            "ensure_shopfloor_role_perms: granted %d perm(s): %s",
            len(granted),
            ", ".join(granted),
        )
    return granted
