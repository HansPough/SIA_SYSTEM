-- 003: backfill qty_replaced for discrepancy reports created before 002.
--
-- The auto-resolve feature was added after this database already had a report:
-- RM-2026-0002 rejected 20 units and RM-2026-0003 accepted the 20 replacement
-- units, but the report was resolved by hand. Recording those later accepted
-- units makes the report's "still outstanding" figure correct.
--
-- LEAST() keeps the backfill from exceeding the quantity on the report. A fresh
-- database has no reports, so this file does nothing there.

UPDATE discrepancy_items di
SET qty_replaced = LEAST(
    di.qty_short + di.qty_rejected,
    COALESCE((
        SELECT SUM(gri.qty_accepted)
        FROM goods_receipt_items gri
        JOIN goods_receipts gr ON gr.id = gri.rm_id
        JOIN discrepancy_reports dr ON dr.id = di.report_id
        WHERE gr.po_id = dr.po_id
          AND gr.id > dr.rm_id
          AND gri.po_item_id = di.po_item_id
    ), 0)
)
WHERE di.qty_replaced = 0;

INSERT INTO schema_migrations (filename)
VALUES ('003_backfill_discrepancy_replaced.sql')
ON CONFLICT (filename) DO NOTHING;
