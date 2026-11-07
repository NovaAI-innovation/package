-- surrealdb/generation_bootstrap.surql
-- Spec: 03-surrealdb-document-retrieval.md §"SurrealQL bootstrap"
--
-- Scope: ONE physical generation. Select the namespace and database first:
--   namespace = ws_<workspace_uuid_without_hyphens>
--   database  = c_<collection_uuid_without_hyphens>_g<generation>
-- The registry's backend_locations.physical_key stores "namespace/database".
--
-- Run with a provisioning (Owner) identity. Never run with the router's Viewer
-- credentials: this script defines tables and drops existing definitions in scope.
--
-- The statements are a verbatim copy of the specification block. Each must be executed
-- as its own request because SurrealDB 3.x rejects a batched text body that is not
-- prefixed with OPTION IMPORT (see README §"SurrealDB statement execution").

DEFINE TABLE document_revision SCHEMAFULL PERMISSIONS NONE;
DEFINE FIELD workspace_id ON document_revision TYPE string;
DEFINE FIELD collection_id ON document_revision TYPE string;
DEFINE FIELD document_id ON document_revision TYPE string;
DEFINE FIELD revision ON document_revision TYPE int ASSERT $value > 0;
DEFINE FIELD title ON document_revision TYPE string;
DEFINE FIELD body ON document_revision TYPE string;
DEFINE FIELD extracted_sha256 ON document_revision TYPE string;
DEFINE FIELD manifest_sha256 ON document_revision TYPE string;
DEFINE FIELD chunk_count ON document_revision TYPE int ASSERT $value >= 0;
DEFINE FIELD recipe_sha256 ON document_revision TYPE string;

DEFINE TABLE chunk SCHEMAFULL PERMISSIONS NONE;
DEFINE FIELD chunk_id ON chunk TYPE string;
DEFINE FIELD document_id ON chunk TYPE string;
DEFINE FIELD revision ON chunk TYPE int ASSERT $value > 0;
DEFINE FIELD ordinal ON chunk TYPE int ASSERT $value >= 0;
DEFINE FIELD body ON chunk TYPE string;
DEFINE FIELD body_sha256 ON chunk TYPE string;
DEFINE FIELD byte_start ON chunk TYPE int ASSERT $value >= 0;
DEFINE FIELD byte_end ON chunk TYPE int ASSERT $value > 0;
DEFINE FIELD page ON chunk TYPE option<int> ASSERT $value = NONE OR $value > 0;
DEFINE FIELD embedding ON chunk TYPE array<float, 1536> ASSERT array::len($value) = 1536;

DEFINE INDEX document_revision_key ON document_revision
  FIELDS document_id, revision UNIQUE;
DEFINE INDEX chunk_identity ON chunk FIELDS chunk_id UNIQUE;
DEFINE INDEX chunk_parent_order ON chunk
  FIELDS document_id, revision, ordinal UNIQUE;
DEFINE ANALYZER rag_terms TOKENIZERS class, punct FILTERS lowercase;
DEFINE INDEX chunk_lexical ON chunk FIELDS body
  FULLTEXT ANALYZER rag_terms BM25;
DEFINE INDEX chunk_vector ON chunk FIELDS embedding
  HNSW DIMENSION 1536 DIST COSINE TYPE F32;
