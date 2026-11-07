-- prelude/supabase_stub.sql  — DISPOSABLE INSTANCES AND TESTS ONLY
--
-- The control-plane bootstrap assumes a Supabase database: it references auth.users and
-- revokes from the anon/authenticated/service_role roles. Acceptance checks must run on a
-- disposable instance matching the release pins (README §"Release and migration
-- governance"), and a bare PostgreSQL 18 has none of those objects.
--
-- This file creates the minimum stub so the specification's schema and acceptance checks
-- can be exercised on a plain PostgreSQL 15+ instance. It is NOT a Supabase substitute:
--   * it is never applied by default — `ks deploy` requires an explicit flag
--   * it is not part of any migration chain and is not recorded in ops.schema_migrations
--   * the real project must be deployed as a Supabase migration, which is an
--     implementation gate stated in README.md
--
-- NEVER run this against a Supabase project.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    CREATE ROLE service_role NOLOGIN;
  END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.users (
  id uuid PRIMARY KEY,
  email text,
  created_at timestamptz NOT NULL DEFAULT now()
);
