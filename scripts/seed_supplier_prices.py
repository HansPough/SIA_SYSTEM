"""Demo agreed prices for every active supplier (development only).

The Suppliers and Record Quotation screens need prices to show. This script
gives every **existing** active supplier an agreed price for every active item,
using the same made-up base prices as `scripts/seed_demo_suppliers.py`. It does
not create suppliers and does not change `is_regular`, so the supplier list you
already have stays as it is.

Run:
    python scripts/seed_supplier_prices.py

The prices are fake but stable (one supplier is a little cheaper or dearer than
the next). Never use this on the real database: delete the demo prices before
go-live and let the owner/warehouse enter the real agreed prices. Safe to run
again: a price is updated in place for the same supplier, item and valid-from.

Why all suppliers and not only the regular ones: the Record Quotation screen
fills a supplier's unit prices from its agreed prices. A non-regular supplier
that sends a quotation can have a price list too; the "agreed price" approval
path still requires `is_regular`.
"""
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app import inventory_service as inv
from seed_demo_suppliers import base_price

VALID_FROM = "2026-01-01"


def price_for(category, item_id, supplier_id):
    """Made-up but stable price for one supplier; each supplier is 0-8% off the base."""
    factor = Decimal(100 + (supplier_id * 3) % 9) / Decimal(100)
    return (Decimal(base_price(category, item_id)) * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def main():
    app = create_app()
    with app.app_context():
        b = inv.backend()
        items = b.all("SELECT id, category FROM items WHERE is_active ORDER BY id")
        suppliers = b.all("SELECT id, supplier_name FROM suppliers WHERE is_active ORDER BY id")
        for supplier in suppliers:
            for item in items:
                b.execute(
                    "INSERT INTO supplier_items (supplier_id, item_id, agreed_price, valid_from, valid_to) "
                    "VALUES (:s, :i, :p, :f, NULL) ON CONFLICT (supplier_id, item_id, valid_from) "
                    "DO UPDATE SET agreed_price = :p, valid_to = NULL",
                    s=supplier["id"], i=item["id"],
                    p=str(price_for(item["category"], item["id"], supplier["id"])), f=VALID_FROM)
            print("%-34s %d agreed prices" % (supplier["supplier_name"], len(items)))

        inv.commit()
        print("Done. Suppliers: %d, agreed price rows: %d" % (
            b.one("SELECT COUNT(*) AS n FROM suppliers")["n"],
            b.one("SELECT COUNT(*) AS n FROM supplier_items")["n"]))


if __name__ == "__main__":
    main()
