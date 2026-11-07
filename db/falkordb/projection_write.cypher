-- falkordb/projection_write.cypher
-- Spec: 04-falkordb-graphrag.md §"Projection writes"
--
-- MERGE by scalar identity under operational uniqueness constraints. Set immutable fields
-- only on create. The worker compares the complete payload and all endpoints on retries
-- before calling this template, and fails readiness if anything differs.
--
-- Variables: $subject_id, $object_id, $assertion_id, $document_id, $revision,
--            $doc_revision_key, $predicate, $polarity, $confidence_ppm
--
-- Engine note (FalkorDB 4.22.x): MANDATORY constraints are checked at node creation and
-- MERGE creates its placeholder before ON CREATE SET runs, so the mandatory properties
-- belong in the MERGE pattern itself. Putting them there also makes the template do what
-- the spec asks: an identical retry matches and changes nothing, while a retry that would
-- rewrite the assertion's identity or payload fails the UNIQUE constraint on
-- assertion_id instead of silently forking a second assertion.
--
-- Check an existing assertion's properties and endpoints BEFORE executing this template.
-- An absent endpoint is a failed write.

MATCH (s:Entity {entity_id: $subject_id}),
      (o:Entity {entity_id: $object_id})
MERGE (a:Assertion {
  assertion_id: $assertion_id,
  doc_revision_key: $doc_revision_key,
  document_id: $document_id,
  revision: $revision,
  predicate: $predicate,
  polarity: $polarity,
  confidence_ppm: $confidence_ppm
})
MERGE (s)-[:SUBJECT_OF]->(a)
MERGE (a)-[:OBJECT]->(o)
RETURN a.assertion_id AS assertion_id;
