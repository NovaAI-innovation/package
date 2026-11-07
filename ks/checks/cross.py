"""Acceptance checks for 01-cross-database-contract.md.

The contract is mostly about *decisions* (identity, canonical encodings, authorization
points). Those become testable in two ways: pure functions with fixed expected outputs, and
cross-store assertions that both physical stores agree with the registry.
"""

from __future__ import annotations

import uuid

from .. import ids
from . import Context, check, need

# Fixed namespaces so expected values are stable across runs and across machines.
WS = "11111111-1111-4111-8111-111111111111"
COLL = "22222222-2222-4222-8222-222222222222"
DOC = "33333333-3333-4333-8333-333333333333"
LOC = "44444444-4444-4444-8444-444444444444"


@check("01", "XD-01", "UUIDv5 chunk identity is stable and follows the documented name")
def xd01(ctx: Context) -> None:
    a = ids.chunk_id(DOC, 1, 0)
    b = ids.chunk_id(DOC, 1, 0)
    need(a == b, "chunk identity is not deterministic")
    need(ids.is_canonical_uuid(a), f"chunk id is not a canonical UUID: {a}")
    # The name is `chunk/<revision>/<ordinal>` under the document UUID's namespace.
    expected = str(uuid.uuid5(uuid.UUID(DOC), "chunk/1/0"))
    need(a == expected, f"chunk id {a} != uuid5(document, 'chunk/1/0') {expected}")
    # Ordinal and revision both move the identity.
    need(ids.chunk_id(DOC, 1, 1) != a, "ordinal does not affect identity")
    need(ids.chunk_id(DOC, 2, 0) != a, "revision does not affect identity")
    # No leading zeroes anywhere in the name.
    need(str(uuid.uuid5(uuid.UUID(DOC), "chunk/01/0")) != a, "leading zeroes were accepted")


@check("01", "XD-02", "Canonical JSON is RFC 8785 for the values the contract emits")
def xd02(ctx: Context) -> None:
    need(ids.canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}', "key ordering")
    need(ids.canonical_json({"n": 1}) == '{"n":1}', "integer form")
    need(ids.canonical_json({"s": "a/b"}) == '{"s":"a/b"}', "slash must not be escaped")
    need(ids.canonical_json({"s": "é"}) == '{"s":"é"}', "unicode must be emitted directly")
    need(ids.canonical_json({"s": "\u0001"}) == '{"s":"\\u0001"}', "control char escaping")
    need(ids.canonical_json([]) == "[]", "empty array")
    need(ids.sha256_hex("[]") == ids.EMPTY_PAYLOAD_SHA256, "empty-payload digest")
    # UTF-16 code-unit ordering: U+1F600 is D83D DE00 and U+FF61 is FF61, so the emoji's
    # leading surrogate sorts it *before* the halfwidth katakana. UTF-8 byte order is the
    # opposite, which is exactly what distinguishes RFC 8785 from naive ordering.
    ordered = ids.canonical_json({"\uff61": 1, "\U0001f600": 2})
    need(ordered.index("\U0001f600") < ordered.index("\uff61"),
         f"keys must sort by UTF-16 code units, got {ordered}")


