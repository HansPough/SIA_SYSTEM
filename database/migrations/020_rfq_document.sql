-- 020: the RFQ becomes a real document (items, signature, PDF).
--
-- Until now an RFQ was only a row saying "sent to these suppliers", so there was nothing to
-- email, print or sign, and a later round could not say which items it asked about. This
-- migration adds:
--   * rfq_items: the items and quantities one RFQ asks about, frozen when the RFQ is created
--     (a later round lists only what was still open at that time).
--   * 'RFQ' as an allowed document type for signatures.
--
-- Existing RFQs are filled in best-effort: items the RFQ's suppliers actually quoted, or, when
-- nothing was quoted yet, every item of the REF. Check older multi-round RFQs by eye.
--
-- Run once on an existing database:
--   psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/migrations/020_rfq_document.sql

BEGIN;

CREATE TABLE IF NOT EXISTS rfq_items (
    rfq_id                INTEGER NOT NULL REFERENCES rfqs(id) ON DELETE CASCADE,
    replenishment_item_id INTEGER NOT NULL REFERENCES replenishment_items(id),
    item_id               INTEGER NOT NULL REFERENCES items(id),
    qty_requested         INTEGER NOT NULL CHECK (qty_requested > 0),
    PRIMARY KEY (rfq_id, replenishment_item_id)
);

CREATE INDEX IF NOT EXISTS ix_rfq_items_rfq ON rfq_items (rfq_id);

-- Backfill: what each existing RFQ got quotes for...
INSERT INTO rfq_items (rfq_id, replenishment_item_id, item_id, qty_requested)
SELECT DISTINCT q.rfq_id, qi.replenishment_item_id, qi.item_id, ri.qty_requested
FROM quotation_items qi
JOIN quotations q ON q.id = qi.quotation_id
JOIN replenishment_items ri ON ri.id = qi.replenishment_item_id
WHERE ri.qty_requested > 0
ON CONFLICT DO NOTHING;

-- ...and every item of the REF for an RFQ nobody has quoted yet.
INSERT INTO rfq_items (rfq_id, replenishment_item_id, item_id, qty_requested)
SELECT rq.id, ri.id, ri.item_id, ri.qty_requested
FROM rfqs rq
JOIN replenishment_items ri ON ri.ref_id = rq.ref_id
WHERE NOT EXISTS (SELECT 1 FROM rfq_items x WHERE x.rfq_id = rq.id)
  AND ri.qty_requested > 0
ON CONFLICT DO NOTHING;

-- Signatures: allow the RFQ document type.
ALTER TABLE document_signatures DROP CONSTRAINT IF EXISTS document_signatures_doc_type_check;
ALTER TABLE document_signatures ADD CONSTRAINT document_signatures_doc_type_check
    CHECK (doc_type IN ('RF', 'REF', 'RFQ', 'PO', 'TS', 'AR', 'RM', 'DR'));

COMMIT;
