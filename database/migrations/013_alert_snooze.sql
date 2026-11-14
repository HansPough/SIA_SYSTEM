-- 013: snooze an alert instead of dismissing it.
--
-- A snoozed alert stays OPEN (it is not closed or dismissed). It is only silent: it does not count in
-- the sidebar, the bell or the dashboard until the date passes, then it counts again by itself.
-- Safe to run again.

ALTER TABLE notifications ADD COLUMN IF NOT EXISTS snoozed_until DATE;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS snoozed_by    INTEGER REFERENCES users(id);

DO $$
DECLARE
    bad text;
BEGIN
    SELECT string_agg(c.table_name || '.' || c.column_name || ' is ' || c.data_type, '; ') INTO bad
    FROM information_schema.columns c
    JOIN (VALUES
        ('notifications', 'snoozed_until', 'date'),
        ('notifications', 'snoozed_by',    'integer')
    ) AS want(t, col, typ) ON want.t = c.table_name AND want.col = c.column_name
    WHERE c.table_schema = 'public' AND c.data_type <> want.typ;
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'Existing columns have the wrong type: %. Rename or drop them, then run this again.', bad;
    END IF;
END $$;

INSERT INTO schema_migrations (filename)
VALUES ('013_alert_snooze.sql')
ON CONFLICT (filename) DO NOTHING;
