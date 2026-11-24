-- 017: one TS line is measured against what it was meant to deliver.
--
-- The Diff and the Variance on a TS/AR compare received against the RF total, so the
-- second delivery of a backorder (1 unit against a request of 5) used to read -4 and
-- "Yes (-4 pcs)" even though the line was complete. Each TS line now snapshots the
-- quantity the RF line still needed when the TS was prepared; totals for earlier
-- deliveries are not deducted twice.
--
-- Run once on an existing database:
--   psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/migrations/017_transfer_line_expected.sql

BEGIN;

ALTER TABLE transfer_slip_items
    ADD COLUMN IF NOT EXISTS qty_needed INTEGER;

-- Existing lines: requested total minus what earlier deliveries brought in (the
-- units accepted on this TS's own AR are part of qty_delivered, so add them back).
UPDATE transfer_slip_items ti
SET qty_needed = GREATEST(
        COALESCE(x.qty_requested, ti.qty_released)
        - COALESCE(x.qty_delivered, 0)
        + COALESCE((SELECT SUM(ari.qty_received) FROM acknowledgment_receipt_items ari
                    WHERE ari.ts_item_id = ti.id), 0),
        ti.qty_released)
FROM requisition_items x
WHERE x.id = ti.rf_item_id AND ti.qty_needed IS NULL;

COMMIT;
