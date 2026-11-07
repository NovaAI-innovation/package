-- falkordb/evidence_write.cypher
-- Spec: 04-falkordb-graphrag.md §"Projection writes" — "Create or validate each evidence
-- node by its deterministic key and then attach it with MERGE (a)-[:SUPPORTED_BY]->(e) in
-- the same broker stream."
--
-- Variables: $assertion_id, $evidence_id, $doc_revision_key, $document_id, $revision,
--            $chunk_id, $byte_start, $byte_end
--
-- Engine note: as in projection_write.cypher, the mandatory properties are part of the
-- MERGE pattern because MANDATORY is enforced when the node is created.

MATCH (a:Assertion {assertion_id: $assertion_id})
MERGE (e:Evidence {
  evidence_id: $evidence_id,
  doc_revision_key: $doc_revision_key,
  document_id: $document_id,
  revision: $revision,
  chunk_id: $chunk_id,
  byte_start: $byte_start,
  byte_end: $byte_end
})
MERGE (a)-[:SUPPORTED_BY]->(e)
RETURN a.assertion_id AS assertion_id, e.evidence_id AS evidence_id;
