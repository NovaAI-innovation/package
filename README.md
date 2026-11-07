# knowledge-schema 1.0.0

Deployable schema package for the AI-agents knowledge platform described by:

| Spec | Scope |
|---|---|
| `01-cross-database-contract.md` | identity, receipts, routing, ingestion |
| `02-postgres-control-plane.md` | Supabase PostgreSQL control plane |
| `03-surrealdb-document-retrieval.md` | SurrealDB document + vector retrieval |
| `04-falkordb-graphrag.md` | FalkorDB agentic GraphRAG |

Ordered, checksummed migrations for all three stores, a deployment runner, and an
acceptance harness whose every check is a command that could have failed.

## Layout

```
compose.yaml              Postgres 18.6 / SurrealDB 3.3.5 / FalkorDB 4.22.3
VERSIONS.json             pinned servers, client, embedding shape
db/postgres/0001..0009    control plane, invariants, grants, queue, eligible query
db/surrealdb/             generation bootstrap, projection write, purge, role grants
db/falkordb/              graph bootstrap, constraints, write + read templates
db/prelude/               disposable Supabase Auth stub (never against a real project)
ks/                       deploy runner, store clients, fixture, acceptance checks
tests/                    offline contract tests (no database required)
```

## Quickstart

```bash
cp .env.example .env            # one file configures client and containers
docker compose up -d --wait
python -m pip install -e .

ks deploy --target all --supabase-stub   # migrations, generation bootstrap, constraints
ks verify --provision                    # 35 acceptance checks across the four specs
```

## MCP

Start the MCP container and its database dependencies with
`docker compose up -d --build --wait mcp`. The service uses Streamable HTTP at
`http://127.0.0.1:8765/mcp` and talks to the databases over the Compose network.
Set `MCP_PORT` in `.env` to change the published port. The published endpoint is
local to the Docker host. For MCP clients that accept JSON configuration:

```json
{
  "mcpServers": {
    "knowledge-schema": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

For clients using stdio, the host command is still
`ks-mcp --env-file /absolute/path/to/package/.env`.
The server advertises two read-only tools with empty input schemas and compact JSON
responses. `status` reports reachability for PostgreSQL, SurrealDB's HTTP health
endpoint, and FalkorDB. `migration_plan` counts matching PostgreSQL migrations and
lists pending or drifted filenames. Apply migrations and provision stores with `ks`.
Knowledge retrieval requires a router that verifies the immutable artifacts and
reauthorizes evidence before returning it.

### Nginx and the domain

Run `docker compose up -d --build --wait nginx` to build and start the reverse proxy
in the same stack. The Nginx image includes its configuration so deployment does
not depend on the source checkout being mounted on the Docker host.
It serves `/mcp` for the `rag.psykonic.com` hostname and listens on loopback by default.
Test the local route with `curl --resolve rag.psykonic.com:80:127.0.0.1 -i http://rag.psykonic.com/mcp`.
An MCP response about the request method or Accept header confirms the route.

For public HTTPS, point the DNS record at your TLS ingress and forward requests
to this Nginx listener while preserving the `rag.psykonic.com` Host header.
Set `RAG_HTTP_BIND` and `RAG_HTTP_PORT` if the ingress needs a different reachable
interface or port. Use `https://rag.psykonic.com/mcp` in the MCP client once DNS and
TLS are live.

`--supabase-stub` creates `auth.users` and the three Supabase roles, which the control
plane references; without it `0002_registry` fails with `schema "auth" does not exist`.
It is for disposable instances only — never run it against a real Supabase project.

`ks deploy` is idempotent: each migration is recorded with its SHA-256 in
`ops.schema_migrations`, and each physical store records its own digest in
`ops.store_deployments`. A changed file is a changed checksum, not a silent re-apply.

`ks verify --provision` additionally creates a disposable workspace/collection/location
fixture and applies the generation schema to every registered location. Each run seeds a
new workspace, so the SurrealDB namespace and FalkorDB graph key never pre-exist: without
`--provision` the store-scoped checks report SKIP rather than blaming the schema for a
missing fixture. The 21 PostgreSQL and cross-database checks run either way.

