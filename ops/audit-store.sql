-- Audit store for MySelfPay. Run once, as the database owner, inside the SEPARATE audit database.
-- Replace CHANGE_ME with a long random password. The app connects as audit_writer, which can add and read rows only.
-- Nobody, including the owner, can edit or delete rows: the triggers below refuse it.

CREATE TABLE IF NOT EXISTS audit_log(
    seq BIGINT PRIMARY KEY, ts DOUBLE PRECISION NOT NULL, request_id TEXT NOT NULL, actor_type TEXT NOT NULL, actor TEXT NOT NULL,
    scope TEXT NOT NULL, action TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL, result TEXT NOT NULL,
    old_value TEXT NOT NULL, new_value TEXT NOT NULL, detail TEXT NOT NULL, ip TEXT NOT NULL, user_agent TEXT NOT NULL,
    prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS audit_scope ON audit_log(scope, ts);
CREATE INDEX IF NOT EXISTS audit_actor ON audit_log(actor, ts);
CREATE INDEX IF NOT EXISTS audit_action ON audit_log(action, ts);

CREATE OR REPLACE FUNCTION audit_refuse_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit log is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_no_update_delete ON audit_log;
CREATE TRIGGER audit_no_update_delete BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_refuse_change();
DROP TRIGGER IF EXISTS audit_no_truncate ON audit_log;
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_refuse_change();

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_writer') THEN
        CREATE ROLE audit_writer LOGIN PASSWORD 'CHANGE_ME';
    END IF;
END $$;
REVOKE ALL ON audit_log FROM PUBLIC;
GRANT SELECT, INSERT ON audit_log TO audit_writer;
