# Module 4 — Delivery (what was built)

Delivery links the modules: the warehouse sends reserved stock to a shop on a **transfer slip
(TS)**, the shop signs an **acknowledgment receipt (AR)**, and supplier deliveries become a
**receiving memo (RM)** with optional **discrepancy reports (DR)**.

Files:

- `app/delivery_ops.py` — queries and save workflows (new).
- `app/blueprints/delivery/` — `transfers.py`, `shop_receiving.py`, `supplier_receiving.py`.
- `app/templates/delivery/` — 20 templates with real data.
- `tests/test_delivery_screens.py` — 39 click-through tests.

## Flow

```
RF queue reserves stock
        │
        ▼
prepare TS (draft) ─► assign driver/vehicle ─► loading check (staff) ─► release (manager)
                                                                          │ stock out (transfer_out)
                                                                          ▼
                                                              in transit ─► shop signs AR
                                                                          │ accepted stock in (transfer_in)
                                                                          ├─► rejected/short ─► back to warehouse stock
                                                                          │                     RF line reopened for balance
                                                                          ▼
                                                        RF partially delivered / closed
                                                                          │
                          later TS fills the RF line ──────────────────► shop variance resolves

PO sent ─► goods arrive ─► receiving check (RM) ─► accepted stock in (supplier_receipt)
                                              └─► short / rejected ─► discrepancy report (DR)
                                                                          │
                          later RM accepts the missing units ───────────► DR resolves itself
```

## Screens

| Screen | Route | Who |
|---|---|---|
| Transfer slip list | `/delivery/transfers` | warehouse manager, warehouse staff, owner |
| Prepare TS (from reserved RFs) | `/delivery/transfers/new` | warehouse manager |
| TS detail | `/delivery/transfers/<id>` | warehouse manager/staff (own), shop manager (in transit and later), owner |
| Cancel TS | `POST /delivery/transfers/<id>/cancel` | warehouse manager |
| Assign driver/vehicle | `/delivery/transfers/<id>/assign` | warehouse manager |
| Loading check | `/delivery/transfers/<id>/load` | warehouse staff |
| Sign and release | `/delivery/transfers/<id>/release` | warehouse manager |
| Incoming deliveries | `/delivery/deliveries` | shop manager |
| Receive delivery (AR) | `/delivery/deliveries/<id>/receive` | shop manager |
| Shop variances | `/delivery/variances` | shop manager, warehouse manager, owner |
| Receiving from suppliers | `/delivery/receiving` | warehouse staff, warehouse manager |
| Check goods (RM) | `/delivery/receiving/<po_id>/check` | warehouse staff, warehouse manager |
| Receiving memo | `/delivery/receiving/rm/<id>` | warehouse staff/manager, owner |
| Discrepancy list | `/delivery/discrepancies` | warehouse manager, owner |
| Discrepancy detail | `/delivery/discrepancies/<id>` | warehouse manager |
| Drivers / Vehicles | `/delivery/drivers`, `/delivery/vehicles` (+ new/edit) | warehouse manager |

## Decisions made while building it

