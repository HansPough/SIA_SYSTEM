-- 009: drawn signatures stored on the user and snapshotted onto documents.
--
-- A manager/owner draws their signature once on My Signature. When they sign a
-- document, the drawing is copied into document_signatures so later changes to
-- the profile do not alter already-signed documents. Safe to run again.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS signature_data TEXT;

ALTER TABLE document_signatures
    ADD COLUMN IF NOT EXISTS signature_data TEXT;

INSERT INTO schema_migrations (filename)
VALUES ('009_drawn_signatures.sql')
ON CONFLICT (filename) DO NOTHING;
