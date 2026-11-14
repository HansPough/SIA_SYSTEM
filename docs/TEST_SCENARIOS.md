# Test scenarios (demo seed)

`scripts/seed_scenarios.py` fills a **demo** database with data that triggers every situation in the system, so you can
test each screen and each rule without creating the data by hand.

It does not insert finished rows. It logs in as the five demo users and works through the **real screens**, so every
document number, signature, fingerprint, stock movement and audit entry is genuine. Some scenarios are finished and some stop
half-way **on purpose**: you do the next step.

## Run it (Windows PowerShell, in the project folder)

```powershell
venv\Scripts\activate
python scripts/seed_scenarios.py --reset --yes
```

- `--reset` empties all transactions first (RF, REF, PO, TS, stock movements, alerts, suppliers, drivers, vehicles, signatures and the
  audit log) and reloads the demo stock. Items, users and locations stay. Run the same command again any time to start over: the
  document numbers come out the same.
- `--yes` is you confirming that this is a **demo database**. Without it the script refuses.
- It also refuses when the database already has transactions (unless `--reset`), and when accounts other than the five demo users exist
  (unless `--force`), because that usually means real data.
- It needs the demo users from `database/seed_users.sql` (password `changeme`).
- The guide below is also written to `instance/scenario_guide.md` after every run.

**Never run it on a real database.** `--reset` deletes data.

## What each situation is for

- **Alerts:** one completed by a delivery, one completed by making a REF, one active, one snoozed, one dismissed in the past.
- **RF and transfers:** draft, waiting in the queue, partly reserved (short stock), reserved, TS assigned, loaded without the driver's
  signature (release is blocked), loaded and signed, in transit, delivered with a shop variance, delivered cleanly, cancelled.
- **Sales and adjustments:** normal, void requested, voided; adjustment waiting, approved, rejected, and a warehouse one.
- **Purchasing:** REF draft, submitted with agreed prices (no RFQ needed), RFQ sent, mixed-supplier quotes with one item nobody has,
  two suppliers waiting for the owner, partly ordered, an item given up (restorable), an expired quotation, cancelled.
- **POs and receiving:** ready to send, sent, received in full, short, rejected, short then replaced, partly received, cancelled.
- **Signatures:** documents waiting for the warehouse manager and for the owner, and PDFs with real signatures.

## Scenario guide (document numbers as produced by a fresh `--reset` run)

## Accounts (password for all: `changeme`)

| Username | Role | Sees |
|---|---|---|
| `owner` | Owner | approvals, documents to sign, everything read-only |
| `whmgr` | Warehouse manager | RF queue, REF/RFQ/PO, transfers, receiving, master data |
| `whstaff` | Warehouse staff | loading check, driver signature, receiving goods |
| `shopmgr` | Shop manager | low-stock alerts, RF, incoming deliveries, adjustments, voids |
| `shopstaff` | Shop staff | sales and stock adjustments |

## Alerts

| What it is | Document | Log in as | Try this |
|---|---|---|---|
| Alert COMPLETED (its RF was delivered) | ALT-0001 | `shopmgr` | Requisition > Low-stock alerts > filter Completed; RF RF-2026-0001, TS TS-2026-0001 |
| Restock alert COMPLETED (REF made from it) | ALT-0002 | `whmgr` | Purchasing > Restock alerts > filter Completed. The REF REF-2026-0001 is waiting for an RFQ |
| ACTIVE low-stock alert (2 items) | ALT-0003 | `shopmgr` | Create RF from the alert, or Snooze it and watch the sidebar badge go away |
| Restock alert SNOOZED for a week | ALT-0004 | `whmgr` | Purchasing > Restock alerts > filter Snoozed > 'Wake now' |

## RF

| What it is | Document | Log in as | Try this |
|---|---|---|---|
| RF draft (not sent) | RF-2026-0002 | `shopmgr` | Open it and press 'Sign & send RF to warehouse' |
| RF waiting in the warehouse queue | RF-2026-0003 | `whmgr` | RF queue > open > Reserve stock (signs 'Noted by') |
| RF partly reserved (stock short) | RF-2026-0004 | `whmgr` | Short item raises a restock alert; make a REF from it. RF waits for the shortage |
| RF cancelled | RF-2026-0012 | `shopmgr` | Requisition forms > filter Cancelled |

## TS

