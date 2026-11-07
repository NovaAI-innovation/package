-- surrealdb/projection_write.surql
-- Spec: 03-surrealdb-document-retrieval.md §"Records and key rules", §"Access and writes"
--
-- Deterministic immutable write of one document revision and its chunks. Record IDs are
-- the canonical UUIDs computed by the worker, never database-assigned. Run with the
-- writer (Editor) identity.
--
-- Variables:
--   $revision_rid   full record id "document_revision:`<document_id>_<revision>`"
--   $revision_data  object with the full document_revision payload
--   $chunks         array of { rid: "chunk:`<chunk_uuid>`", data: { full chunk payload } }
--
-- Idempotency contract (enforced by the worker, not by this script): same key with the
-- same content is success and is skipped before this script runs; same key with different
-- content is a projection defect that must fail readiness. This script therefore only
-- ever creates records it has already verified to be absent.
--
-- TWO TRANSPORT DETAILS OF SURREALDB 3.x, both discovered by running the schema:
--
--   1. UUID-shaped JSON strings in bound variables are coerced to the `uuid` type, and
--      these record fields are TYPE string. Every UUID-valued field is therefore cast
--      explicitly with <string>.
--   2. `type::record('table', <string>$key)` does NOT round-trip a bound UUID-shaped
--      string: the parser truncates it at the first '.' and mis-parses the hyphens.
--      Building the full record id in the caller and binding it as $rid is the form that
--      reproduces the canonical key exactly.

LET $doc = <string>$revision_data.document_id;
LET $rev = $revision_data.revision;

CREATE $revision_rid CONTENT {
  workspace_id: <string>$revision_data.workspace_id,
  collection_id: <string>$revision_data.collection_id,
  document_id: $doc,
  revision: $rev,
  title: <string>$revision_data.title,
  body: <string>$revision_data.body,
  extracted_sha256: <string>$revision_data.extracted_sha256,
  manifest_sha256: <string>$revision_data.manifest_sha256,
  chunk_count: $revision_data.chunk_count,
  recipe_sha256: <string>$revision_data.recipe_sha256
};

FOR $c IN $chunks {
  CREATE $c.rid CONTENT {
    chunk_id: <string>$c.data.chunk_id,
    document_id: <string>$c.data.document_id,
    revision: $c.data.revision,
    ordinal: $c.data.ordinal,
    body: <string>$c.data.body,
    body_sha256: <string>$c.data.body_sha256,
    byte_start: $c.data.byte_start,
    byte_end: $c.data.byte_end,
    embedding: $c.data.embedding
  };
};

RETURN {
  written_revision: $revision_rid,
  written_chunks: array::len($chunks),
  observed_chunks: array::len(
    SELECT VALUE chunk_id FROM chunk WHERE document_id = $doc AND revision = $rev
  )
};
