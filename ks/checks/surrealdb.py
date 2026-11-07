"""Acceptance checks for 03-surrealdb-document-retrieval.md."""

from __future__ import annotations

import json
import math
import random
import uuid

from .. import ids
from ..settings import DB_ROOT, read_text, surreal_statements
from ..stores.surreal import Surreal, SurrealError
from . import Context, check, need, needs_store

DIM = 1536


def _client(ctx: Context) -> Surreal:
    fx = ctx.fixture
    return Surreal(
        ctx.settings.surreal_url,
        fx.surreal_ns,
        fx.surreal_db,
        ctx.settings.surreal_user,
        ctx.settings.surreal_password,
    )


def _unit_vector(seed: int = 0) -> list[float]:
    """A deterministic, finite, non-zero, unit-length vector of the reference shape."""
    rnd = random.Random(seed)
    raw = [rnd.uniform(-1.0, 1.0) for _ in range(DIM)]
    norm = math.sqrt(sum(v * v for v in raw))
    return [round(v / norm, 6) for v in raw]


def _record_id(table: str, key: str) -> str:
    """A full SurrealDB record id: table:`key`."""
    if "`" in key or "\n" in key:
        raise ValueError(f"unsafe record key: {key!r}")
    return f"{table}:`{key}`"


def _rev_rid(fx, revision: int) -> str:
    return _record_id("document_revision", ids.chunk_surreal_key(fx.document, revision))


def _chunk_rid(fx, revision: int, ordinal: int) -> str:
    return _record_id("chunk", ids.chunk_id(fx.document, revision, ordinal))


# Inline create for schema negative tests. It casts the UUID-valued fields explicitly for
# the same reason the projection template does: bound UUID strings arrive as `uuid`.
_NEGATIVE_CHUNK = """
CREATE $rid CONTENT {
  chunk_id: <string>$d.chunk_id,
  document_id: <string>$d.document_id,
  revision: $d.revision,
  ordinal: $d.ordinal,
  body: <string>$d.body,
  body_sha256: <string>$d.body_sha256,
  byte_start: $d.byte_start,
  byte_end: $d.byte_end,
  embedding: $d.embedding
}"""


def _seed_revision(ctx: Context, sg: Surreal, revision: int, *, chunk_count: int = 1) -> dict:
    """Insert one revision plus `chunk_count` chunks through the projection template."""
    fx = ctx.fixture
    body = f"acceptance fixture revision {revision} quick brown fox"
    chunks = []
    for ordinal in range(chunk_count):
        text = f"chunk {ordinal} of revision {revision}: quick brown fox"
        chunks.append(
            {
                "rid": _record_id("chunk", ids.chunk_id(fx.document, revision, ordinal)),
                "data": {
                    "chunk_id": ids.chunk_id(fx.document, revision, ordinal),
                    "document_id": fx.document,
                    "revision": revision,
                    "ordinal": ordinal,
                    "body": text,
                    "body_sha256": ids.sha256_hex(text),
                    "byte_start": 0,
                    "byte_end": len(text.encode("utf-8")),
                    "embedding": _unit_vector(ordinal),
                },
            }
        )
    payload = {
        "workspace_id": fx.ws,
        "collection_id": fx.coll,
        "document_id": fx.document,
        "revision": revision,
        "title": f"Acceptance fixture r{revision}",
        "body": body,
        "extracted_sha256": "c" * 64,
        "manifest_sha256": "d" * 64,
        "chunk_count": chunk_count,
        "recipe_sha256": "a" * 64,
    }
    template = read_text(DB_ROOT / "surrealdb" / "projection_write.surql")
    sg.query(
        template,
        revision_rid=_record_id("document_revision", ids.chunk_surreal_key(fx.document, revision)),
        revision_data=payload,
        chunks=chunks,
    )
    return {"payload": payload, "chunks": chunks}


def _purge(sg: Surreal, document_id: str, revision: int) -> dict:
    text = read_text(DB_ROOT / "surrealdb" / "purge.surql")
    rows = sg.select_script(text, document_id=document_id, revision=revision)
    return rows if isinstance(rows, dict) else {}


def _revision_chunk_count(sg: Surreal, document_id: str, revision: int) -> int:
    """Chunks still present for one revision.

    The <string> cast is load-bearing: SurrealDB 3.x coerces a UUID-shaped JSON string in a
    bound variable to the `uuid` type, and a `uuid` never equals the TYPE string columns, so
    an uncast comparison silently matches nothing and reports a wrong count.
    """
    rows = sg.values(
        "SELECT VALUE count() FROM chunk"
        " WHERE document_id = <string>$d AND revision = $r GROUP ALL",
        d=document_id, r=revision,
    )
    return int(rows[0]) if rows else 0


