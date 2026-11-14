-- 011: the driver signs on the warehouse screen after loading.
--
-- The driver has no login, so the signature belongs to the driver (name taken from the
-- driver assigned to the TS) and the warehouse account that held the device is recorded in
-- captured_by_user_id. If the driver cannot sign on screen, the manager releases with a written
-- reason, kept in transfer_slips.driver_paper_reason. Safe to run again.

ALTER TABLE document_signatures ADD COLUMN IF NOT EXISTS captured_by_user_id INTEGER REFERENCES users(id);
ALTER TABLE transfer_slips      ADD COLUMN IF NOT EXISTS driver_paper_reason VARCHAR(300);

-- ADD COLUMN IF NOT EXISTS silently keeps a column that already exists, even with another type.
-- Stop here with a clear message instead of failing later inside the app.
DO $$
DECLARE
    bad text;
BEGIN
    SELECT string_agg(c.table_name || '.' || c.column_name || ' is ' || c.data_type, '; ') INTO bad
    FROM information_schema.columns c
    JOIN (VALUES
        ('document_signatures', 'signer_role',         'character varying'),
        ('document_signatures', 'doc_hash',            'character varying'),
        ('document_signatures', 'ip_address',          'character varying'),
        ('document_signatures', 'captured_by_user_id', 'integer'),
        ('transfer_slips',      'driver_paper_reason', 'character varying')
    ) AS want(t, col, typ) ON want.t = c.table_name AND want.col = c.column_name
    WHERE c.table_schema = 'public' AND c.data_type <> want.typ;
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'Existing columns have the wrong type: %. Rename or drop them, then run this again.', bad;
    END IF;
END $$;

INSERT INTO schema_migrations (filename)
VALUES ('011_driver_signature.sql')
ON CONFLICT (filename) DO NOTHING;
