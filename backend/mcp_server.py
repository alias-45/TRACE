"""TRACE MCP server.

Exposes TRACE's fixed, allowlisted graph tools over MCP (stdio), one MCP tool per
catalogue entry (see main.py::execute_tool / GET /api/tools) -- so an MCP client picks
among the same atomic, Cypher-backed tools TRACE's internal tool-choosing agent uses,
not one black-box "investigate" call. Also exposes the higher-level investigate /
overview / status endpoints for a client that wants the bounded multi-step agent instead.

This process never touches FalkorDB directly: every call is an HTTP request to the
running FastAPI app (TRACE_API_URL), so tenant auth (X-Tenant-ID / X-Tenant-Key) and the
"no caller-supplied Cypher" guarantee live in exactly one place -- main.py -- rather than
being re-implemented here.

Run from backend/: `python mcp_server.py`. Requires the FastAPI service to be running
and reachable at TRACE_API_URL (default http://127.0.0.1:8000).
"""
import json
from importlib import import_module
import os
import urllib.error
import urllib.request

try:
    FastMCP = import_module("mcp.server.fastmcp").FastMCP
except ModuleNotFoundError as exc:
    if exc.name == "mcp" or (exc.name or "").startswith("mcp."):
        raise ModuleNotFoundError(
            "The MCP Python SDK is required. Install it with `pip install mcp`."
        ) from exc
    raise

mcp = FastMCP("TRACE Investigation Tools")
API_URL = os.getenv("TRACE_API_URL", "http://127.0.0.1:8000").rstrip("/")
DEFAULT_TENANT_ID = os.getenv("DEFAULT_TENANT_ID", "default")

# Static fallback so the server still starts and exposes tools even if the FastAPI app
# isn't reachable yet at import time; a live GET /api/tools (fetched lazily, see below)
# is the source of truth for descriptions once the API is up.
_FALLBACK_CATALOG = {
    "lookup_incident": "Read the requested incident record.",
    "lookup_alerts": "Read alerts attached to the requested incident.",
    "lookup_evidence": "Read evidence attached to alerts of the requested incident.",
    "correlate_graph_paths": "Find recorded multi-hop graph correlations to other incidents.",
    "recall_memory": "Read prior Memory records for the requested incident.",
    "shortest_path_to_critical_service": "Graph algorithm (FalkorDB algo.SPpaths, with Python fallback): shortest access path from each incident device to a high/critical service.",
    "rank_entities_pagerank": "Graph algorithm (FalkorDB algo.pageRank, with Python fallback): rank users, devices and services by structural centrality.",
}


def _request(path: str, tenant_id: str, method: str = "GET", payload: dict | None = None):
    if not tenant_id or len(tenant_id) > 63 or not tenant_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("tenant_id must be 1-63 alphanumeric, underscore, or hyphen characters")
    body = json.dumps(payload or {}).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if tenant_id != DEFAULT_TENANT_ID:
        headers["X-Tenant-ID"] = tenant_id
    tenant_key = os.getenv("TRACE_TENANT_KEY", "")
    if tenant_key:
        headers["X-Tenant-Key"] = tenant_key
        headers["X-Tenant-ID"] = tenant_id
    req = urllib.request.Request(API_URL + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:1000]
        raise RuntimeError(f"TRACE API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"TRACE API unavailable at {API_URL}: {exc.reason}") from exc


def _fetch_catalog() -> dict:
    """Best-effort live fetch of GET /api/tools; falls back to the static list above so
    tool registration below never blocks on the API being up yet."""
    try:
        body = _request("/api/tools", DEFAULT_TENANT_ID)
        tools = body.get("tools") or {}
        if tools:
            return {name: (spec.get("description") or _FALLBACK_CATALOG.get(name, name))
                    for name, spec in tools.items()}
    except Exception:
        pass
    return dict(_FALLBACK_CATALOG)


# --- One MCP tool per atomic catalogue entry -------------------------------------------
# Each becomes POST /api/tools/{name}, i.e. exactly the tool an MCP client picked, nothing
# auto-chosen in between. depth only affects correlate_graph_paths; the API ignores it
# harmlessly for every other tool.
def _make_atomic_tool(name: str, description: str):
    def _tool(incident_id: str, tenant_id: str = DEFAULT_TENANT_ID, depth: int = 2) -> dict:
        return _request(f"/api/tools/{name}", tenant_id, "POST",
                         {"incident_id": incident_id, "depth": depth})
    _tool.__name__ = name
    _tool.__doc__ = description
    return _tool


for _name, _description in _fetch_catalog().items():
    mcp.tool()(_make_atomic_tool(_name, _description))


# --- Higher-level tools: the bounded multi-step agent and plain reads ------------------
@mcp.tool()
def investigate_incident(tenant_id: str, incident_id: str, prompt: str = "Investigate the incident, inspect graph paths and evidence, and report remaining knowledge gaps.", max_steps: int = 8, depth: int = 2) -> dict:
    """Run TRACE's bounded, LLM-directed multi-step investigation for one incident, letting
    the model choose among the same atomic tools exposed individually above. Use the
    individual tools instead when you want to pick the next step yourself."""
    return _request("/api/agents/graph-investigate", tenant_id, "POST", {
        "incident_id": incident_id, "prompt": prompt,
        "max_steps": max(1, min(int(max_steps), 8)), "depth": max(1, min(int(depth), 5)),
    })


@mcp.tool()
def list_incidents(tenant_id: str) -> dict:
    """Return incidents and graph statistics for the selected tenant."""
    return _request("/api/overview", tenant_id)


@mcp.tool()
def ollama_status(tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    """Check whether the configured Ollama model is reachable by the TRACE backend."""
    return _request("/api/agents/status", tenant_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
