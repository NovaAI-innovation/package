-- ops_claim_projection_job.sql — lease-based job claim
-- Spec: 02-postgres-control-plane.md §"Queue semantics"
--
-- Claim one eligible job inside a short transaction using a deterministic order over
-- availability and the primary key with FOR UPDATE SKIP LOCKED. Eligible rows are queued
-- and due, or leased and expired. Sets state to leased, increments attempts, issues a new
-- UUID token and a lease expiry, and returns the claim. Commit before backend I/O.
--
-- The caller must hold the row lock only for the duration of this statement; never sleep
-- while holding it. Renew/acknowledge/requeue are separate statements keyed by the full
-- primary key with state = 'leased', the exact token, and an unexpired lease.

BEGIN;

CREATE OR REPLACE FUNCTION ops.claim_projection_job(
  lease_seconds integer,
  worker_token uuid DEFAULT gen_random_uuid()
)
RETURNS TABLE (
  workspace_id uuid,
  collection_id uuid,
  document_id uuid,
  revision integer,
  location_id uuid,
  action text,
  attempt_count integer,
  lease_token uuid,
  lease_until timestamptz
)
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, control
AS $$
UPDATE control.projection_jobs j
   SET state = 'leased',
       attempt_count = j.attempt_count + 1,
       lease_token = worker_token,
       lease_until = now() + make_interval(secs => lease_seconds),
       updated_at = now()
 WHERE (j.workspace_id, j.collection_id, j.document_id, j.revision, j.location_id, j.action) = (
         SELECT c.workspace_id, c.collection_id, c.document_id, c.revision, c.location_id, c.action
           FROM control.projection_jobs c
          WHERE (c.state = 'queued' AND c.available_at <= now())
             OR (c.state = 'leased' AND c.lease_until < now())
          ORDER BY c.available_at, c.workspace_id, c.collection_id,
                   c.document_id, c.revision, c.location_id, c.action
          FOR UPDATE SKIP LOCKED
          LIMIT 1)
RETURNING j.workspace_id, j.collection_id, j.document_id, j.revision,
          j.location_id, j.action, j.attempt_count, j.lease_token, j.lease_until;
$$;

REVOKE ALL ON FUNCTION ops.claim_projection_job(integer, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.claim_projection_job(integer, uuid) TO cp_worker;

-- Acknowledge readiness. Only the holder of the current, unexpired lease may publish,
-- and readiness requires the receipt digest. Returns false when the predicate did not
-- match (stale token, expired lease, already acked) — the caller treats that as a reject.
CREATE OR REPLACE FUNCTION ops.ack_projection_job(
  job_workspace uuid,
  job_collection uuid,
  job_document uuid,
  job_revision integer,
  job_location uuid,
  job_action text,
  token uuid,
  receipt_sha256 text
)
RETURNS boolean
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, control
AS $$
WITH acked AS (
  UPDATE control.projection_jobs
     SET state = 'ready',
         result_sha256 = receipt_sha256,
         lease_token = NULL,
         lease_until = NULL,
         last_error_code = NULL,
         updated_at = now()
   WHERE workspace_id = job_workspace
     AND collection_id = job_collection
     AND document_id = job_document
     AND revision = job_revision
     AND location_id = job_location
     AND action = job_action
     AND state = 'leased'
     AND lease_token = token
     AND lease_until >= now()
  RETURNING 1
)
SELECT EXISTS (SELECT 1 FROM acked);
$$;

REVOKE ALL ON FUNCTION ops.ack_projection_job(uuid, uuid, uuid, integer, uuid, text, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.ack_projection_job(uuid, uuid, uuid, integer, uuid, text, uuid, text) TO cp_worker;

COMMIT;
