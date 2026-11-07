"""Fixture: a small, realistic registry used by the acceptance checks.

Shape (deliberately exercising the spec's edge cases):

    workspace ws
      user  u    member + invoker of both profiles
      user  v    member, NOT an invoker
      user  out  member of another workspace only
      collection c1
        location surreal  g1 active
        location falkor   g1 active
        document d1   revision 1  indexed on surreal only  (partial coverage)
      collection c2 (second workspace)   <- cross-workspace denial target

Two profiles share c1: one hybrid, one graph-only. The spec's first acceptance scenario is
precisely that both must see the same registered document and projection identities.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from . import ids


def _h(ch: str) -> str:
    return ch * 64


@dataclass
class Fixture:
    ws: str
    ws_foreign: str
    user: str
    member_no_invoke: str
    outsider: str
    coll: str
    coll_foreign: str
    agent: str
    profile_hybrid: str
    profile_graph: str
    profile_document: str
    surreal_loc: str
    falkor_loc: str
    document: str
    routeless_profile: str
    created: list[str] = field(default_factory=list)

    @property
    def surreal_key(self) -> str:
        return ids.surreal_physical_key(self.ws, self.coll, 1)

    @property
    def falkor_key(self) -> str:
        return ids.falkor_graph_key(self.coll, 1)

    @property
    def surreal_ns(self) -> str:
        return ids.surreal_namespace(self.ws)

    @property
    def surreal_db(self) -> str:
        return ids.surreal_database(self.coll, 1)

    @property
    def doc_revision_key(self) -> str:
        return f"{self.document}/1"


def seed(conn) -> Fixture:
    ws, ws_f = str(uuid.uuid4()), str(uuid.uuid4())
    user, member, outsider = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    coll, coll_f = str(uuid.uuid4()), str(uuid.uuid4())
    agent = str(uuid.uuid4())
    p_hyb, p_graph, p_doc, p_none = (str(uuid.uuid4()) for _ in range(4))
    s_loc, f_loc = str(uuid.uuid4()), str(uuid.uuid4())
    doc = str(uuid.uuid4())

    suffix = uuid.uuid4().hex[:8]

    conn.execute(
        "INSERT INTO auth.users(id, email) VALUES (%s,%s),(%s,%s),(%s,%s)",
        (user, f"u-{suffix}@x", member, f"m-{suffix}@x", outsider, f"o-{suffix}@x"),
    )
    conn.execute(
        "INSERT INTO control.workspaces(id, slug) VALUES (%s,%s),(%s,%s)",
        (ws, f"ws-{suffix}", ws_f, f"wsf-{suffix}"),
    )
    conn.execute(
        "INSERT INTO control.workspace_members(workspace_id,user_id,role) VALUES "
        "(%s,%s,'owner'),(%s,%s,'member'),(%s,%s,'owner')",
        (ws, user, ws, member, ws_f, outsider),
    )
    conn.execute(
        "INSERT INTO control.agents(workspace_id,id,slug) VALUES (%s,%s,%s)", (ws, agent, f"a-{suffix}")
    )
    conn.execute(
        "INSERT INTO control.agent_profiles(workspace_id,id,agent_id,slug,mode) VALUES "
        "(%s,%s,%s,%s,'hybrid'),(%s,%s,%s,%s,'graph'),(%s,%s,%s,%s,'document'),(%s,%s,%s,%s,'fallback')",
        (
            ws, p_hyb, agent, f"p-hyb-{suffix}",
            ws, p_graph, agent, f"p-graph-{suffix}",
            ws, p_doc, agent, f"p-doc-{suffix}",
            ws, p_none, agent, f"p-none-{suffix}",
        ),
    )
    conn.execute(
        "INSERT INTO control.profile_invokers(workspace_id,profile_id,user_id) VALUES "
        "(%s,%s,%s),(%s,%s,%s),(%s,%s,%s)",
        (ws, p_hyb, user, ws, p_graph, user, ws, p_doc, user),
    )
    conn.execute(
        "INSERT INTO control.collections(workspace_id,id,slug) VALUES (%s,%s,%s),(%s,%s,%s)",
        (ws, coll, f"c-{suffix}", ws_f, coll_f, f"cf-{suffix}"),
    )
    conn.execute(
        "INSERT INTO control.profile_routes(workspace_id,profile_id,collection_id,backend,rank) VALUES "
        "(%s,%s,%s,'surreal',1),(%s,%s,%s,'falkor',2),(%s,%s,%s,'surreal',1),(%s,%s,%s,'surreal',1)",
        (ws, p_hyb, coll, ws, p_hyb, coll, ws, p_graph, coll, ws, p_doc, coll),
    )
    conn.execute(
        "INSERT INTO control.backend_locations"
        "(workspace_id,collection_id,id,backend,generation,endpoint_ref,physical_key,recipe_sha256,state) VALUES "
        "(%s,%s,%s,'surreal',1,'surreal-default',%s,%s,'active'),"
        "(%s,%s,%s,'falkor',1,'falkor-default',%s,%s,'active')",
        (
            ws, coll, s_loc, ids.surreal_physical_key(ws, coll, 1), _h("a"),
            ws, coll, f_loc, ids.falkor_graph_key(coll, 1), _h("a"),
        ),
    )
    conn.execute(
        "INSERT INTO control.documents(workspace_id,collection_id,id,source_key) VALUES (%s,%s,%s,%s)",
        (ws, coll, doc, f"src/{suffix}"),
    )
    conn.execute(
        "INSERT INTO control.document_revisions"
        "(workspace_id,collection_id,document_id,revision,title,media_type,source_object_key,source_sha256,"
        " extracted_object_key,extracted_sha256,manifest_object_key,manifest_sha256,parser_recipe,chunk_count) "
        "VALUES (%s,%s,%s,1,'Acceptance fixture','text/plain','src/obj',%s,'ext/obj',%s,'man/obj',%s,'parser/1',3)",
        (ws, coll, doc, _h("b"), _h("c"), _h("d")),
    )
    conn.execute(
        "UPDATE control.documents SET current_revision = 1 WHERE workspace_id=%s AND collection_id=%s AND id=%s",
        (ws, coll, doc),
    )
    # Ready on surreal, still queued on falkor: the spec's "publish a revision with only one
    # backend ready" scenario, so partial coverage is exercised by every later check.
    conn.execute(
        "INSERT INTO control.projection_jobs"
        "(workspace_id,collection_id,document_id,revision,location_id,action,state,result_sha256) VALUES "
        "(%s,%s,%s,1,%s,'index','ready',%s),(%s,%s,%s,1,%s,'index','queued',NULL)",
        (ws, coll, doc, s_loc, _h("e"), ws, coll, doc, f_loc),
    )
    return Fixture(
        ws=ws, ws_foreign=ws_f, user=user, member_no_invoke=member, outsider=outsider,
        coll=coll, coll_foreign=coll_f, agent=agent,
        profile_hybrid=p_hyb, profile_graph=p_graph, profile_document=p_doc,
        surreal_loc=s_loc, falkor_loc=f_loc, document=doc,
        routeless_profile=p_none,
    )


def teardown(conn, fx: Fixture) -> None:
    """Remove fixture rows. Order follows the foreign keys, not convenience."""
    for ws_id in (fx.ws, fx.ws_foreign):
        conn.execute(
            "DELETE FROM control.projection_jobs WHERE workspace_id = %s", (ws_id,)
        )
        conn.execute(
            "DELETE FROM control.run_events WHERE workspace_id = %s", (ws_id,)
        )
        conn.execute("DELETE FROM control.run_steps WHERE workspace_id = %s", (ws_id,))
        conn.execute("DELETE FROM control.agent_runs WHERE workspace_id = %s", (ws_id,))
        conn.execute("DELETE FROM control.messages WHERE workspace_id = %s", (ws_id,))
        conn.execute("DELETE FROM control.conversations WHERE workspace_id = %s", (ws_id,))
        conn.execute("DELETE FROM control.audit_log WHERE workspace_id = %s", (ws_id,))
    conn.execute(
        "UPDATE control.documents SET current_revision = NULL WHERE workspace_id = %s", (fx.ws,)
    )
    conn.execute("DELETE FROM control.document_revisions WHERE workspace_id = %s", (fx.ws,))
    conn.execute("DELETE FROM control.documents WHERE workspace_id = %s", (fx.ws,))
    conn.execute("DELETE FROM control.profile_routes WHERE workspace_id = %s", (fx.ws,))
    conn.execute("DELETE FROM control.backend_locations WHERE workspace_id = %s", (fx.ws,))
    conn.execute("DELETE FROM control.profile_invokers WHERE workspace_id = %s", (fx.ws,))
    conn.execute("DELETE FROM control.agent_versions WHERE workspace_id = %s", (fx.ws,))
    conn.execute("DELETE FROM control.agent_profiles WHERE workspace_id = %s", (fx.ws,))
    conn.execute("DELETE FROM control.agents WHERE workspace_id = %s", (fx.ws,))
    conn.execute("DELETE FROM control.collections WHERE workspace_id IN (%s,%s)", (fx.ws, fx.ws_foreign))
    conn.execute("DELETE FROM control.workspace_members WHERE workspace_id IN (%s,%s)", (fx.ws, fx.ws_foreign))
    conn.execute("DELETE FROM control.workspaces WHERE id IN (%s,%s)", (fx.ws, fx.ws_foreign))
