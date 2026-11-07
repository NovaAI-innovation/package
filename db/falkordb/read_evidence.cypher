-- falkordb/read_evidence.cypher
-- Spec: 04-falkordb-graphrag.md §"Read templates"
--
-- Support lookup. Batch by a bounded list of assertion IDs and the same ready set, then
-- verify every hop: drop the full path if any assertion lacks surviving support.

MATCH (a:Assertion)-[:SUPPORTED_BY]->(e:Evidence)
WHERE a.assertion_id IN $assertion_ids
  AND a.doc_revision_key IN $eligible_keys
  AND e.doc_revision_key = a.doc_revision_key
RETURN a.assertion_id AS assertion_id, a.predicate AS predicate,
       a.polarity AS polarity, a.confidence_ppm AS confidence_ppm,
       e.document_id AS document_id, e.revision AS revision,
       e.chunk_id AS chunk_id, e.byte_start AS byte_start,
       e.byte_end AS byte_end;