## What the acceptance harness proves

35 checks, one per normative claim that can be executed:

| Prefix | Count | Covers |
|---|---|---|
| `XD-*` | 9 | UUIDv5 identity, RFC 8785 canonical JSON, 9-key receipts, manifests, graph namespacing, partial coverage, pointer advance |
| `PG-*` | 12 | 19-table bootstrap, browser-role isolation, five scoped service roles, append-only audit, invalid-state rejection, immutability triggers, eligible-query authorization, tombstone behaviour, single-writer lease claim/ack/expiry |
| `SD-*` | 6 | schemafull tables, 1536-dim cosine HNSW + BM25, schema rejection cases, retrieval + fusion + stale-revision exclusion, scoped purge, idempotent write |
| `FK-*` | 8 | six OPERATIONAL constraints, supporting exact-match indexes, constraint-spec integrity, UNIQUE/MANDATORY enforcement, idempotent projection, allowlisted multi-hop, independent contradictions, read-only surface |

Stores that are unreachable are reported as skipped, never as passed.

## Engine behaviours the package encodes

These are properties of the pinned servers that the specs' text does not state, and that
the templates and clients are written around. Each is covered by a check or a unit test.

**SurrealDB 3.x**

- `/sql` treats a JSON request body as a SurrealQL *object literal*, not a query
  document. Parameterised calls go through `/rpc` as
  `{"method":"query","params":[sql, vars]}` (`ks/stores/surreal.py`).
- UUID-shaped string parameters are coerced to the native `uuid` type, which a
  schemafull `TYPE string` field rejects. Templates cast with `<string>$var` and bind
  full record IDs as `$rid` (`db/surrealdb/projection_write.surql`).
- Multi-statement DDL fails without `OPTION IMPORT;`, so the bootstrap is split and sent
  one statement at a time.
- Concurrent writes can return "Write conflict, retry the transaction". The client retries
  that specific error with backoff; anything else surfaces unchanged.

**FalkorDB 4.22.x**

- MANDATORY constraints are evaluated when a node is created, and `MERGE` creates its
  placeholder before `ON CREATE SET` runs. A pattern carrying only the unique key fails
  with *mandatory constraint violation* instead of creating the node. Every mandatory
  property therefore belongs in the `MERGE` pattern, which also makes an identical retry
  a no-op and a rewriting retry a UNIQUE violation — the semantics the spec asks for.
- One Cypher statement per call; a template with a trailing `;` is normalised, and a
  second statement is treated as a template defect.
- Constraints return `PENDING` and must be polled to `OPERATIONAL`; deployment fails on
  `FAILED` or timeout rather than assuming uniqueness.
- `CALL db.indexes()` rows are `(label, properties, types, options, language, ...)`.

## Tests

```bash
python -m pytest tests -q          # 34 offline tests: identity, JCS, receipts, statements
ks verify --provision              # live acceptance against the three stores
```

The offline suite needs no database and runs in under a second, so a regression in the
deterministic layer is caught before an acceptance run.

## Configuration

See `.env.example`. The CLI and MCP read the package's `.env` by default.
Explicit client endpoints override the localhost defaults built from the published
ports. Keep the file private when it contains credentials.

Process environment variables override `.env` for both Compose and Python. An existing
PostgreSQL or SurrealDB volume retains the credentials initialized in that database.
Use the existing credentials or rotate them in the database before updating `.env`.

## Limits

- `VERSIONS.json` pins the servers and the 1536-float cosine embedding shape, not the
  embedding model. Bind the model revision in the location's `recipe_sha256` before
  production use.
- `db/prelude/supabase_stub.sql` exists so the control plane can be exercised outside a
  managed Supabase project. It creates `auth.users` and the three browser/service roles;
  it is a test fixture, not a deployment step.
- The specs describe a Supabase project as the control-plane home. This package was
  verified against a stock PostgreSQL 18.6 instance with the stub applied; migrating a
  real Supabase project is a separate gate.
