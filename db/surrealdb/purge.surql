-- surrealdb/purge.surql
-- Spec: 03-surrealdb-document-retrieval.md §"Access and writes"
--   "Purge the chunks for the exact document and revision, then the revision record.
--    Verify both queries return zero matches once writes are fenced."
--
-- Variables: $document_id (string), $revision (int).
-- Order matters: chunks first, then the revision record, so a partially completed purge
-- never leaves chunks whose parent is already gone (which would break span validation).
--
-- UUID-shaped variables arrive as `uuid` in SurrealDB 3.x; cast to <string> to match the
-- TYPE string fields and compare correctly.

LET $doc = <string>$document_id;
LET $rev = $revision;

DELETE chunk WHERE document_id = $doc AND revision = $rev;
DELETE document_revision WHERE document_id = $doc AND revision = $rev;

RETURN {
  ok: true,
  document_id: $doc,
  revision: $rev,
  residual_chunks: array::len(SELECT VALUE chunk_id FROM chunk WHERE document_id = $doc AND revision = $rev),
  residual_revisions: array::len(SELECT VALUE document_id FROM document_revision WHERE document_id = $doc AND revision = $rev)
};