| Topic | Decision |
|---|---|
| Creating a TS | The old screens had no way to create one, so a **Prepare TS** screen was added. It lists RFs whose stock is currently reserved and not already on an in-progress TS. The TS takes the exact reserved quantities and shows, per line, what is still needed, what goes on the TS, and the shortfall. |
| Short dispatch | A line that sends less than the RF line still needs asks for a **reason** before the TS is created (`transfer_slip_items.dispatch_note`, migration 016). `still needed` is the RF quantity minus what earlier deliveries already brought in, so sending the remainder of a backorder is not "short" and asks for nothing. The reason prints in the Remarks column of the TS and of the AR. |
| Expected per delivery | Each TS line snapshots `qty_needed` (what the RF line still owed when the TS was prepared, migration 017). Requested, Diff and the AR Variance are measured against that number, so the second delivery of a backorder (1 unit owed, 1 sent) reads `1 / 1 / 0` and **No** variance instead of `5 / 1 / -4`. |
| One TS per RF at a time | A second TS is refused while one is `draft/assigned/loaded/in_transit`. Cancel it to start again. |
| Cancelling a TS | Allowed in `draft/assigned/loaded` only. It does **not** free stock: the reservation stays for the RF, so a new TS can be prepared. |
| Cancelling an RF with a TS | Blocked in Module 1 while a TS is in progress, otherwise a shop could cancel the RF under a loaded truck. |
| Releasing | Consumes the active reservations (one `transfer_out` ledger row each). If the reserved quantity no longer matches the TS, release is refused with a message to cancel and prepare again. |
| Shop receiving | Only the **shop manager** signs the AR. The receiving screen shows the actual TS and the AR it is about to sign (status Pending, no signature yet), then the counting form. Accepted units (0 up to what was released) are added to shop stock as `transfer_in`; `requisition_items.qty_delivered` is raised and the RF becomes `partially_delivered` or `closed`. |
| Shop variance | A reason is required when accepted is less than the quantity on the TS. The rejected/short units are returned to **warehouse stock** (`transfer_in`, ref AR); `requisition_items.qty_reserved` is reduced by the same amount, which reopens that line in the RF queue so the warehouse can send the balance again. The TS keeps `delivered_with_variance`. |
| AR variance field | The AR says **Yes** with the total unit difference (e.g. `Yes (-24 pcs)`) whenever a line's received quantity differs from what that delivery was expected to bring (`transfer_slip_items.qty_needed`), so a short dispatch counts even when every dispatched unit was accepted. Each AR line shows Requested, Received, Diff and Remarks, with a totals row; a negative Diff is printed in bold red. The Remarks cell carries the warehouse's short-dispatch reason and, when the shop also rejected part of the line, its variance reason after it (`reason · reason`). |
| AR sign-off | The AR is generated when the shop confirms delivery, so it has no "Prepared by" line. It keeps the receiver's signature, time and account, and the footer says it was generated automatically. |
| Shop variance auto-resolve | `acknowledgment_receipt_items.variance_resolved_at` is set automatically when a later delivery raises `qty_delivered` to `qty_requested` for that RF line. The incoming-delivery list shows the variance as Open (n) or Resolved. |
| Last shop manager | The owner cannot deactivate a shop's last active shop manager while a TS is `in_transit` to that shop or an AR there has no `received_by` signature, because nobody could receive the delivery. Add another shop manager first. |
| No manual resolve | **Discrepancies → To shops** is read-only: every row opens its AR. An open variance clears only by a later delivery that fills the RF line, or by approving a stock adjustment linked to the AR. A `partially_delivered` RF has no other close path: Module 1 only cancels `draft`/`open` RFs, so a balance that will never be sent stays open (see Known gaps). |
| Variance settled by an adjustment | Approving a stock adjustment whose **Linked AR no.** names an AR resolves every open variance line on that AR (a manager's own adjustment does this in the same save). The adjustment detail shows the AR as a link. |
| Accepted / rejected at the shop | The shop receiving page uses the same pair of boxes: type accepted and rejected fills in (`qty on TS - accepted`), or type rejected and accepted fills in. Both blank means everything on the TS was accepted; the server applies the same rule without JavaScript. |
| Draft TS visibility | A shop manager can only open a TS that is `in_transit` or already delivered, never a draft being prepared. |
| Supplier receiving | A PO must be `sent` or `partially_received` first. `accepted + rejected` must equal `delivered`; rejected stock needs a reason. Only **accepted** stock is added (`supplier_receipt`). |
| Accepted / rejected entry | The two boxes fill each other: type accepted and rejected becomes `delivered - accepted`, or type rejected and accepted becomes `delivered - rejected`. The server does the same when one box is left blank; both blank means everything delivered was accepted. |
| Partial supplier deliveries | `delivered` cannot exceed what is still expected. PO becomes `partially_received` or `received`; the REF becomes `receiving` or `closed`. |
| Discrepancies | Created when a delivery is short or stock is rejected. Items store `qty_short`, `qty_rejected` and `qty_replaced` (units accepted on later RMs); status is `open`, `follow_up` or `resolved` (with notes and `resolved_at`). |
| Discrepancy auto-resolve | When a later RM accepts units for the same PO line, they are matched oldest-report-first to the open discrepancy lines (`qty_replaced`). A report resolves itself (`resolved`, `resolved_at`, a note naming the RM) once every line is covered. The warehouse manager can still resolve or reopen it by hand. |
| Numbers | `inv.next_doc_no()` for `TS`, `AR`, `RM`, `DR`; a failed save rolls back without burning a number. |
| Drivers/vehicles | Managed by the warehouse manager; only active ones can be assigned. Plate numbers are unique (case-insensitive). |

## Known gaps

A `partially_delivered` RF cannot be closed unless the balance is later delivered, because the
manual shop-variance resolve (and its Close RF option) was removed and Module 1 only cancels
`draft`/`open` RFs. A line the shop will never take therefore keeps the RF and its variance
open. Closing it needs either a small "close remainder" action in Module 1 or a delivery of the
balance.

## Tests and verification

`python -m unittest discover tests -v` → **384 tests, all passing** (Inventory, Requisition,
Purchasing and Delivery screens plus the stock core).

A full flow was also run by hand on PostgreSQL: shop RF → reserve → prepare TS → assign → load
→ release → shop AR, then supplier REF → agreed price → owner approval → PO sent → receiving
memo. Stock, statuses and totals were correct.

## Upgrading an existing database

- `database/migrations/004_shop_variance_resolution.sql` adds `variance_resolved_at` to the AR
  lines. Run it (and the earlier migrations) once on an existing database.
- `python scripts/backfill_shop_variances.py` returns the rejected/short units from ARs signed
  **before** that change to warehouse stock and reduces `qty_reserved`, so the RF queue can send
  the balance again. It is safe to run more than once.
