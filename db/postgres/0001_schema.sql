-- 0001_schema.sql — schema, enumerations, domain
-- Spec: 02-postgres-control-plane.md §"Registry and routing DDL"
-- Applied once. Immutable after apply (recorded in ops.schema_migrations).

BEGIN;

CREATE SCHEMA IF NOT EXISTS control;
CREATE SCHEMA IF NOT EXISTS ops;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                 WHERE n.nspname = 'control' AND t.typname = 'backend') THEN
    CREATE TYPE control.backend AS ENUM ('surreal', 'falkor');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                 WHERE n.nspname = 'control' AND t.typname = 'route_mode') THEN
    CREATE TYPE control.route_mode AS ENUM ('document', 'graph', 'hybrid', 'fallback');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                 WHERE n.nspname = 'control' AND t.typname = 'sha256') THEN
    CREATE DOMAIN control.sha256 AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');
  END IF;
END $$;

-- Deployment ledger. Deliberately outside `control`: the spec schema is left exactly
-- as specified, and runtime roles never see deployment history.
CREATE TABLE IF NOT EXISTS ops.schema_migrations (
  target       text        NOT NULL,
  version      integer     NOT NULL,
  name         text        NOT NULL,
  sha256       text        NOT NULL,
  applied_at   timestamptz NOT NULL DEFAULT now(),
  duration_ms  integer     NOT NULL,
  PRIMARY KEY (target, version)
);

-- One row per physical store instantiation (SurrealDB database, FalkorDB graph key).
-- physical_key is the registry's physical_key value, so deployment history joins the registry.
CREATE TABLE IF NOT EXISTS ops.store_deployments (
  backend       text        NOT NULL CHECK (backend IN ('surreal', 'falkor')),
  physical_key  text        NOT NULL,
  script        text        NOT NULL,
  sha256        text        NOT NULL,
  applied_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (backend, physical_key, script)
);

REVOKE ALL ON SCHEMA ops FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA ops FROM PUBLIC;

COMMIT;
