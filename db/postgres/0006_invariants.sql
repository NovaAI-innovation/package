-- 0006_invariants.sql — additive integrity guards
-- Spec: 01-cross-database-contract.md §"Ownership and identifiers"
--       ("Each source revision is immutable after registration.")
-- Spec: 02-postgres-control-plane.md §"Grants and service boundaries"
--       ("The registry service ... validates monotonic revision allocation, state
--         transitions, location recipe immutability, and document identity immutability.")
--
-- The spec places that validation in the registry service. These triggers are additive
-- defence in depth: they close the window where a direct SQL writer (or a registry bug)
-- could rewrite identity or recipe fields, which would silently break the cross-database
-- contract while every foreign key still checked out. They do not restrict any transition
-- the spec permits: `state`, `current_revision`, `deleted_at` and job bookkeeping columns
-- all remain updatable.

BEGIN;

-- Document identity and its source binding are immutable; moving to another collection
-- creates a new document identity (contract: "Moving to another collection creates a new
-- document identity").
CREATE OR REPLACE FUNCTION control.documents_identity_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.workspace_id <> OLD.workspace_id
     OR NEW.collection_id <> OLD.collection_id
     OR NEW.id <> OLD.id
     OR NEW.source_key <> OLD.source_key THEN
    RAISE EXCEPTION 'control.documents identity is immutable (workspace_id, collection_id, id, source_key)'
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS documents_identity_immutable ON control.documents;
CREATE TRIGGER documents_identity_immutable
  BEFORE UPDATE ON control.documents
  FOR EACH ROW EXECUTE FUNCTION control.documents_identity_immutable();

-- A registered revision is frozen forever. A text or segmentation change is a new revision.
CREATE OR REPLACE FUNCTION control.document_revisions_frozen()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'control.document_revisions rows are immutable after registration; register a new revision'
    USING ERRCODE = 'restrict_violation';
END $$;

DROP TRIGGER IF EXISTS document_revisions_frozen ON control.document_revisions;
CREATE TRIGGER document_revisions_frozen
  BEFORE UPDATE ON control.document_revisions
  FOR EACH ROW EXECUTE FUNCTION control.document_revisions_frozen();

-- A location's generation, physical binding and recipe are immutable. Location setup is
-- serialized on the collection row and a new generation is a new location.
CREATE OR REPLACE FUNCTION control.backend_locations_binding_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.workspace_id <> OLD.workspace_id
     OR NEW.collection_id <> OLD.collection_id
     OR NEW.id <> OLD.id
     OR NEW.backend <> OLD.backend
     OR NEW.generation <> OLD.generation
     OR NEW.endpoint_ref <> OLD.endpoint_ref
     OR NEW.physical_key <> OLD.physical_key
     OR NEW.recipe_sha256 <> OLD.recipe_sha256 THEN
    RAISE EXCEPTION 'control.backend_locations binding is immutable; provision a new generation instead'
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS backend_locations_binding_immutable ON control.backend_locations;
CREATE TRIGGER backend_locations_binding_immutable
  BEFORE UPDATE ON control.backend_locations
  FOR EACH ROW EXECUTE FUNCTION control.backend_locations_binding_immutable();

-- The audit log is append-only for every role, not only for runtime roles.
CREATE OR REPLACE FUNCTION control.audit_log_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'control.audit_log is append-only'
    USING ERRCODE = 'restrict_violation';
END $$;

DROP TRIGGER IF EXISTS audit_log_append_only ON control.audit_log;
CREATE TRIGGER audit_log_append_only
  BEFORE UPDATE OR DELETE ON control.audit_log
  FOR EACH ROW EXECUTE FUNCTION control.audit_log_append_only();

COMMIT;
