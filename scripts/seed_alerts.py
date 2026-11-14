"""Create the alert rows for stock that is already at or below ROP.

Inventory raises alerts when stock MOVES (a sale, an approved adjustment, a
reservation). Stock loaded straight with SQL -- seed_demo_stock.sql, or an
import from another system -- therefore has no alert rows, and the alert
screens look empty even though items are below ROP.

Run this once after such an import:

    python scripts/seed_alerts.py

It only reads stock and ROP, then calls the same refresh_alerts() the app uses.
Safe to run more than once: there is one active alert per type and location, and
it is closed when no item is low any more.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app import inventory_ops as ops
from app import inventory_service as inv


def main():
    app = create_app()
    with app.app_context():
        b = inv.backend()
        for loc in b.all("SELECT id, code, loc_type FROM locations WHERE is_active ORDER BY id"):
            item_ids = [r["item_id"] for r in b.all(
                "SELECT item_id FROM item_location_settings WHERE location_id = :l", l=loc["id"])]
            ops.refresh_alerts(loc["id"], item_ids)
            inv.commit()
            n = b.one("SELECT COUNT(*) AS n FROM notifications "
                      "WHERE location_id = :l AND status = 'active'", l=loc["id"])["n"]
            print("%s (%s): %d item(s) checked, %d active alert(s)"
                  % (loc["code"], loc["loc_type"], len(item_ids), n))


if __name__ == "__main__":
    main()
