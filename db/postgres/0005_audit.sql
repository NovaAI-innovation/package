-- 0005_audit.sql — append-only audit log
-- Spec: 02-postgres-control-plane.md §"Execution and audit DDL"
-- Append-only for runtime roles: no UPDATE/DELETE grant is issued in 0007_grants.sql.

BEGIN;

CREATE TABLE IF NOT EXISTS control.audit_log (
  workspace_id uuid REFERENCES control.workspaces(id),
  event_id uuid DEFAULT gen_random_uuid(),
  actor_kind text NOT NULL CHECK (actor_kind IN ('user','service')),
  actor_id text NOT NULL,
  action text NOT NULL,
  subject_id uuid NOT NULL,
  correlation_id uuid NOT NULL,
  details jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, event_id)
);
CREATE INDEX IF NOT EXISTS audit_by_workspace_time
  ON control.audit_log(workspace_id, created_at);

COMMIT;
