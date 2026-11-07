"""SurrealDB HTTP client, scoped to one namespace/database.

Two constraints from the spec shape this module:

1. "Select the provisioned namespace and database before running the block." Every
   request therefore carries surreal-ns / surreal-db headers. There is no global
   connection state that a caller could leave pointing at the wrong generation.
2. SurrealDB 3.x rejects a multi-statement text body on /sql unless it begins with
   OPTION IMPORT. Each statement must be its own request. `execute_many` enforces this
   instead of leaving it to the caller to remember.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import time

import httpx


class SurrealError(RuntimeError):
    pass


def _is_retryable(message: str) -> bool:
    """SurrealDB transaction conflicts are an instruction to retry, not a rejection.

    RocksDB reports the same class of conflict two ways: "Write conflict, retry the
    transaction" and "Transaction conflict: Resource busy. This transaction can be
    retried". Both are transient and both say so; anything else is a real error and is
    surfaced unchanged.
    """
    text = message.lower()
    return "retry the transaction" in text or "transaction conflict" in text


@dataclass
class StatementResult:
    statement: str
    status: str
    result: Any

    @property
    def ok(self) -> bool:
        return self.status == "OK"


class Surreal:
    def __init__(
        self,
        url: str,
        namespace: str,
        database: str,
        user: str,
        password: str,
        timeout: float = 30.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.namespace = namespace
        self.database = database
        self._client = httpx.Client(
            base_url=self.url,
            auth=(user, password),
            timeout=timeout,
            headers={
                "surreal-ns": namespace,
                "surreal-db": database,
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Surreal":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ protocol

    def health(self) -> bool:
        try:
            r = self._client.get("/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def query(self, sql: str, **vars: Any) -> StatementResult:
        """Execute a single statement and return its first result.

        Uses /rpc, not /sql. On SurrealDB 3.x the /sql endpoint treats a JSON request body
        as a SurrealQL *object literal* rather than a query document, so a parameterized
        call silently evaluates to the object instead of running the statement. /rpc with
        {"method": "query", "params": [sql, vars]} executes the statement and binds $vars.
        """
        results = self.query_all(sql, vars)
        if not results:
            raise SurrealError(f"statement produced no result entry: {sql[:80]}")
        return results[0]

    def query_all(self, sql: str, vars: Mapping[str, Any] | None = None) -> list[StatementResult]:
        """Execute a statement (or a multi-statement script) and return every result entry.

        Transient key-value write conflicts are retried. SurrealDB reports these as
        "... Write conflict, retry the transaction", which is an explicit instruction to
        retry rather than a schema or data rejection; surfacing it to the caller would make
        an idempotent projection look non-idempotent under concurrency.
        """
        last: SurrealError | None = None
        for delay in (0.0, 0.05, 0.2, 0.5, 1.0):
            try:
                return self._query_all_once(sql, vars)
            except SurrealError as exc:
                if not _is_retryable(str(exc)):
                    raise
                last = exc
                time.sleep(delay)
        raise last if last else SurrealError("unreachable")

    def _query_all_once(self, sql: str, vars: Mapping[str, Any] | None) -> list[StatementResult]:
        r = self._client.post(
            "/rpc",
            json={"method": "query", "params": [sql, _jsonable(vars or {})]},
        )
        if r.status_code >= 400:
            raise SurrealError(f"{r.status_code}: {r.text[:600]}")
        body = r.json()
        if isinstance(body, dict) and "error" in body:
            raise SurrealError(f"rpc error: {body['error']}")
        entries = body.get("result") if isinstance(body, dict) else body
        if not isinstance(entries, list):
            raise SurrealError(f"unexpected response shape: {str(body)[:300]}")
        out: list[StatementResult] = []
        for entry in entries:
            status = str(entry.get("status", "?"))
            result = entry.get("result")
            if status != "OK":
                raise SurrealError(f"{status}: {result}")
            out.append(StatementResult(sql, status, result))
        return out

    def execute_many(self, statements: Iterable[str]) -> list[StatementResult]:
        return [self.query(s) for s in statements]

    # ------------------------------------------------------------------ reads

    def select(self, sql: str, **vars: Any) -> list[dict]:
        """Rows from a row-returning statement. ONLY returns one dict; VALUE returns scalars."""
        result = self.query(sql, **vars).result
        if result is None:
            return []
        if isinstance(result, dict):
            return [result]
        return list(result)

    def select_script(self, script: str, **vars: Any) -> Any:
        """Run a multi-statement script and return the result of its final statement.

        A script produced by LET/CREATE/DELETE/... returns one entry per statement. The
        script's own RETURN is the last entry, which is the only one a caller wants.
        """
        results = self.query_all(script, vars)
        return results[-1].result if results else None

    def values(self, sql: str, **vars: Any) -> list[Any]:
        result = self.query(sql, **vars).result
        return list(result) if isinstance(result, list) else ([] if result is None else [result])

    def info_for(self, what: str) -> dict:
        if what not in ("DB", "NAMESPACE"):
            raise ValueError("what must be DB or NAMESPACE")
        result = self.query(f"INFO FOR {what}").result
        return result if isinstance(result, dict) else {}

    def ensure_scope(self) -> None:
        """Create the namespace and database if absent.

        SurrealDB does not create a namespace implicitly on first write; a request that
        names a missing namespace fails with "The namespace ... does not exist". The
        registry has already decided these names (spec 03: "No caller supplies these
        names"), so provisioning them is part of applying the generation, not a caller
        choice. Both statements are IF NOT EXISTS and are therefore safe to re-run.
        """
        self.query(f"DEFINE NAMESPACE IF NOT EXISTS {self._ident(self.namespace)}")
        self.query(f"DEFINE DATABASE IF NOT EXISTS {self._ident(self.database)}")
        scope = self.info_for("DB")
        if not scope:
            raise SurrealError(f"database {self.database} is not addressable after provisioning")

    @staticmethod
    def _ident(name: str) -> str:
        """Namespace/database names are generated from UUIDs; reject anything else."""
        if not name or not all(c.isalnum() or c == "_" for c in name):
            raise SurrealError(f"unsafe namespace/database identifier: {name!r}")
        return name

    def info_for_table(self, table: str) -> dict:
        result = self.query(f"INFO FOR TABLE {table}").result
        return result if isinstance(result, dict) else {}

    def count(self, table: str, where: str = "true", **vars: Any) -> int:
        rows = self.values(f"SELECT VALUE count() FROM {table} WHERE {where} GROUP ALL", **vars)
        if not rows:
            return 0
        first = rows[0]
        return int(first) if isinstance(first, (int, float)) else 0


def _jsonable(value: Any) -> Any:
    """UUIDs and other non-JSON scalars in variables travel as strings.

    SurrealDB is strict about string fields (`document_id` TYPE string), so a `uuid.UUID`
    arriving as a JSON string is what the schema expects.
    """
    import uuid as _uuid

    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value