| What it is | Document | Log in as | Try this |
|---|---|---|---|
| RF fully reserved (no TS yet) | RF-2026-0005 | `whmgr` | Delivery > Transfer slips > Prepare: pick driver + vehicle |
| TS assigned (waiting for loading check) | TS-2026-0002 | `whstaff` | Transfers to load > Loading check |
| TS loaded, driver has NOT signed | TS-2026-0003 | `whmgr` | Try 'Sign & release' first: it is blocked. Then Driver signs on the staff screen |
| TS loaded + driver signed | TS-2026-0004 | `whmgr` | Press 'Sign & release' |
| TS in transit (shop must receive) | TS-2026-0005 | `shopmgr` | Incoming deliveries > Receive: tick 'Counted' on every line, then sign |
| Delivered WITH shop variance | TS-2026-0006 | `whmgr` | Delivery > Discrepancies > tab 'To shops': open variance |
| Delivered cleanly (AR signed) | TS-2026-0007 | `owner` | Open the TS: it shows the TS and its AR with all signatures |

## Sales

| What it is | Document | Log in as | Try this |
|---|---|---|---|
| Normal sale | ISS-2026-0001 | `shopstaff` | Sales / issues |
| Void REQUESTED (waiting) | ISS-2026-0002 | `shopmgr` | Open it > confirm or reject the void |
| Sale VOIDED (stock put back) | ISS-2026-0003 | `owner` | Sales / issues > filter Voided |

## Adjustments

| What it is | Document | Log in as | Try this |
|---|---|---|---|
| Adjustment waiting for review | ADJ-2026-0001 | `shopmgr` | Stock adjustments > review: approve or reject |
| Adjustment APPROVED | ADJ-2026-0002 | `owner` | Stock adjustments (view): stock was changed |
| Adjustment REJECTED | ADJ-2026-0003 | `shopstaff` | See the reviewer's remark |
| Warehouse adjustment waiting | ADJ-2026-0004 | `whmgr` | Stock adjustments > review |

## REF

| What it is | Document | Log in as | Try this |
|---|---|---|---|
| REF draft | REF-2026-0002 | `whmgr` | Open > 'Sign & submit REF' |
| REF submitted (agreed prices exist) | REF-2026-0003 | `whmgr` | Choose suppliers: pick 'agreed price' without sending an RFQ |
| RFQ sent, no replies yet | REF-2026-0004 | `whmgr` | Record quotation (as suppliers reply) |
| Quotes in: mixed suppliers, 1 item unquoted | REF-2026-0005 | `whmgr` | Choose suppliers: split items across suppliers; item 4 has NO supplier: keep open |
| Partly ordered (1 item still open) | REF-2026-0008 | `whmgr` | 'Ask more suppliers (new RFQ)' for the open item |
| An item was GIVEN UP (can be restored) | REF-2026-0009 | `whmgr` | Press 'Restore' on the dropped item |
| Quotation EXPIRED | REF-2026-0010 | `whmgr` | Choose suppliers: the expired offer is disabled; record a fresh one |
| REF cancelled | REF-2026-0011 | `whmgr` | Replenishment forms > filter Cancelled |

## Approvals

| What it is | Document | Log in as | Try this |
|---|---|---|---|
| Two suppliers waiting for the OWNER | REF-2026-0006 | `owner` | Approvals: approve one, reject the other (rejecting needs remarks) |
| Owner REJECTED one supplier | REF-2026-0018 | `whmgr` | The rejected item is open again: choose another supplier |

## PO

| What it is | Document | Log in as | Try this |
|---|---|---|---|
| PO SENT (waiting for goods) | PO-2026-0001 | `whstaff` | Receiving from suppliers > Check goods |
| PO READY to send (owner approved) | PO-2026-0002 | `whmgr` | Press 'Sign & mark as sent' |
| PO PARTLY received (balance due) | PO-2026-0009 | `whstaff` | Check goods again when the rest arrives (a DR is opened for the short quantity) |
| PO cancelled (REF can be re-ordered) | PO-2026-0010 | `whmgr` | Open the REF: the item needs a supplier again |

## Receiving

| What it is | Document | Log in as | Try this |
|---|---|---|---|
| Received IN FULL by staff (memo unsigned) | REF-2026-0012 | `whmgr` | Documents to sign: the receiving memo waits for 'Checked by'. The REF is now closed |

## Discrepancy

| What it is | Document | Log in as | Try this |
|---|---|---|---|
| Supplier delivered SHORT (open) | DR-2026-0001 | `whmgr` | Documents to sign: sign 'Prepared by', then the owner signs 'Noted by' |
| Goods REJECTED; DR waits for the owner | DR-2026-0002 | `owner` | Documents to sign: 'Noted by' |
| Short, then REPLACED (DR resolved) | DR-2026-0003 | `whmgr` | Discrepancies > From suppliers > filter Resolved |
