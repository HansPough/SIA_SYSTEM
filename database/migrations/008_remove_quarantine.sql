-- 008: remove the quarantine location flag.
--
-- The client does not use a quarantine location, so rejected shop units go
-- back to ordinary warehouse stock. This removes the column added by 006.
-- Safe to run again.

ALTER TABLE locations
    DROP CONSTRAINT IF EXISTS ck_locations_quarantine_warehouse;

ALTER TABLE locations
    DROP COLUMN IF EXISTS is_quarantine;

INSERT INTO schema_migrations (filename)
VALUES ('008_remove_quarantine.sql')
ON CONFLICT (filename) DO NOTHING;
