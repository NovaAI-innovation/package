-- 0007_grants.sql — role declarations, boundary revocation, and least-privilege grants
-- Spec: 02-postgres-control-plane.md §"Grants and service boundaries"
--
-- Idempotent on re-apply: roles are created only when absent and every grant is a no-op
-- when already held. Login identities and credentials are provisioned outside this schema
-- (the spec: "Provision separate login identities and credentials outside the schema"), so
-- every role here is NOLOGIN.

BEGIN;

DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['cp_router','cp_registry','cp_worker','cp_execution','cp_auditor'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN', r);
    END IF;
  END LOOP;
END $$;

-- The control schema is private. No browser or service role may reach it.
-- The Supabase roles (anon, authenticated, service_role) exist in a Supabase database. On a
-- plain PostgreSQL instance they do not, and there is nothing to revoke — so the revoke is
-- built from the roles that actually exist rather than hard-coded, which keeps this file
-- runnable on both a Supabase project and a disposable PostgreSQL 15+ instance.
DO $$
DECLARE
  grantee text;
  present text[];
BEGIN
  SELECT array_agg(rolname) INTO present
    FROM pg_roles
   WHERE rolname IN ('anon', 'authenticated', 'service_role');

  FOREACH grantee IN ARRAY (ARRAY['anon', 'authenticated', 'service_role'] || ARRAY['PUBLIC']) LOOP
    IF grantee = 'PUBLIC' OR grantee = ANY (present) THEN
      EXECUTE format('REVOKE ALL ON SCHEMA control FROM %s',
                     CASE WHEN grantee = 'PUBLIC' THEN 'PUBLIC' ELSE quote_ident(grantee) END);
      EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA control FROM %s',
                     CASE WHEN grantee = 'PUBLIC' THEN 'PUBLIC' ELSE quote_ident(grantee) END);
      EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA control FROM %s',
                     CASE WHEN grantee = 'PUBLIC' THEN 'PUBLIC' ELSE quote_ident(grantee) END);
    END IF;
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA control TO
  cp_router, cp_registry, cp_worker, cp_execution, cp_auditor;

-- Router: read-only. It resolves authority and eligibility; it never writes.
GRANT SELECT ON control.workspace_members, control.profile_invokers,
  control.agent_profiles, control.agent_versions, control.profile_routes,
  control.collections, control.backend_locations, control.documents,
  control.document_revisions, control.projection_jobs TO cp_router;

-- Worker: reads the revision it projects and moves its own job through the lease states.
GRANT SELECT ON control.backend_locations, control.documents,
  control.document_revisions, control.projection_jobs TO cp_worker;
GRANT UPDATE (state, attempt_count, available_at, lease_token, lease_until,
  result_sha256, last_error_code, updated_at)
  ON control.projection_jobs TO cp_worker;

-- Registry: the only writer of identity, routing and queue intent.
GRANT SELECT, INSERT, UPDATE ON control.user_profiles, control.workspaces,
  control.workspace_members, control.agents, control.agent_profiles,
  control.profile_routes, control.collections, control.backend_locations,
  control.documents, control.projection_jobs TO cp_registry;
GRANT SELECT, INSERT ON control.agent_versions, control.document_revisions,
  control.profile_invokers TO cp_registry;
GRANT DELETE ON control.profile_invokers TO cp_registry;

-- Execution: conversations and run records. Never registry, never readiness.
GRANT SELECT, INSERT ON control.conversations, control.messages,
  control.agent_runs, control.run_steps, control.run_events TO cp_execution;
GRANT UPDATE (status, finished_at) ON control.agent_runs TO cp_execution;
GRANT UPDATE (status, details) ON control.run_steps TO cp_execution;

-- Audit: append for the writers, read for the auditor. No UPDATE, no DELETE — anywhere.
GRANT INSERT ON control.audit_log TO cp_registry, cp_execution, cp_worker;
GRANT SELECT ON control.audit_log TO cp_auditor;

-- Future objects in `control` must not inherit PUBLIC access.
ALTER DEFAULT PRIVILEGES IN SCHEMA control REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA control REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA control REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

COMMIT;
