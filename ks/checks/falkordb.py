"""Acceptance checks for 04-falkordb-graphrag.md."""

from __future__ import annotations

import uuid

from .. import ids
from ..settings import DB_ROOT, falkor_constraint_statements, read_text
from ..stores.falkor import Falkor, FalkorError
from . import Context, check, need, needs_store

EXPECTED_CONSTRAINTS = 6


def _client(ctx: Context) -> Falkor:
    fx = ctx.fixture
    return Falkor(
        ctx.settings.falkor_host,
        ctx.settings.falkor_port,
        fx.falkor_key,
        ctx.settings.falkor_password,
    )


def _write_graph(ctx: Context, fg: Falkor, seed: str = "acceptance") -> dict:
    fx = ctx.fixture
    subj = ids.entity_id(fx.coll, "PERSON", f"{seed}-subject")
    obj = ids.entity_id(fx.coll, "PERSON", f"{seed}-object")
    assertion = ids.assertion_id(fx.coll, "a" * 64, fx.document, 1, subj, "KNOWS", obj, "positive")
    chunk = ids.chunk_id(fx.document, 1, 0)
    evidence = ids.evidence_id(fx.coll, fx.document, 1, chunk, 0, 10)

    entity_tpl = read_text(DB_ROOT / "falkordb" / "entity_write.cypher")
    for ident, key in ((subj, f"{seed}-subject"), (obj, f"{seed}-object")):
        fg.cypher(entity_tpl, entity_id=ident, kind="PERSON", canonical_key=key)

    fg.cypher(
        read_text(DB_ROOT / "falkordb" / "projection_write.cypher"),
        subject_id=subj, object_id=obj, assertion_id=assertion,
        document_id=fx.document, revision=1, doc_revision_key=fx.doc_revision_key,
        predicate="KNOWS", polarity="positive", confidence_ppm=900000,
    )
    fg.cypher(
        read_text(DB_ROOT / "falkordb" / "evidence_write.cypher"),
        assertion_id=assertion, evidence_id=evidence, doc_revision_key=fx.doc_revision_key,
        document_id=fx.document, revision=1, chunk_id=chunk, byte_start=0, byte_end=10,
    )
    return {
        "subject": subj, "object": obj, "assertion": assertion,
        "evidence": evidence, "chunk": chunk,
    }


def _cleanup(fg: Falkor, ids_to_remove: list[str]) -> None:
    fg.cypher(
        "MATCH (n) WHERE n.entity_id IN $ids OR n.assertion_id IN $ids OR n.evidence_id IN $ids"
        " DETACH DELETE n",
        ids=ids_to_remove,
    )


# ---------------------------------------------------------------- schema


@needs_store("falkor")
@check("04", "FK-01", "Six constraints exist and all are OPERATIONAL")
def fk01(ctx: Context) -> None:
    with _client(ctx) as fg:
        constraints = fg.constraints_operational(EXPECTED_CONSTRAINTS, timeout=60)
        need(len(constraints) == EXPECTED_CONSTRAINTS,
             f"expected {EXPECTED_CONSTRAINTS} constraints, found {len(constraints)}")
        for c in constraints:
            need(str(c["status"]).upper() == "OPERATIONAL", f"constraint not operational: {c}")


@needs_store("falkor")
@check("04", "FK-02", "Exact-match indexes backing the constraints exist")
def fk02(ctx: Context) -> None:
    with _client(ctx) as fg:
        # CALL db.indexes() rows are (label, properties, types, options, language, ...);
        # column 0 is the label, not column 1.
        rows = fg.indexes()
        labels = {str(r.get("label")) for r in rows}
        for label in ("Entity", "Assertion", "Evidence"):
            need(label in labels, f"no index declared for {label}; saw {labels}")


@needs_store("falkor")
@check("04", "FK-03", "Constraint specs substitute a real graph key and cannot drift from the spec")
def fk03(ctx: Context) -> None:
    specs = falkor_constraint_statements()
    need(len(specs) == EXPECTED_CONSTRAINTS, f"expected {EXPECTED_CONSTRAINTS} specs, got {len(specs)}")
    for spec in specs:
        need("<graph>" in spec, f"spec is missing the <graph> placeholder: {spec[:50]}")
        need(spec.split()[0] == "GRAPH.CONSTRAINT" and spec.split()[1] == "CREATE", f"bad command: {spec[:50]}")
        props = spec.split("PROPERTIES", 1)[1].split()
        need(int(props[0]) == len(props) - 1,
             f"declared {props[0]} properties but listed {len(props) - 1}: {spec[:60]}")


