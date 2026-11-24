-- 016: a short dispatch says why.
--
-- When the warehouse sends fewer units than the RF line still needs, that line
-- carries a reason. It prints in the Remarks column of the TS and of the AR, so
-- the shop sees the explanation without asking the warehouse.
--
-- Run once on an existing database:
--   psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/migrations/016_dispatch_note.sql

BEGIN;

ALTER TABLE transfer_slip_items
    ADD COLUMN IF NOT EXISTS dispatch_note VARCHAR(200);

COMMIT;
