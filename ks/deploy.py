"""Deployment orchestration: Postgres control plane, then per-location physical stores.

The registry is the source of truth for what physical stores should exist. Deployment reads
it rather than taking a namespace/database from the operator, which is exactly the rule in
spec 03: "No caller supplies these names."

Ordering follows the release governance in README.md:
  expand (additive control-plane) -> backfill (per-location schemas) -> verify -> switch.
Postgres goes first so a physical store never exists without a registry row that points at
it, and a failed store deployment leaves a `building` location rather than an orphan.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import ids
from .pg import ControlPlane
from .settings import (
    Settings,
    falkor_constraint_statements,
    falkor_index_statements,
    surreal_statements,
)
from .stores.falkor import Falkor, FalkorError
from .stores.surreal import Surreal, SurrealError


@dataclass
class Location:
    workspace_id: str
    collection_id: str
    location_id: str
    backend: str
    generation: int
    physical_key: str
    endpoint_ref: str
    recipe_sha256: str
    state: str

    @property
    def namespace(self) -> str:
        return ids.split_physical_key(self.backend, self.physical_key)[0]

    @property
    def database(self) -> str:
        return ids.split_physical_key(self.backend, self.physical_key)[1]


@dataclass
class DeployReport:
    postgres: list[str] = field(default_factory=list)
    surreal: list[str] = field(default_factory=list)
    falkor: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class Deployer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cp = ControlPlane(settings)

    # ---------------------------------------------------------------- postgres

    def migrate_postgres(self, dry_run: bool = False, supabase_stub: bool = False) -> list[str]:
        applied = self.cp.migrate(dry_run=dry_run, supabase_stub=supabase_stub)
        verb = "would apply" if dry_run else "applied"
        return [f"postgres {verb} {m.version:04d}_{m.name} ({m.duration_ms}ms)" for m in applied]

    def locations(self, backend: str | None = None) -> list[Location]:
        if not self.cp.applied():
            raise RuntimeError(
                "the control plane is not deployed; run `ks deploy --target postgres` first "
                "(physical stores are derived from control.backend_locations)"
            )
        sql = """
            SELECT workspace_id, collection_id, id, backend, generation, physical_key,
                   endpoint_ref, recipe_sha256, state
              FROM control.backend_locations
        """
        params: tuple = ()
        if backend:
            sql += " WHERE backend = %s"
            params = (backend,)
        rows = self.cp.maintenance(sql + " ORDER BY workspace_id, collection_id, backend, generation", params)
        return [
            Location(
                workspace_id=str(r[0]),
                collection_id=str(r[1]),
                location_id=str(r[2]),
                backend=str(r[3]),
                generation=int(r[4]),
                physical_key=str(r[5]),
                endpoint_ref=str(r[6]),
                recipe_sha256=str(r[7]),
                state=str(r[8]),
            )
            for r in rows
        ]

    def _record_store(self, backend: str, physical_key: str, script: str, digest: str) -> None:
        self.cp.maintenance(
            """
            INSERT INTO ops.store_deployments (backend, physical_key, script, sha256)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (backend, physical_key, script) DO NOTHING
            """,
            (backend, physical_key, script, digest),
        )

    # ---------------------------------------------------------------- surreal

    def deploy_surreal(self, dry_run: bool = False) -> list[str]:
        statements = surreal_statements()
        digest = ids.sha256_hex("\n".join(statements))
        out: list[str] = []
        for loc in self.locations("surreal"):
            if loc.state == "retired":
                # Retired locations stay readable for the rollback window but must not be
                # re-schema'd: a rebuild always targets a fresh generation.
                out.append(f"surreal skip {loc.physical_key} (retired)")
                continue
            if dry_run:
                out.append(f"surreal would apply {loc.physical_key} ({len(statements)} statements)")
                continue
            with Surreal(
                self.settings.surreal_url,
                loc.namespace,
                loc.database,
                self.settings.surreal_user,
                self.settings.surreal_password,
            ) as sg:
                try:
                    sg.ensure_scope()
                    sg.execute_many(statements)
                    info = sg.info_for("DB")
                    tables = sorted((info.get("tables") or {}).keys())
                    if "document_revision" not in tables or "chunk" not in tables:
                        raise SurrealError(f"bootstrap incomplete in {loc.physical_key}: tables={tables}")
                except SurrealError as exc:
                    raise SurrealError(f"{loc.physical_key}: {exc}") from exc
            self._record_store("surreal", loc.physical_key, "generation_bootstrap.surql", digest)
            out.append(f"surreal applied {loc.physical_key}")
        return out

    # ---------------------------------------------------------------- falkor

    def deploy_falkor(self, dry_run: bool = False) -> list[str]:
        indexes = falkor_index_statements()
        constraints = falkor_constraint_statements()
        digest = ids.sha256_hex("\n".join(indexes + constraints))
        out: list[str] = []
        for loc in self.locations("falkor"):
            if loc.state == "retired":
                out.append(f"falkor skip {loc.physical_key} (retired)")
                continue
            if dry_run:
                out.append(
                    f"falkor would apply {loc.physical_key} "
                    f"({len(indexes)} indexes, {len(constraints)} constraints)"
                )
                continue
            with Falkor(
                self.settings.falkor_host,
                self.settings.falkor_port,
                loc.physical_key,
                self.settings.falkor_password,
            ) as fg:
                try:
                    for stmt in indexes:
                        fg.cypher(stmt)
                    for stmt in constraints:
                        spec = stmt.replace("<graph>", loc.physical_key)
                        fg.create_constraint(spec, loc.physical_key)
                    # Constraints are asynchronous. Deployment is not complete until every
                    # one is OPERATIONAL, because a unique constraint that never becomes
                    # operational silently permits duplicates.
                    fg.constraints_operational(len(constraints), timeout=90)
                except (FalkorError, ValueError) as exc:
                    raise FalkorError(f"{loc.physical_key}: {exc}") from exc
            self._record_store("falkor", loc.physical_key, "graph_bootstrap.cypher+constraints.txt", digest)
            out.append(f"falkor applied {loc.physical_key} ({len(constraints)} constraints operational)")
        return out

    # ---------------------------------------------------------------- all

    def deploy(self, targets: tuple[str, ...] = ("postgres", "surreal", "falkor"),
               dry_run: bool = False, supabase_stub: bool = False) -> DeployReport:
        report = DeployReport()
        if "postgres" in targets:
            # The registry and ledger are prerequisites for every other target.
            report.postgres = self.migrate_postgres(dry_run=dry_run, supabase_stub=supabase_stub)
        if "surreal" in targets:
            report.surreal = self.deploy_surreal(dry_run=dry_run)
        if "falkor" in targets:
            report.falkor = self.deploy_falkor(dry_run=dry_run)
        return report
