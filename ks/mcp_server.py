"""Read-only MCP for the knowledge stack."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable

import psycopg
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from .pg import ControlPlane
from .settings import Settings
from .stores.falkor import Falkor
from .stores.surreal import Surreal


def _reachable(probe: Callable[[], bool]) -> bool:
    try:
        return probe()
    except Exception:
        return False


def create_server(settings: Settings, host: str = "127.0.0.1", port: int = 8765) -> FastMCP:
    server = FastMCP(
        "knowledge-schema",
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["localhost:*", "127.0.0.1:*", "mcp:*"],
        ),
    )
    read_only = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

    @server.tool(annotations=read_only, structured_output=False)
    def status() -> str:
        """Check PostgreSQL, SurrealDB HTTP, and FalkorDB reachability."""

        def postgres() -> bool:
            with psycopg.connect(settings.pg_dsn, connect_timeout=5) as conn:
                return conn.execute("SELECT 1").fetchone() == (1,)

        def surreal() -> bool:
            with Surreal(settings.surreal_url, settings.debug_ns, settings.debug_ns,
                         settings.surreal_user, settings.surreal_password, timeout=5) as client:
                return client.health()

        def falkor() -> bool:
            with Falkor(settings.falkor_host, settings.falkor_port, "_probe",
                        settings.falkor_password, timeout=5) as client:
                return client.ping()

        return json.dumps({
            "postgres": _reachable(postgres),
            "surreal": _reachable(surreal),
            "falkor": _reachable(falkor),
        }, separators=(",", ":"))

    @server.tool(annotations=read_only, structured_output=False)
    def migration_plan() -> str:
        """Summarize applied, pending, and changed PostgreSQL migrations."""
        control = ControlPlane(settings)
        try:
            applied = control.applied()
            plan = control.plan(applied)
        except (psycopg.Error, OSError) as exc:
            raise ToolError("Could not read the migration ledger or package files.") from exc

        pending = []
        drifted = []
        for version, name, _, matches in plan:
            if not matches:
                entry = f"{version:04d}_{name}"
                (drifted if version in applied else pending).append(entry)
        return json.dumps({
            "applied": sum(matches for _, _, _, matches in plan),
            "pending": pending,
            "drifted": drifted,
        }, separators=(",", ":"))

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", help="Path to deployment environment file")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    create_server(Settings.from_env(args.env_file), args.host, args.port).run(
        transport=args.transport,
    )


if __name__ == "__main__":
    main()
