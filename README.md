# TRACE

Security incident triage on FalkorDB: a multi-tenant agent that correlates incidents through the access graph instead of a shared field, runs FalkorDB's own graph algorithms where it can, exposes its tools over MCP one at a time, and cannot name an entity the graph doesn't contain.

A 2023 survey of 2,000 security operations analysts found SOCs facing an average of roughly 4,500 alerts a day, with most left unaddressed ([Vectra AI, reported by stealthagents.com](https://stealthagents.com/research/cybersecurity-alert-triage-workload-statistics-2026)). The bottleneck isn't reading one alert — it's knowing that this alert and three others from last week point at the same exposed service through a path nobody wrote down. TRACE walks that path in FalkorDB: users, devices and services connected by `LOGGED_IN_FROM`, `CONNECTS_TO` and `HAS_ACCESS_TO`, so two incidents on unrelated devices can still be shown to share a route into the same high-criticality service, with the hops as the evidence.

Built for Graph Hacks (FalkorDB × WeMakeDevs, October 2026). Primary track: **01 Agents That Act on Connected Data**. Partial fit, not separately submitted: **02 Agent Memory and Coordination** 

## What the graph does here

Every correlation, path and ranking below is a Cypher query or a FalkorDB graph algorithm — a flat alert table has no `criticality` field to filter on and no path between two devices that never appear in the same row.

| Step | What runs | What it returns |
|---|---|---|
| Correlate an incident | `backend/trace_agents.py::_correlate_cypher` — `Alert→Device, (Device)-[access]-*1..d-(Service)-*1..d-(Device)→Alert` through a high/critical service, `d` a validated depth of 1–5 | other incidents reachable through a shared service, each with the node path as `via_service` and `path` |
| Shortest path to risk | `CALL algo.SPpaths` (native), BFS fallback — from an incident's devices to the nearest high/critical service | hop count and the named route, or `reachable: false` if none is recorded |
| Rank by centrality | `CALL algo.pageRank` (native, run per label/relationship-type pair and aggregated), Python fallback | the most structurally central users/devices/services, and where this incident's devices rank |
| Resume | `recall_memory` tool — `(Memory)-[:ABOUT]->(Incident)`, scoped to the active tenant's graph | prior findings for this incident, with when they were written |

Depth (1–5) is a real, validated parameter, not a fixed constant — the UI has a slider, and both `/api/investigate` and `/api/agents/graph-investigate` take it, so the same query can be run shallow and then re-run deep to show a link depth 1 misses.

## Multi-tenant by default

Every request selects a physically separate FalkorDB graph: `db.select_graph(f"ops:{tenant_id}")`, carried per-request in a `ContextVar`, never a shared mutable handle. Two tenants can both have an incident `I001` with no cross-tenant read possible — each query runs against a different graph entirely, not a shared graph filtered by a tenant field.

- Default tenant: `ops:default`, used when no tenant headers are sent.
- Non-default tenants require `TENANT_API_KEYS` to be configured server-side (`{"team-a": "long-random-secret", ...}`) and the caller to send matching `X-Tenant-ID` + `X-Tenant-Key` headers. A caller cannot simply claim a tenant name: a key that doesn't match → `401`; a tenant header that doesn't match the key's owner → `403`; a non-default tenant with no keys configured at all → `403`.
- `load_trace.py`, `check_graph.py`, `query.py`, `verify_persistence.py` and `verify_graph_dependency.py` all target `ops:default`, consistently.

## How the agent stays honest

Two independent agent paths read and write the same tenant's graph, and both pass through the same two checks before anything reaches the person: **grounding** and **evidence gating**.

**Grounding** (`backend/agents.py::ground`, `known_ids`): every sentence a model writes is split and checked against the exact set of IDs/names that came back from the graph for that run. A sentence citing anything else — a plausible but non-existent service, device or incident ID — is dropped before it's shown.

**Evidence gating** (`backend/main.py::approve`): `POST /api/actions/{id}/approve` fails closed — `409`, not a silent no-op — if the incident has no evidence at all, or if any linked evidence is unverified. It fails open the other way too: an already-approved action can't be rejected and vice versa, so a decision, once made, is final.

| Outcome | When | What's returned |
|---|---|---|
| Correlation found | traversal succeeds | the path, plus `evidence_state`: `verified_evidence` / `unverified_evidence` / `partial_evidence` / `no_evidence_recorded` |
| No path recorded | traversal returns empty | `knowledge_status: no_relevant_path_recorded` — explicitly *not* the same claim as "nothing is wrong" |
| Unknown entity | the id isn't in the active tenant's graph | `404`, `code: unknown_entity`, no traversal runs |
| Approval blocked | evidence missing/unverified | `409` with the unverified evidence ids and the required next step |

## Tools: one HTTP endpoint, one MCP tool, one dispatcher

Agents never write Cypher. Every tool is a human-written, parameterised query, dispatched from a single function — `backend/main.py::execute_tool` — that both surfaces below call into, so they carry identical guarantees: a fixed allowlist, no caller-supplied Cypher, the same tenant-scoped `run`.

| Tool | What it does |
|---|---|
| `lookup_incident` | Read the requested incident record |
| `lookup_alerts` | Read alerts attached to the requested incident |
| `lookup_evidence` | Read evidence attached to alerts of the requested incident |
| `correlate_graph_paths` | Multi-hop graph correlation to other incidents, at the requested depth |
| `recall_memory` | Read prior `Memory` records for the requested incident |
| `shortest_path_to_critical_service` | `algo.SPpaths` (native) / BFS (fallback) |
| `rank_entities_pagerank` | `algo.pageRank` (native, aggregated) / Python PageRank (fallback) |

- **`GET /api/tools`** — the live catalogue (same descriptions as above).
- **`POST /api/tools/{tool_name}`** — run exactly one tool for one incident. This is the atomic surface an MCP client picks from directly.
- **`POST /api/agents/graph-investigate`** — the bounded, LLM-directed loop: the model picks which of the same seven tools to call next, up to `max_steps`, never repeating one.
- **`backend/mcp_server.py`** — an MCP server (stdio) exposing one MCP tool per catalogue entry above, plus three higher-level tools (`investigate_incident`, `list_incidents`, `ollama_status`). It never touches FalkorDB directly; every call is an HTTP request to the running FastAPI app, so tenant auth lives in exactly one place. Verified end-to-end in this repo: started the API with a fake graph, called `lookup_incident`/`lookup_alerts` through the actual MCP tool functions, and confirmed an unknown incident correctly surfaces `404`/`unknown_entity` through the MCP layer rather than being swallowed.

```bash
cd backend && uvicorn main:app --reload --port 8000   # terminal 1
cd backend && python mcp_server.py                     # terminal 2 (stdio MCP server)
```

## Cypher the product depends on

The correlation rule — the one query the whole project leans on, depth validated as an int 1–5 before interpolation (Cypher has no way to parameterise a variable-length bound):

```cypher
MATCH (a:Alert {incident_id: $iid})-[:ON_DEVICE]->(d:Device)
MATCH p = (d)-[:CONNECTS_TO|OWNED_BY|HAS_ACCESS_TO*1..{depth}]-(s:Service)
          -[:CONNECTS_TO|OWNED_BY|HAS_ACCESS_TO*1..{depth}]-(d2:Device)
WHERE d2.device_id <> d.device_id AND s.criticality IN ['high', 'critical']
MATCH (a2:Alert {device_id: d2.device_id}) WHERE a2.incident_id <> $iid
MATCH (i2:Incident {incident_id: a2.incident_id})
RETURN DISTINCT i2.incident_id, i2.title, s.name, a.alert_id, a2.alert_id,
       [n IN nodes(p) | [labels(n)[0], properties(n)]],
       [r IN relationships(p) | type(r)]
LIMIT 20
```

The two graph algorithms, run natively against FalkorDB first:

```cypher
-- shortest_path_to_critical_service (per device/service pair; weightProp is required
-- by SPpaths, so unweighted access edges are backfilled with weight=1 once)
MATCH (source:Device {device_id:$device_id}), (target:Service {service_id:$service_id})
CALL algo.SPpaths({sourceNode:source, targetNode:target,
  relTypes:['CONNECTS_TO','OWNED_BY','HAS_ACCESS_TO'], weightProp:'weight',
  relDirection:'both', pathCount:1}) YIELD path, pathWeight
RETURN [n IN nodes(path) | {labels:labels(n), props:properties(n)}], pathWeight LIMIT 1

-- rank_entities_pagerank (algo.pageRank takes exactly one label and one relationship
-- type per call, so all 9 User/Device/Service x CONNECTS_TO/OWNED_BY/HAS_ACCESS_TO
-- combinations are run and the scores aggregated -- a documented heuristic, not a
-- claim of true multi-relationship PageRank)
CALL algo.pageRank('Device', 'CONNECTS_TO') YIELD node, score
RETURN labels(node)[0], properties(node), score
```

If the installed FalkorDB doesn't expose the `algo.*` module (older/self-hosted builds), both tools catch the failure and fall back to the pure-Python BFS/PageRank in `graph_algos.py`, tagging the response `native_algorithm_fallback` rather than silently claiming native execution.

## Graph data model

| Node | Key | From |
|---|---|---|
| `Incident`, `Alert`, `Device`, `User`, `Service` | `*_id` | `incidents.csv`, `alerts.csv`, `devices.csv`, `users.csv`, `services.csv` |
| `Evidence` | `evidence_id` | `evidence.csv` — `confidence`, `verified` |
| `Memory` | `memory_id` | `agent_memory.csv` + written by agent runs — `agent`, `memory_type`, `confidence`, `created_at` |
| `Task`, `Action` | `task_id`, `action_id` | `agent_tasks.csv`, `response_actions.csv` — `Action` is load-once (`ON CREATE SET`) so a reload never undoes a human decision |

| Edge | From → to | Meaning |
|---|---|---|
| `ON_DEVICE`, `PART_OF` | Alert → Device, Alert → Incident | structural, derived from the alert row |
| `OWNED_BY` | Device → User | device ownership |
| `LOGGED_IN_FROM` | User → Device | access history |
| `CONNECTS_TO` | Device → Service | network reachability |
| `HAS_ACCESS_TO` | User → Service | authorization |
| `SUPPORTS` | Alert → Incident | from `relationships.csv` |
| `ABOUT`, `SUPPORTED_BY` | Memory → Incident, Memory → Alert | finding provenance |
| `FOR`, `PROPOSES` | Task → Incident, Incident → Action | bookkeeping |

`CONNECTS_TO`, `OWNED_BY` and `HAS_ACCESS_TO` together are the **access graph** — the only edges the correlator and the two graph algorithms traverse, at every tenant's graph independently.

## Benchmark

`heldout_benchmark.py` is a self-contained, offline sanity check — no FalkorDB connection, 6 hand-built incidents with 2 true links and 2 deliberate decoys. Actual output from this repo:

| Method | Precision | Recall | F1 | Found |
|---|---|---|---|---|
| Flat log: same device | 1.00 | 0.50 | 0.667 | H005-H006 |
| Flat log: same device or owner | 1.00 | 0.50 | 0.667 | H005-H006 |
| Graph: any shared service | 0.667 | 1.00 | 0.800 | H001-H002, H003-H004 (decoy), H005-H006 |
| Graph: shared high/critical service (the production rule) | 1.00 | 0.50 | 0.667 | H001-H002 |

Read honestly: the criticality filter correctly drops the low-criticality decoy (H003-H004) but also drops H005-H006 here, because in this *held-out* 6-incident set that link isn't gated through a high-criticality service — it's a same-device link, which `main.py` correlates separately and this standalone benchmark doesn't model. This is a plumbing/decoy-handling check, not a production accuracy number. The full 36-pair, 9-incident evaluation (`GET /api/eval`, scored against `csv/eval_labels.csv`) needs a live FalkorDB instance and wasn't run against one in this authoring session.

```bash
pytest tests/   # 21 tests, no FalkorDB required — verified passing in this repo,
                 # including 2 offline tenant-routing tests (test_tenant_isolation.py)
```

## Run it locally

```bash
# 1. FalkorDB
docker run --rm -p 6379:6379 -p 3000:3000 falkordb/falkordb:latest

# 2. Install and load the synthetic dataset 
pip install -r backend/requirements.txt
python load_trace.py ./csv

# 3. API
cd backend && uvicorn main:app --reload --port 8000

# 4. Frontend (includes a traversal-depth slider, 1-5)
cd frontend && npm install && npm run dev

# 5. MCP server, one tool per catalogue entry
cd backend && python mcp_server.py
```

Copy `.env.example` → `.env`. `DEFAULT_TENANT_ID` defaults to `default`; set `TENANT_API_KEYS` (JSON) to enable additional tenants. Leave `FALKOR_HOST`/`PORT` unset to use the local container on `localhost:6379`. Optional: install [Ollama](https://ollama.com) and `ollama pull llama3.2` for the LLM-directed agent paths — every endpoint still works without it, falling back to deterministic behaviour and reporting `llm_used: false`.

| Endpoint | Purpose |
|---|---|
| `GET /api/overview` | full graph snapshot for the active tenant |
| `POST /api/investigate` | fixed-tool investigation at a chosen depth: alerts, evidence, correlated incidents, memory |
| `POST /api/agents/run` | deterministic pipeline, no LLM: Memory → Investigator → Verifier → Planner |
| `POST /api/agents/llm-run` | the same pipeline, grounded LLM via local Ollama |
| `GET /api/tools` | the atomic tool catalogue |
| `POST /api/tools/{tool_name}` | run exactly one tool |
| `POST /api/agents/graph-investigate` | tool-choosing agent loop — model picks tools, Python runs the Cypher |
| `POST /api/actions/{id}/approve` / `/reject` | human decision, evidence-gated, final |
| `POST /api/evidence/{id}/verify` | records a human verification |
| `GET /api/eval?llm=false` | graph vs. flat-log correlation benchmark |

## Track fit

One submission is judged per track. This repo is submitted to **Track 1**.

| Track 1 — Agents That Act on Connected Data | Where | Status |
|---|---|---|
| Multi-hop reasoning | `trace_agents.py::correlate`, parameterised depth 1–5 | done |
| Graph algorithms as tools | `CALL algo.SPpaths` / `CALL algo.pageRank`, Python fallback | done |
| Tool selection | fixed allowlist; exposed over MCP, one tool per catalogue entry | done |
| Explainable results | `tool_trace`, path returned with every answer, grounding | done |

| Track 2 — Agent Memory and Coordination | Where | Status |
|---|---|---|
| Episodic/semantic memory | `Memory` nodes: type, confidence, provenance edges | done |
| Persistent history across sessions | `resumed_memories`, idempotent `save_memory` | done |
| Separate memory per tenant | `db.select_graph(f"ops:{tenant_id}")`, auth-gated | done |
| Handoffs between agents | Investigator → Verifier → Planner pass state sequentially | partial — no cross-*run* handoff or agent-to-agent negotiation |

## Known limits

- **PageRank is aggregated, not native multi-relationship PageRank.** `algo.pageRank` takes exactly one label and one relationship type per call; the tool runs it over all 9 combinations and sums scores. Documented as a heuristic in `graph_algos.py`, not claimed as more than it is.
- **Agent handoffs are sequential within one run, not cross-run or negotiated.** The Planner reads the Verifier's output because both read the same incident context, not through an explicit typed handoff.
- **Synthetic dataset, single scenario.** 9 incidents generated for this project, with 2 deliberate decoys. It demonstrates the mechanism.
- **`mcp` must stay pinned below 2.0** (`backend/requirements.txt`) — `mcp` 2.x renamed `FastMCP` to `MCPServer`, and `mcp_server.py` targets the 1.x API.

## How AI was used

The code, tests and docs in this repository were written with AI coding assistants under the author's direction. The design decisions, the choice of concept and the acceptance of each change are the author's. The demo agent itself runs on a local open model, with no paid API.

## Repository

| Path | What |
|---|---|
| `backend/main.py` | FastAPI app: tenant middleware, overview, investigate, approve/reject, tool catalogue, agent endpoints |
| `backend/agents.py` | grounded LLM pipeline (Ollama) + the tool-choosing agent loop |
| `backend/trace_agents.py` | the correlation rule (parameterised depth) + deterministic (no-LLM) agent pipeline + evaluation |
| `backend/graph_algos.py` | native `algo.SPpaths`/`algo.pageRank` calls, with Python BFS/PageRank fallback |
| `backend/mcp_server.py` | MCP server: one tool per catalogue entry, plus 3 higher-level tools |
| `frontend/` | React + Cytoscape graph view, investigation panel, agent trace, traversal-depth slider |
| `csv/` | the synthetic dataset |
| `tests/` | 21 tests, no FalkorDB required, including tenant-routing tests |
| `heldout_benchmark.py` | offline precision/recall sanity check |
| `verify_graph_dependency.py`, `verify_persistence.py`, `verify_native_algorithms.py` | read-only live checks against a running `ops:default` FalkorDB graph |
| `check_graph.py`, `query.py` | inspection / one-off Cypher scripts |
| `README_TENANCY_MCP.md` | deeper notes on tenant auth, native algorithms, and running the MCP server |


