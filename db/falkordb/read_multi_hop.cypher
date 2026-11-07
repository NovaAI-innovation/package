-- falkordb/read_multi_hop.cypher
-- Spec: 04-falkordb-graphrag.md §"Read templates"
--
-- Read-only. Up to three directed hops, two alternating edges per hop. $eligible_keys is
-- sourced from the router's ready revision set for this graph. Every assertion in the path
-- must be allowlisted BEFORE the path is returned or ranked.
--
-- The literal form in the spec interpolates the vector-free allowlist via WHERE; keep
-- $eligible_keys a bounded parameter. If the authorized set exceeds the query budget,
-- narrow the request and report partial coverage.

MATCH p = (seed:Entity {entity_id: $seed_id})
          -[:SUBJECT_OF|OBJECT*2..6]->(target:Entity)
WHERE all(n IN nodes(p) WHERE
  'Entity' IN labels(n) OR
  ('Assertion' IN labels(n) AND n.doc_revision_key IN $eligible_keys))
RETURN [n IN nodes(p) | n.entity_id] AS entity_ids,
       [n IN nodes(p) | n.assertion_id] AS assertion_ids
LIMIT 50;
