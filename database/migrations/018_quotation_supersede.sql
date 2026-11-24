-- 018: a newer reply from the same supplier replaces their older line for that item.
--
-- When a second RFQ round asks a supplier about an item they already quoted, both
-- prices used to be offered on Choose suppliers, so a stale round-1 price could win
-- as "cheapest". A line re-quoted in a later round is now marked superseded and is no
-- longer offered. Lines for items the supplier did not re-quote stay valid.
--
-- Run once on an existing database:
--   psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/migrations/018_quotation_supersede.sql

BEGIN;

ALTER TABLE quotation_items
    ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;

COMMIT;
