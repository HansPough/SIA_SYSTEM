-- 021: the owner approves the REF before any RFQ goes out.
--
-- New order: the warehouse manager signs and submits the REF -> the owner approves it ->
-- the warehouse manager sends the RFQ -> quotations -> the owner approves the supplier choice (PO).
--
-- * New REF status 'pending_owner' (waiting for the owner). 'submitted' now means
--   "the owner approved it, ready for RFQ". REFs already past 'draft' are left as they are:
--   they are treated as already approved, so nothing in flight is interrupted.
-- * Who decided, when, and what they wrote.
--
-- Run once on an existing database:
--   psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/migrations/021_owner_approves_ref.sql

BEGIN;

ALTER TABLE replenishments DROP CONSTRAINT IF EXISTS replenishments_status_check;
ALTER TABLE replenishments ADD CONSTRAINT replenishments_status_check
    CHECK (status IN ('draft', 'pending_owner', 'submitted', 'rfq_sent', 'for_approval',
                      'approved', 'receiving', 'closed', 'cancelled'));

ALTER TABLE replenishments ADD COLUMN IF NOT EXISTS owner_decided_by INTEGER REFERENCES users(id);
ALTER TABLE replenishments ADD COLUMN IF NOT EXISTS owner_decided_at TIMESTAMPTZ;
ALTER TABLE replenishments ADD COLUMN IF NOT EXISTS owner_remarks    TEXT;

COMMIT;
