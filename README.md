# Inventory System (Flask + PostgreSQL)

Structure for the four modules: **Requisition, Inventory, Purchasing, Delivery**.
Status: all four modules work. **Inventory** (stock, ROP with suggestions, stock-count sheets, sales / issues and void requests, adjustments, items, brands and patterns, alerts, RF queue, reports), **Requisition** (low-stock alerts for the shop manager, RFs sent to the warehouse), **Purchasing** (restock alerts, REF, RFQ, quotations, owner approval, PO edit/cancel, suppliers and agreed prices) and **Delivery** (transfer slips, loading/release, shop AR, supplier receiving memo, discrepancy reports, shop variances, drivers, vehicles). Users and locations are managed in the app, login is real, and RF/REF/PO/TS/AR/RM/DR have printable, e-signable document views; each user can save a drawn signature under **My signature** and it is used automatically when they sign.
The interface keeps itself current: the bell and sidebar badges update by themselves, list and detail
screens refresh when another user saves, and every status word has one colour (green = done, amber =
waiting, red = stopped, blue = in progress, grey = draft). See `docs/UI_FLOWS_LIVE_AND_STATUS.md`.

See `docs/MODULE1_REQUISITION.md`, `docs/MODULE3_PURCHASING.md`, `docs/MODULE4_DELIVERY.md`, `docs/STOCK_CORE_FOR_OTHER_MODULES.md` and `docs/ADMIN_AND_DOCUMENTS.md`.

## Run it (Windows PowerShell)

```powershell
# 1. Python packages
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# 2. settings: copy the example, then open .env and put your postgres password in DATABASE_URL
copy .env.example .env

# 3. database (psql must be on PATH, see below)
dropdb   -U postgres inventory_db                      # skip if it does not exist; stop the app first
createdb -U postgres inventory_db
psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/schema.sql
psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/seed_items.sql
psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/seed_users.sql
psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/seed_demo_stock.sql   # optional, DEMO data
python scripts/seed_alerts.py                          # only for the demo stock: fills the alert screens
python scripts/seed_demo_suppliers.py                  # optional, DEMO suppliers + agreed prices for every item
python scripts/seed_supplier_prices.py                 # optional, DEMO agreed prices for suppliers already in the database

# 4. start
python run.py                  # open http://127.0.0.1:5000
```

If `psql` is not recognized, add PostgreSQL to PATH for this window (change 18 to your version):
`$env:Path += ";C:\Program Files\PostgreSQL\18\bin"`

If the database **already exists**, do not run `schema.sql` again. Apply the migrations added after it
once, oldest first (each filename is also recorded in `schema_migrations`):
```powershell
Get-ChildItem database\migrations\*.sql | Sort-Object Name | ForEach-Object {
    psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f $_.FullName
}
python scripts/backfill_shop_variances.py   # once, to return old shop shortfalls to warehouse stock
```

Log in at `/login` with a real account. The demo accounts are `owner`, `whmgr`, `whstaff`, `shopmgr` and
`shopstaff`; all use the password `changeme`. The "Preview as another role" menu is a development shortcut —
set `DEV_LOGIN=0` in `.env` to switch it off on a real deployment.

The owner can add users and shops/warehouses under **Users** and **Locations**. Every RF, REF, PO, TS, AR, RM
and DR has a **Print / Sign** button: it opens a clean printable page, lets the right role type an e-signature,
and stores the signer and timestamp.

### What goes into the database

| Layer | File / action |
|---|---|
| Tables (44) | `database/schema.sql` (run on an **empty** database) |
| Items (416, names from the client Excel) | `database/seed_items.sql` |
| Demo locations (WH, SHOP) and 5 demo users | `database/seed_users.sql` |
| Stock, ROP, max, lead time, racks — **made-up demo numbers** | `database/seed_demo_stock.sql` — never in a real database |
| Alert rows for that demo stock | `python scripts/seed_alerts.py` (alerts normally appear only when stock moves) |
| **Complete stock for every item** (ROP, max, lead time, rack, warehouse and shop stock, opening ledger, low-stock examples) | `python scripts/seed_complete_demo.py --yes` — resets working data, keeps users/items/suppliers; **demo database only** |
| **Test scenarios that trigger every situation** (alerts, RF/TS in every state, REF/PO, approvals, discrepancies, voids...) | `python scripts/seed_scenarios.py --reset --yes` — **demo database only**, see `docs/TEST_SCENARIOS.md` |
| Demo suppliers and agreed prices for every item — **made-up numbers** | `python scripts/seed_demo_suppliers.py` — never in a real database |
| Agreed prices for the suppliers already in the database — **made-up numbers** | `python scripts/seed_supplier_prices.py` — never in a real database |
| Real stock at go-live | Approved adjustments in the app, never the demo seed; the owner enters the real ROP values |

If an error says a table like `requisitions` does not exist, the schema was never loaded into
`inventory_db`. Load it with the `psql -f database/schema.sql` line above.

If a page shows **Internal Server Error**, run this first — it checks the connection, every required
table and the columns added by the migrations, without changing anything:

```powershell
python scripts/check_setup.py
```

