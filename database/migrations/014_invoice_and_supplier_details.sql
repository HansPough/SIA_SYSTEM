-- 014: real business details on two forms.
--
--  * Sales (stock out): the e-invoice number (stock_issues.document_ref) is required for a sale and must not
--    repeat. A voided sale frees its number. If duplicates already exist, the unique index is skipped with a
--    notice (the app still refuses new duplicates).
--  * Receiving from a supplier: the supplier's invoice number and who delivered the goods are recorded.
-- Safe to run again.

ALTER TABLE goods_receipts ADD COLUMN IF NOT EXISTS supplier_invoice_no VARCHAR(40);
ALTER TABLE goods_receipts ADD COLUMN IF NOT EXISTS delivered_by        VARCHAR(80);

DO $$
DECLARE
    bad text;
BEGIN
    SELECT string_agg(c.table_name || '.' || c.column_name || ' is ' || c.data_type, '; ') INTO bad
    FROM information_schema.columns c
    JOIN (VALUES
        ('goods_receipts', 'supplier_invoice_no', 'character varying'),
        ('goods_receipts', 'delivered_by',        'character varying')
    ) AS want(t, col, typ) ON want.t = c.table_name AND want.col = c.column_name
    WHERE c.table_schema = 'public' AND c.data_type <> want.typ;
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'Existing columns have the wrong type: %. Rename or drop them, then run this again.', bad;
    END IF;

    IF EXISTS (SELECT 1 FROM stock_issues WHERE document_ref IS NOT NULL AND status <> 'voided'
               GROUP BY LOWER(document_ref) HAVING COUNT(*) > 1) THEN
        RAISE NOTICE 'Duplicate e-invoice numbers already exist in stock_issues, so the unique index was NOT created. New duplicates are still refused by the app.';
    ELSE
        CREATE UNIQUE INDEX IF NOT EXISTS ux_stock_issues_einvoice
            ON stock_issues (LOWER(document_ref)) WHERE document_ref IS NOT NULL AND status <> 'voided';
    END IF;
END $$;

INSERT INTO schema_migrations (filename)
VALUES ('014_invoice_and_supplier_details.sql')
ON CONFLICT (filename) DO NOTHING;
