# Module 3 — Purchasing (what was built)

The warehouse restocks through a **REF** (replenishment form). It asks suppliers for
quotations (**RFQ**), records the replies, chooses a supplier, and the **owner approves the
supplier and price**. Approval creates a **purchase order (PO)**; Delivery receives the goods
later (Module 4).

Files:

- `app/purchasing_ops.py` — queries and save workflows (new).
- `app/blueprints/purchasing.py` — the 17 screens/actions, thin routes.
- `app/templates/purchasing/` — 17 templates with real data.
- `tests/test_purchasing_screens.py` — 23 click-through tests (new).

Nothing here changes stock. Receiving (Delivery) is what will call the Inventory stock
functions.

## Flow

```
restock alert ──► REF (draft ─► submitted)
                   │
                   ├─ RFQ to suppliers ──► quotations (one per supplier)
                   │                        │
                   └─ regular supplier ─────┤ (agreed prices, no RFQ)
                                            ▼
                                choose quotation / agreed price
                                            ▼
                            purchase approval (owner: approve / reject)
                                            ▼
                          PO (draft) ─► sent ─► Delivery receives goods
```

## Screens

| Screen | Route | Who |
|---|---|---|
| Restock alert list / detail / dismiss | `/purchasing/alerts`, `/<id>`, `/<id>/dismiss` | warehouse manager |
| REF list | `/purchasing/ref` | warehouse manager (own warehouse), owner (all) |
| REF new (from an alert or blank) | `/purchasing/ref/new` | warehouse manager |
| REF detail (+ submit a draft) | `/purchasing/ref/<id>` | same as the list |
| REF cancel | `/purchasing/ref/<id>/cancel` | warehouse manager |
| New RFQ | `/purchasing/ref/<id>/rfq/new` | warehouse manager |
| Record quotation | `/purchasing/ref/<id>/quotation/new` | warehouse manager |
| Choose supplier | `/purchasing/ref/<id>/compare` | warehouse manager |
| Approval list | `/purchasing/approvals` | owner, warehouse manager (own warehouse) |
| Approval detail (approve/reject) | `/purchasing/approvals/<id>` | owner |
| PO list / detail (+ mark sent) | `/purchasing/po`, `/<id>` | warehouse manager, owner, warehouse staff |
| Suppliers list | `/purchasing/suppliers` | warehouse manager, owner |
| Supplier new / edit (with agreed prices) | `/purchasing/suppliers/new`, `/<id>/edit` | warehouse manager |

## Decisions made while building it

