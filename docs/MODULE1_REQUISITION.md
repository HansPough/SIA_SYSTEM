# Module 1 — Requisition (what was built)

Module 1 is now wired to the database. The shop manager sees the shop's low-stock
alerts, turns them into a requisition form (RF) and sends it to the warehouse.
The warehouse manager reserves stock from the RF in Inventory's **RF queue**
(already built); Delivery moves the stock later.

Files:

- `app/requisition_ops.py` — queries and save workflows (new).
- `app/blueprints/requisition.py` — the 7 screens/actions, thin routes.
- `app/templates/requisition/` — the 7 templates, real data.
- `tests/test_requisition_screens.py` — 17 click-through tests (new).

Nothing here changes stock. Cancel is the only stock-adjacent action, and it only
calls `inv.release_reservation()` so the warehouse's "reserved" figure stays right.

## Screens

| Screen | Route | Who |
|---|---|---|
| Low-stock alert list | `/requisition/alerts` | shop manager |
| Alert detail (+ RFs made from it) | `/requisition/alerts/<id>` | shop manager |
| Dismiss alert (reason required) | `/requisition/alerts/<id>/dismiss` | shop manager |
| New RF (pre-filled from an alert, or blank) | `/requisition/rf/new` | shop manager |
| RF list (scoped) | `/requisition/rf` | shop manager (own shop), warehouse manager (to their warehouse), owner (all) |
| RF detail + submit a draft | `/requisition/rf/<id>` | same as the list; submit only shop manager |
| Cancel RF (reason required) | `/requisition/rf/<id>/cancel` | shop manager, own shop |

## Decisions made while building it

| Topic | Decision |
|---|---|
| Alert number | Alerts have **no** document number in the schema. The screens show `ALT-0001` from the row id, only as a readable reference. Nothing is added to `document_sequences`. |
| Alert contents | `notification_items` is a snapshot taken when Inventory raised the alert. The detail shows `current_qty` (available then), `rop` and `suggested_qty`; it does not recompute them. |
| Dismiss | Reason is required (the table also enforces it). Sets `status = 'dismissed'`, `dismissed_reason`, `closed_by`, `closed_at` and writes an audit row. A dismissed alert is never reopened; the next movement of a still-low item creates a new one. |
| RF from an alert | Pre-fills the alert's items and `suggested_qty`; the shop manager can change quantities or add items. `requisitions.notification_id` links back to the alert (shown on both screens). |
| Drafts | "Save draft" keeps the RF out of the warehouse's RF queue and out of the warehouse manager's RF list. The shop manager submits it later from the RF detail page. |
| RF number | `inv.next_doc_no("RF")` → `RF-2026-0001`. A failed save rolls back, so it does not burn a number. |
| RF date | The template's old "Date" field was dropped: the schema has `created_at` / `submitted_at`, not an RF date the user types. |
| Cancel | Only the shop manager of that shop, and only `draft` or `open` (nothing delivered yet). Reason required. Every still-`reserved` reservation is released as `cancelled`, `qty_reserved` goes back to 0, `closed_at` is set and an audit row is written. `partially_delivered` and `closed` RFs are refused. |
| Alert after RF | The alert is **not** auto-dismissed when an RF is created. It stays active (showing any RFs made from it) until stock recovers, which is when Inventory removes it, or until the shop manager dismisses it. |
| Available, not on hand | The screens label stock as **available** (on hand minus reserved), matching how Inventory decides "at or below ROP". |
| Warehouse visibility of drafts | Warehouse managers cannot list or open a `draft` RF; a draft is private to the shop until submitted. |
| Prices | None anywhere in Module 1, as required. |

## Database rows written

- `requisitions` + `requisition_items` on create; `status` / `submitted_at` on submit.
- `notifications` on dismiss (`dismissed_reason`, `closed_by`, `closed_at`).
- `reservations` only through `inv.release_reservation()` on cancel.
- `audit_log`: `create`, `submit`, `dismiss`, `cancel` (with before/after values).

## Populating data for Module 1

Low-stock alerts are **not** a stored list of low items: Inventory raises them when stock
moves (sale, approved adjustment, reservation). Two consequences:

- Normal use needs no seeding. Selling stock down to the ROP makes the alert appear.
- Stock loaded straight with SQL (`database/seed_demo_stock.sql`, an import from another
  system) creates **no** alerts, so the alert screens look empty. Run
  `python scripts/seed_alerts.py` once after such an import; it calls the same
  `refresh_alerts()` the app uses, for every location.

Demo data is fake. Never load `seed_demo_stock.sql` in the real database; at go-live the
owner enters real ROP/max values and the first real stock goes in through approved
adjustments.

## Tests

`python -m unittest discover tests -v` → **75 tests, all passing** (58 Inventory + 17 Requisition),
on the SQLite test schema. `tests/sqlite_schema.py` gained a small `transfer_slips` table so the
RF detail page's "Transfer slips" section can be queried.

Still not done (not Module 1): editing an RF after submit, real login, and Delivery's transfer slips.
