-- 0003_projection_jobs.sql — durable projection work queue
-- Spec: 02-postgres-control-plane.md §"Registry and routing DDL", §"Queue semantics"

BEGIN;

CREATE TABLE IF NOT EXISTS control.projection_jobs (
  workspace_id uuid,
  collection_id uuid,
  document_id uuid,
  revision integer,
  location_id uuid,
  action text CHECK (action IN ('index','purge')),
  state text NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued','leased','ready','failed','cancelled')),
  attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  available_at timestamptz NOT NULL DEFAULT now(),
  lease_token uuid,
  lease_until timestamptz,
  result_sha256 control.sha256,
  last_error_code text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, collection_id, document_id,
               revision, location_id, action),
  FOREIGN KEY (workspace_id, collection_id, document_id, revision)
    REFERENCES control.document_revisions,
  FOREIGN KEY (workspace_id, collection_id, location_id)
    REFERENCES control.backend_locations,
  -- A leased job always carries a token and an expiry; an unleased job carries neither.
  CHECK ((state = 'leased') = (lease_token IS NOT NULL)),
  CHECK ((state = 'leased') = (lease_until IS NOT NULL)),
  -- Readiness is only publishable with a receipt digest.
  CHECK ((state = 'ready') = (result_sha256 IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS projection_jobs_pending
  ON control.projection_jobs(available_at) WHERE state = 'queued';
CREATE INDEX IF NOT EXISTS projection_jobs_expiring
  ON control.projection_jobs(lease_until) WHERE state = 'leased';
CREATE INDEX IF NOT EXISTS projection_jobs_by_location
  ON control.projection_jobs(workspace_id, collection_id, location_id);

COMMIT;
