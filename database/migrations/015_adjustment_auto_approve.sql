-- A manager's own stock adjustment is applied on save: no second person approves it.
-- Staff requests still wait for their manager (reviewed_by <> requested_by).
--
-- Run once on an existing database:
--   psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/migrations/015_adjustment_auto_approve.sql

BEGIN;

ALTER TABLE stock_adjustments
    ADD COLUMN IF NOT EXISTS auto_approved BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE stock_adjustments DROP CONSTRAINT IF EXISTS stock_adjustments_check;
ALTER TABLE stock_adjustments DROP CONSTRAINT IF EXISTS stock_adjustments_check1;

ALTER TABLE stock_adjustments
    ADD CONSTRAINT stock_adjustments_status_reviewed
    CHECK (status = 'pending' OR auto_approved OR reviewed_by IS NOT NULL);

ALTER TABLE stock_adjustments
    ADD CONSTRAINT stock_adjustments_not_own_review
    CHECK (reviewed_by IS NULL OR auto_approved OR reviewed_by <> requested_by);

COMMIT;
