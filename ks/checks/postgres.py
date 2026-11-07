"""Acceptance checks for 02-postgres-control-plane.md."""

from __future__ import annotations

import uuid

import psycopg

from .. import ids
from . import Context, check, need

EXPECTED_TABLES = {
    "user_profiles", "workspaces", "workspace_members", "agents", "agent_profiles",
    "agent_versions", "profile_invokers", "collections", "profile_routes",
    "backend_locations", "documents", "document_revisions", "projection_jobs",
    "conversations", "messages", "agent_runs", "run_steps", "run_events", "audit_log",
}


def _tx(conn):
    """Run a statement that must be rejected, restoring the connection state after."""
    conn.execute("BEGIN")


def _rejects(conn, sql: str, params: tuple, label: str) -> str:
    _tx(conn)
    try:
        conn.execute(sql, params)
    except psycopg.Error as exc:
        conn.execute("ROLLBACK")
        return type(exc).__name__
    conn.execute("ROLLBACK")
    raise AssertionError(f"{label} was NOT rejected")


def _accepts(conn, sql: str, params: tuple, label: str) -> None:
    _tx(conn)
    try:
        conn.execute(sql, params)
    except psycopg.Error as exc:
        conn.execute("ROLLBACK")
        raise AssertionError(f"{label} was rejected: {type(exc).__name__}: {exc}") from exc
    conn.execute("ROLLBACK")


# ---------------------------------------------------------------- structural


@check("02", "PG-01", "Bootstrap creates the full control table set")
def pg01(ctx: Context) -> None:
    tables = set(ctx.pg.control_tables())
    missing = EXPECTED_TABLES - tables
    need(not missing, f"missing tables: {sorted(missing)}")
    need(len(tables) >= len(EXPECTED_TABLES), f"expected >= {len(EXPECTED_TABLES)}, found {len(tables)}")


@check("02", "PG-02", "Browser roles cannot reach the private control schema")
def pg02(ctx: Context) -> None:
    grants = ctx.pg.browser_role_privileges()
    need(not grants, f"PUBLIC/anon/authenticated/service_role hold privileges: {grants}")
    # And prove it as the role itself, not only from the catalog.
    with ctx.pg.admin() as conn:
        conn.execute("BEGIN")
        try:
            conn.execute("SET LOCAL ROLE anon")
            conn.execute("SELECT count(*) FROM control.documents")
        except psycopg.Error as exc:
            conn.execute("ROLLBACK")
            need("permission denied" in str(exc).lower(), f"unexpected error: {exc}")
            return
        conn.execute("ROLLBACK")
    raise AssertionError("anon could read control.documents")


@check("02", "PG-03", "All five service roles exist and each holds narrowly scoped grants")
def pg03(ctx: Context) -> None:
    grants = ctx.pg.group_role_grants()
    for role in ("cp_router", "cp_registry", "cp_worker", "cp_execution", "cp_auditor"):
        need(role in grants, f"{role} holds no grants on control")
    need(grants["cp_router"] < grants["cp_registry"],
         f"router ({grants.get('cp_router')}) should hold fewer objects than registry ({grants.get('cp_registry')})")
    need(grants["cp_auditor"] == 1, f"auditor should see exactly audit_log, sees {grants.get('cp_auditor')}")


@check("02", "PG-04", "Router role is read-only and the worker cannot write identity tables")
def pg04(ctx: Context) -> None:
    with ctx.pg.admin() as conn:
        writable = conn.execute(
            """
            SELECT table_name, privilege_type
              FROM information_schema.role_table_grants
             WHERE table_schema = 'control' AND grantee = 'cp_router'
               AND privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')
            """
        ).fetchall()
        need(not writable, f"cp_router holds write privileges: {writable}")

        worker_identity = conn.execute(
            """
            SELECT table_name, privilege_type
              FROM information_schema.role_table_grants
             WHERE table_schema = 'control' AND grantee = 'cp_worker'
               AND table_name IN ('workspace_members','documents','document_revisions','backend_locations')
               AND privilege_type <> 'SELECT'
            """
        ).fetchall()
        need(not worker_identity, f"cp_worker can modify identity tables: {worker_identity}")


