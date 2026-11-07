"""Offline checks: identity, canonical JSON, receipts, namespacing, statement parsing.

These run without a database. They exist so a regression in the deterministic layer is
caught in a second rather than after a 3-minute acceptance run.
"""

import math

import pytest

from ks import ids
from ks.settings import (
    DB_ROOT,
    falkor_constraint_statements,
    falkor_index_statements,
    postgres_statements,
    read_text,
    surreal_statements,
)

WS = "11111111-1111-4111-8111-111111111111"
COLL = "22222222-2222-4222-8222-222222222222"
DOC = "33333333-3333-4333-8333-333333333333"
LOC = "44444444-4444-4444-8444-444444444444"


# ---------------------------------------------------------------- canonical JSON


def test_canonical_json_object_key_order():
    assert ids.canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_canonical_json_preserves_slash_and_unicode():
    assert ids.canonical_json({"p": "a/b", "u": "café"}) == '{"p":"a/b","u":"café"}'


def test_canonical_json_escapes_control_and_quote():
    assert ids.canonical_json({"s": 'q"\\\n\u0007'}) == '{"s":"q\\"\\\\\\n\\u0007"}'


def test_canonical_json_rejects_nan():
    with pytest.raises(ValueError):
        ids.canonical_json({"x": float("nan")})


def test_canonical_json_orders_by_utf16():
    # RFC 8785 sorts by UTF-16 code units: an emoji's leading surrogate (0xD83D) sorts
    # before U+FF61, even though UTF-8/Unicode code-point order would put it after.
    text = ids.canonical_json({"\uff61": 1, "\U0001f600": 2})
    assert text.index("\U0001f600") < text.index("\uff61")


def test_empty_payload_digest_is_sha256_of_empty_array():
    assert ids.EMPTY_PAYLOAD_SHA256 == ids.sha256_hex("[]")


# ---------------------------------------------------------------- identity


def test_chunk_id_matches_uuid5_of_documented_name():
    import uuid

    assert ids.chunk_id(DOC, 3, 7) == str(uuid.uuid5(uuid.UUID(DOC), "chunk/3/7"))


def test_chunk_id_rejects_nonpositive_revision():
    with pytest.raises(ValueError):
        ids.chunk_id(DOC, 0, 0)


def test_chunk_surreal_key_and_doc_revision_key_shapes():
    assert ids.chunk_surreal_key(DOC, 2) == f"{DOC}_2"
    assert ids.doc_revision_key(DOC, 2) == f"{DOC}/2"


def test_entity_assertion_evidence_are_deterministic_and_distinct():
    e = ids.entity_id(COLL, "PERSON", "ada")
    assert e == ids.entity_id(COLL, "PERSON", "ada")
    assert e != ids.entity_id(COLL, "PERSON", "grace")
    a = ids.assertion_id(COLL, "a" * 64, DOC, 1, e, "WROTE", e, "positive")
    assert a != ids.assertion_id(COLL, "a" * 64, DOC, 1, e, "WROTE", e, "negative")
    ev = ids.evidence_id(COLL, DOC, 1, ids.chunk_id(DOC, 1, 0), 0, 4)
    assert ids.is_canonical_uuid(ev)


def test_assertion_rejects_unknown_polarity():
    with pytest.raises(ValueError):
        ids.assertion_id(COLL, "a" * 64, DOC, 1, ids.entity_id(COLL, "P", "x"), "P", ids.entity_id(COLL, "P", "y"), "maybe")


def test_evidence_rejects_zero_width_span():
    with pytest.raises(ValueError):
        ids.evidence_id(COLL, DOC, 1, ids.chunk_id(DOC, 1, 0), 9, 9)


def test_graph_identity_uses_decimal_strings_without_leading_zeroes():
    # A leading-zero encoding would derive a different UUIDv5: assert we do not produce it.
    import uuid

    expected = str(uuid.uuid5(
        uuid.UUID(COLL),
        '["evidence","%s","1","%s","0","4"]' % (DOC, ids.chunk_id(DOC, 1, 0)),
    ))
    assert ids.evidence_id(COLL, DOC, 1, ids.chunk_id(DOC, 1, 0), 0, 4) == expected


# ---------------------------------------------------------------- receipts


def _receipt_kwargs(**over):
    base = dict(
        workspace_id=WS, collection_id=COLL, document_id=DOC, revision=1,
        location_id=LOC, recipe_sha256="a" * 64, manifest_sha256="b" * 64,
        action="index", payload_sha256=ids.index_payload_sha256([]),
    )
    base.update(over)
    return base


def test_receipt_is_sha256_over_the_nine_documented_keys():
    expected = ids.sha256_hex(ids.canonical_json({
        "workspace_id": WS, "collection_id": COLL, "document_id": DOC, "revision": 1,
        "location_id": LOC, "recipe_sha256": "a" * 64, "manifest_sha256": "b" * 64,
        "action": "index", "payload_sha256": ids.EMPTY_PAYLOAD_SHA256,
    }))
    assert ids.projection_receipt(**_receipt_kwargs()) == expected


def test_receipt_changes_with_every_documented_input():
    base = ids.projection_receipt(**_receipt_kwargs())
    variations = [
        _receipt_kwargs(revision=2),
        _receipt_kwargs(location_id="55555555-5555-4555-8555-555555555555"),
        _receipt_kwargs(manifest_sha256="c" * 64),
        _receipt_kwargs(recipe_sha256="c" * 64),
        _receipt_kwargs(payload_sha256="c" * 64),
        _receipt_kwargs(action="purge"),
    ]
    for variation in variations:
        assert ids.projection_receipt(**variation) != base