| Topic | Decision |
|---|---|
| Prices | Purchasing **does** use prices: `quotation_items.unit_price`, `supplier_items.agreed_price`, `purchase_order_items.unit_price`. The handoff's "no unit prices" decision was about Inventory/sales; the purchasing schema and the owner's "approve supplier and price" role need them. |
| Money | Validated and rounded to 2 decimals here, passed to SQL as strings, so PostgreSQL `NUMERIC` and SQLite store the same value. Templates print with a `money` filter. |
| Completing the restock alert | The alert is completed (status, `closed_by`, `closed_at`, audit row) when the **REF is submitted**, not when a draft is saved. Buying now owns that restock. |
| REF drafts | A draft can be submitted from the REF detail page. |
| RFQ email | The system does **not** send email. "Send RFQ" records the RFQ as sent (`sent_at`, `rfq_suppliers.sent_at`) and the warehouse emails the suppliers itself. The page says so. |
| One RFQ per REF | Kept simple: one RFQ with any number of suppliers. Sending a second is refused; record more quotations on the same RFQ instead. |
| Quotations | One per supplier per RFQ. Saving the same supplier again **replaces** their lines and totals. `qty_offered` can be less than requested (0 = not offered). |
| Quotation supplier list | Every **active** supplier is listed, with the ones the RFQ was sent to first. A supplier who replied but was not recorded as sent is accepted and added to `rfq_suppliers` (with `sent_at` left empty), instead of refusing the reply. |
| Quotation prices | Opening the page selects the RFQ supplier and fills the unit-price boxes from that supplier's valid `supplier_items.agreed_price` rows, so 40+ lines do not have to be typed. Opening a supplier who already has a quotation reloads that quotation's quantities and prices; the screen says the user must change a price to what the supplier actually quoted. |
| Quotation expiry | A quotation whose `valid_until` is before today cannot be sent for approval; record a fresh quotation first. |
| Partial quotation | If a quotation does not cover every REF line, the approval page lists the missing items and warns that they will not be on the PO. The remaining balance needs a new REF. |
| ROP = 0 | A ROP of 0 is a real threshold, not "not set up": the item appears on the restock alert as soon as available stock reaches 0. Only an item with **no** `item_location_settings` row is treated as not configured. |
| Choosing a supplier | Either a recorded quotation, or a regular supplier's valid agreed prices (all REF items must be covered). Either way one `purchase_approvals` row with `decision = 'pending'`. |
| Approving | Owner only. The approval form can capture the **expected delivery date** and **PO terms**; approval creates the **PO as a draft** (no email), sets the REF to `approved`, and writes the audit row. The PO page has "Mark as sent" for the warehouse manager/owner. |
| Rejecting | Remarks are required. The REF goes back to `submitted` so the warehouse can choose again; no PO is made. |
| Edit PO | A `draft` PO can change its expected delivery date and terms. Items and prices stay as the owner approved them; cancel and re-order if they must change. |
| Cancel PO | A `draft` or `sent` PO can be cancelled with a reason. If no other PO is active for the REF, the REF goes back to `submitted`. A partially received PO cannot be cancelled. |
| Supplier returns | Removed. Goods that are short or rejected at receiving are handled by the discrepancy report. The tables `supplier_returns` and `supplier_return_items` stay in the database so old data is kept, but no screen uses them. |
| Cancel REF | Only `draft`, `submitted` or `rfq_sent`, with a reason. `for_approval` cannot be cancelled (the owner must decide first); approved/receiving/closed cannot either. Cancelling also closes the RFQ. |
| Receiving | Delivery's `delivery.receiving_check` screen records the RM; accepted stock is added and short/rejected stock opens a DR. |
| PO numbers | `inv.next_doc_no("PO")`; REF/RFQ use `REF`/`RFQ`. A failed save rolls back, so no number is burned. |

## Database rows written

- `replenishments` + `replenishment_items` (create/submit/cancel); shortage lines can point at
  the waiting RF (`rf_id`).
- `notifications` (completed when the REF is submitted), `audit_log` for every action.
- `rfqs`, `rfq_suppliers`, `quotations`, `quotation_items`.
- `purchase_approvals` (pending → approved/rejected).
- `purchase_orders` + `purchase_order_items` (draft → sent; expected date and terms on approval; edit/cancel).
- `supplier_returns` + `supplier_return_items` (draft → confirmed/cancelled). Confirming writes a negative
  `stock_ledger` row with `ref_type='SR'` and saves `credit_amount`.
- `suppliers` + `supplier_items` (agreed prices, upsert by supplier + item + valid_from).

## Demo suppliers and agreed prices

For development only:

```powershell
python scripts/seed_demo_suppliers.py
```

It creates/updates four suppliers and gives **every active item** an agreed price for each
**regular** supplier:

| Supplier | Regular | Agreed prices |
|---|---|---|
| Demo Supplier (if it already exists) | yes | 416 items |
| Northern Tire Trading | yes | 416 items |
| Metro Rubber Supply | yes | 416 items (about 7% higher) |
| Highland Wheels Corp | no | none — quotes through an RFQ |
| Prime Tubes & Flaps | no | none — quotes through an RFQ |

The prices are fake but stable: tires 5,200–14,999, tubes 320–799, flaps 160–399, rims/other
850–4,049, valid from `2026-01-01` with no end date. Non-regular suppliers have no agreed
prices on purpose — that is what makes the RFQ/quotation path different. Never use this on
the real database; delete the demo suppliers before go-live.

## Tests

`python -m unittest discover tests -v` → **152 tests, all passing** on the SQLite test schema
(Inventory, Requisition, Purchasing, Delivery, admin/login and printable documents).

A full flow was also run by hand on PostgreSQL: supplier → REF → RFQ → quotation → compare →
owner approval → PO → mark sent. Totals and statuses were correct.

Not done by design: sending email, receiving goods (Module 4), editing a REF after it is
submitted.
