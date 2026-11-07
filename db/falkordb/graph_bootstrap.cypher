-- falkordb/graph_bootstrap.cypher
-- Spec: 04-falkordb-graphrag.md §"Indexes and constraints"
--
-- Scope: ONE graph key, kg:<collection_uuid>:g<generation>.
-- Run each statement SEPARATELY with GRAPH.QUERY — the runner does this for you.
-- Wait for exact-match indexes to become operational before creating uniqueness
-- constraints; constraints are asynchronous (PENDING -> UNDER CONSTRUCTION -> OPERATIONAL).
-- `ks deploy --target falkor` polls CALL db.constraints() until every constraint is
-- OPERATIONAL and fails the deployment otherwise.

CREATE INDEX FOR (n:Entity) ON (n.entity_id);
CREATE INDEX FOR (n:Assertion) ON (n.assertion_id);
CREATE INDEX FOR (n:Evidence) ON (n.evidence_id);
CREATE INDEX FOR (a:Assertion) ON (a.doc_revision_key);
CREATE INDEX FOR (e:Evidence) ON (e.doc_revision_key);