@check("01", "XD-03", "Projection receipt is a SHA-256 of the 9 documented keys, nothing else")
def xd03(ctx: Context) -> None:
    kwargs = dict(
        workspace_id=WS, collection_id=COLL, document_id=DOC, revision=1,
        location_id=LOC, recipe_sha256="a" * 64, manifest_sha256="b" * 64,
        action="index", payload_sha256=ids.index_payload_sha256([]),
    )
    receipt = ids.projection_receipt(**kwargs)
    need(ids.is_sha256_hex(receipt), f"receipt is not a sha256 digest: {receipt}")
    need(receipt == ids.projection_receipt(**kwargs), "receipt is not deterministic")

    expected_body = ids.canonical_json({
        "workspace_id": WS, "collection_id": COLL, "document_id": DOC, "revision": 1,
        "location_id": LOC, "recipe_sha256": "a" * 64, "manifest_sha256": "b" * 64,
        "action": "index", "payload_sha256": ids.EMPTY_PAYLOAD_SHA256,
    })
    need(receipt == ids.sha256_hex(expected_body), "receipt inputs differ from the documented object")

    for field in ("collection_id", "revision", "location_id", "payload_sha256", "action"):
        changed = dict(kwargs, **{field: kwargs[field] + 1 if field == "revision" else
                                  ("c" * 64 if field == "payload_sha256" else
                                   ("purge" if field == "action" else str(uuid.uuid4())))})
        need(ids.projection_receipt(**changed) != receipt, f"{field} does not affect the receipt")

    purge = dict(kwargs, action="purge", payload_sha256=ids.EMPTY_PAYLOAD_SHA256)
    need(ids.projection_receipt(**purge) != receipt, "purge and index collided")


@check("01", "XD-04", "Projection manifest sorts by identity so write order cannot change the digest")
def xd04(ctx: Context) -> None:
    a = {"identity": ids.chunk_id(DOC, 1, 0), "sha256": "a" * 64}
    b = {"identity": ids.chunk_id(DOC, 1, 1), "sha256": "b" * 64}
    need(ids.index_payload_sha256([a, b]) == ids.index_payload_sha256([b, a]),
         "manifest digest depends on input order")
    reversed_bodies = {"identity": b["identity"], "sha256": "c" * 64}
    need(ids.index_payload_sha256([a, b]) != ids.index_payload_sha256([a, reversed_bodies]),
         "manifest digest ignores content")


@check("01", "XD-05", "Graph identity is collection-namespaced and stable across generations")
def xd05(ctx: Context) -> None:
    e1 = ids.entity_id(COLL, "PERSON", "ada-lovelace")
    need(e1 == ids.entity_id(COLL, "PERSON", "ada-lovelace"), "entity id is not deterministic")
    need(e1 != ids.entity_id(COLL, "ORG", "ada-lovelace"), "kind does not affect entity id")
    need(e1 != ids.entity_id(WS, "PERSON", "ada-lovelace"), "collection namespace not applied")

    a = ids.assertion_id(COLL, "a" * 64, DOC, 1, e1, "WROTE", e1, "positive")
    need(a == ids.assertion_id(COLL, "a" * 64, DOC, 1, e1, "WROTE", e1, "positive"), "assertion unstable")
    need(a != ids.assertion_id(COLL, "a" * 64, DOC, 1, e1, "WROTE", e1, "negative"),
         "polarity does not affect assertion id")
    need(a != ids.assertion_id(COLL, "a" * 64, DOC, 2, e1, "WROTE", e1, "positive"),
         "revision does not affect assertion id")

    ev = ids.evidence_id(COLL, DOC, 1, ids.chunk_id(DOC, 1, 0), 0, 10)
    need(ev != ids.evidence_id(COLL, DOC, 1, ids.chunk_id(DOC, 1, 0), 0, 11), "span end not covered")
    try:
        ids.evidence_id(COLL, DOC, 1, ids.chunk_id(DOC, 1, 0), 10, 10)
    except ValueError:
        pass
    else:
        raise AssertionError("zero-width evidence span was accepted")