@check("02", "PG-05", "Audit log admits INSERT only for writers; UPDATE and DELETE are rejected")
def pg05(ctx: Context) -> None:
    # Scope to the runtime roles. The table owner legitimately holds every privilege on
    # every object it owns; what matters is that no runtime role can rewrite history.
    with ctx.pg.admin() as conn:
        bad = conn.execute(
            """
            SELECT grantee, table_name, privilege_type
              FROM information_schema.role_table_grants
             WHERE table_schema='control' AND table_name='audit_log'
               AND grantee IN ('cp_router','cp_registry','cp_worker','cp_execution','cp_auditor')
               AND privilege_type IN ('UPDATE','DELETE','TRUNCATE')
            """
        ).fetchall()
        need(not bad, f"runtime roles can mutate audit_log: {bad}")

        inserters = {
            r[0]
            for r in conn.execute(
                """
                SELECT DISTINCT grantee
                  FROM information_schema.role_table_grants
                 WHERE table_schema='control' AND table_name='audit_log'
                   AND privilege_type='INSERT'
                """
            ).fetchall()
        }
        need({"cp_registry", "cp_execution", "cp_worker"} <= inserters,
             f"writers that must append are missing INSERT: {inserters}")

        # Do this inside one transaction and roll it back: the assertion is about refusal,
        # and a refused transaction leaves nothing behind to clean up.
        fx = ctx.fixture
        event = str(uuid.uuid4())
        conn.execute("BEGIN")
        try:
            conn.execute(
                "INSERT INTO control.audit_log"
                "(workspace_id,event_id,actor_kind,actor_id,action,subject_id,correlation_id)"
                " VALUES (%s,%s,'user','tester','acceptance.pg05',%s,%s)",
                (fx.ws, event, fx.document, str(uuid.uuid4())),
            )
            for sql in (
                "UPDATE control.audit_log SET action='tampered' WHERE event_id=%s",
                "DELETE FROM control.audit_log WHERE event_id=%s",
            ):
                save = f"sp_{uuid.uuid4().hex[:8]}"
                conn.execute(f"SAVEPOINT {save}")
                try:
                    conn.execute(sql, (event,))
                except psycopg.Error:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {save}")
                    continue
                raise AssertionError(f"audit_log accepted: {sql.split()[0]}")
        finally:
            conn.execute("ROLLBACK")


# ---------------------------------------------------------------- constraints


@check("02", "PG-06", "Schema rejects the documented invalid states")
def pg06(ctx: Context) -> None:
    fx = ctx.fixture
    with ctx.pg.admin() as conn:
        got = []
        got.append(_rejects(
            conn,
            "INSERT INTO control.backend_locations"
            "(workspace_id,collection_id,backend,generation,endpoint_ref,physical_key,recipe_sha256,state)"
            " VALUES (%s,%s,'surreal',2,'dup-endpoint','ws_x/c_x_g2',%s,'active')",
            (fx.ws, fx.coll, "a" * 64),
            "second active location for one backend",
        ))
        got.append(_rejects(
            conn,
            "INSERT INTO control.profile_routes(workspace_id,profile_id,collection_id,backend,rank)"
            " VALUES (%s,%s,%s,'surreal',9)",
            (fx.ws, fx.profile_hybrid, fx.coll_foreign),
            "cross-workspace route",
        ))
        got.append(_rejects(
            conn,
            "INSERT INTO control.profile_routes(workspace_id,profile_id,collection_id,backend,rank)"
            " VALUES (%s,%s,%s,'falkor',1)",
            (fx.ws, fx.profile_hybrid, fx.coll),
            "duplicate rank in one profile",
        ))
        got.append(_rejects(
            conn,
            "UPDATE control.documents SET current_revision = 2 WHERE workspace_id=%s AND collection_id=%s AND id=%s",
            (fx.ws, fx.coll, fx.document),
            "current_revision pointing at a missing revision",
        ))
        got.append(_rejects(
            conn,
            "UPDATE control.projection_jobs SET state='ready', result_sha256=NULL"
            " WHERE workspace_id=%s AND collection_id=%s AND document_id=%s AND location_id=%s",
            (fx.ws, fx.coll, fx.document, fx.falkor_loc),
            "ready without a receipt digest",
        ))
        got.append(_rejects(
            conn,
            "UPDATE control.projection_jobs SET state='leased', lease_token=NULL, lease_until=now()"
            " WHERE workspace_id=%s AND collection_id=%s AND document_id=%s AND location_id=%s",
            (fx.ws, fx.coll, fx.document, fx.falkor_loc),
            "leased without a token",
        ))
        need(len(got) == 6, f"expected 6 rejections, got {got}")


@check("02", "PG-07", "Contract immutability guards reject identity, revision and recipe rewrites")
def pg07(ctx: Context) -> None:
    fx = ctx.fixture
    with ctx.pg.admin() as conn:
        _rejects(
            conn,
            "UPDATE control.document_revisions SET manifest_sha256=%s"
            " WHERE workspace_id=%s AND collection_id=%s AND document_id=%s AND revision=1",
            ("f" * 64, fx.ws, fx.coll, fx.document),
            "revision manifest rewrite",
        )
        _rejects(
            conn,
            "UPDATE control.documents SET source_key='src/moved'"
            " WHERE workspace_id=%s AND collection_id=%s AND id=%s",
            (fx.ws, fx.coll, fx.document),
            "document source_key rewrite",
        )
        _rejects(
            conn,
            "UPDATE control.backend_locations SET recipe_sha256=%s"
            " WHERE workspace_id=%s AND collection_id=%s AND id=%s",
            ("f" * 64, fx.ws, fx.coll, fx.surreal_loc),
            "location recipe rewrite",
        )
        # The permitted transition must still work: retiring a location is a state change.
        _accepts(
            conn,
            "UPDATE control.backend_locations SET state='building'"
            " WHERE workspace_id=%s AND collection_id=%s AND id=%s",
            (fx.ws, fx.coll, fx.surreal_loc),
            "location state transition",
        )


