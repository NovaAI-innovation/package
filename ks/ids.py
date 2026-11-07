"""Deterministic identifiers and canonical encodings.

Implements the identity rules from 01-cross-database-contract.md and the receipt rule from
the same document's "Projection receipts" section, plus the FalkorDB namespaced UUIDv5 keys
from 04-falkordb-graphrag.md.

Every function here is pure. Nothing touches a database. That is deliberate: identifiers
must be reproducible by the writer, the reconciliation scanner and the acceptance checks
independently, so they are computed, never read back.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from typing import Any, Iterable, Mapping

# Fixed namespace for all derived identity. UUIDv5 requires one; the spec says "with the
# collection UUID as namespace" for the graph and "the document UUID" for chunks. The
# constants below are only used where the spec does not name a namespace.
_SPEC_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "knowledge-schema/1.0.0")


def parse_uuid(value: str | uuid.UUID) -> uuid.UUID:
    """Parse a UUID, rejecting anything that is not a canonical lowercase-hyphenated form."""
    u = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    return u


def canonical_uuid(value: str | uuid.UUID) -> str:
    """Serialize a UUID the way service boundaries must: lowercase, hyphenated."""
    return str(parse_uuid(value))


def is_canonical_uuid(value: str) -> bool:
    """True when `value` is a lowercase, hyphenated UUID string."""
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def is_sha256_hex(value: str) -> bool:
    """True when `value` is 64 lowercase hex characters."""
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(c in "0123456789abcdef" for c in value)


# --------------------------------------------------------------------------------------
# Chunk identity — contract: "UUIDv5 derived from the document UUID and ASCII name
# chunk/<revision>/<ordinal>, with decimal numbers and no leading zeroes".
# --------------------------------------------------------------------------------------

def chunk_id(document_id: str, revision: int, ordinal: int) -> str:
    if int(revision) < 1:
        raise ValueError("revision must be positive")
    if int(ordinal) < 0:
        raise ValueError("ordinal must be non-negative")
    name = f"chunk/{int(revision)}/{int(ordinal)}"
    return str(uuid.uuid5(parse_uuid(document_id), name))


def chunk_surreal_key(document_id: str, revision: int) -> str:
    """SurrealDB `document_revision` record id: canonical document UUID + '_' + revision."""
    if int(revision) < 1:
        raise ValueError("revision must be positive")
    return f"{canonical_uuid(document_id)}_{int(revision)}"


def doc_revision_key(document_id: str, revision: int) -> str:
    """FalkorDB `doc_revision_key`: canonical document UUID, a slash, and the revision."""
    if int(revision) < 1:
        raise ValueError("revision must be positive")
    return f"{canonical_uuid(document_id)}/{int(revision)}"


# --------------------------------------------------------------------------------------
# Graph identity — 04: "Use UUIDv5 with the collection UUID as namespace. The name is
# UTF-8 for the compact JSON array ... NFC normalization before encoding ... integers as
# decimal strings without leading zeroes ... RFC 8785 JSON string encoding."
# --------------------------------------------------------------------------------------

def _graph_name(parts: Iterable[Any]) -> bytes:
    encoded = canonical_json(list(parts))
    return unicodedata.normalize("NFC", encoded).encode("utf-8")


def entity_id(collection_id: str, kind: str, key: str) -> str:
    """`["entity", kind, canonical_key]`"""
    return str(uuid.uuid5(parse_uuid(collection_id), _graph_name(["entity", kind, key]).decode("utf-8")))


def assertion_id(
    collection_id: str,
    recipe_sha256: str,
    document_id: str,
    revision: int,
    subject_entity_id: str,
    predicate: str,
    object_entity_id: str,
    polarity: str,
) -> str:
    """`["assertion", recipe_sha256, document_id, revision, subject, predicate, object, polarity]`"""
    if polarity not in ("positive", "negative"):
        raise ValueError("polarity must be 'positive' or 'negative'")
    name = [
        "assertion",
        recipe_sha256,
        canonical_uuid(document_id),
        _int_str(revision),
        canonical_uuid(subject_entity_id),
        predicate,
        canonical_uuid(object_entity_id),
        polarity,
    ]
    return str(uuid.uuid5(parse_uuid(collection_id), _graph_name(name).decode("utf-8")))


def evidence_id(
    collection_id: str,
    document_id: str,
    revision: int,
    chunk: str,
    byte_start: int,
    byte_end: int,
) -> str:
    """`["evidence", document_id, revision, chunk_id, byte_start, byte_end]`"""
    if int(byte_end) <= int(byte_start):
        raise ValueError("byte_end must be greater than byte_start")
    name = [
        "evidence",
        canonical_uuid(document_id),
        _int_str(revision),
        canonical_uuid(chunk),
        _int_str(byte_start),
        _int_str(byte_end),
    ]
    return str(uuid.uuid5(parse_uuid(collection_id), _graph_name(name).decode("utf-8")))


def _int_str(value: int) -> str:
    """Decimal string, no leading zeroes. Rejects bools and non-integers."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("graph identity integers must be plain ints")
    return str(int(value))


