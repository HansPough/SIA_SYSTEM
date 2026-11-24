-- 019: one supplier, one approval even when its lines mix quotation and agreed prices.
--
-- Choose suppliers used to create one approval per supplier AND basis, so a supplier who
-- quoted one item and sells another at their standing agreed price got two approvals and
-- two POs. The approval now has basis 'mixed' for those groups. A supplier whose lines came
-- from different quotation rounds also keeps one approval; its quotation_id is NULL because
-- no single quotation covers every line (purchase_approval_items holds the real lines).
--
-- Run once on an existing database:
--   psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/migrations/019_mixed_approval_basis.sql

BEGIN;

ALTER TABLE purchase_approvals DROP CONSTRAINT IF EXISTS purchase_approvals_basis_check;
ALTER TABLE purchase_approvals DROP CONSTRAINT IF EXISTS purchase_approvals_check;

ALTER TABLE purchase_approvals ADD CONSTRAINT purchase_approvals_basis_check
    CHECK (basis IN ('quotation', 'agreed_price', 'mixed'));

COMMIT;
