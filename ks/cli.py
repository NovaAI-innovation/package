"""ks — knowledge-schema deployment and acceptance CLI.

  ks deploy   [--target postgres|surreal|falkor] [--dry-run]
  ks verify   [--spec 01|02|03|04|all] [--json]
  ks status
  ks sql      --target surreal --ns <ns> --db <db> --file <path>
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import __version__, ids
from .checks import FAIL, PASS, SKIP, Context, run_checks, summarise
from .deploy import Deployer
from .fixture import seed, teardown
from .pg import ControlPlane
from .settings import Settings
from .stores.falkor import Falkor, FalkorError
from .stores.surreal import Surreal, SurrealError

TARGETS = ("postgres", "surreal", "falkor")


def _exit(results) -> int:
    passed, failed, skipped = summarise(results)
    return 0 if failed == 0 else 1


def cmd_deploy(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    deployer = Deployer(settings)
    targets = TARGETS if args.target == "all" else (args.target,)
    try:
        report = deployer.deploy(targets=targets, dry_run=args.dry_run,
                                 supabase_stub=args.supabase_stub)
    except Exception as exc:
        print(f"deploy failed: {type(exc).__name__}: {exc}")
        return 2
    for line in report.postgres + report.surreal + report.falkor:
        print(line)
    if not (report.postgres or report.surreal or report.falkor):
        print("nothing to do: every target is at or ahead of this package")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    cp = ControlPlane(settings)
    ctx = Context(settings=settings, pg=cp)
    conn = None
    try:
        from .pg import connect

        conn = connect(settings)
        try:
            ctx.fixture = seed(conn)
        except Exception as exc:
            print(f"could not seed the acceptance fixture: {type(exc).__name__}: {exc}")
            print("hint: run `ks deploy` first, and check KS_PG_DSN")
            return 2

        if args.provision:
            # The acceptance fixture registers physical locations; provisioning them is a
            # separate, explicit step so a verify run never silently creates databases.
            deployer = Deployer(settings)
            for backend, fn in (("surreal", deployer.deploy_surreal), ("falkor", deployer.deploy_falkor)):
                try:
                    for line in fn():
                        print(f"provision: {line}")
                except Exception as exc:
                    # An unreachable store is a SKIP, not a failure: the checks that need it
                    # are marked skipped below and the rest of the suite still means something.
                    print(f"provision: {backend} unavailable ({type(exc).__name__}: {exc})")

        # Presence, not opening: deploy the stores first, then this records what it found.
        def falkor_up() -> bool:
            try:
                fg = Falkor(settings.falkor_host, settings.falkor_port, "_probe",
                            settings.falkor_password)
            except Exception:
                return False
            try:
                return fg.ping()
            finally:
                fg.close()

        try:
            surreal_up = Surreal(settings.surreal_url, "_probe", "_probe",
                                 settings.surreal_user, settings.surreal_password).health()
        except Exception:
            surreal_up = False

        ctx.available = {
            "postgres": True,
            "surreal": surreal_up,
            "falkor": falkor_up(),
        }
        ctx.extras["provisioned"] = bool(args.provision)

        spec_arg = None if args.spec in ("all", None) else args.spec
        results = run_checks(ctx, tag=spec_arg)

        if args.json:
            print(json.dumps([r.__dict__ for r in results], indent=2))
        else:
            for r in results:
                print(r.line())
            passed, failed, skipped = summarise(results)
            unreachable = [name for name, ok in ctx.available.items() if not ok]
            print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
            if unreachable:
                print(f"skipped stores unreachable: {', '.join(unreachable)}")
        return _exit(results)
    finally:
        if conn is not None:
            try:
                if ctx.fixture is not None:
                    teardown(conn, ctx.fixture)
            finally:
                conn.close()


def cmd_status(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    cp = ControlPlane(settings)
    print(f"ks {__version__}")
    try:
        applied = cp.applied()
        plan = cp.plan(applied)
        print("\npostgres")
        for version, name, digest, done in plan:
            mark = "applied" if done else ("DRIFTED" if version in applied else "pending")
            print(f"  {version:04d} {name:<24} {mark:<8} {digest[:12]}")
        tables = len(cp.control_tables())
        print(f"  control tables: {tables}")

        rows = cp.maintenance(
            """
            SELECT workspace_id, collection_id, backend, generation, state, physical_key
              FROM control.backend_locations ORDER BY backend, generation
            """
        )
        print(f"\nphysical locations ({len(rows)})")
        for ws, coll, backend, gen, state, key in rows:
            print(f"  {backend:<7} g{gen:<3} {state:<9} {key}")
    except Exception as exc:
        print(f"  (control plane unavailable: {type(exc).__name__}: {exc})")

    print(f"\nsurreal ({settings.surreal_url})")
    try:
        with Surreal(settings.surreal_url, settings.debug_ns, settings.debug_ns,
                     settings.surreal_user, settings.surreal_password) as _:
            pass
        import httpx

        ok = httpx.get(f"{settings.surreal_url}/health", timeout=5).status_code == 200
        print(f"  health: {'ok' if ok else 'unhealthy'}")
    except Exception as exc:
        print(f"  unreachable: {type(exc).__name__}")

    print(f"\nfalkor ({settings.falkor_host}:{settings.falkor_port})")
    try:
        with Falkor(settings.falkor_host, settings.falkor_port, "ks_probe",
                    settings.falkor_password) as fg:
            print(f"  ping: {'ok' if fg.ping() else 'unhealthy'}")
    except Exception as exc:
        print(f"  unreachable: {type(exc).__name__}")
    return 0


def cmd_sql(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    text = pathlib.Path(args.file).read_text(encoding="utf-8")
    if args.target == "surreal":
        statements = [s.strip() for s in text.split(";") if s.strip()]
        with Surreal(settings.surreal_url, args.ns, args.db,
                     settings.surreal_user, settings.surreal_password) as sg:
            for s in statements:
                print(sg.query(s).status)
        return 0
    if args.target == "falkor":
        with Falkor(settings.falkor_host, settings.falkor_port, args.graph,
                    settings.falkor_password) as fg:
            for s in [x.strip() for x in text.split(";") if x.strip()]:
                fg.cypher(s)
        return 0
    print("--target must be surreal or falkor", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ks", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"ks {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("deploy", help="apply schemas")
    d.add_argument("--target", choices=[*TARGETS, "all"], default="all")
    d.add_argument("--dry-run", action="store_true")
    d.add_argument(
        "--supabase-stub",
        action="store_true",
        help="create the auth.users/anon/authenticated/service_role stub first "
             "(DISPOSABLE INSTANCES ONLY — never against a Supabase project)",
    )
    d.set_defaults(func=cmd_deploy)

    v = sub.add_parser("verify", help="run acceptance checks")
    v.add_argument("--spec", choices=["01", "02", "03", "04", "all"], default="all")
    v.add_argument("--json", action="store_true")
    v.add_argument(
        "--provision",
        action="store_true",
        help="apply the generation schema to every registered physical location first "
             "(disposable instances only)",
    )
    v.set_defaults(func=cmd_verify)

    s = sub.add_parser("status", help="show deployment state")
    s.set_defaults(func=cmd_status)

    q = sub.add_parser("sql", help="run a statement file against one physical store")
    q.add_argument("--target", choices=["surreal", "falkor"], required=True)
    q.add_argument("--file", required=True)
    q.add_argument("--ns", default=None)
    q.add_argument("--db", default=None)
    q.add_argument("--graph", default=None)
    q.set_defaults(func=cmd_sql)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (SurrealError, FalkorError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
