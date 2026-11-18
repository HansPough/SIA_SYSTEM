"""Complete demo stock data for testing the four modules.

Every active item gets a complete row at BOTH locations (warehouse and shop):
ROP, max level, lead time and rack, plus an on-hand quantity and an opening
ledger row. A few items are deliberately below their ROP so the alert -> RF /
REF flows have something to work with.

Users, roles, locations, items, suppliers and agreed prices are kept. Only the
working data is reset (requisitions, transfers, receiving, issues, adjustments,
alerts, approvals, POs, discrepancies, signatures, ledger, document numbers).

Run on a DEMO database only:

    python scripts/seed_complete_demo.py --yes

It is safe to run again: it resets and rebuilds the same stock.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app import inventory_ops as inv_ops
from app import inventory_service as inv
from app.extensions import db
from sqlalchemy import text

# Working data that belongs to test runs. CASCADE also clears their lines and
# dependent rows; master data (users, items, suppliers) is not touched.
RESET_TABLES = (
    "stock_ledger", "stock_adjustment_items", "stock_adjustments",
    "stock_issue_items", "stock_issues",
    "notification_items", "notifications",
    "reservations", "requisition_items", "requisitions",
    "replenishment_items", "replenishments",
    "rfq_suppliers", "rfqs", "quotation_items", "quotations",
    "purchase_approval_items", "purchase_approvals",
    "purchase_order_items", "purchase_orders",
    "goods_receipt_items", "goods_receipts",
    "discrepancy_items", "discrepancy_reports",
    "acknowledgment_receipt_items", "acknowledgment_receipts",
    "transfer_slip_items", "transfer_slips",
    "supplier_return_items", "supplier_returns",
    "document_signatures", "audit_log", "document_sequences",
    "stock_balances", "item_location_settings",
)


def _rack(prefix, n):
    head = (prefix + " ") if prefix else ""
    return "%sRack %s - Row %d" % (head, "ABCD"[(n - 1) % 4], 1 + (n - 1) % 6)


def seed():
    locations = {r["loc_type"]: r for r in db.session.execute(text(
        "SELECT id, code, loc_type FROM locations WHERE is_active")).mappings().all()}
    if "warehouse" not in locations or "shop" not in locations:
        raise SystemExit("A warehouse and a shop must exist first (run database/seed_users.sql).")
    warehouse_id, shop_id = locations["warehouse"]["id"], locations["shop"]["id"]

    items = db.session.execute(text(
        "SELECT id, item_code, item_name FROM items WHERE is_active ORDER BY id")).mappings().all()
    if not items:
        raise SystemExit("No active items found (run database/seed_items.sql first).")

    owner = db.session.execute(text(
        "SELECT u.id FROM users u JOIN roles r ON r.id = u.role_id WHERE r.code = 'owner' LIMIT 1")).scalar()

    db.session.execute(text("TRUNCATE " + ", ".join(RESET_TABLES) + " RESTART IDENTITY CASCADE"))

    settings, balances, ledger = [], [], []
    low_warehouse, low_shop = [], []
    for n, item in enumerate(items, 1):
        rop = 8 + (n * 7) % 25
        max_level = rop + 30 + (n * 11) % 40
        lead = 3 + n % 8
        warehouse_qty = 40 + (n * 17) % 120
        shop_qty = max(1, rop + 6 + (n * 13) % 20)

        # A handful of real low-stock examples for the alert -> RF/REF flows.
        if n % 23 == 0:
            shop_qty = max(1, rop - 2)
        if n % 31 == 0:
            warehouse_qty = max(1, rop - 3)

        settings.append({"i": item["id"], "l": warehouse_id, "r": rop, "m": max_level,
                         "t": lead, "k": _rack("", n), "u": owner})
        settings.append({"i": item["id"], "l": shop_id, "r": rop, "m": max_level,
                         "t": lead, "k": _rack("Shop", n), "u": owner})
        balances.append({"i": item["id"], "l": warehouse_id, "q": warehouse_qty})
        balances.append({"i": item["id"], "l": shop_id, "q": shop_qty})
        ledger.append({"i": item["id"], "l": warehouse_id, "q": warehouse_qty,
                       "n": item["item_code"], "u": owner})
        ledger.append({"i": item["id"], "l": shop_id, "q": shop_qty,
                       "n": item["item_code"], "u": owner})
        if warehouse_qty <= rop:
            low_warehouse.append(item["id"])
        if shop_qty <= rop:
            low_shop.append(item["id"])

    db.session.execute(text(
        "INSERT INTO item_location_settings (item_id, location_id, rop, max_level, lead_time_days, "
        "rack, updated_by) VALUES (:i, :l, :r, :m, :t, :k, :u)"), settings)
    db.session.execute(text(
        "INSERT INTO stock_balances (item_id, location_id, on_hand, reserved) "
        "VALUES (:i, :l, :q, 0)"), balances)
    db.session.execute(text(
        "INSERT INTO stock_ledger (item_id, location_id, movement_type, qty_change, balance_after, "
        "ref_type, ref_no, remarks, created_by) "
        "VALUES (:i, :l, 'opening', :q, :q, 'OPEN', :n, 'Opening stock (demo)', :u)"), ledger)

    # One active alert per location, filled with the items that are below ROP.
    if low_warehouse:
        inv_ops.refresh_alerts(warehouse_id, low_warehouse)
    if low_shop:
        inv_ops.refresh_alerts(shop_id, low_shop)

    db.session.commit()
    return len(items), len(settings), len(balances), len(low_warehouse), len(low_shop)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="really reset and reseed")
    args = parser.parse_args()
    if not args.yes:
        print(__doc__)
        print("Nothing changed. Run again with --yes on a DEMO database.")
        return 1

    app = create_app()
    with app.app_context():
        try:
            items, settings, balances, low_wh, low_shop = seed()
        except Exception as e:
            db.session.rollback()
            print("Seeding failed: %s" % e)
            return 1
        print("Items with complete stock: %d" % items)
        print("Settings rows: %d (warehouse + shop for every item)" % settings)
        print("Stock balance rows: %d" % balances)
        print("Deliberate low-stock items: %d warehouse, %d shop" % (low_wh, low_shop))
        print("Demo data is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