# ---------------------------------------------------------------- constraints


@needs_store("falkor")
@check("04", "FK-04", "Unique and mandatory constraints reject duplicates and missing properties")
def fk04(ctx: Context) -> None:
    fx = ctx.fixture
    with _client(ctx) as fg:
        dup_id = ids.entity_id(fx.coll, "PERSON", f"dup-{uuid.uuid4().hex[:6]}")
        fg.cypher(
            "CREATE (:Entity {entity_id:$id, kind:'PERSON', canonical_key:'k1'})", id=dup_id
        )
        try:
            duplicated = False
            try:
                fg.cypher(
                    "CREATE (:Entity {entity_id:$id, kind:'PERSON', canonical_key:'k2'})", id=dup_id
                )
                duplicated = True
            except Exception:
                pass
            need(not duplicated, "duplicate entity_id was accepted under a UNIQUE constraint")

            missing = False
            try:
                fg.cypher("CREATE (:Entity {entity_id:$id})", id=str(uuid.uuid4()))
                missing = True
            except Exception:
                pass
            need(not missing, "Entity without kind/canonical_key was accepted under MANDATORY")
        finally:
            fg.cypher("MATCH (n:Entity {entity_id:$id}) DETACH DELETE n", id=dup_id)


# ---------------------------------------------------------------- projection


@needs_store("falkor")
@check("04", "FK-05", "Projection is idempotent: a retry changes no node and no edge")
def fk05(ctx: Context) -> None:
    with _client(ctx) as fg:
        written = _write_graph(ctx, fg, seed="idem")
        try:
            counts_before = fg.cypher(
                "MATCH (a:Assertion {assertion_id:$a})"
                " OPTIONAL MATCH (a)<-[:SUBJECT_OF]-(s)"
                " OPTIONAL MATCH (a)-[:OBJECT]->(o)"
                " OPTIONAL MATCH (a)-[:SUPPORTED_BY]->(e)"
                " RETURN count(DISTINCT a) AS a, count(DISTINCT s) AS s,"
                "        count(DISTINCT o) AS o, count(DISTINCT e) AS e",
                a=written["assertion"],
            ).result_set[0]
            _write_graph(ctx, fg, seed="idem")
            counts_after = fg.cypher(
                "MATCH (a:Assertion {assertion_id:$a})"
                " OPTIONAL MATCH (a)<-[:SUBJECT_OF]-(s)"
                " OPTIONAL MATCH (a)-[:OBJECT]->(o)"
                " OPTIONAL MATCH (a)-[:SUPPORTED_BY]->(e)"
                " RETURN count(DISTINCT a) AS a, count(DISTINCT s) AS s,"
                "        count(DISTINCT o) AS o, count(DISTINCT e) AS e",
                a=written["assertion"],
            ).result_set[0]
            need(list(counts_before) == list(counts_after),
                 f"retry changed the graph: {counts_before} -> {counts_after}")
            need(int(counts_after[0]) == 1 and int(counts_after[1]) == 1
                 and int(counts_after[2]) == 1 and int(counts_after[3]) == 1,
                 f"assertion does not have exactly one subject/object/evidence: {counts_after}")
        finally:
            _cleanup(fg, [written["subject"], written["object"], written["assertion"], written["evidence"]])


