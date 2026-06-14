#!/usr/bin/env python3
"""WorldHub MCP — read/write a WorldHub world as the logged-in neowow user.

Auth chain: local deploy-token (~/.hermes/webui/neowow.json) -> GET
/api/worldhub/token -> Supabase JWT -> Supabase PostgREST (schema worldhub),
all gated by the world's RLS. Mirrors webui/mcp_server.py (stdio).

    pip install mcp
    python3 worldhub_mcp.py   # start via stdio

config.yaml:
    mcp_servers:
      worldhub:
        command: /path/to/venv/bin/python3
        args: [/path/to/hermes-webui/worldhub_mcp.py]
"""
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

_REPO_ROOT = Path(__file__).parent.resolve()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import os

APP_BASE       = os.environ.get("WORLDHUB_APP_BASE", "https://app.neowow.studio")
TOKEN_ENDPOINT = APP_BASE + "/api/worldhub/token"
SUPABASE_URL   = os.environ.get("WORLDHUB_SUPABASE_URL", "https://nazmftasoknlcwnlftow.supabase.co")
SUPABASE_ANON  = os.environ.get(
    "WORLDHUB_SUPABASE_ANON",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5hem1mdGFzb2tubGN3bmxmdG93Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODEzMTU5MzgsImV4cCI6MjA5Njg5MTkzOH0."
    "b8ljzKl_UbZ9db7WpMzW0m7xofz0j7DZpBlG3ylLPAk")
REST   = SUPABASE_URL + "/rest/v1"
SCHEMA = "worldhub"

ENTITY_TYPES = ["systems","locations","factions","species","cultures","events",
                "characters","abilities","items","storylines","other"]
EDGE_TYPES   = ["located_in","member_of","ruled_by","governs","controls","ally_of",
                "enemy_of","worships","belongs_to_species","wields","derived_from",
                "owned_by","owns","involves","caused_by","led_to","triggered_by",
                "created","related_to"]
_MUTEX_EDGE_PAIRS = [("ally_of","enemy_of")]

server = Server("worldhub")
_jwt_cache = {"token": "", "exp": 0.0}


def _jwt_sub(jwt: str) -> str:
    """Extract the `sub` claim from a JWT without verifying the signature."""
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("sub", "") or ""
    except Exception:
        return ""


def _deploy_token() -> str:
    """Local neowow deploy-token (or login JWT) from ~/.hermes/webui/neowow.json."""
    from api.neowow import _read_state
    st = _read_state() or {}
    return (st.get("token") or st.get("jwt") or "").strip()


