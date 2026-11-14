"""Demo suppliers and agreed prices for every item (development only).

Creates four suppliers and gives the two REGULAR suppliers an agreed price for
every active item, so the "agreed price" path in Purchasing can be used without
an RFQ. The two non-regular suppliers are there to receive RFQs and send
quotations, so they intentionally have no agreed prices.

Run:
    python scripts/seed_demo_suppliers.py

The prices are made up. Do not use this on the real database: delete the demo
suppliers before go-live and let the owner/warehouse enter the real ones.
Safe to run again: suppliers are matched by name, prices are updated in place.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app import inventory_service as inv

# (name, contact, email, phone, address, regular, price factor)
SUPPLIERS = [
    ("Northern Tire Trading", "Ana Cruz", "ana@northerntire.example", "0917 555 0101",
     "Batangas City", True, 1.00),
    ("Metro Rubber Supply", "Ben Santos", "ben@metrorubber.example", "0918 555 0102",
     "Quezon City", True, 1.07),
    ("Highland Wheels Corp", "Carla Reyes", "carla@highlandwheels.example", "0919 555 0103",
     "Baguio City", False, 0),
    ("Prime Tubes & Flaps", "Dan Lim", "dan@primetubes.example", "0920 555 0104",
     "Cebu City", False, 0),
]

VALID_FROM = "2026-01-01"


def base_price(category, item_id):
    """Made-up but stable prices per category (Philippine pesos)."""
    if category == "tire":
        return 5200 + (item_id * 173) % 9800          # 5,200 - 14,999
    if category == "tube":
        return 320 + (item_id * 31) % 480             # 320 - 799
    if category == "flap":
        return 160 + (item_id * 19) % 240             # 160 - 399
    return 850 + (item_id * 61) % 3200                # 850 - 4,049 (rims, other)


def fill_prices(b, supplier_id, items, factor=1.00):
    for item in items:
        price = "%.2f" % (base_price(item["category"], item["id"]) * factor)
        b.execute(
            "INSERT INTO supplier_items (supplier_id, item_id, agreed_price, valid_from, valid_to) "
            "VALUES (:s, :i, :p, :f, NULL) ON CONFLICT (supplier_id, item_id, valid_from) "
            "DO UPDATE SET agreed_price = :p, valid_to = NULL",
            s=supplier_id, i=item["id"], p=price, f=VALID_FROM)


def main():
    app = create_app()
    with app.app_context():
        b = inv.backend()
        items = b.all("SELECT id, category FROM items WHERE is_active ORDER BY id")
        for name, contact, email, phone, address, regular, factor in SUPPLIERS:
            row = b.one("SELECT id FROM suppliers WHERE supplier_name = :n", n=name)
            if row is None:
                row = b.one(
                    "INSERT INTO suppliers (supplier_name, contact_person, email, phone, address, "
                    "is_regular, is_active) VALUES (:n, :c, :e, :p, :a, :r, TRUE) RETURNING id",
                    n=name, c=contact, e=email, p=phone, a=address, r=regular)
                action = "created"
            else:
                b.execute("UPDATE suppliers SET contact_person = :c, email = :e, phone = :p, "
                          "address = :a, is_regular = :r, is_active = TRUE WHERE id = :i",
                          c=contact, e=email, p=phone, a=address, r=regular, i=row["id"])
                action = "updated"
            supplier_id = row["id"]
            if not regular:
                print("%-24s %s (non-regular, no agreed prices)" % (name, action))
                continue
            fill_prices(b, supplier_id, items, factor)
            print("%-24s %s (%d agreed prices)" % (name, action, len(items)))

        # Any other regular supplier already in the database also gets a full price list.
        scripted = {s[0] for s in SUPPLIERS}
        for row in b.all("SELECT id, supplier_name FROM suppliers WHERE is_regular AND is_active ORDER BY id"):
            if row["supplier_name"] in scripted:
                continue
            fill_prices(b, row["id"], items)
            print("%-24s existing regular supplier (%d agreed prices)" % (row["supplier_name"], len(items)))

        inv.commit()
        print("Done. Suppliers: %d, agreed price rows: %d" % (
            b.one("SELECT COUNT(*) AS n FROM suppliers")["n"],
            b.one("SELECT COUNT(*) AS n FROM supplier_items")["n"]))


if __name__ == "__main__":
    main()