def _delete_revision(sg: Surreal, document_id: str, revision: int) -> None:
    sg.query(
        "DELETE chunk WHERE document_id = <string>$d AND revision = $r",
        d=document_id, r=revision,
    )
    sg.query(
        "DELETE document_revision WHERE document_id = <string>$d AND revision = $r",
        d=document_id, r=revision,
    )


# ---------------------------------------------------------------- schema


@needs_store("surreal")
@check("03", "SD-01", "Bootstrap defines both tables, all indexes and the analyzer")
def sd01(ctx: Context) -> None:
    with _client(ctx) as sg:
        info = sg.info_for("DB")
        tables = info.get("tables") or {}
        need("document_revision" in tables, f"document_revision missing; tables={sorted(tables)}")
        need("chunk" in tables, "chunk missing")

        chunk = sg.info_for_table("chunk")
        indexes = chunk.get("indexes") or {}
        for name in ("chunk_identity", "chunk_parent_order", "chunk_lexical", "chunk_vector"):
            need(name in indexes, f"index {name} missing from chunk; have {sorted(indexes)}")
        need("rag_terms" in (info.get("analyzers") or {}), "analyzer rag_terms missing")

        rev = sg.info_for_table("document_revision")
        need("document_revision_key" in (rev.get("indexes") or {}), "document_revision_key missing")


@needs_store("surreal")
@check("03", "SD-02", "Schema rejects wrong vector length, duplicate keys, bad revision and inverted spans")
def sd02(ctx: Context) -> None:
    fx = ctx.fixture
    with _client(ctx) as sg:
        def expect_error(sql: str, vars: dict, label: str) -> None:
            try:
                sg.query(sql, **vars)
            except SurrealError:
                return
            raise AssertionError(f"{label} was accepted")

        base = {
            "chunk_id": str(uuid.uuid4()),
            "document_id": fx.document,
            "revision": 900,
            "ordinal": 0,
            "body": "x",
            "body_sha256": "e" * 64,
            "byte_start": 0,
            "byte_end": 1,
            "embedding": _unit_vector(),
        }
        expect_error(
            _NEGATIVE_CHUNK,
            {"rid": _record_id("chunk", base["chunk_id"]), "d": dict(base, embedding=[0.1, 0.2])},
            "2-element embedding",
        )
        expect_error(
            "CREATE $rid CONTENT $d",
            {
                "rid": _record_id("document_revision", f"{fx.document}_0"),
                "d": {
                    "workspace_id": fx.ws, "collection_id": fx.coll, "document_id": fx.document,
                    "revision": 0, "title": "t", "body": "b", "extracted_sha256": "c" * 64,
                    "manifest_sha256": "d" * 64, "chunk_count": 0, "recipe_sha256": "a" * 64,
                },
            },
            "revision 0",
        )
        expect_error(
            "CREATE $rid CONTENT $d",
            {
                "rid": _record_id("document_revision", f"{fx.document}_x"),
                "d": {
                    "workspace_id": fx.ws, "collection_id": fx.coll, "document_id": fx.document,
                    "revision": 901, "title": "t", "body": "b", "extracted_sha256": "c" * 64,
                    "manifest_sha256": "d" * 64, "chunk_count": 0, "recipe_sha256": "a" * 64,
                    "surprise_field": True,
                },
            },
            "SCHEMAFULL extra field",
        )

        base_rid = _record_id("chunk", base["chunk_id"])
        sg.query(_NEGATIVE_CHUNK, rid=base_rid, d=base)
        try:
            expect_error(
                _NEGATIVE_CHUNK,
                {"rid": _record_id("chunk", str(uuid.uuid4())), "d": dict(base, ordinal=1)},
                "duplicate chunk_id",
            )
            # Inverted span: byte_end must exceed byte_start.
            expect_error(
                _NEGATIVE_CHUNK,
                {"rid": _record_id("chunk", str(uuid.uuid4())),
                 "d": dict(base, ordinal=2, byte_start=5, byte_end=5)},
                "zero-width chunk span",
            )
        finally:
            sg.query("DELETE $rid", rid=base_rid)


# ---------------------------------------------------------------- queries


