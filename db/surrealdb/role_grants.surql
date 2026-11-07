-- surrealdb/role_grants.surql
-- Spec: 03-surrealdb-document-retrieval.md §"Access and writes"
--   "Use database-scoped system Viewer credentials for the router and database-scoped
--    Editor credentials for writers. Use an Owner only for provisioning and migration."
--
-- This file is a TEMPLATE. It is not run by `ks deploy`. Secrets must never live in a
-- repository, a deployment script, or the registry: the caller supplies passwords through
-- environment variables, and the broker maps endpoint_ref -> credential out of band.
--
-- Substitute before running, with an Owner identity against the target namespace/database:
--   {{ROUTER_PASSWORD}}  {{WRITER_PASSWORD}}
--
-- Define these ONCE per namespace (they are database-scoped users, so each generation
-- database gets its own pair). Re-running rotates the password.

-- Namespace-scoped is NOT enough: scope must include DATABASE so one collection's
-- credentials cannot read another collection's generation.
DEFINE USER ks_router ON DATABASE PASSWORD '{{ROUTER_PASSWORD}}' ROLES VIEWER;
DEFINE USER ks_writer ON DATABASE PASSWORD '{{WRITER_PASSWORD}}' ROLES EDITOR;

-- Verify with the real credentials, not with root (acceptance: cross-database denial).
-- INFO FOR USER ks_router;
-- INFO FOR USER ks_writer;
