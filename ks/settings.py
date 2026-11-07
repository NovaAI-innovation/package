"""Settings, resolved from the environment, plus statement-file loading."""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass

PKG_ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_ROOT = PKG_ROOT / "db"

FALLBACK_NS = "ks_router_dev"


@dataclass(frozen=True)
class Settings:
    pg_dsn: str
    surreal_url: str
    surreal_user: str
    surreal_password: str
    falkor_host: str
    falkor_port: int
    falkor_password: str | None
    # Namespace/database used only for sanity checks; deployments are per-location.
    debug_ns: str

    @classmethod
    def from_env(cls) -> "Settings":
        fk_pass = os.environ.get("KS_FALKOR_PASSWORD") or None
        return cls(
            pg_dsn=os.environ.get(
                "KS_PG_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/postgres"
            ),
            surreal_url=os.environ.get("KS_SURREAL_URL", "http://127.0.0.1:8000").rstrip("/"),
            surreal_user=os.environ.get("KS_SURREAL_USER", "root"),
            surreal_password=os.environ.get("KS_SURREAL_PASSWORD", "root"),
            falkor_host=os.environ.get("KS_FALKOR_HOST", "127.0.0.1"),
            falkor_port=int(os.environ.get("KS_FALKOR_PORT", "6379")),
            falkor_password=fk_pass,
            debug_ns=FALLBACK_NS,
        )


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def postgres_statements() -> list[tuple[pathlib.Path, str]]:
    """Ordered migration files. Filename encodes the order and the version."""
    files = sorted((DB_ROOT / "postgres").glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"no postgres migrations under {DB_ROOT / 'postgres'}")
    return [(p, read_text(p)) for p in files]


def surreal_bootstrap_text() -> str:
    return read_text(DB_ROOT / "surrealdb" / "generation_bootstrap.surql")


def supabase_stub_text() -> str:
    return read_text(DB_ROOT / "prelude" / "supabase_stub.sql")


def surreal_statements() -> list[str]:
    """Split the bootstrap into statements.

    SurrealDB 3.x refuses a multi-statement text body unless it begins with OPTION IMPORT,
    and every statement in the bootstrap block is a single-line DEFINE. Splitting on
    ';' at a statement boundary is therefore exact — and each statement must be sent as
    its own request (see ks/stores/surreal.py).
    """
    text = surreal_bootstrap_text()
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    )
    return [s.strip() for s in body.split(";") if s.strip()]


def falkor_index_statements() -> list[str]:
    text = read_text(DB_ROOT / "falkordb" / "graph_bootstrap.cypher")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def falkor_constraint_statements() -> list[str]:
    text = read_text(DB_ROOT / "falkordb" / "constraints.txt")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
