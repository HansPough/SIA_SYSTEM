-- 012: order one REF from several suppliers.
--
-- Before: one approval = one supplier = one PO, and items the supplier did not quote were left off.
-- Now: each approval keeps its OWN list of lines (purchase_approval_items). Approvals submitted together
-- share a batch_id so the owner decides them on one screen. A REF line can be partly ordered, kept open
-- for a later supplier, or dropped with a reason (replenishment_items.qty_dropped / dropped_reason).
-- Safe to run again.

CREATE TABLE IF NOT EXISTS purchase_approval_items (
    id                    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    approval_id           INTEGER NOT NULL REFERENCES purchase_approvals(id) ON DELETE CASCADE,
    replenishment_item_id INTEGER NOT NULL REFERENCES replenishment_items(id),
    item_id               INTEGER NOT NULL REFERENCES items(id),
    qty                   INTEGER NOT NULL CHECK (qty > 0),
    unit_price            NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    UNIQUE (approval_id, replenishment_item_id)
);
CREATE INDEX IF NOT EXISTS ix_purchase_approval_items_approval ON purchase_approval_items (approval_id);

ALTER TABLE purchase_approvals  ADD COLUMN IF NOT EXISTS batch_id       INTEGER;
ALTER TABLE replenishment_items ADD COLUMN IF NOT EXISTS qty_dropped    INTEGER NOT NULL DEFAULT 0 CHECK (qty_dropped >= 0);
ALTER TABLE replenishment_items ADD COLUMN IF NOT EXISTS dropped_reason VARCHAR(200);

-- Stop with a clear message if a same-named column already exists with another type.
DO $$
DECLARE
    bad text;
BEGIN
    SELECT string_agg(c.table_name || '.' || c.column_name || ' is ' || c.data_type, '; ') INTO bad
    FROM information_schema.columns c
    JOIN (VALUES
        ('purchase_approvals',  'batch_id',       'integer'),
        ('replenishment_items', 'qty_dropped',    'integer'),
        ('replenishment_items', 'dropped_reason', 'character varying'),
        ('purchase_approval_items', 'qty',        'integer'),
        ('purchase_approval_items', 'unit_price', 'numeric')
    ) AS want(t, col, typ) ON want.t = c.table_name AND want.col = c.column_name
    WHERE c.table_schema = 'public' AND c.data_type <> want.typ;
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'Existing columns have the wrong type: %. Rename or drop them, then run this again.', bad;
    END IF;
END $$;

DO $$
BEGIN
    ALTER TABLE replenishment_items ADD CONSTRAINT ck_replenishment_items_dropped CHECK (qty_dropped <= qty_requested);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Approvals that are still waiting for the owner were created before this change and have no lines of
-- their own yet. Copy what they would have ordered, so the owner screen keeps working for them.
INSERT INTO purchase_approval_items (approval_id, replenishment_item_id, item_id, qty, unit_price)
SELECT pa.id, qi.replenishment_item_id, qi.item_id, qi.qty_offered, qi.unit_price
FROM purchase_approvals pa
JOIN quotation_items qi ON qi.quotation_id = pa.quotation_id
WHERE pa.decision = 'pending' AND pa.basis = 'quotation' AND qi.qty_offered > 0
  AND NOT EXISTS (SELECT 1 FROM purchase_approval_items x WHERE x.approval_id = pa.id);

INSERT INTO purchase_approval_items (approval_id, replenishment_item_id, item_id, qty, unit_price)
SELECT pa.id, ri.id, ri.item_id, ri.qty_requested,
       (SELECT si.agreed_price FROM supplier_items si
         WHERE si.supplier_id = pa.supplier_id AND si.item_id = ri.item_id
           AND si.valid_from <= CURRENT_DATE AND (si.valid_to IS NULL OR si.valid_to >= CURRENT_DATE)
         ORDER BY si.valid_from DESC LIMIT 1)
FROM purchase_approvals pa
JOIN replenishment_items ri ON ri.ref_id = pa.ref_id
WHERE pa.decision = 'pending' AND pa.basis = 'agreed_price'
  AND NOT EXISTS (SELECT 1 FROM purchase_approval_items x WHERE x.approval_id = pa.id)
  AND (SELECT si.agreed_price FROM supplier_items si
        WHERE si.supplier_id = pa.supplier_id AND si.item_id = ri.item_id
          AND si.valid_from <= CURRENT_DATE AND (si.valid_to IS NULL OR si.valid_to >= CURRENT_DATE)
        ORDER BY si.valid_from DESC LIMIT 1) IS NOT NULL;

INSERT INTO schema_migrations (filename)
VALUES ('012_split_ordering.sql')
ON CONFLICT (filename) DO NOTHING;
