-- ops_eligible_projections.sql — parameterized eligible projection query
-- Spec: 02-postgres-control-plane.md §"Eligible projection query"
--
-- Parameters, in order: caller user UUID, selected workspace UUID, profile UUID.
-- Returns the eligible revision-location pairs the router may search. The router then
-- intersects these with requested candidates and applies the cross-database contract's
-- final disclosure authorization (§"Consistency and evidence").
--
-- Implemented as a SQL function so the query has exactly one definition. The spec's
-- inline $1/$2/$3 form is reproduced verbatim as the function body, so the semantics
-- cannot drift from the specification text.

BEGIN;

CREATE OR REPLACE FUNCTION ops.eligible_projections(
  caller_user uuid,
  selected_workspace uuid,
  selected_profile uuid
)
RETURNS TABLE (
  route_id uuid,
  rank integer,
  backend control.backend,
  mode control.route_mode,
  collection_id uuid,
  location_id uuid,
  endpoint_ref text,
  physical_key text,
  document_id uuid,
  revision integer,
  manifest_object_key text,
  manifest_sha256 text,
  result_sha256 text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, control
AS $$
SELECT r.id AS route_id, r.rank, r.backend, p.mode, r.collection_id,
       l.id AS location_id, l.endpoint_ref, l.physical_key,
       d.id AS document_id, d.current_revision AS revision,
       v.manifest_object_key, v.manifest_sha256, j.result_sha256
FROM control.agent_profiles p
JOIN control.workspace_members m
  ON m.workspace_id = p.workspace_id AND m.user_id = caller_user AND m.active
JOIN control.profile_invokers i
  ON i.workspace_id = p.workspace_id AND i.profile_id = p.id
 AND i.user_id = m.user_id
JOIN control.profile_routes r
  ON r.workspace_id = p.workspace_id AND r.profile_id = p.id AND r.enabled
JOIN control.collections c
  ON c.workspace_id = r.workspace_id AND c.id = r.collection_id AND c.enabled
JOIN control.backend_locations l
  ON l.workspace_id = c.workspace_id AND l.collection_id = c.id
 AND l.backend = r.backend AND l.state = 'active'
JOIN control.documents d
  ON d.workspace_id = c.workspace_id AND d.collection_id = c.id
 AND d.deleted_at IS NULL
JOIN control.document_revisions v
  ON v.workspace_id = d.workspace_id AND v.collection_id = d.collection_id
 AND v.document_id = d.id AND v.revision = d.current_revision
JOIN control.projection_jobs j
  ON j.workspace_id = v.workspace_id AND j.collection_id = v.collection_id
 AND j.document_id = v.document_id AND j.revision = v.revision
 AND j.location_id = l.id AND j.action = 'index' AND j.state = 'ready'
WHERE p.workspace_id = selected_workspace AND p.id = selected_profile AND p.enabled
  AND (p.mode IN ('hybrid','fallback')
    OR (p.mode = 'document' AND r.backend = 'surreal')
    OR (p.mode = 'graph' AND r.backend = 'falkor'))
ORDER BY r.rank, d.id;
$$;

REVOKE ALL ON FUNCTION ops.eligible_projections(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.eligible_projections(uuid, uuid, uuid) TO cp_router;

COMMIT;
