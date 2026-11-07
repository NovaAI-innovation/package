"""PostgreSQL control-plane access.

Two roles matter here and they are not interchangeable:

* the deployment connection (an owner / migration identity) applies DDL and reads the
  ledger. The spec requires DDL to run "through an administrative migration connection".
* the runtime connection is a login that belongs to exactly one of the cp_* group roles
  and therefore sees only what 0007_grants.sql allows. Acceptance checks use a runtime
  identity precisely so a test cannot pass on owner privileges.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg

from . import ids
from .settings import Settings, postgres_statements


@dataclass
class AppliedMigration:
    version: int
    name: str
    sha256: str
    duration_ms: int


def _split(path_name: str) -> tuple[int, str]:
    stem = path_name.rsplit(".", 1)[0]
    version, _, name = stem.partition("_")
    return int(version), name


def connect(settings: Settings) -> psycopg.Connection:
    return psycopg.connect(settings.pg_dsn, autocommit=True)


class ControlPlane:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @contextmanager
    def admin(self):
        conn = psycopg.connect(self.settings.pg_dsn, autocommit=True, connect_timeout=5)
        try:
            yield conn
        finally:
            conn.close()

    # ---------------------------------------------------------------- deployment

    def bootstrap_ledger(self) -> None:
        """Create ops.* before any migration can record itself.

        ops.schema_migrations is defined in 0001_schema.sql, which is itself a migration.
        The ledger therefore has to exist first; we create it here with the identical
        definition, and 0001's IF NOT EXISTS makes the two agree.
        """
        with self.admin() as conn:
            conn.execute("CREATE SCHEMA IF NOT EXISTS ops")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ops.schema_migrations (
                  target text NOT NULL,
                  version integer NOT NULL,
                  name text NOT NULL,
                  sha256 text NOT NULL,
                  applied_at timestamptz NOT NULL DEFAULT now(),
                  duration_ms integer NOT NULL,
                  PRIMARY KEY (target, version)
                )
                """
            )

    def applied(self) -> dict[int, str]:
        with self.admin() as conn:
            if conn.execute("SELECT to_regclass('ops.schema_migrations')").fetchone()[0] is None:
                return {}
            rows = conn.execute(
                "SELECT version, sha256 FROM ops.schema_migrations WHERE target = 'postgres'"
            ).fetchall()
        return {int(v): str(h) for v, h in rows}

    def plan(self, applied: dict[int, str] | None = None) -> list[tuple[int, str, str, bool]]:
        """(version, name, sha256, already_applied) for every migration file."""
        done = self.applied() if applied is None else applied
        out = []
        for path, text in postgres_statements():
            version, name = _split(path.name)
            digest = ids.sha256_hex(text)
            out.append((version, name, digest, done.get(version) == digest))
        return out

    def migrate(self, dry_run: bool = False, supabase_stub: bool = False) -> list[AppliedMigration]:
        if not dry_run:
            self.bootstrap_ledger()
        if supabase_stub and not dry_run:
            from .settings import supabase_stub_text

            with self.admin() as conn:
                conn.execute(supabase_stub_text())
        done = self.applied()
        results: list[AppliedMigration] = []
        for path, text in postgres_statements():
            version, name = _split(path.name)
            digest = ids.sha256_hex(text)
            if version in done:
                if done[version] != digest:
                    raise RuntimeError(
                        f"migration {path.name} changed after being applied "
                        f"(ledger {done[version][:12]}…, file {digest[:12]}…). "
                        "Applied migrations are immutable; correct it with a new file."
                    )
                continue
            if dry_run:
                results.append(AppliedMigration(version, name, digest, 0))
                continue
            started = time.perf_counter()
            with self.admin() as conn:
                conn.execute(text)
                ms = int((time.perf_counter() - started) * 1000)
                conn.execute(
                    "INSERT INTO ops.schema_migrations (target, version, name, sha256, duration_ms)"
                    " VALUES ('postgres', %s, %s, %s, %s)",
                    (version, name, digest, ms),
                )
            results.append(AppliedMigration(version, name, digest, ms))
        return results

    # ---------------------------------------------------------------- inspection

    def control_tables(self) -> list[str]:
        with self.admin() as conn:
            rows = conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'control' ORDER BY 1"
            ).fetchall()
        return [r[0] for r in rows]

    def browser_role_privileges(self) -> list[tuple[str, str]]:
        """Any privilege held by PUBLIC/anon/authenticated/service_role on control objects.

        The spec asserts browser roles cannot reach the private schema. An empty list is
        the pass condition.
        """
        with self.admin() as conn:
            rows = conn.execute(
                """
                SELECT grantee, table_name
                  FROM information_schema.role_table_grants
                 WHERE table_schema = 'control'
                   AND grantee IN ('PUBLIC','anon','authenticated','service_role')
                UNION ALL
                SELECT 'PUBLIC', n.nspname || '.' || c.relname
                  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'control'
                   AND (has_table_privilege('public', c.oid, 'SELECT')
                        OR has_table_privilege('public', c.oid, 'INSERT')
                        OR has_table_privilege('public', c.oid, 'UPDATE')
                        OR has_table_privilege('public', c.oid, 'DELETE'))
                """
            ).fetchall()
        return [(str(g), str(t)) for g, t in rows]

    def group_role_grants(self) -> dict[str, int]:
        with self.admin() as conn:
            rows = conn.execute(
                """
                SELECT grantee, count(*)
                  FROM information_schema.role_table_grants
                 WHERE table_schema = 'control'
                   AND grantee LIKE 'cp\\_%'
                 GROUP BY 1 ORDER BY 1
                """
            ).fetchall()
        return {str(g): int(c) for g, c in rows}

    def eligible(
        self, user_id: str, workspace_id: str, profile_id: str, conn: Any = None
    ) -> list[dict]:
        sql = "SELECT * FROM ops.eligible_projections(%s, %s, %s)"
        args = (user_id, workspace_id, profile_id)
        if conn is not None:
            # Same-transaction read: a caller that has just moved a revision pointer must
            # see its own write, which a second pooled connection would not.
            cur = conn.execute(sql, args)
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        with self.admin() as own:
            cur = own.execute(sql, args)
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def claim(self, lease_seconds: int = 30) -> dict | None:
        with self.admin() as conn:
            cur = conn.execute(
                "SELECT * FROM ops.claim_projection_job(%s, gen_random_uuid())",
                (lease_seconds,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return dict(zip([d.name for d in cur.description], row))

    def ack(self, job: dict, receipt: str) -> bool:
        with self.admin() as conn:
            cur = conn.execute(
                "SELECT ops.ack_projection_job(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    job["workspace_id"], job["collection_id"], job["document_id"],
                    job["revision"], job["location_id"], job["action"],
                    job["lease_token"], receipt,
                ),
            )
            return bool(cur.fetchone()[0])

    def maintenance(self, sql: str, params: tuple = ()) -> list[tuple]:
        """Run a statement and return its rows (empty for statements that produce none)."""
        with self.admin() as conn:
            cur = conn.execute(sql, params)
            if cur.description is None:
                return []
            return cur.fetchall()
