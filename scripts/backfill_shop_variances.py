"""Return rejected/short shop deliveries to warehouse stock (one-off backfill).

Shop AR lines signed before migration 004 did not return the rejected/short
units to the warehouse: the units were deducted from warehouse stock at TS
release but never entered shop stock. Those lines also still look fully
reserved on the RF, so the balance cannot be sent again.

This script finds every short AR line that has no matching return ledger row,
puts the shortfall back into the sending warehouse, and reduces
`requisition_items.qty_reserved` by the same amount. It does NOT resolve the
variance; that happens automatically when the balance is delivered later.

Run once:
    python scripts/backfill_shop_variances.py

Safe to run again: a line that already has a `transfer_in` ledger row for its
AR is skipped.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app import inventory_service as inv
from app import inventory_ops as inv_ops

BACKFILL_NOTE = "Backfill: rejected/short units returned to warehouse"


def main():
    app = create_app()
    with app.app_context():
        b = inv.backend()
        owner = b.one("SELECT u.id FROM users u JOIN roles r ON r.id = u.role_id "
                      "WHERE r.code = 'owner' AND u.is_active ORDER BY u.id LIMIT 1")
        user_id = owner["id"] if owner else None
        rows = b.all(
            "SELECT ari.id AS ar_item_id, ari.ar_id, ari.item_id, ari.qty_expected, ari.qty_received, "
            "ar.ar_no, t.from_location_id, tsi.rf_item_id "
            "FROM acknowledgment_receipt_items ari "
            "JOIN acknowledgment_receipts ar ON ar.id = ari.ar_id "
            "JOIN transfer_slips t ON t.id = ar.ts_id "
            "JOIN transfer_slip_items tsi ON tsi.id = ari.ts_item_id "
            "WHERE ari.qty_received < ari.qty_expected ORDER BY ari.id")
        fixed = 0
        touched = {}
        for row in rows:
            already = b.one(
                "SELECT 1 AS x FROM stock_ledger WHERE ref_type = 'AR' AND ref_id = :a "
                "AND item_id = :i AND location_id = :l AND movement_type = 'transfer_in' LIMIT 1",
                a=row["ar_id"], i=row["item_id"], l=row["from_location_id"])
            if already:
                print("skip AR item %d (%s): already returned" % (row["ar_item_id"], row["ar_no"]))
                continue
            short = row["qty_expected"] - row["qty_received"]
            if short <= 0:
                continue
            inv.change_stock(row["item_id"], row["from_location_id"], short, "transfer_in",
                             ref_type="AR", ref_id=row["ar_id"], ref_no=row["ar_no"],
                             remarks=BACKFILL_NOTE, user_id=user_id)
            b.execute("UPDATE requisition_items SET qty_reserved = "
                      "CASE WHEN qty_reserved < :s THEN 0 ELSE qty_reserved - :s END "
                      "WHERE id = :i", s=short, i=row["rf_item_id"])
            inv.write_audit(user_id, "backfill", "acknowledgment_receipt_items", row["ar_item_id"],
                            new={"ar_no": row["ar_no"], "item_id": row["item_id"],
                                 "qty_returned": short, "reason": BACKFILL_NOTE})
            print("AR item %d (%s): returned %d unit(s) of item %d to warehouse"
                  % (row["ar_item_id"], row["ar_no"], short, row["item_id"]))
            touched.setdefault(row["from_location_id"], []).append(row["item_id"])
            fixed += 1
        for location_id, item_ids in touched.items():
            inv_ops.refresh_alerts(location_id, item_ids)
        inv.commit()
        print("Done. Lines fixed: %d" % fixed)


if __name__ == "__main__":
    main()
