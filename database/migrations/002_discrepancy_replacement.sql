-- 002: units received on a later RM that fill a discrepancy line.
--
-- A rejected or short line is not closed when the supplier replaces the units
-- later. This column records how many of qty_short + qty_rejected have been
-- received on subsequent receiving memos, so the report can resolve itself
-- when the outstanding quantity reaches zero.
--
-- New databases get qty_replaced from schema.sql. This file is safe to run
-- again on a database that already has the column.

ALTER TABLE discrepancy_items
    ADD COLUMN IF NOT EXISTS qty_replaced INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_discrepancy_items_replaced') THEN
        ALTER TABLE discrepancy_items
            ADD CONSTRAINT ck_discrepancy_items_replaced CHECK (qty_replaced >= 0);
    END IF;
END $$;

INSERT INTO schema_migrations (filename)
VALUES ('002_discrepancy_replacement.sql')
ON CONFLICT (filename) DO NOTHING;