# ---------------------------------------------------------------- routing


@check("02", "PG-08", "Eligible query enforces membership, invocation, route mode and readiness")
def pg08(ctx: Context) -> None:
    fx = ctx.fixture
    rows = ctx.pg.eligible(fx.user, fx.ws, fx.profile_hybrid)
    need(len(rows) == 1, f"hybrid should see exactly the one ready revision, saw {len(rows)}")
    need(rows[0]["backend"] == "surreal", f"only surreal is ready; saw {rows[0]['backend']}")
    need(str(rows[0]["document_id"]) == fx.document, "wrong document returned")
    need(int(rows[0]["revision"]) == 1, "wrong revision returned")

    need(not ctx.pg.eligible(fx.outsider, fx.ws, fx.profile_hybrid), "foreign-workspace user was admitted")
    need(not ctx.pg.eligible(fx.member_no_invoke, fx.ws, fx.profile_hybrid), "non-invoker was admitted")
    need(not ctx.pg.eligible(fx.user, fx.ws_foreign, fx.profile_hybrid), "wrong selected workspace was admitted")

    graph_rows = ctx.pg.eligible(fx.user, fx.ws, fx.profile_graph)
    need(not graph_rows, "graph mode must ignore the surreal route and the not-ready falkor route")

    doc_rows = ctx.pg.eligible(fx.user, fx.ws, fx.profile_document)
    need(len(doc_rows) == 1 and doc_rows[0]["backend"] == "surreal", "document mode must keep the surreal route")

    need(not ctx.pg.eligible(fx.user, fx.ws, fx.routeless_profile), "a profile with no routes returned knowledge")


@check("02", "PG-09", "A disabled route, inactive member or disabled collection removes eligibility")
def pg09(ctx: Context) -> None:
    fx = ctx.fixture
    # ctx.pg.admin() opens its own connection, so the mutations must be committed and
    # undone explicitly rather than relied on to roll back with the block.
    def with_mutation(sql: str, params: tuple, label: str) -> None:
        with ctx.pg.admin() as conn:
            conn.execute(sql, params)
        try:
            need(not ctx.pg.eligible(fx.user, fx.ws, fx.profile_hybrid), f"{label} still eligible")
        finally:
            with ctx.pg.admin() as conn:
                conn.execute(
                    "UPDATE control.profile_routes SET enabled = true WHERE workspace_id=%s AND profile_id=%s",
                    (fx.ws, fx.profile_hybrid),
                )
                conn.execute(
                    "UPDATE control.workspace_members SET active = true WHERE workspace_id=%s AND user_id=%s",
                    (fx.ws, fx.user),
                )
                conn.execute("UPDATE control.collections SET enabled = true WHERE id=%s", (fx.coll,))

    with_mutation(
        "UPDATE control.profile_routes SET enabled = false WHERE workspace_id=%s AND profile_id=%s",
        (fx.ws, fx.profile_hybrid), "disabled route",
    )
    with_mutation(
        "UPDATE control.workspace_members SET active = false WHERE workspace_id=%s AND user_id=%s",
        (fx.ws, fx.user), "inactive member",
    )
    with_mutation(
        "UPDATE control.collections SET enabled = false WHERE id=%s",
        (fx.coll,), "disabled collection",
    )
    need(ctx.pg.eligible(fx.user, fx.ws, fx.profile_hybrid), "restore of the baseline failed")


@check("02", "PG-10", "Tombstoned documents stop serving immediately, with the current pointer intact")
def pg10(ctx: Context) -> None:
    fx = ctx.fixture
    with ctx.pg.admin() as conn:
        conn.execute(
            "UPDATE control.documents SET deleted_at = now()"
            " WHERE workspace_id=%s AND collection_id=%s AND id=%s",
            (fx.ws, fx.coll, fx.document),
        )
    try:
        need(not ctx.pg.eligible(fx.user, fx.ws, fx.profile_hybrid), "tombstoned document still eligible")
    finally:
        with ctx.pg.admin() as conn:
            conn.execute(
                "UPDATE control.documents SET deleted_at = NULL"
                " WHERE workspace_id=%s AND collection_id=%s AND id=%s",
                (fx.ws, fx.coll, fx.document),
            )
    need(ctx.pg.eligible(fx.user, fx.ws, fx.profile_hybrid), "clearing the tombstone did not restore eligibility")


