-- 010: tamper-evident e-signatures.
--
-- Every signature now records WHO signed (user, name, role, from the account, never typed),
-- a SHA-256 fingerprint of the document content at the moment of signing, and the IP address.
-- A signed line can never be replaced (enforced in the app and by the trigger below).
-- Safe to run again.

ALTER TABLE document_signatures ADD COLUMN IF NOT EXISTS signer_role VARCHAR(30);
ALTER TABLE document_signatures ADD COLUMN IF NOT EXISTS doc_hash    VARCHAR(64);
ALTER TABLE document_signatures ADD COLUMN IF NOT EXISTS ip_address  VARCHAR(45);

-- Signed lines are final: block UPDATE and DELETE at the database level.
CREATE OR REPLACE FUNCTION document_signatures_locked() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'A signed document line cannot be changed or removed.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_document_signatures_locked ON document_signatures;
CREATE TRIGGER trg_document_signatures_locked
    BEFORE UPDATE OR DELETE ON document_signatures
    FOR EACH ROW EXECUTE FUNCTION document_signatures_locked();

-- The RM and DR documents are no longer signed by warehouse staff. Old rows are kept as history.

INSERT INTO schema_migrations (filename)
VALUES ('010_signature_integrity.sql')
ON CONFLICT (filename) DO NOTHING;
