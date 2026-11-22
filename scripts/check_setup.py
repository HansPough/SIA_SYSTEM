"""Check that the app can reach its database and that the schema is current.

Run:
    python scripts/check_setup.py

It prints what is missing and exits with code 1 when the database is not
usable. It never changes anything.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app import inventory_service as inv

REQUIRED_TABLES = [
    "purchase_approval_items",
    "roles", "users", "locations", "items", "item_location_settings",
    "stock_balances", "stock_ledger", "stock_issues", "stock_issue_items",
    "stock_adjustments", "stock_adjustment_items", "notifications",
    "notification_items", "requisitions", "requisition_items", "reservations",
    "suppliers", "supplier_items", "replenishments", "replenishment_items",
    "rfqs", "rfq_suppliers", "quotations", "quotation_items",
    "purchase_approvals", "purchase_orders", "purchase_order_items",
    "goods_receipts", "goods_receipt_items", "discrepancy_reports",
    "discrepancy_items", "transfer_slips", "transfer_slip_items",
    "acknowledgment_receipts", "acknowledgment_receipt_items",
    "document_sequences", "audit_log", "document_signatures",
    "supplier_returns", "supplier_return_items", "schema_migrations",
]

REQUIRED_COLUMNS = {
    "discrepancy_items": ["qty_replaced"],
    "acknowledgment_receipt_items": ["variance_resolved_at"],
    "purchase_orders": ["expected_date", "terms"],
    "document_signatures": ["signer_role", "doc_hash", "ip_address", "captured_by_user_id"],     # migrations 010, 011
    "transfer_slips": ["driver_paper_reason"],                                           # migration 011
    "transfer_slip_items": ["dispatch_note", "qty_needed"],                              # migrations 016, 017
    "goods_receipts": ["supplier_invoice_no", "delivered_by"],                           # migration 014
    "notifications": ["snoozed_until", "snoozed_by"],                                    # migration 013
    "purchase_approvals": ["batch_id"],                                                  # migration 012
    "replenishment_items": ["qty_dropped", "dropped_reason"],                            # migration 012
    "purchase_approval_items": ["approval_id", "replenishment_item_id", "item_id", "qty", "unit_price"],
}


# Column types the new code relies on (a same-named column of another type breaks queries).
EXPECTED_TYPES = {
    ("document_signatures", "captured_by_user_id"): "INTEGER",
    ("document_signatures", "signer_user_id"): "INTEGER",
    ("document_signatures", "doc_hash"): "VARCHAR",
    ("document_signatures", "signature_data"): "TEXT",
    ("users", "signature_data"): "TEXT",
    ("transfer_slips", "driver_paper_reason"): "VARCHAR",
    ("purchase_approvals", "batch_id"): "INTEGER",
    ("replenishment_items", "qty_dropped"): "INTEGER",
}


def main():
    app = create_app()
    problems = []
    try:
        with app.app_context():
            print("Database:", db.engine.url.render_as_string(hide_password=True))
            insp = inspect(db.engine)
            tables = set(insp.get_table_names())
            for table in REQUIRED_TABLES:
                if table not in tables:
                    problems.append("missing table: %s" % table)
            for table, columns in REQUIRED_COLUMNS.items():
                if table not in tables:
                    continue
                present = {c["name"] for c in insp.get_columns(table)}
                for column in columns:
                    if column not in present:
                        problems.append("missing column: %s.%s" % (table, column))
            for (table, column), want in EXPECTED_TYPES.items():
                if table in tables:
                    for c in insp.get_columns(table):
                        if c["name"] == column and want not in str(c["type"]).upper():
                            problems.append("wrong column type: %s.%s is %s, expected %s"
                                            % (table, column, c["type"], want))
            if "locations" in tables:
                location_columns = {c["name"] for c in insp.get_columns("locations")}
                if "is_quarantine" in location_columns:
                    print("Note: the removed locations.is_quarantine column still exists. "
                          "Run database/migrations/008_remove_quarantine.sql if you want it gone.")
            if "users" in tables:
                row = db.session.execute(text("SELECT COUNT(*) AS n FROM users")).mappings().first()
                print("Users:", row["n"])
                if row["n"] == 0:
                    problems.append("no users: run database/seed_users.sql or create an owner")
                else:
                    sample = db.session.execute(
                        text("SELECT username, password_hash FROM users ORDER BY id LIMIT 1")).mappings().first()
                    if sample and (not sample["password_hash"] or ":" not in sample["password_hash"]):
                        problems.append("user %s has no hashed password; reset it in Admin > Users" % sample["username"])
            if "items" in tables:
                print("Items:", db.session.execute(text("SELECT COUNT(*) AS n FROM items")).scalar())
            if "locations" in tables:
                print("Locations:", db.session.execute(text("SELECT COUNT(*) AS n FROM locations")).scalar())
            for table in ("document_signatures", "supplier_returns"):
                if table in tables:
                    print("%s rows:" % table,
                          db.session.execute(text("SELECT COUNT(*) AS n FROM " + table)).scalar())
    except Exception as e:
        print("Could not connect or read the schema:")
        print(" ", type(e).__name__ + ":", e)
        print("\nCheck DATABASE_URL in .env and that PostgreSQL is running.")
        return 1

    if problems:
        print("\nProblems found:")
        for problem in problems:
            print(" -", problem)
        print("\nFix: load database/schema.sql on an empty database, or run every file in "
              "database/migrations/ once (oldest first).")
        return 1
    print("\nSchema looks current. The app should start.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