Also stop and restart `python run.py` after pulling code changes; an already-running server keeps the old
code even when the database has been migrated. If a 500 still occurs, the traceback is appended to
`instance/error.log`; send that file (or run with `FLASK_DEBUG=1`) so the exact failing line can be found.

### Minimal sample database (16 items) for testing

The full seed has 416 items. For a smaller, faster test database, create a separate one:

```powershell
createdb -U postgres inventory_sample
psql -U postgres -d inventory_sample -v ON_ERROR_STOP=1 -f database/schema.sql
psql -U postgres -d inventory_sample -v ON_ERROR_STOP=1 -f database/seed_minimal.sql
```

Point `DATABASE_URL` in `.env` at `inventory_sample` to use it. It has 16 items (10 tires, 3 tubes,
3 flaps), warehouse and shop stock, ROP/max/lead/rack, 5 demo users, 3 suppliers and agreed prices.
It is safe to run again.

## Folders

| Path | What it is |
|---|---|
| `database/schema.sql` | all 44 tables, constraints and indexes |
| `app/__init__.py` | app factory, registers the blueprints |
| `app/inventory_service.py` | **stock core**: the only place that changes stock (`change_stock`, `reserve`, `release_reservation`, `consume_reservation`, `items_below_rop`, `next_doc_no`, `write_audit`). Other modules call these, never write their own stock UPDATE |
| `app/inventory_ops.py` | Inventory module queries and save workflows (ROP, stock list, issues, adjustments, dashboard numbers) |
| `app/requisition_ops.py` | Requisition module queries and save workflows (alerts, RFs, submit, cancel) |
| `app/purchasing_ops.py` | Purchasing module queries and save workflows (REF, RFQ, quotations, approvals, PO, suppliers) |
| `app/admin_ops.py` | users and locations (owner administration, password hashing) |
| `app/document_ops.py` | printable document data and e-signature slots (RF, REF, PO, TS, AR, RM, DR) |
| `app/delivery_ops.py` | Delivery module queries and save workflows (transfer slips, AR, receiving memo, discrepancies, drivers, vehicles) |
| `app/report_ops.py` | the owner's inventory reports (page and CSV) |
| `docs/STOCK_CORE_FOR_OTHER_MODULES.md` | **read this if you build Requisition, Purchasing or Delivery**: which stock functions to call, and when |
| `app/item_ops.py` | item list, add and edit item (owner), brand and pattern pick-lists |
| `app/session_user.py` | who is logged in and which location they may see |
| `scripts/import_excel.py`, `scripts/import_rules.py` | turn the client's Excel item names into seed SQL (see below) |
| `scripts/seed_alerts.py` | create the alert rows for stock that was imported with SQL (demo stock) |
| `scripts/seed_demo_suppliers.py` | create demo suppliers and an agreed price for every item (demo only) |
| `tests/` | `python -m unittest discover tests -v` (runs on SQLite, no PostgreSQL needed) |
| `app/roles.py` | roles, the sidebar menu per role, `roles_required` |
| `app/statuses.py` | the one place a status word gets its colour (ok, warn, danger, info, neutral) |
| `scripts/seed_complete_demo.py` | complete stock for every active item at both locations (demo) |
| `app/live.py` | revision and sidebar/bell data used by `/api/live`; which screens may refresh themselves |
| `app/blueprints/live.py` | `GET /api/live`, the small JSON the browser polls for live updates |
| `app/static/js/live.js` | updates the bell/sidebar, refreshes a screen another user changed, and shows toasts |
| `app/blueprints/` | `requisition.py`, `inventory.py`, `purchasing.py`, `auth.py`, `dashboard.py`, `live.py`. `delivery/` and `admin/` are folders with one file per owner (`transfers.py`, `shop_receiving.py`, `supplier_receiving.py`, `people.py`, `items.py`) so team members do not edit the same file |
| `app/templates/<module>/` | one HTML file per screen (lists, forms, details) |
| `app/templates/dashboard/` | one dashboard per role |
| `app/static/` | `css/style.css`, `js/app.js` (add/remove rows in item tables) |

## Where to start coding

The four modules are built end-to-end, including login, admin, printable e-signed documents,
stock-count sheets and ROP suggestions.

Each route has a `TODO` where the database work goes. Document numbers (RF-2025-0001 and so on) use the `document_sequences` table.


## About the seed files

`seed_items.sql` is the item list (names from the client Excel). `seed_users.sql` creates demo locations and demo users.
`seed_demo_stock.sql` is made-up stock, ROP, lead times and racks for development only. For real use skip it, delete the demo
users, and enter the real stock through approved adjustments.

## Rebuilding the seed files from the client Excel

```
pip install openpyxl
python scripts/import_excel.py INVENTORY.xlsx
```

The Excel is used only for the item names. The script cuts each name into brand, model, size, ply, pattern and
category (rules in `scripts/import_rules.py`, one place to change), and writes `database/seed_items.sql`,
`database/seed_demo_stock.sql` and `database/import_report.md`. Read the report: it lists what was guessed or made up.
Items are classified only by their specs. There is no vehicle type.

## Tests

`python -m unittest discover tests -v` (384 tests, run on SQLite, no PostgreSQL needed).