@check("01", "XD-06", "Physical keys are derivable from the registry and round-trip")
def xd06(ctx: Context) -> None:
    key = ids.surreal_physical_key(WS, COLL, 3)
    need(key == f"ws_{WS.replace('-', '')}/c_{COLL.replace('-', '')}_g3", f"surreal key shape: {key}")
    need(ids.split_physical_key("surreal", key) == (
        f"ws_{WS.replace('-', '')}", f"c_{COLL.replace('-', '')}_g3"), "surreal key does not round-trip")

    gkey = ids.falkor_graph_key(COLL, 3)
    need(gkey == f"kg:{COLL}:g3", f"falkor key shape: {gkey}")
    need(ids.split_physical_key("falkor", gkey) == (COLL, "g3"), "falkor key does not round-trip")

    for bad in ("ws/c", "ws_x/g1", "kg:c", f"kg:{COLL}", f"kg:{COLL}:g3:extra"):
        try:
            ids.split_physical_key("surreal" if bad.startswith("ws") else "falkor", bad)
        except ValueError:
            continue
        raise AssertionError(f"malformed physical key accepted: {bad}")


@check("01", "XD-07", "The two profiles sharing a collection resolve the same projection identities")
def xd07(ctx: Context) -> None:
    fx = ctx.fixture
    hybrid = ctx.pg.eligible(fx.user, fx.ws, fx.profile_hybrid)
    document = ctx.pg.eligible(fx.user, fx.ws, fx.profile_document)
    need(hybrid and document, "both profiles should see the ready surreal projection")
    need(
        (str(hybrid[0]["document_id"]), int(hybrid[0]["revision"]), str(hybrid[0]["location_id"]))
        == (str(document[0]["document_id"]), int(document[0]["revision"]), str(document[0]["location_id"])),
        "shared collection produced different identities per profile",
    )


@check("01", "XD-08", "Partial coverage is reported rather than silently narrowed")
def xd08(ctx: Context) -> None:
    fx = ctx.fixture
    rows = ctx.pg.eligible(fx.user, fx.ws, fx.profile_hybrid)
    backends = {r["backend"] for r in rows}
    need(backends == {"surreal"}, f"expected surreal-only coverage while falkor builds, saw {backends}")

    routes = ctx.pg.maintenance(
        "SELECT backend FROM control.profile_routes"
        " WHERE workspace_id=%s AND profile_id=%s AND enabled ORDER BY rank",
        (fx.ws, fx.profile_hybrid),
    )
    need({r[0] for r in routes} == {"surreal", "falkor"},
         "the configured route set should cover both backends; discovery must report the gap")


@check("01", "XD-09", "The current revision is the only eligible revision after a publish")
def xd09(ctx: Context) -> None:
    fx = ctx.fixture
    with ctx.pg.admin() as conn:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO control.document_revisions"
            "(workspace_id,collection_id,document_id,revision,title,media_type,source_object_key,source_sha256,"
            " extracted_object_key,extracted_sha256,manifest_object_key,manifest_sha256,parser_recipe,chunk_count)"
            " VALUES (%s,%s,%s,2,'r2','text/plain','o2',%s,'e2',%s,'m2',%s,'parser/1',1)",
            (fx.ws, fx.coll, fx.document, "b" * 64, "c" * 64, "d" * 64),
        )
        conn.execute(
            "INSERT INTO control.projection_jobs"
            "(workspace_id,collection_id,document_id,revision,location_id,action,state,result_sha256)"
            " VALUES (%s,%s,%s,2,%s,'index','ready',%s)",
            (fx.ws, fx.coll, fx.document, fx.surreal_loc, "e" * 64),
        )
        # Both revisions are ready for the location, but the pointer has not moved.
        rows = ctx.pg.eligible(fx.user, fx.ws, fx.profile_hybrid, conn=conn)
        need([int(r["revision"]) for r in rows] == [1], f"served {[r['revision'] for r in rows]}, expected [1]")

        conn.execute(
            "UPDATE control.documents SET current_revision=2"
            " WHERE workspace_id=%s AND collection_id=%s AND id=%s",
            (fx.ws, fx.coll, fx.document),
        )
        rows = ctx.pg.eligible(fx.user, fx.ws, fx.profile_hybrid, conn=conn)
        need([int(r["revision"]) for r in rows] == [2],
             f"after pointer advance served {[r['revision'] for r in rows]}, expected [2]")
        conn.execute("ROLLBACK")
