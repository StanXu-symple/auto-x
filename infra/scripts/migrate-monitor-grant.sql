-- psql -v old_node=tc-1 -v new_node=hn-1 -v ON_ERROR_STOP=1
-- Back up service_auth_grants before running. Existing identities do not
-- re-import changed grants from Nacos; PostgreSQL is their authority.
BEGIN;
LOCK TABLE service_auth_grants IN SHARE ROW EXCLUSIVE MODE;
CREATE TEMP TABLE monitor_node_migration (source text, target text) ON COMMIT DROP;
INSERT INTO monitor_node_migration VALUES ('agent:' || :'old_node', 'agent:' || :'new_node');
DO $$
DECLARE
    source_scope text;
    target_scope text;
BEGIN
    IF (SELECT source = target FROM monitor_node_migration) THEN
        RAISE EXCEPTION 'Source and target must differ';
    END IF;
    SELECT scopes INTO source_scope FROM service_auth_grants
      WHERE client_id = 'monitor' AND audience = (SELECT source FROM monitor_node_migration);
    SELECT scopes INTO target_scope FROM service_auth_grants
      WHERE client_id = 'monitor' AND audience = (SELECT target FROM monitor_node_migration);
    IF source_scope IS NULL AND target_scope IS NULL THEN
        RAISE EXCEPTION 'No monitor grant exists for either node';
    END IF;
    IF source_scope IS NOT NULL AND target_scope IS NOT NULL AND source_scope <> target_scope THEN
        RAISE EXCEPTION 'Conflicting monitor scopes; refusing to overwrite';
    END IF;
END $$;
INSERT INTO service_auth_grants (client_id, audience, scopes, created_at, updated_at)
SELECT client_id, (SELECT target FROM monitor_node_migration), scopes, created_at, now()
FROM service_auth_grants
WHERE client_id = 'monitor' AND audience = (SELECT source FROM monitor_node_migration)
ON CONFLICT (client_id, audience) DO NOTHING;
DELETE FROM service_auth_grants
WHERE client_id = 'monitor' AND audience = (SELECT source FROM monitor_node_migration);
COMMIT;
