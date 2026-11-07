"""FalkorDB client, scoped to one graph key.

Constraints are asynchronous, so this module refuses to pretend a constraint exists just
because the command returned. `wait_constraints_operational` polls CALL db.constraints()
and fails on FAILED or on timeout. Uniqueness that never becomes operational is not
uniqueness.
"""

from __future__ import annotations

import time
from typing import Any, Iterable

import falkordb
import redis

TERMINAL_BAD = {"FAILED", "DROPPED"}


def _one_statement(text: str) -> str:
    """FalkorDB accepts exactly one Cypher statement per call.

    Templates are authored with a trailing semicolon so they read like the spec's fenced
    blocks; the server rejects anything with more than one statement, so the single
    trailing terminator is stripped and any additional one is a template defect.
    """
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
    body = body.strip()
    if body.endswith(";"):
        body = body[:-1]
    if ";" in body:
        raise FalkorError("template contains more than one Cypher statement")
    return body.strip()


class FalkorError(RuntimeError):
    pass


class Falkor:
    def __init__(
        self,
        host: str,
        port: int,
        graph_key: str,
        password: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.graph_key = graph_key
        self._db = falkordb.FalkorDB(
            host=host, port=port, password=password, socket_timeout=timeout
        )
        self._graph = self._db.select_graph(graph_key)

    def close(self) -> None:
        try:
            self._db.connection.close()
        except Exception:
            pass

    def __enter__(self) -> "Falkor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ protocol

    def ping(self) -> bool:
        try:
            return bool(self._db.connection.ping())
        except redis.RedisError:
            return False

    def raw(self, command: str, *args: Any) -> Any:
        """Send a Cypher-level call (CALL db.constraints(), CALL db.indexes(), ...)."""
        return self._graph.query(command, *args) if args else self._graph.query(command)

    def command(self, *parts: Any) -> Any:
        """Send a raw Redis-level command, e.g. ("GRAPH.CONSTRAINT", "CREATE", ...).

        GRAPH.* is a server command, not Cypher; the graph object has no connection of its
        own in falkordb 1.x, so this goes through the client's connection.
        """
        return self._db.connection.execute_command(*parts)

    def cypher(self, text: str, **params: Any) -> Any:
        return self._graph.query(_one_statement(text), params or None)

    def cypher_ro(self, text: str, **params: Any) -> Any:
        return self._graph.ro_query(_one_statement(text), params or None)

    # ------------------------------------------------------------------ constraints

    def constraints(self) -> list[dict]:
        try:
            return list(self._graph.list_constraints())
        except Exception as exc:  # pragma: no cover - server-version dependent
            raise FalkorError(f"could not list constraints: {exc}") from exc

    def create_constraint(self, spec: str, graph_key: str | None = None) -> str:
        """spec is one line from db/falkordb/constraints.txt with <graph> substituted."""
        key = graph_key or self.graph_key
        if "<graph>" in spec:
            raise ValueError("constraint spec still contains <graph>")
        # GRAPH.CONSTRAINT CREATE <key> <TYPE> NODE <Label> PROPERTIES <n> <props...>
        tokens = spec.split()
        if tokens[0] != "GRAPH.CONSTRAINT" or tokens[1] != "CREATE":
            raise ValueError(f"not a constraint creation command: {spec[:60]}")
        result = self.command(*tokens)
        return str(result)

    def constraints_operational(self, expected: int, timeout: float = 90.0) -> list[dict]:
        deadline = time.monotonic() + timeout
        last: list[dict] = []
        while time.monotonic() < deadline:
            last = self.constraints()
            if len(last) >= expected and all(
                str(c.get("status", "")).upper() == "OPERATIONAL" for c in last
            ):
                return last
            for c in last:
                if str(c.get("status", "")).upper() in TERMINAL_BAD:
                    raise FalkorError(f"constraint failed: {c}")
            time.sleep(1.0)
        raise FalkorError(
            f"constraints not operational after {timeout}s: "
            + ", ".join(f"{c.get('label')}/{c.get('type')}={c.get('status')}" for c in last)
        )

    # ------------------------------------------------------------------ inspection

    def indexes(self) -> list[dict]:
        """CALL db.indexes() rows: (label, properties, types, options, language, ...)."""
        result = self._graph.list_indices()
        rows = getattr(result, "result_set", result) or []
        keys = ("label", "properties", "types", "options", "language")
        return [dict(zip(keys, r)) for r in rows]

    def explain(self, text: str, **params: Any) -> str:
        return str(self._graph.explain(text, params or None))


__all__ = ["Falkor", "FalkorError", "falkordb"]
