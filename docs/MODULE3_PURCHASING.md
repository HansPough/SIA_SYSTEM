# Module 3 — Purchasing (what was built)

The warehouse restocks through a **REF** (replenishment form). It asks suppliers for
quotations (**RFQ**), records the replies, chooses a supplier, and the **owner approves the
supplier and price**. Approval creates a **purchase order (PO)**; Delivery receives the goods
later (Module 4).

Files:

- `app/purchasing_ops.py` — queries and save workflows (new).
- `app/blueprints/purchasing.py` — the 17 screens/actions, thin routes.
- `app/templates/purchasing/` — 17 templates with real data.
- `tests/test_purchasing_screens.py` — 68 click-through tests.

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
| Withdraw a pending approval | `/purchasing/approvals/<id>/withdraw` (POST) | the warehouse manager who submitted it |
| PO list / detail (+ mark sent) | `/purchasing/po`, `/<id>` | warehouse manager, owner, warehouse staff |
| Suppliers list | `/purchasing/suppliers` | warehouse manager, owner |
| Supplier new / edit (with agreed prices) | `/purchasing/suppliers/new`, `/<id>/edit` | warehouse manager |

## RFQ replies and "waiting for you" alerts

* **Replies area** (REF page and RFQ page, `purchasing/_rfq_replies.html`): one block per RFQ. The summary line shows `n of m replied`, the **lowest comparable quote** (with the supplier's name), the reply-by date and links to the RFQ and its PDF. Under it, one row per supplier with a column for each fact: **Supplier, Status, Sent, Replied, Items, Quote, Actions**. Quotes are right-aligned, bold, in tabular numbers with the peso sign (`peso` filter). Dates are short (`day` filter: "Nov 25"). A round with more than five suppliers starts folded; older rounds start folded too.
* **"Lowest" only counts quotes that cover every item of the RFQ.** A supplier that quoted 1 of 2 items has a smaller total that cannot be compared, so it is flagged ("1 of 2") and never called lowest. A quote replaced by a later one shows as *Replaced*.
* **Alerts for the warehouse manager** (sidebar badge and bell, same counts as the dashboard): **POs ready to send** (owner-approved POs not yet sent) on *Purchase orders (PO)*, and **REFs ready for RFQ** (owner-approved REFs with no RFQ yet) on *Replenishments (REF)*. Each disappears as soon as the manager acts. The owner is not shown these.

## Order of work: the owner decides twice (migration 021)

1. The warehouse manager signs and submits the **REF**. Its status is **Waiting for owner** (`pending_owner`).
2. The **owner approves the REF** (or rejects it with a reason, which cancels it). Approving signs the REF's "Approved by (Owner)" line and sets it to **Ready for RFQ** (`submitted`). Until then there is no RFQ, no quotation and no supplier choice for that REF.
3. The warehouse manager sends the **RFQ**, records the replies and chooses suppliers. This is a *recommendation*.
4. The **owner approves the supplier choice** (the PO decision) one supplier at a time. Each approved supplier gets one purchase order.

* The owner finds REFs waiting in **Approvals** (a section at the top), in the sidebar badge and in the dashboard "Approvals" tile.
* REFs already past `draft` when migration 021 ran stay as they are and are treated as already approved. Nothing in flight stops.
* Who decided, when and what they wrote are kept in `replenishments.owner_decided_by / owner_decided_at / owner_remarks`.

## Choosing suppliers: how offers are compared

The screen shows one card per item with every offer in aligned columns: unit price, how far above the lowest, quantity it can supply, lead time, valid until, and the total for the quantity needed.

* **One flat "Lowest price" label** (`purchasing_ops._annotate_offers`) on the cheapest offer, given only when there are at least two different prices. There is no 2nd or 3rd place and no gradients.
* **Other labels** appear only when they tell you something the columns do not: *Fastest* (shortest stated lead time, only when lead times differ), *Agreed price*, *Short by N*, *Expired*.
* **No order-quantity box.** The REF already fixes the quantity: each supplier is ordered everything still needed, or what it can cover. A line left open stays open for another round.
* Each item card has three parts: its own numbers (requested, ordered, dropped, still needed), the offers table, and a footer with the selected supplier and subtotal.
* The labels are display only. They never change which offer is pre-selected (the cheapest offer that covers the full quantity).
* **The owner's approval screen** shows, under each item, the chosen price against every other offer in one table: Supplier, Basis, Unit price, Lead time, Total for the quantity, and *Against chosen* (the difference in ₱ and %). If a cheaper offer was passed over, the table opens by itself ("2 cheaper offers not chosen"); if the chosen price is the lowest it stays folded ("Lowest of 3 offers"). A cheaper offer that cannot cover the quantity says by how much it falls short.

## RFQ document and button standard (migration 020)

* **The RFQ is a document.** *Sign & create RFQ* signs it (Prepared by, warehouse manager), freezes the items it asks about in `rfq_items` (a later round lists only what was still open) and opens the RFQ page at `/purchasing/ref/<id>/rfq/<rfq_id>`. The document is laid out like the PO (items, **Unit price**, **Amount**, total) with no status badge. A supplier's copy shows the prices that supplier quoted; the internal copy shows dashes. The page also has a **Suppliers** tracker (copy, PDF, record reply, replied or waiting) and the lifecycle. `/documents/RFQ/<id>` and `.pdf` work like every other document.
* **One copy per supplier.** `?supplier=<id>` addresses the copy to that supplier and does not name the others; without it the copy is internal and lists everyone invited. The addressee is not part of the content fingerprint, so a supplier copy never shows "changed after signing".
* **Older RFQs.** Run `database/migrations/020_rfq_document.sql` once (it also allows `RFQ` in the `document_signatures` check). It fills `rfq_items` best-effort: the items each RFQ's suppliers quoted, else every item of the REF. An RFQ with no `rfq_items` rows falls back to the REF's items, and a manager can sign it from its page.
* **REF page.** A six-step strip (REF, RFQ, Quotes, Approval, Purchase order, Receiving) with one *Next:* sentence (`purchasing_ops.ref_flow`). Buttons appear once, in the bottom bar: Cancel on the left, other actions, the one main action, Back last.
* **Quotations.** *Save & next supplier* jumps to the next supplier of the RFQ that has not replied, and to the REF when none are left.
* **Press once.** Every POST form shows a spinner on the pressed button and ignores a second submit. The button is deliberately not `disabled` (that would drop its `action` value).

## Decisions made while building it

| Topic | Decision |
|---|---|
| Prices | Purchasing **does** use prices: `quotation_items.unit_price`, `supplier_items.agreed_price`, `purchase_order_items.unit_price`. The handoff's "no unit prices" decision was about Inventory/sales; the purchasing schema and the owner's "approve supplier and price" role need them. |
| Money | Validated and rounded to 2 decimals here, passed to SQL as strings, so PostgreSQL `NUMERIC` and SQLite store the same value. Templates print with a `money` filter. |
| Completing the restock alert | Submitting the **REF** (not a draft) takes the REF's items off the alert. The alert is completed (status, `closed_by`, `closed_at`, audit row) only when no item rows are left, so items left out of that REF stay on the alert. |
| REF drafts | A draft can be submitted from the REF detail page. |
| RFQ email | The system does **not** send email. "Send RFQ" records the RFQ as sent (`sent_at`, `rfq_suppliers.sent_at`) and the warehouse emails the suppliers itself. The page says so. |
| RFQ rounds | One RFQ per round, each sent to any number of suppliers. **Ask more suppliers** sends a new round covering only the items still open; the REF page groups the suppliers and replies under their RFQ number, with the note "One RFQ per round". A second round is how you follow up on a supplier who had no stock. |
| Quotations | One per supplier per RFQ. Saving the same supplier again **replaces** their lines and totals. `qty_offered` can be less than requested (0 = not offered). |
| Quotation supplier list | Every **active** supplier is listed, with the ones the RFQ was sent to first. A supplier who replied but was not recorded as sent is accepted and added to `rfq_suppliers` (with `sent_at` left empty), instead of refusing the reply. |
| Quotation prices | Opening the page selects the RFQ supplier and fills the unit-price boxes from that supplier's valid `supplier_items.agreed_price` rows, so 40+ lines do not have to be typed. Opening a supplier who already has a quotation reloads that quotation's quantities and prices; the screen says the user must change a price to what the supplier actually quoted. A unit price must be more than zero: an item the supplier did not offer is entered with **quantity 0**, not price 0. A stored 0-price line is never offered on **Choose suppliers** (the screen names that supplier and asks for the quotation again), so it cannot become the "cheapest" preselected supplier. |
| Quotation expiry | A quotation whose `valid_until` is before today is never offered on **Choose suppliers**, and if it is somehow chosen it refuses to send for approval (record a fresh quotation first). When the same regular supplier has a valid agreed price for that item, their agreed price is offered in place of the expired quotation (labelled "agreed price"); an expired quotation only blocks the supplier when there is no agreed price to fall back to. |
| Quotation rounds | The Record quotation screen loads the supplier's **newest reply on the REF**, whichever RFQ round it came from, so a later round starts from their earlier quantities and prices. Saving records the reply under the current RFQ. A line re-quoted in a later round marks the older line `superseded_at`, and Choose suppliers ignores superseded lines, so a stale round-1 price can never win as "cheapest". Lines for items the supplier did not re-quote stay valid. A quotation whose lines were all replaced shows "replaced by a later quotation" on the REF page. |
| Agreed-price prefill | Prices filled from `supplier_items.agreed_price` are labelled "agreed price" in the row and explained above the items table, so they are not mistaken for the supplier's reply. The label clears when the user types the quoted price. |
| Expected delivery | The owner's approval screen prefills Expected delivery with the **quotation date + the largest lead time** among the pending quotations (never in the past). The owner can change it; one date is used on every PO approved in that batch. Agreed-price approvals have no lead time, so the field stays empty. |
| Partial quotation | A quotation does not have to cover every REF line: items it does not offer simply stay open, and can go to another supplier, a later RFQ round, or a regular supplier's agreed price. The approval page lists the lines still open for the REF so the owner knows what the PO does not cover. |
| ROP = 0 | A ROP of 0 is a real threshold, not "not set up": the item appears on the restock alert as soon as available stock reaches 0. Only an item with **no** `item_location_settings` row is treated as not configured. |
| Choosing a supplier | **Choose suppliers** splits an REF **item by item**: each open item is sent to exactly one supplier — a recorded quotation, or a regular supplier's valid agreed prices — or kept open. A supplier who quoted an item appears **once**, with the quotation; their agreed price is only offered for items they did not quote (or quoted with an expired quotation). The preselected offer is the cheapest per item. A **Quick select** bar above the table resets every row to its lowest-priced valid offer (**Cheapest per item**) or picks one supplier everywhere they have a valid offer; each supplier pill shows their item coverage and grand total at max quantities (e.g. "Sup A (2/2 · 400.00)"), and items a supplier does not cover keep their current selection. Each row has its **Qty to order** prefilled with the quantity that will be ordered and its **subtotal** (qty × unit price), updated when the offer or quantity changes. A **split preview** shows, per supplier, the item count, the lines and the estimated PO total; the extra Record quotation button is not repeated at the bottom (Send for approval and Back only). The screen works without an RFQ at all when agreed prices exist. One `purchase_approvals` row (decision `pending`) is written **per supplier**, all sharing a `batch_id`; the same supplier chosen across two RFQ rounds merges into a single approval. Lines of one supplier are always grouped into **one** approval and **one** PO, even when some lines use a quotation price and others their standing agreed price — that approval has `basis = 'mixed'`. `basis = 'quotation'` with `quotation_id` is used only when every line came from the same single quotation. |
| REF page next steps | Record quotation, Choose suppliers and Ask more suppliers also sit in the **page header**, so the next step does not require scrolling past the document and its signatures. The same buttons stay in the bottom action bar. |
| Dropping an item | Only on the REF page (**Reason to drop...** / **Drop**), never on Choose suppliers. A real reason (at least 5 characters, not just "drop") is required, and if the item is still at or below its reorder point it shows up in the restock alerts again. A dropped item can be restored from the REF page and then needs a supplier again. |
| Approving | Owner only. The approval form can capture the **expected delivery date** and **PO terms**; approval creates the **PO as a draft** (no email), sets the REF to `approved`, and writes the audit row. The PO page has "Mark as sent" for the warehouse manager/owner. |
| Rejecting | Remarks are required. The REF goes back to `submitted` so the warehouse can choose again; no PO is made. |
| Edit PO | A `draft` PO can change its expected delivery date and terms. Items and prices stay as the owner approved them; cancel and re-order if they must change. |
| Cancel PO | A `draft` or `sent` PO can be cancelled with a reason. If no other PO is active for the REF, the REF goes back to `submitted`. A partially received PO cannot be cancelled. |
| Supplier returns | Removed. Goods that are short or rejected at receiving are handled by the discrepancy report. The tables `supplier_returns` and `supplier_return_items` stay in the database so old data is kept, but no screen uses them. |
| Cancel REF | Only `draft`, `submitted` or `rfq_sent`, with a reason. `for_approval` cannot be cancelled (the owner must decide first); approved/receiving/closed cannot either. Cancelling also closes the RFQ. |
| Withdraw approval | The **Withdraw** button on the approval row of the REF page deletes a `pending` approval before the owner acts: the lines are free again, the REF returns to `submitted`/`rfq_sent`, and the items can go to another supplier or an agreed price. Only the account that submitted it (same warehouse manager) can withdraw; an approved or rejected approval cannot be withdrawn (`for_approval` otherwise waits for the owner's decision). |
| Receiving | Delivery's `delivery.receiving_check` screen records the RM; accepted stock is added and short/rejected stock opens a DR. |
| PO numbers | `inv.next_doc_no("PO")`; REF/RFQ use `REF`/`RFQ`. A failed save rolls back, so no number is burned. |

## Database rows written

- `replenishments` + `replenishment_items` (create/submit/cancel); shortage lines can point at
  the waiting RF (`rf_id`).
- `notifications` (the REF's items are removed when it is submitted; completed only when the alert is empty),
  `audit_log` for every action.
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

`python -m unittest discover tests -v` → **384 tests, all passing** on the SQLite test schema
(Inventory, Requisition, Purchasing, Delivery, admin/login and printable documents).

A full flow was also run by hand on PostgreSQL: supplier → REF → RFQ → quotation → compare →
owner approval → PO → mark sent. Totals and statuses were correct.

Not done by design: sending email, receiving goods (Module 4), editing a REF after it is
submitted.
