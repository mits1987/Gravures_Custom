### Gravures Custom

GC — Core customizations for Kreativ Gravures ERPNext v16.

**Current Version:** v0.2.0

---

### Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/mits1987/Gravures_Custom.git --branch main
bench install-app gravures_custom
```

### Release v0.2.0 (2026-07-26)

| Module | Description |
|--------|-------------|
| **Print Designer** | Custom print formats (Sales Order, Dispatch Register, Invoice, etc.) |
| **WhatsApp Dispatch Dashboards** | 7 Custom HTML Block buttons (Proofing, Dispatch, Engraving, Monthly, Job Status, SO Status, Monthly Report) |
| **DG ITC GSTR2B** | Input Tax Credit reconciliation against GSTR-2B |
| **Cylinder Performa** | Cylinder manufacturing performa with stage tracking |
| **Proforma Invoice** | Enhanced Proforma Invoice with jewellery-specific fields |
| **Jewellery Manufacturing** | Gold/Silver rate management, making charges, wastage calc |
| **WhatsApp Send API** | `send_print_pdf_whatsapp` — PDF generation + WhatsApp delivery |
| **WhatsApp Send Log** | Audit trail for all outbound WhatsApp messages |

### Dependencies

- `kreativ_notification` (WhatsApp engine, OpenWA integration)
- `kreativ_attendance` (Employee checkin data)
- `india_compliance` (GST/GSTR)

### Contributing

```bash
cd apps/gravures_custom
pre-commit install
```

Pre-commit: `ruff`, `eslint`, `prettier`, `pyupgrade`
