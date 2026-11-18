# Interface: flow, wording, status colours and live updates

This document covers the parts of the interface that are shared by every module. Module rules
(stock, approvals, receiving) stay in their own documents.

## 1. One flow, one screen per step

The business flow is unchanged, and every screen now has exactly one job:

| Step | Screen | Who |
|---|---|---|
| Low stock appears | Requisition → Low-stock alerts | Shop manager |
| Ask for stock | Requisition → Requisitions (RF) → New | Shop manager |
| Reserve stock | Inventory → RF queue → Review | Warehouse manager |
| Send stock | Delivery → Transfers (TS) → New | Warehouse manager |
| Load, sign, release | Transfer page → Load check → Driver sign → Release | Warehouse staff / manager |
| Receive at shop | Delivery → Incoming deliveries → Receive | Shop manager |
| Restock warehouse | Purchasing → Restock alerts → Replenishments (REF) | Warehouse manager |
| Quote and choose | REF page → RFQ → Quotation → Choose suppliers | Warehouse manager |
| Approve | To do → Approvals | Owner |
| Send and receive goods | Purchase orders (PO) → Supplier receiving | Warehouse manager / staff |

Redundancy removed in this pass:

* **Shop menu:** "Record sale / issue" and "Sales / issues" were two menu entries for one job.
  There is now one **Sales** list and its **+ Record sale** button (also a dashboard tile).
* **Warehouse staff menu:** "Purchase orders" sat under Inventory. It now sits under Purchasing,
  where the rest of the buying flow lives.
* **Shared screens:** Adjustments, Transfers and Replenishments use one short name in the menu and
  the page title instead of two different names.
* **Dead screen:** the unused `inventory_manager` dashboard template was removed (no such role).

## 2. Short wording

Page titles, menu labels, notes, buttons and flash messages were shortened to what a user needs at
that moment. Examples:

| Before | After |
|---|---|
| Record Sale / Stock Issue | Record sale |
| Replenishment Forms (REF) | Replenishments (REF) |
| Transfer Slips (TS) | Transfers (TS) |
| Choose suppliers and send for approval | Choose suppliers |
| Check Goods (RM) | Check goods |
| You have not saved a drawn signature yet, so your name and the time will be stamped. | (same idea, one short line above the Sign button) |

Keep new copy short: a label is a noun, a note is one sentence, a flash is one sentence, and a
button says the action ("Release", "Save", "Reserve stock").

## 3. Status colours

`app/statuses.py` is the only place that decides a status colour. Templates call

```jinja
{{ m.status(row.status) }}
```

and the badge gets one of five tones:

| Tone | Meaning | Examples |
|---|---|---|
| `ok` (green) | finished, safe | active, approved, completed, received, delivered, resolved, posted |
| `warn` (amber) | waiting for a person, or work in progress | pending, partially received, in transit, assigned, loaded, follow-up, below ROP |
| `danger` (red) | stopped or a problem | cancelled, rejected, voided, out of stock |
| `info` (blue) | recorded / moving, no action needed | sent, submitted, RFQ sent |
| `neutral` (grey) | draft, or nothing decided yet | draft, inactive, unknown values |

The word is always printed as well as the colour, and the printed page keeps a dot so a black and
white copy still reads correctly. To add a new status, add it to `STATUS_TONES` (and to
`STATUS_LABELS` if normal capitalisation is wrong). Unknown values fall back to neutral instead of
breaking the page.

## 4. Notifications

There are three layers, and they agree because they use the same numbers:

1. **Flash message** after your own save (success or error). It is `role="status"`, has a close
   button, and hides itself after a few seconds.
2. **Bell + sidebar badges** for work waiting for you. `app/nav_ops.py` is the single source; the
   dashboard, the bell and the sidebar all read it, so they can never disagree.
3. **Toast** when another user's save creates work for you ("New for you: Approvals (1)"). There is
   no "live" light or "updated" toast: the page catches up silently.

Business notifications are created by the stock core, not by screens: a sale or receipt that
crosses the ROP opens the low-stock/restock alert, an RF the warehouse cannot cover adds a shortage
row, and finishing the REF closes the alert. Snoozing keeps the alert open but silent until the
date passes.

## 5. Live updates between users

`GET /api/live` returns the bell counts, the sidebar counts and a revision token. `live.js` polls it
every 9 seconds (and immediately when the tab becomes visible).

* The **bell, sidebar badges and attention list** are redrawn in place. No reload.
* A list or read-only detail page is **fetched again and swapped in place** when the revision
  changes, keeping the scroll position and open `<details>` panels. It happens silently.
* A **data-entry screen is never replaced** under the user's hands. `app/live.py` lists those
  screens, and the browser also refuses to refresh while a field has unsubmitted text, a changed
  dropdown or selected text. Nothing is shown in that case.
* Logging in or out does not change the revision, so a colleague signing in does not disturb you.
  Any real save (audit row) does.
* If polling fails (server restart), the page simply keeps working and catches up on the next poll.

## 6. Buttons stay visible

* The action column of a wide table (Open / Edit / Receive) is pinned to the right edge while the
  table scrolls sideways, so a button is never off-screen on a phone or small laptop.
* The bottom action bar is sticky, opaque and has a top border; buttons inside a data-entry card
  stay in the normal flow so they never cover an input.
* On narrow screens the brand shrinks, the user name hides, and the bell/avatar stay in the bar.
  The menu button opens the sidebar as a drawer.
* Buttons are real buttons or links styled as buttons (`.btn`), never bare links in a table.

## 7. Stock count and adjustments

* **Stock** has two shortcuts in its header: **Stock count** and **Adjust stock**.
* The count sheet and the new adjustment are **search-first**: the full item list is not printed.
  Type a name or code and a short result list appears; pick the item, change the quantity, save.
  Items that match are not touched.
* **Staff** save a request; their manager approves it.
* **A manager's own count or adjustment is applied on save** (`auto_approved`), because a manager
  does not approve their own work. The stock ledger records it like any approved adjustment.

## 8. Document numbers, details and lifecycle

* A create form shows the **real next number** (`RF-2026-0001`, `REF-…`, `ISS-…`, `ADJ-…`, `RM-…`,
  `AR-…`, `RFQ-…`, `TS-…`) instead of `auto`. The number is only reserved when the save succeeds.
* Document detail pages show the **document first**, then **Progress and details below it**
  (not at the side). Progress uses compact item blocks, never squeezed tables.
* Each details panel ends with a **Lifecycle** list: created, submitted, approved, sent, received,
  released, cancelled and signed, with the time and the person. It is read from the audit log and
  the stored signatures, so it cannot drift from the document.
* After a supplier is approved, the warehouse manager gets a **Send** button in Purchase orders and
  **Send PO** on the REF page.
* A delivered transfer shows an **AR** button that opens the acknowledgment receipt preview.

## 8a. One request per alert

An alert produces one RF (shop) or one REF (warehouse). Once one exists, the alert page shows
**Open RF-… / Open REF-…** instead of **Create**, and a second save is refused with “already has …”.
A cancelled RF/REF no longer counts, so the alert can be used again after a cancel.

## 9. Checking it

```
python -m unittest discover tests -v
```

`tests/test_ui_redesign.py` covers the menu, badges, document-first layout, status colours, document
numbers, the stock shortcuts, the AR preview and the live endpoint.
