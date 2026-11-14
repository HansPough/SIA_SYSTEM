-- 007: supplier returns / credit notes.
--
-- Goods sent back to a supplier against a purchase order. Confirming a return
-- takes the units out of warehouse stock and stores the credit amount.
-- Safe to run again.

CREATE TABLE IF NOT EXISTS supplier_returns (
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    return_no     VARCHAR(20) NOT NULL UNIQUE,
    po_id         INTEGER NOT NULL REFERENCES purchase_orders(id),
    supplier_id   INTEGER NOT NULL REFERENCES suppliers(id),
    rm_id         INTEGER REFERENCES goods_receipts(id),
    return_date   DATE NOT NULL DEFAULT CURRENT_DATE,
    reason        VARCHAR(300) NOT NULL,
    credit_amount NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (credit_amount >= 0),
    status        VARCHAR(10) NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft', 'confirmed', 'cancelled')),
    created_by    INTEGER NOT NULL REFERENCES users(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_by  INTEGER REFERENCES users(id),
    confirmed_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS supplier_return_items (
    id           INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    return_id    INTEGER NOT NULL REFERENCES supplier_returns(id) ON DELETE CASCADE,
    po_item_id   INTEGER NOT NULL REFERENCES purchase_order_items(id),
    item_id      INTEGER NOT NULL REFERENCES items(id),
    qty_returned INTEGER NOT NULL CHECK (qty_returned > 0),
    unit_price   NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (unit_price >= 0),
    UNIQUE (return_id, po_item_id)
);

CREATE INDEX IF NOT EXISTS ix_supplier_returns_status ON supplier_returns (status);
CREATE INDEX IF NOT EXISTS ix_supplier_return_items   ON supplier_return_items (return_id);

INSERT INTO schema_migrations (filename)
VALUES ('007_supplier_returns.sql')
ON CONFLICT (filename) DO NOTHING;
