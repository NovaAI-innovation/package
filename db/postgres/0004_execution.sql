-- 0004_execution.sql — conversations, messages, runs, steps, events
-- Spec: 02-postgres-control-plane.md §"Execution and audit DDL"

BEGIN;

CREATE TABLE IF NOT EXISTS control.conversations (
  workspace_id uuid REFERENCES control.workspaces(id),
  id uuid DEFAULT gen_random_uuid(),
  owner_user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS control.messages (
  workspace_id uuid,
  conversation_id uuid,
  seq bigint CHECK (seq > 0),
  role text NOT NULL CHECK (role IN ('user','assistant','tool','system')),
  body jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, conversation_id, seq),
  FOREIGN KEY (workspace_id, conversation_id) REFERENCES control.conversations
);

CREATE TABLE IF NOT EXISTS control.agent_runs (
  workspace_id uuid,
  id uuid DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL,
  profile_id uuid NOT NULL,
  profile_version integer NOT NULL,
  status text NOT NULL CHECK (status IN
    ('queued','running','succeeded','failed','cancelled')),
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, conversation_id) REFERENCES control.conversations,
  FOREIGN KEY (workspace_id, profile_id, profile_version)
    REFERENCES control.agent_versions
);
CREATE INDEX IF NOT EXISTS runs_by_conversation
  ON control.agent_runs(workspace_id, conversation_id, created_at);
CREATE INDEX IF NOT EXISTS runs_by_profile_version
  ON control.agent_runs(workspace_id, profile_id, profile_version);

CREATE TABLE IF NOT EXISTS control.run_steps (
  workspace_id uuid,
  run_id uuid,
  seq integer CHECK (seq > 0),
  kind text NOT NULL,
  status text NOT NULL,
  details jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, run_id, seq),
  FOREIGN KEY (workspace_id, run_id) REFERENCES control.agent_runs
);

CREATE TABLE IF NOT EXISTS control.run_events (
  workspace_id uuid,
  run_id uuid,
  event_id uuid DEFAULT gen_random_uuid(),
  kind text NOT NULL CHECK (kind IN ('retrieval','usage')),
  details jsonb NOT NULL CHECK (jsonb_typeof(details) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, run_id, event_id),
  FOREIGN KEY (workspace_id, run_id) REFERENCES control.agent_runs
);

COMMIT;
