-- 0002_registry.sql — workspace, agent, profile, route, collection, location, document
-- Spec: 02-postgres-control-plane.md §"Registry and routing DDL"

BEGIN;

CREATE TABLE IF NOT EXISTS control.user_profiles (
  user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  display_name text NOT NULL
);

CREATE TABLE IF NOT EXISTS control.workspaces (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS control.workspace_members (
  workspace_id uuid REFERENCES control.workspaces(id),
  user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('owner','admin','member')),
  active boolean NOT NULL DEFAULT true,
  PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX IF NOT EXISTS workspace_members_by_user ON control.workspace_members(user_id);

CREATE TABLE IF NOT EXISTS control.agents (
  workspace_id uuid REFERENCES control.workspaces(id),
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  slug text NOT NULL,
  PRIMARY KEY (workspace_id, id),
  UNIQUE (workspace_id, slug)
);

CREATE TABLE IF NOT EXISTS control.agent_profiles (
  workspace_id uuid,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  agent_id uuid NOT NULL,
  slug text NOT NULL,
  mode control.route_mode NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  PRIMARY KEY (workspace_id, id),
  UNIQUE (workspace_id, agent_id, slug),
  FOREIGN KEY (workspace_id, agent_id)
    REFERENCES control.agents(workspace_id, id)
);

CREATE TABLE IF NOT EXISTS control.agent_versions (
  workspace_id uuid,
  profile_id uuid,
  version integer CHECK (version > 0),
  config jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, profile_id, version),
  FOREIGN KEY (workspace_id, profile_id)
    REFERENCES control.agent_profiles(workspace_id, id)
);

CREATE TABLE IF NOT EXISTS control.profile_invokers (
  workspace_id uuid,
  profile_id uuid,
  user_id uuid,
  PRIMARY KEY (workspace_id, profile_id, user_id),
  FOREIGN KEY (workspace_id, profile_id)
    REFERENCES control.agent_profiles(workspace_id, id),
  FOREIGN KEY (workspace_id, user_id)
    REFERENCES control.workspace_members ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS profile_invokers_by_member
  ON control.profile_invokers(workspace_id, user_id);

CREATE TABLE IF NOT EXISTS control.collections (
  workspace_id uuid REFERENCES control.workspaces(id),
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  slug text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  PRIMARY KEY (workspace_id, id),
  UNIQUE (id),
  UNIQUE (workspace_id, slug)
);

CREATE TABLE IF NOT EXISTS control.profile_routes (
  workspace_id uuid,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  profile_id uuid NOT NULL,
  collection_id uuid NOT NULL,
  backend control.backend NOT NULL,
  rank integer NOT NULL CHECK (rank > 0),
  enabled boolean NOT NULL DEFAULT true,
  PRIMARY KEY (workspace_id, id),
  UNIQUE (workspace_id, profile_id, collection_id, backend),
  UNIQUE (workspace_id, profile_id, rank),
  FOREIGN KEY (workspace_id, profile_id)
    REFERENCES control.agent_profiles(workspace_id, id),
  FOREIGN KEY (workspace_id, collection_id)
    REFERENCES control.collections(workspace_id, id)
);
CREATE INDEX IF NOT EXISTS profile_routes_by_collection
  ON control.profile_routes(workspace_id, collection_id);

CREATE TABLE IF NOT EXISTS control.backend_locations (
  workspace_id uuid,
  collection_id uuid,
  id uuid DEFAULT gen_random_uuid(),
  backend control.backend NOT NULL,
  generation integer NOT NULL CHECK (generation > 0),
  endpoint_ref text NOT NULL,
  physical_key text NOT NULL,
  recipe_sha256 control.sha256 NOT NULL,
  state text NOT NULL CHECK (state IN ('building','active','retired')),
  PRIMARY KEY (workspace_id, collection_id, id),
  UNIQUE (id),
  UNIQUE (endpoint_ref, backend, physical_key),
  UNIQUE (workspace_id, collection_id, backend, generation),
  FOREIGN KEY (workspace_id, collection_id)
    REFERENCES control.collections(workspace_id, id)
);
-- At most one active physical location per (workspace, collection, backend).
CREATE UNIQUE INDEX IF NOT EXISTS one_active_backend_location
  ON control.backend_locations(workspace_id, collection_id, backend)
  WHERE state = 'active';

CREATE TABLE IF NOT EXISTS control.documents (
  workspace_id uuid,
  collection_id uuid,
  id uuid DEFAULT gen_random_uuid(),
  source_key text NOT NULL,
  current_revision integer CHECK (current_revision > 0),
  deleted_at timestamptz,
  PRIMARY KEY (workspace_id, collection_id, id),
  UNIQUE (id),
  UNIQUE (workspace_id, collection_id, source_key),
  FOREIGN KEY (workspace_id, collection_id)
    REFERENCES control.collections(workspace_id, id)
);

CREATE TABLE IF NOT EXISTS control.document_revisions (
  workspace_id uuid,
  collection_id uuid,
  document_id uuid,
  revision integer CHECK (revision > 0),
  title text NOT NULL,
  media_type text NOT NULL,
  source_object_key text NOT NULL,
  source_sha256 control.sha256 NOT NULL,
  extracted_object_key text NOT NULL,
  extracted_sha256 control.sha256 NOT NULL,
  manifest_object_key text NOT NULL,
  manifest_sha256 control.sha256 NOT NULL,
  parser_recipe text NOT NULL,
  chunk_count integer NOT NULL CHECK (chunk_count >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, collection_id, document_id, revision),
  FOREIGN KEY (workspace_id, collection_id, document_id)
    REFERENCES control.documents(workspace_id, collection_id, id)
);

-- Current pointer must reference a revision of the same document.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname = 'current_document_revision'
                   AND conrelid = 'control.documents'::regclass) THEN
    ALTER TABLE control.documents ADD CONSTRAINT current_document_revision
      FOREIGN KEY (workspace_id, collection_id, id, current_revision)
      REFERENCES control.document_revisions
        (workspace_id, collection_id, document_id, revision);
  END IF;
END $$;

COMMIT;
