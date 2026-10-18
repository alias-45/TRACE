"""Live check that FalkorDB's native algo.SPpaths and algo.pageRank procedures actually
run against the connected server -- not TRACE's Python fallback.

This calls the exact functions backend/main.py calls (graph_algos.native_paths_to_
critical_services, graph_algos.native_rank_entities), so a PASS here is a PASS for the
"Native algorithm calls aren't yet verified against a live FalkorDB server" line in
README.md's Known limits -- once this passes against your deployment, remove that line.

Requires: pip install -r backend/requirements.txt, and a FalkorDB server (local Docker
or Cloud) already loaded via load_trace.py.

Usage:
    python verify_native_algorithms.py
    python verify_native_algorithms.py --incident-id I002
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from falkordb import FalkorDB

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "backend" / ".env")
sys.path.insert(0, str(ROOT / "backend"))
import graph_algos  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--incident-id", default="I001", help="Incident to scope the check to (default: I001)")
args = parser.parse_args()

required = ["FALKOR_HOST", "FALKOR_PORT"]
missing = [k for k in required if not os.getenv(k)]
if missing:
    raise SystemExit(f"Missing required environment variables: {', '.join(missing)} (configure .env first).")

kwargs = {"host": os.environ["FALKOR_HOST"], "port": int(os.environ["FALKOR_PORT"]),
          "socket_connect_timeout": 10, "socket_timeout": 30}
if os.getenv("FALKOR_USER"):
    kwargs["username"] = os.environ["FALKOR_USER"]
if os.getenv("FALKOR_PASS"):
    kwargs["password"] = os.environ["FALKOR_PASS"]

graph_name = os.getenv("FALKOR_GRAPH", "ops:default")
graph = FalkorDB(**kwargs).select_graph(graph_name)


def run(query, params=None):
    return graph.query(query, params=params or {}).result_set


def plain(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if hasattr(v, "properties"):
        return plain(v.properties)
    return str(v)


ID_KEY = {"User": "user_id", "Device": "device_id", "Service": "service_id"}


def nid(label, props):
    key = ID_KEY.get(label)
    return str(props[key]) if key and props.get(key) is not None else f"{label}:{id(props)}"


def display_name(props, fallback):
    for k in ("name", "hostname", "title"):
        if props.get(k):
            return str(props[k])
    return fallback


iid = args.incident_id
exists = run("MATCH (i:Incident {incident_id:$iid}) RETURN i.incident_id LIMIT 1", {"iid": iid})
if not exists:
    raise SystemExit(f"FAIL: Incident {iid} not found in graph {graph_name!r}. "
                      f"Run load_trace.py first, or pass --incident-id.")

devices = [r[0] for r in run(graph_algos.INCIDENT_DEVICES_CYPHER, {"iid": iid})]
services = [r[0] for r in run(graph_algos.CRITICAL_SERVICES_CYPHER)]
if not devices:
    raise SystemExit(f"FAIL: Incident {iid} has no alerting devices to test paths from.")

print(f"Graph: {graph_name}  Incident: {iid}  Devices: {len(devices)}  Critical services: {len(services)}")

print("\n-- algo.SPpaths ---------------------------------------------------------")
try:
    sp = graph_algos.native_paths_to_critical_services(run, devices, services, plain, nid, display_name)
    print(f"PASS: native algo.SPpaths ran. {sp['count']} device(s) checked, {sp['unreachable']} unreachable.")
    for p in sp["paths"][:3]:
        print(f"  {p['device_name']}: reachable={p['reachable']} hops={p.get('hops')}")
except Exception as exc:
    print(f"FAIL: algo.SPpaths did not run natively on this server: {exc}")
    print("      main.py will silently fall back to the Python BFS implementation in this case --")
    print("      correct behaviour, but it means the native claim isn't true for this deployment yet.")
    sys.exit(1)

print("\n-- algo.pageRank --------------------------------------------------------")
scope = [nid("Device", plain(d) or {}) for d in devices]
try:
    pr = graph_algos.native_rank_entities(run, scope, plain, nid, display_name)
    print(f"PASS: native algo.pageRank ran. {pr['entities_ranked']} entities ranked.")
    for item in pr["top"][:5]:
        print(f"  #{item['rank']} {item['label']} {item['name']} score={item['score']}")
except Exception as exc:
    print(f"FAIL: algo.pageRank did not run natively on this server: {exc}")
    sys.exit(1)

print("\nPASS: both graph algorithms ran as native FalkorDB procedures, not the Python fallback.")