# --------------------------------------------------------------------------------------
# RFC 8785 (JCS) canonical JSON
#
# The spec names RFC 8785 explicitly for receipts and for graph identity names. Python's
# json.dumps(sort_keys=True) is close but not conformant: key ordering must be by UTF-16
# code units, and numbers must follow the ES6 Number-to-string algorithm. We only ever
# encode integers and strings here (every receipt field is a string or an integer
# revision), so the number rule reduces to "plain base-10, no leading zeroes, no +, no
# exponent for integer magnitudes".
# --------------------------------------------------------------------------------------

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _escape(value: str) -> str:
    out = []
    for ch in value:
        esc = _ESCAPES.get(ch)
        if esc is not None:
            out.append(esc)
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            # RFC 8785 emits Unicode directly. No optional escaping of '/'.
            out.append(ch)
    return '"' + "".join(out) + '"'


def _encode(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("RFC 8785 forbids NaN and Infinity")
        return repr(value)
    if isinstance(value, str):
        return _escape(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode(v) for v in value) + "]"
    if isinstance(value, Mapping):
        # RFC 8785 sorts keys by UTF-16 code units. Encoding to UTF-16-BE gives an
        # order-preserving byte comparison for the BMP, which covers every key here.
        items = sorted(value.items(), key=lambda kv: _utf16_key(kv[0]))
        return "{" + ",".join(f"{_escape(str(k))}:{_encode(v)}" for k, v in items) + "}"
    raise TypeError(f"cannot canonicalize {type(value).__name__}")


def _utf16_key(key: Any) -> bytes:
    return str(key).encode("utf-16-be")


def canonical_json(value: Any) -> str:
    """RFC 8785 canonical JSON, as a str."""
    return _encode(value)


def canonical_json_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------------------
# Projection receipt — contract: "Compute the readiness receipt as SHA-256 of an RFC 8785
# canonical JSON object with keys workspace_id, collection_id, document_id, revision,
# location_id, recipe_sha256, manifest_sha256, action and payload_sha256."
# --------------------------------------------------------------------------------------

RECEIPT_KEYS = (
    "workspace_id",
    "collection_id",
    "document_id",
    "revision",
    "location_id",
    "recipe_sha256",
    "manifest_sha256",
    "action",
    "payload_sha256",
)

# "For purge actions use the SHA-256 of the canonical empty array."
EMPTY_PAYLOAD_SHA256 = sha256_hex("[]")


def projection_receipt(
    *,
    workspace_id: str,
    collection_id: str,
    document_id: str,
    revision: int,
    location_id: str,
    recipe_sha256: str,
    manifest_sha256: str,
    action: str,
    payload_sha256: str,
) -> str:
    """Readiness receipt digest for one (revision, location, action)."""
    if action not in ("index", "purge"):
        raise ValueError("action must be 'index' or 'purge'")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise TypeError("revision must be a JSON integer")
    for name, value in (
        ("workspace_id", workspace_id),
        ("collection_id", collection_id),
        ("document_id", document_id),
        ("location_id", location_id),
    ):
        if not is_canonical_uuid(value):
            raise ValueError(f"{name} must be a canonical lowercase UUID")
    for name, value in (("recipe_sha256", recipe_sha256), ("manifest_sha256", manifest_sha256),
                        ("payload_sha256", payload_sha256)):
        if not is_sha256_hex(value):
            raise ValueError(f"{name} must be 64 lowercase hex characters")
    body = {
        "workspace_id": workspace_id,
        "collection_id": collection_id,
        "document_id": document_id,
        "revision": revision,
        "location_id": location_id,
        "recipe_sha256": recipe_sha256,
        "manifest_sha256": manifest_sha256,
        "action": action,
        "payload_sha256": payload_sha256,
    }
    return sha256_hex(canonical_json_bytes(body))


def projection_manifest(records: Iterable[Mapping[str, str]]) -> str:
    """Canonical projection manifest: sorted identities and content digests.

    `records` entries are {"identity": str, "sha256": str}. Sorting is by identity so the
    digest is stable regardless of write order. Returns the canonical JSON text whose
    SHA-256 is the index-action payload_sha256.
    """
    rows = sorted(
        ({"identity": str(r["identity"]), "sha256": str(r["sha256"])} for r in records),
        key=lambda r: r["identity"],
    )
    return canonical_json(rows)


def index_payload_sha256(records: Iterable[Mapping[str, str]]) -> str:
    return sha256_hex(projection_manifest(records))


# --------------------------------------------------------------------------------------
# Namespacing — 03: ws_<workspace_uuid_without_hyphens> / c_<collection>_g<generation>
#               04: kg:<collection_uuid>:g<generation>
# --------------------------------------------------------------------------------------

def surreal_namespace(workspace_id: str) -> str:
    return "ws_" + canonical_uuid(workspace_id).replace("-", "")


def surreal_database(collection_id: str, generation: int) -> str:
    return f"c_{canonical_uuid(collection_id).replace('-', '')}_g{int(generation)}"


def surreal_physical_key(workspace_id: str, collection_id: str, generation: int) -> str:
    """"The location's physical_key stores the namespace and database separated by a slash." """
    return f"{surreal_namespace(workspace_id)}/{surreal_database(collection_id, generation)}"


def falkor_graph_key(collection_id: str, generation: int) -> str:
    return f"kg:{canonical_uuid(collection_id)}:g{int(generation)}"


def split_physical_key(backend: str, physical_key: str) -> tuple[str, ...]:
    """Parse a registry physical_key into its addressable parts, validating the shape."""
    if backend == "surreal":
        if physical_key.count("/") != 1:
            raise ValueError("surreal physical_key must be '<namespace>/<database>'")
        ns, db = physical_key.split("/", 1)
        if not ns.startswith("ws_") or not db.startswith("c_"):
            raise ValueError("surreal physical_key must be ws_<uuid>/c_<uuid>_g<n>")
        return (ns, db)
    if backend == "falkor":
        parts = physical_key.split(":")
        if len(parts) != 3 or parts[0] != "kg":
            raise ValueError("falkor physical_key must be 'kg:<collection_uuid>:g<generation>'")
        return (parts[1], parts[2])
    raise ValueError(f"unknown backend {backend!r}")


__all__ = [
    "EMPTY_PAYLOAD_SHA256",
    "RECEIPT_KEYS",
    "assertion_id",
    "canonical_json",
    "canonical_json_bytes",
    "canonical_uuid",
    "chunk_id",
    "chunk_surreal_key",
    "doc_revision_key",
    "entity_id",
    "evidence_id",
    "falkor_graph_key",
    "index_payload_sha256",
    "is_canonical_uuid",
    "is_sha256_hex",
    "parse_uuid",
    "projection_manifest",
    "projection_receipt",
    "sha256_hex",
    "split_physical_key",
    "surreal_database",
    "surreal_namespace",
    "surreal_physical_key",
]