# ---------------------------------------------------------------- queue


def _reset_fixture_jobs(ctx: Context) -> None:
    """Return the fixture's jobs to their seeded state: surreal ready, falkor queued+due."""
    fx = ctx.fixture
    with ctx.pg.admin() as conn:
        conn.execute(
            "UPDATE control.projection_jobs SET state='ready', result_sha256=%s,"
            " lease_token=NULL, lease_until=NULL"
            " WHERE workspace_id=%s AND location_id=%s",
            ("e" * 64, fx.ws, fx.surreal_loc),
        )
        conn.execute(
            "UPDATE control.projection_jobs SET state='queued', result_sha256=NULL,"
            " lease_token=NULL, lease_until=NULL, attempt_count=0, available_at=now()"
            " WHERE workspace_id=%s AND location_id=%s",
            (fx.ws, fx.falkor_loc),
        )
        # Clear anything else this run left queued, so the claim below is unambiguous.
        conn.execute(
            "UPDATE control.projection_jobs SET state='cancelled'"
            " WHERE workspace_id=%s AND state IN ('queued','leased') AND location_id <> %s",
            (fx.ws, fx.falkor_loc),
        )


@check("02", "PG-11", "Lease claim is single-writer and acknowledges only with the current token")
def pg11(ctx: Context) -> None:
    fx = ctx.fixture
    _reset_fixture_jobs(ctx)

    first = ctx.pg.claim(30)
    need(first is not None, "no job was claimable")
    claimed_location = str(first["location_id"])
    need(claimed_location == fx.falkor_loc,
         f"claimed {claimed_location}, expected the due falkor job {fx.falkor_loc}")
    need(int(first["attempt_count"]) == 1, f"first claim should be attempt 1, got {first['attempt_count']}")

    second = ctx.pg.claim(30)
    need(second is None or str(second["location_id"]) != claimed_location,
         "the same job was leased twice")

    receipt = ids.projection_receipt(
        workspace_id=fx.ws,
        collection_id=fx.coll,
        document_id=fx.document,
        revision=1,
        location_id=fx.falkor_loc,
        recipe_sha256="a" * 64,
        manifest_sha256="d" * 64,
        action="index",
        payload_sha256=ids.index_payload_sha256(
            [{"identity": ids.chunk_id(fx.document, 1, 0), "sha256": "b" * 64}]
        ),
    )
    stale = dict(first, lease_token=str(uuid.uuid4()))
    need(not ctx.pg.ack(stale, receipt), "a stale token published readiness")
    need(ctx.pg.ack(first, receipt), "the live lease could not publish readiness")

    with ctx.pg.admin() as conn:
        row = conn.execute(
            "SELECT state, result_sha256, lease_token, lease_until FROM control.projection_jobs"
            " WHERE workspace_id=%s AND location_id=%s AND document_id=%s AND revision=1 AND action='index'",
            (fx.ws, fx.falkor_loc, fx.document),
        ).fetchone()
    need(row[0] == "ready", f"state after ack is {row[0]}")
    need(row[1] == receipt, "stored receipt is not the submitted receipt")
    need(row[2] is None and row[3] is None, "acknowledgement left a lease behind")


@check("02", "PG-12", "An expired lease is reclaimable and the old token can no longer publish")
def pg12(ctx: Context) -> None:
    fx = ctx.fixture
    _reset_fixture_jobs(ctx)
    first = ctx.pg.claim(1)
    need(first is not None, "nothing to claim")
    need(str(first["location_id"]) == fx.falkor_loc, "claimed a job outside the fixture")
    with ctx.pg.admin() as conn:
        conn.execute(
            "UPDATE control.projection_jobs SET lease_until = now() - interval '5 seconds'"
            " WHERE workspace_id=%s AND location_id=%s",
            (fx.ws, fx.falkor_loc),
        )
    # Restore the surreal job so the only reclaimable row is still the falkor one.
    second = ctx.pg.claim(60)
    need(second is not None, "expired lease was not reclaimable")
    need(second["lease_token"] != first["lease_token"], "reclaim reused the previous token")
    need(str(second["location_id"]) == fx.falkor_loc, f"reclaim took another job: {second['location_id']}")

    receipt = ids.projection_receipt(
        workspace_id=fx.ws, collection_id=fx.coll, document_id=fx.document, revision=1,
        location_id=fx.falkor_loc, recipe_sha256="a" * 64, manifest_sha256="d" * 64,
        action="index", payload_sha256=ids.index_payload_sha256([]),
    )
    need(not ctx.pg.ack(first, receipt), "expired token published readiness")
    need(ctx.pg.ack(second, receipt), "current token could not publish readiness")
