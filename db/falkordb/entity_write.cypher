-- falkordb/entity_write.cypher
-- Spec: 04-falkordb-graphrag.md §"Projection writes" — entity resolution from the frozen
-- artifact, before any assertion is written.
--
-- Variables: $entity_id, $kind, $canonical_key
--
-- Engine note (FalkorDB 4.22.x): MANDATORY is evaluated when a node is created, and MERGE
-- creates the placeholder node before ON CREATE SET runs. A pattern carrying only the
-- unique key therefore fails with "mandatory constraint violation" rather than creating
-- the node, so every mandatory property is part of the pattern. An identical retry matches
-- the existing node, while a retry resolving the same entity_id to a different kind or
-- canonical_key does not match and is refused by the UNIQUE constraint on entity_id.

MERGE (e:Entity {
  entity_id: $entity_id,
  kind: $kind,
  canonical_key: $canonical_key
})
RETURN e.entity_id AS entity_id;
