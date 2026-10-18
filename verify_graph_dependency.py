"""Read-only live check that TRACE's production graph traversal adds signal beyond flat alert fields.

Run from project root after configuring .env:
    python verify_graph_dependency.py

This script queries the configured FalkorDB graph. It does not mutate data.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from falkordb import FalkorDB

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "backend" / ".env")

required = ["FALKOR_HOST", "FALKOR_PORT", "FALKOR_PASS"]
missing = [key for key in required if not os.getenv(key)]
if missing:
    raise SystemExit(f"Missing required environment variables: {', '.join(missing)} (configure .env first).")

kwargs = {
    "host": os.environ["FALKOR_HOST"],
    "port": int(os.environ["FALKOR_PORT"]),
    "password": os.environ["FALKOR_PASS"],
    "socket_connect_timeout": 10,
    "socket_timeout": 30,
}
if os.getenv("FALKOR_USER"):
    kwargs["username"] = os.environ["FALKOR_USER"]

db = FalkorDB(**kwargs)
graph_name = os.getenv("FALKOR_GRAPH", "ops:default")
g = db.select_graph(graph_name)

# Import the same graph-correlator used by the production agent pipeline.
sys.path.insert(0, str(ROOT / "backend"))
from trace_agents import correlate  # noqa: E402


def run(query, params=None):
    return g.query(query, params=params or {}).result_set


def plain(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if hasattr(value, "properties"):
        return plain(value.properties)
    return str(value)


def nid(label, props):
    keys = {"User": "user_id", "Device": "device_id", "Service": "service_id",
            "Alert": "alert_id", "Incident": "incident_id", "Evidence": "evidence_id",
            "Memory": "memory_id", "Task": "task_id", "Action": "action_id"}
    key = keys.get(label)
    return str(props.get(key)) if key and props.get(key) is not None else str(props.get("name") or label)


def display_name(props, fallback):
    for key in ("name", "title", "hostname", "alert_type", "observed_fact", "content", "description"):
        if props.get(key):
            return str(props[key])
    return fallback


nodes = run("MATCH (n) RETURN count(n)")[0][0]
edges = run("MATCH ()-[r]->() RETURN count(r)")[0][0]
incident_ids = sorted(row[0] for row in run("MATCH (i:Incident) RETURN i.incident_id"))
print(f"Graph: {graph_name}")
print(f"Nodes: {nodes}; directed relationships: {edges}; incidents: {len(incident_ids)}")
print("Incident IDs:", ", ".join(incident_ids))
if edges <= 0:
    raise SystemExit("FAIL: graph has no relationships; a node-only graph cannot support path reasoning.")

# This is the production graph traversal from trace_agents.py, not a simplified proxy.
links = []
for iid in incident_ids:
    links.extend((iid, link) for link in correlate(run, iid, plain, nid, display_name))

pairs = {}
for source_iid, link in links:
    target = link["incident_id"]
    pair = tuple(sorted((source_iid, target)))
    pairs.setdefault(pair, link)

print("\nProduction graph-correlator links:")
for (left, right), link in sorted(pairs.items()):
    path = " -> ".join(item["name"] for item in link["path"])
    print(f"  {left} <-> {right} via {link['via_service']}: {path}")

# The known scenario demonstrates a relationship-mediated link that a flat alert row
# (alert fields + direct device ID/owner only) cannot see.
required_pair = ("I001", "I002")
if required_pair not in pairs:
    raise SystemExit(
        "FAIL: expected graph-mediated I001-I002 link was not found. Check graph load, relationship edges, "
        "and the production CORRELATE_CYPHER query."
    )
path = pairs[required_pair]["path"]
if not any(item["label"] == "Service" and item["name"] == "Customer Database" for item in path):
    raise SystemExit("FAIL: I001-I002 was found, but the expected Customer Database service is absent from its path.")

# Flat baseline: incidents are linked only when alert rows share device_id or owner_user_id.
flat_rows = run("""MATCH (a:Alert) OPTIONAL MATCH (a)-[:ON_DEVICE]->(d:Device)
                  RETURN a.incident_id, d.device_id, d.owner_user_id""")
by_incident = {}
for iid, device_id, owner_id in flat_rows:
    by_incident.setdefault(iid, []).append((device_id, owner_id))
flat_pair_found = False
for a_dev, a_owner in by_incident.get("I001", []):
    for b_dev, b_owner in by_incident.get("I002", []):
        if (a_dev and a_dev == b_dev) or (a_owner and a_owner == b_owner):
            flat_pair_found = True

if flat_pair_found:
    print("NOTE: I001-I002 is also inferable from direct flat fields in this dataset; graph traversal still provides the explicit service path.")
else:
    print("PASS: flat alert fields (shared device/owner) do not link I001-I002; the production graph traversal links them through Customer Database.")

print("PASS: live graph has relationships and the production graph-correlator returns the expected multi-hop service path.")
print("Interpretation: this validates a scenario-specific graph dependency, not general model accuracy or causality.")
