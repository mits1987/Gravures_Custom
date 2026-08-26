import frappe

def execute():
    cf = frappe.get_doc('Custom Field', 'Employee Checkin-whatsapp_sent')
    import json
    print(json.dumps(cf.as_dict(), default=str, indent=2))
