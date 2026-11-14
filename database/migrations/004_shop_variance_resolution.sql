-- 004: shop-side delivery variance resolution.
--
-- When the shop accepts less than the transfer slip released, the rejected /
-- short units are returned to the warehouse and the RF line is reopened so the
-- balance can be sent again. This column records when a later AR filled the
-- RF line, so the earlier variance is shown as resolved instead of staying
-- open forever.
--
-- New databases get variance_resolved_at from schema.sql. Safe to run again.

ALTER TABLE acknowledgment_receipt_items
    ADD COLUMN IF NOT EXISTS variance_resolved_at TIMESTAMPTZ;

-- Old variances on RF lines that are already fully delivered count as resolved.
UPDATE acknowledgment_receipt_items ari
SET variance_resolved_at = CURRENT_TIMESTAMP
FROM transfer_slip_items tsi
JOIN requisition_items ri ON ri.id = tsi.rf_item_id
WHERE ari.ts_item_id = tsi.id
  AND ari.qty_received < ari.qty_expected
  AND ari.variance_resolved_at IS NULL
  AND ri.qty_delivered >= ri.qty_requested;

INSERT INTO schema_migrations (filename)
VALUES ('004_shop_variance_resolution.sql')
ON CONFLICT (filename) DO NOTHING;