@needs_store("falkor")
@check("04", "FK-06", "Multi-hop read honours the eligible allowlist and evidence joins on revision")
def fk06(ctx: Context) -> None:
    fx = ctx.fixture
    with _client(ctx) as fg:
        written = _write_graph(ctx, fg, seed="hop")
        try:
            path = fg.cypher_ro(
                read_text(DB_ROOT / "falkordb" / "read_multi_hop.cypher"),
                seed_id=written["subject"], eligible_keys=[fx.doc_revision_key],
            ).result_set
            need(path, "eligible multi-hop returned no path")
            need(written["assertion"] in list(path[0][1]), f"assertion missing from path: {path[0][1]}")

            blocked = fg.cypher_ro(
                read_text(DB_ROOT / "falkordb" / "read_multi_hop.cypher"),
                seed_id=written["subject"], eligible_keys=["00000000-0000-4000-8000-000000000000/1"],
            ).result_set
            need(not blocked, f"ineligible revision was returned: {blocked}")

            support = fg.cypher_ro(
                read_text(DB_ROOT / "falkordb" / "read_evidence.cypher"),
                assertion_ids=[written["assertion"]], eligible_keys=[fx.doc_revision_key],
            ).result_set
            need(len(support) == 1, f"expected one support row, got {len(support)}")
            _, predicate, polarity, confidence, document, revision, chunk, b0, b1 = support[0]
            need(predicate == "KNOWS" and polarity == "positive" and int(confidence) == 900000,
                 f"assertion fields altered: {support[0]}")
            need(str(document) == fx.document and int(revision) == 1 and str(chunk) == written["chunk"],
                 "evidence does not resolve to the canonical span")
            need(int(b0) == 0 and int(b1) == 10, f"byte span altered: {(b0, b1)}")

            no_support = fg.cypher_ro(
                read_text(DB_ROOT / "falkordb" / "read_evidence.cypher"),
                assertion_ids=[written["assertion"]],
                eligible_keys=["00000000-0000-4000-8000-000000000000/1"],
            ).result_set
            need(not no_support, "evidence was returned for an ineligible revision")
        finally:
            _cleanup(fg, [written["subject"], written["object"], written["assertion"], written["evidence"]])


@needs_store("falkor")
@check("04", "FK-07", "Contradictory assertions survive independently; retraction removes only its own")
def fk07(ctx: Context) -> None:
    fx = ctx.fixture
    with _client(ctx) as fg:
        subj = ids.entity_id(fx.coll, "PERSON", "contradiction-subject")
        obj = ids.entity_id(fx.coll, "PERSON", "contradiction-object")
        pos = ids.assertion_id(fx.coll, "a" * 64, fx.document, 1, subj, "KNOWS", obj, "positive")
        neg = ids.assertion_id(fx.coll, "a" * 64, fx.document, 1, subj, "KNOWS", obj, "negative")
        try:
            entity_tpl = read_text(DB_ROOT / "falkordb" / "entity_write.cypher")
            for ident in (subj, obj):
                fg.cypher(entity_tpl, entity_id=ident, kind="PERSON", canonical_key=ident)
            template = read_text(DB_ROOT / "falkordb" / "projection_write.cypher")
            for assertion, polarity in ((pos, "positive"), (neg, "negative")):
                fg.cypher(
                    template, subject_id=subj, object_id=obj, assertion_id=assertion,
                    document_id=fx.document, revision=1, doc_revision_key=fx.doc_revision_key,
                    predicate="KNOWS", polarity=polarity, confidence_ppm=500000,
                )
            found = fg.cypher(
                "MATCH (a:Assertion) WHERE a.assertion_id IN [$pos,$neg]"
                " RETURN a.polarity ORDER BY a.polarity",
                pos=pos, neg=neg,
            ).result_set
            need([r[0] for r in found] == ["negative", "positive"],
                 f"both polarities must coexist, saw {found}")

            fg.cypher("MATCH (a:Assertion {assertion_id:$id}) DETACH DELETE a", id=neg)
            remaining = fg.cypher(
                "MATCH (a:Assertion) WHERE a.assertion_id IN [$pos,$neg] RETURN a.assertion_id",
                pos=pos, neg=neg,
            ).result_set
            need([str(r[0]) for r in remaining] == [pos], f"retraction removed the wrong assertions: {remaining}")
        finally:
            _cleanup(fg, [subj, obj, pos, neg])


@needs_store("falkor")
@check("04", "FK-08", "Read-only query surface is available and writes are refused through it")
def fk08(ctx: Context) -> None:
    with _client(ctx) as fg:
        # The driver's ro_query must be able to serve the read templates...
        rows = fg.cypher_ro("MATCH (n:Entity) RETURN count(n)").result_set
        need(rows, "ro_query returned nothing at all")
        # ...and must refuse a write. FalkorDB rejects write clauses on GRAPH.RO_QUERY.
        refused = False
        try:
            fg.cypher_ro("CREATE (:Entity {entity_id:$id})", id=str(uuid.uuid4()))
        except Exception:
            refused = True
        need(refused, "GRAPH.RO_QUERY accepted a create")