def test_receipt_rejects_noncanonical_uuids_and_bad_digests():
    with pytest.raises(ValueError):
        ids.projection_receipt(**_receipt_kwargs(workspace_id="AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"))
    with pytest.raises(ValueError):
        ids.projection_receipt(**_receipt_kwargs(manifest_sha256="B" * 64))
    with pytest.raises(ValueError):
        ids.projection_receipt(**_receipt_kwargs(action="rebuild"))


def test_manifest_digest_is_order_independent_but_content_sensitive():
    a = {"identity": "b", "sha256": "1" * 64}
    b = {"identity": "a", "sha256": "2" * 64}
    assert ids.index_payload_sha256([a, b]) == ids.index_payload_sha256([b, a])
    assert ids.index_payload_sha256([a, b]) != ids.index_payload_sha256([a, {**b, "sha256": "3" * 64}])


# ---------------------------------------------------------------- namespacing


def test_surreal_namespace_and_database_shapes():
    assert ids.surreal_namespace(WS) == "ws_" + WS.replace("-", "")
    assert ids.surreal_database(COLL, 4) == "c_" + COLL.replace("-", "") + "_g4"
    assert ids.surreal_physical_key(WS, COLL, 4) == (
        "ws_" + WS.replace("-", "") + "/c_" + COLL.replace("-", "") + "_g4"
    )


def test_falkor_key_shape():
    assert ids.falkor_graph_key(COLL, 4) == f"kg:{COLL}:g4"


@pytest.mark.parametrize("backend,key", [
    ("surreal", "ws_x/c_y_g1/extra"),
    ("surreal", "x/c_y_g1"),
    ("falkor", f"kg:{COLL}"),
    ("falkor", f"graph:{COLL}:g1"),
    ("falkor", f"kg:{COLL}:g1:x"),
])
def test_split_physical_key_rejects_malformed(backend, key):
    with pytest.raises(ValueError):
        ids.split_physical_key(backend, key)


# ---------------------------------------------------------------- statement parsing


def test_postgres_migrations_are_ordered_and_versioned():
    files = [p.name for p, _ in postgres_statements()]
    assert files == sorted(files)
    versions = [int(name.split("_")[0]) for name in files]
    assert versions == list(range(1, len(versions) + 1))


def test_surreal_bootstrap_splits_into_only_defines():
    statements = surreal_statements()
    assert len(statements) == 28
    assert all(s.upper().startswith("DEFINE") for s in statements)
    assert sum(1 for s in statements if s.upper().startswith("DEFINE INDEX")) == 5


def test_falkor_constraint_specs_declare_matching_property_counts():
    specs = falkor_constraint_statements()
    assert len(specs) == 6
    for spec in specs:
        props = spec.split("PROPERTIES", 1)[1].split()
        assert int(props[0]) == len(props) - 1


def test_falkor_index_statements_target_the_three_labels():
    statements = falkor_index_statements()
    assert len(statements) == 5
    joined = "\n".join(statements)
    for label in ("Entity", "Assertion", "Evidence"):
        assert f":{label})" in joined


def test_reference_vector_shape_is_the_specified_one():
    # 03 fixes 1536 floats / cosine; the schema files must agree with the ids helper.
    text = "\n".join(surreal_statements())
    assert "array<float, 1536>" in text
    assert "HNSW DIMENSION 1536 DIST COSINE TYPE F32" in text


@pytest.mark.parametrize("name", ["entity_write", "projection_write", "evidence_write"])
def test_falkor_write_templates_are_single_statements(name):
    # FalkorDB rejects more than one Cypher statement per call, and MANDATORY is checked
    # when a node is created, so every mandatory property must appear in the MERGE
    # pattern rather than in an ON CREATE SET clause that runs after creation.
    from ks.stores.falkor import _one_statement

    text = read_text(DB_ROOT / "falkordb" / f"{name}.cypher")
    body = _one_statement(text)
    assert body.count("MERGE") >= 1
    assert "ON CREATE SET" not in body


def test_falkor_write_templates_carry_every_mandatory_property():
    mandatory = {
        "entity_write": ("entity_id", "kind", "canonical_key"),
        "projection_write": (
            "assertion_id", "doc_revision_key", "document_id",
            "revision", "predicate", "polarity", "confidence_ppm",
        ),
        "evidence_write": (
            "evidence_id", "doc_revision_key", "document_id", "revision",
            "chunk_id", "byte_start", "byte_end",
        ),
    }
    for name, props in mandatory.items():
        text = read_text(DB_ROOT / "falkordb" / f"{name}.cypher")
        for prop in props:
            assert f"{prop}:" in text, f"{name}.cypher omits mandatory property {prop}"


def test_one_statement_rejects_a_second_statement():
    from ks.stores.falkor import FalkorError, _one_statement

    assert _one_statement("MATCH (n) RETURN n;\n") == "MATCH (n) RETURN n"
    assert _one_statement("-- note; with a semicolon\nMATCH (n) RETURN n;") == "MATCH (n) RETURN n"
    with pytest.raises(FalkorError):
        _one_statement("MATCH (n) RETURN n; MATCH (m) RETURN m;")
