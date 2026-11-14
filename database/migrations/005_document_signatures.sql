-- 005: e-signatures for printable documents.
--
-- One row per document and signature slot. Safe to run again.

CREATE TABLE IF NOT EXISTS document_signatures (
    id             INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    doc_type       VARCHAR(10) NOT NULL CHECK (doc_type IN ('RF', 'REF', 'PO', 'TS', 'AR', 'RM', 'DR')),
    doc_id         INTEGER NOT NULL,
    slot           VARCHAR(30) NOT NULL,
    signer_user_id INTEGER REFERENCES users(id),
    signer_name    VARCHAR(100) NOT NULL,
    remarks        VARCHAR(200),
    signed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (doc_type, doc_id, slot)
);

CREATE INDEX IF NOT EXISTS ix_document_signatures_doc
    ON document_signatures (doc_type, doc_id);

INSERT INTO schema_migrations (filename)
VALUES ('005_document_signatures.sql')
ON CONFLICT (filename) DO NOTHING;
