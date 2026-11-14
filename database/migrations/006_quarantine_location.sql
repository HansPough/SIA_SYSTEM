-- 006: quarantine location for rejected stock.
--
-- A warehouse can be marked as a quarantine location. When the shop rejects
-- units, they are returned there instead of to sellable warehouse stock.
-- Safe to run again.

ALTER TABLE locations
    ADD COLUMN IF NOT EXISTS is_quarantine BOOLEAN NOT NULL DEFAULT FALSE;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_locations_quarantine_warehouse') THEN
        ALTER TABLE locations
            ADD CONSTRAINT ck_locations_quarantine_warehouse
            CHECK (NOT is_quarantine OR loc_type = 'warehouse');
    END IF;
END $$;

INSERT INTO schema_migrations (filename)
VALUES ('006_quarantine_location.sql')
ON CONFLICT (filename) DO NOTHING;