def _http(method, url, *, headers, body=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
        return resp.status, (json.loads(raw) if raw.strip() else None)


def _supabase_jwt() -> str:
    """Exchange the deploy-token for a Supabase JWT via the identity bridge; cache 1h."""
    if _jwt_cache["token"] and time.time() < _jwt_cache["exp"] - 60:
        return _jwt_cache["token"]
    tok = _deploy_token()
    if not tok:
        raise RuntimeError("No neowow token saved — open Hermes WebUI settings and paste your neowow Token.")
    status, body = _http("GET", TOKEN_ENDPOINT,
                         headers={"Authorization": f"Bearer {tok}"})
    jwt = (body or {}).get("token", "")
    if not jwt:
        raise RuntimeError(f"Identity bridge returned no token (HTTP {status}).")
    _jwt_cache["token"] = jwt
    _jwt_cache["exp"] = time.time() + int((body or {}).get("expires_in", 3600))
    return jwt


def _rest(method, path, *, params=None, body=None, prefer=None):
    """Call Supabase PostgREST as the logged-in user. Returns parsed JSON (or None)."""
    from urllib.parse import urlencode
    url = REST + path
    if params:
        url += "?" + urlencode(params, safe="*,()")
    headers = {
        "apikey": SUPABASE_ANON,
        "Authorization": f"Bearer {_supabase_jwt()}",
        "Content-Type": "application/json",
    }
    if method == "GET":
        headers["Accept-Profile"] = SCHEMA
    else:
        headers["Content-Profile"] = SCHEMA
    if prefer:
        headers["Prefer"] = prefer
    status, data = _http(method, url, headers=headers, body=body)
    return data


def _detect_conflicts(existing, draft):
    """Mechanical (deterministic) consistency check of a draft against canon.

    existing: {"entities":[{ent_id,name,aliases,...}], "edges":[{source,target,type}]}
    draft:    {"entities":[{id,name,type,...}], "relations":[{source,target,type}],
               "edits":[{id,...}], "deletes":[id]}
    Returns a list of {kind, detail} conflicts (empty = clean).
    """
    conflicts = []
    ents = existing.get("entities", [])
    by_id = {e["ent_id"]: e for e in ents}
    # name/alias -> ent_id (lowercased)
    name_index = {}
    for e in ents:
        for label in [e.get("name", "")] + list(e.get("aliases") or []):
            if label:
                name_index[label.strip().lower()] = e["ent_id"]

    new_ids = set(by_id)  # grows with draft new entities
    for d in draft.get("entities", []):
        did = d.get("id", "")
        if did in by_id:
            conflicts.append({"kind": "duplicate_id", "detail": f"实体 id 已存在：{did}（编辑应走 edits 并复用该 id）"})
        t = d.get("type", "other")
        if t not in ENTITY_TYPES:
            conflicts.append({"kind": "bad_entity_type", "detail": f"实体 {did} 的 type 不在受支持枚举：{t}"})
        for label in [d.get("name", "")] + list(d.get("aliases") or []):
            key = (label or "").strip().lower()
            if key and key in name_index and name_index[key] != did:
                conflicts.append({"kind": "name_collision", "detail": f"名称/别名「{label}」已属于 {name_index[key]}"})
        if did:
            new_ids.add(did)

    existing_edge_keys = {(e["source"], e["target"], e["type"]) for e in existing.get("edges", [])}
    existing_pairs = {(e["source"], e["target"], e["type"]) for e in existing.get("edges", [])}
    for r in draft.get("relations", []):
        s, tg, ty = r.get("source"), r.get("target"), r.get("type", "related_to")
        if ty not in EDGE_TYPES:
            conflicts.append({"kind": "bad_edge_type", "detail": f"关系 type 不在受支持枚举：{ty}"})
        if s not in new_ids:
            conflicts.append({"kind": "dangling_edge", "detail": f"关系 source 指向不存在的实体：{s}"})
        if tg not in new_ids:
            conflicts.append({"kind": "dangling_edge", "detail": f"关系 target 指向不存在的实体：{tg}"})
        for a, b in _MUTEX_EDGE_PAIRS:
            other = b if ty == a else (a if ty == b else None)
            if other and ((s, tg, other) in existing_pairs or (tg, s, other) in existing_pairs):
                conflicts.append({"kind": "mutually_exclusive_edge",
                                  "detail": f"{s}↔{tg} 已有互斥关系 {other}，与新增 {ty} 冲突"})

    for d in draft.get("deletes", []) or []:
        if d not in by_id:
            conflicts.append({"kind": "delete_missing", "detail": f"要删除的实体不存在：{d}"})
    for ed in draft.get("edits", []) or []:
        if ed.get("id") not in by_id:
            conflicts.append({"kind": "edit_missing", "detail": f"要编辑的实体不存在：{ed.get('id')}"})
    return conflicts


def _resolve_world(world):
    """Return the world row by id or slug, or None."""
    rows = _rest("GET", "/worlds", params={
        "or": f"(id.eq.{world},slug.eq.{world})", "select": "*", "limit": 1})
    return rows[0] if rows else None


def _list_worlds_data():
    worlds = _rest("GET", "/worlds", params={
        "select": "id,slug,name,visibility,created_by", "order": "updated_at.desc"}) or []
    mems = _rest("GET", "/world_members", params={"select": "world_id,role,user_id"}) or []
    me = _jwt_sub(_supabase_jwt())
    role_by_world = {m["world_id"]: m["role"] for m in mems if m.get("user_id") == me}
    for w in worlds:
        w["my_role"] = role_by_world.get(w["id"])
    return worlds


def _get_world_data(world):
    w = _resolve_world(world)
    if not w:
        return {"error": f"world not found: {world}"}
    wid = w["id"]
    entities = _rest("GET", "/entities", params={
        "world_id": f"eq.{wid}",
        "select": "ent_id,type,name,aliases,tags,status,summary,fields,body",
        "order": "type.asc"}) or []
    edges = _rest("GET", "/edges", params={
        "world_id": f"eq.{wid}", "select": "source,target,type,note"}) or []
    overview = {k: w.get(k) for k in
                ("id","slug","name","genre","tone","premise","themes","narrative_form","visibility","created_by")}
    return {"overview": overview, "entities": entities, "edges": edges,
            "supported_entity_types": ENTITY_TYPES, "supported_edge_types": EDGE_TYPES}


def _search_entities_data(world, query):
    data = _get_world_data(world)
    if "error" in data:
        return data
    q = (query or "").strip().lower()
    def hit(e):
        hay = " ".join([e.get("name",""), e.get("type",""), e.get("summary","")]
                       + list(e.get("aliases") or []) + list(e.get("tags") or [])).lower()
        return q in hay
    return [e for e in data["entities"] if hit(e)]


def _my_role(world_id):
    me = _jwt_sub(_supabase_jwt())
    rows = _rest("GET", "/world_members", params={
        "world_id": f"eq.{world_id}", "user_id": f"eq.{me}", "select": "role", "limit": 1})
    return rows[0]["role"] if rows else None


def _submit_world_changes_data(world, changes, summary):
    w = _resolve_world(world)
    if not w:
        return {"error": f"world not found: {world}"}
    wid = w["id"]
    role = _my_role(wid)
    if role is None:                       # auto-join public/unlisted as contributor
        _rest("POST", "/rpc/join_world", body={"w": wid})
        role = "contributor"
    is_reviewer = role in ("founder", "admin")
    me = _jwt_sub(_supabase_jwt())

    proposals = []
    ents = changes.get("entities") or []
    rels = changes.get("relations") or []
    if ents or rels:
        proposals.append(("ingest", {"entities": ents, "relations": rels}))
    for ed in changes.get("edits") or []:
        proposals.append(("edit_node", {"id": ed["id"], "patch": ed.get("patch", {})}))
    for d in changes.get("deletes") or []:
        proposals.append(("delete_node", {"id": d}))

    created_ids = []
    for kind, payload in proposals:
        rows = _rest("POST", "/proposals",
                     body={"world_id": wid, "kind": kind, "payload": payload,
                           "summary": summary, "by_user": me, "status": "pending"},
                     prefer="return=representation")
        pid = (rows[0]["id"] if rows else None)
        created_ids.append(pid)
        if is_reviewer and pid:
            _rest("POST", "/rpc/approve_proposal", body={"p_id": pid})

    return {"applied": is_reviewer, "proposals_created": len(created_ids),
            "proposal_ids": created_ids, "role": role}


def _ok(obj):
    return [TextContent(type="text", text=json.dumps(obj, ensure_ascii=False, indent=2))]


async def handle_list_worlds(_a):       return _ok(_list_worlds_data())
async def handle_get_world(a):          return _ok(_get_world_data(a.get("world", "")))
async def handle_search_entities(a):    return _ok(_search_entities_data(a.get("world", ""), a.get("query", "")))
async def handle_submit(a):             return _ok(_submit_world_changes_data(a.get("world", ""), a.get("changes", {}), a.get("summary", "")))


async def handle_check_consistency(a):
    data = _get_world_data(a.get("world", ""))
    if "error" in data:
        return _ok(data)
    conflicts = _detect_conflicts(data, a.get("draft", {}))
    return _ok({"conflicts": conflicts, "ok": not conflicts})


_CHANGES_SCHEMA = {
    "type": "object",
    "properties": {
        "entities":  {"type": "array", "items": {"type": "object"},
                      "description": "新增实体，对象用 id/type/name/aliases/tags/status/summary/fields/body"},
        "relations": {"type": "array", "items": {"type": "object"},
                      "description": "新增关系，对象用 source/target/type/note"},
        "edits":     {"type": "array", "items": {"type": "object"},
                      "description": "编辑既有实体：{id, patch:{name?,status?,summary?,body?,aliases?,tags?,fields?}}"},
        "deletes":   {"type": "array", "items": {"type": "string"}, "description": "要删除的 ent_id"},
    },
}

TOOLS = [
    Tool(name="list_worlds", description="列出我可见/参与的世界（含我的角色）。", inputSchema={"type": "object", "properties": {}, "required": []}),
    Tool(name="get_world", description="读取一个世界的完整设定（概述+全部实体+全部关系），用于一致性参考。",
         inputSchema={"type": "object", "properties": {"world": {"type": "string", "description": "世界 id 或 slug"}}, "required": ["world"]}),
    Tool(name="search_entities", description="在一个世界里按名称/别名/标签/类型/概述检索实体。",
         inputSchema={"type": "object", "properties": {"world": {"type": "string"}, "query": {"type": "string"}}, "required": ["world", "query"]}),
    Tool(name="check_consistency", description="把拟写入的 draft 与世界现有设定做机械式冲突检测（重复id/重名/悬空关系/互斥关系/越界type）。写回前必调。",
         inputSchema={"type": "object", "properties": {"world": {"type": "string"}, "draft": _CHANGES_SCHEMA}, "required": ["world", "draft"]}),
    Tool(name="submit_world_changes", description="把 changes 写回世界。贡献者→待审提案；创始人/管理员→直接生效。",
         inputSchema={"type": "object", "properties": {"world": {"type": "string"}, "changes": _CHANGES_SCHEMA, "summary": {"type": "string"}}, "required": ["world", "changes", "summary"]}),
]

HANDLERS = {
    "list_worlds": handle_list_worlds,
    "get_world": handle_get_world,
    "search_entities": handle_search_entities,
    "check_consistency": handle_check_consistency,
    "submit_world_changes": handle_submit,
}


@server.list_tools()
async def list_tools():
    return TOOLS


@server.call_tool()
async def call_tool(name, arguments):
    handler = HANDLERS.get(name)
    if not handler:
        return _ok({"error": f"Unknown tool: {name}"})
    try:
        return await handler(arguments or {})
    except Exception as e:
        return _ok({"error": str(e)})


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio as _asyncio
    _asyncio.run(main())