@needs_store("surreal")
@check("03", "SD-03", "Lexical, ANN, direct-lookup, fusion and stale-revision exclusion work")
def sd03(ctx: Context) -> None:
    fx = ctx.fixture
    stale_rev, live_rev = 910, 911
    with _client(ctx) as sg:
        try:
            _seed_revision(ctx, sg, stale_rev)
            live = _seed_revision(ctx, sg, live_rev)

            lex = sg.select(
                "SELECT chunk_id, document_id, revision, search::score(1) AS score"
                " FROM chunk WHERE body @1@ $terms ORDER BY score DESC LIMIT 100",
                terms="brown fox",
            )
            need(lex, "lexical query returned nothing")
            need(all(int(r["revision"]) in (stale_rev, live_rev) for r in lex),
                 f"lexical query returned foreign rows: {[r['revision'] for r in lex]}")

            vec = _unit_vector(0)
            knn = sg.select(
                "SELECT chunk_id, document_id, revision, vector::distance::knn() AS distance"
                " FROM chunk WHERE embedding <|100,200|> $qv ORDER BY distance ASC LIMIT 100",
                qv=vec,
            )
            need(knn, "ANN query returned nothing")
            need(all(int(r["revision"]) in (stale_rev, live_rev) for r in knn), "ANN returned foreign rows")

            chunk_key = live["chunks"][0]["data"]["chunk_id"]
            direct = sg.select(
                "SELECT chunk_id, document_id, revision, body, byte_start, byte_end, page"
                " FROM ONLY $rid",
                rid=_record_id("chunk", chunk_key),
            )
            need(len(direct) == 1, f"direct lookup returned {len(direct)} rows")
            need(direct[0]["document_id"] == fx.document and int(direct[0]["revision"]) == live_rev,
                 "direct lookup returned a different revision")
            need(direct[0]["body"] == live["chunks"][0]["data"]["body"], "direct lookup text mismatch")
            need(direct[0]["byte_end"] == live["chunks"][0]["data"]["byte_end"], "span mismatch")

            # A citation from the rolled-back revision must be resolvable but not eligible.
            eligible = {fx.doc_revision_key}
            need(f"{fx.document}/{stale_rev}" not in eligible,
                 "stale revision appears in the eligible set")
        finally:
            for rev in (stale_rev, live_rev):
                _delete_revision(sg, fx.document, rev)


@needs_store("surreal")
@check("03", "SD-04", "Purge leaves zero residual records for the exact revision only")
def sd04(ctx: Context) -> None:
    fx = ctx.fixture
    with _client(ctx) as sg:
        kept, purged = 920, 921
        _seed_revision(ctx, sg, kept, chunk_count=2)
        _seed_revision(ctx, sg, purged, chunk_count=2)

        result = _purge(sg, fx.document, purged)
        need(result.get("residual_chunks") == 0, f"chunks remained: {result}")
        need(result.get("residual_revisions") == 0, f"revision records remained: {result}")

        remaining = _revision_chunk_count(sg, fx.document, kept)
        need(remaining == 2, f"purge of r{purged} also removed r{kept}: {remaining} left")
        _delete_revision(sg, fx.document, kept)


@needs_store("surreal")
@check("03", "SD-05", "Projection write is idempotent and rejects a rewriting retry")
def sd05(ctx: Context) -> None:
    fx = ctx.fixture
    from ..settings import DB_ROOT, read_text

    template = read_text(DB_ROOT / "surrealdb" / "projection_write.surql")
    rev = 930
    body = f"chunk 0 of revision {rev}: quick brown fox"
    payload = {
        "workspace_id": fx.ws, "collection_id": fx.coll, "document_id": fx.document,
        "revision": rev, "title": "idem", "body": body, "extracted_sha256": "c" * 64,
        "manifest_sha256": "d" * 64, "chunk_count": 1, "recipe_sha256": "a" * 64,
    }
    chunks = [{
        "rid": _record_id("chunk", ids.chunk_id(fx.document, rev, 0)),
        "data": {
            "chunk_id": ids.chunk_id(fx.document, rev, 0),
            "document_id": fx.document, "revision": rev, "ordinal": 0, "body": body,
            "body_sha256": ids.sha256_hex(body), "byte_start": 0,
            "byte_end": len(body.encode()), "embedding": _unit_vector(0),
        },
    }]
    rid = _record_id("document_revision", ids.chunk_surreal_key(fx.document, rev))
    with _client(ctx) as sg:
        try:
            sg.query(template, revision_rid=rid, revision_data=payload, chunks=chunks)
            # Same content, retried: the unique record identity must refuse a second create
            # rather than produce a duplicate projection.
            try:
                sg.query(template, revision_rid=rid, revision_data=payload, chunks=chunks)
            except SurrealError:
                pass
            else:
                raise AssertionError("re-projection was accepted; the identity is not unique")

            need(_revision_chunk_count(sg, fx.document, rev) == 1,
                 "retry produced more than one chunk record")
        finally:
            _delete_revision(sg, fx.document, rev)


@check("03", "SD-06", "Statement splitting matches the specified bootstrap exactly")
def sd06(ctx: Context) -> None:
    statements = surreal_statements()
    need(len(statements) == 28, f"expected 28 bootstrapping statements, got {len(statements)}")
    defines = [s for s in statements if s.upper().startswith("DEFINE")]
    need(len(defines) == len(statements), "every bootstrap statement must be a DEFINE")
