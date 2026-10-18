"""(Cloud version) Load the TRACE synthetic dataset (CSV) into FalkorDB, no Docker needed.

FalkorDB Cloud or any remote server (set the variables first, then run):
    pip install falkordb
    FALKOR_HOST=... FALKOR_PORT=... FALKOR_USER=... FALKOR_PASS=... python load_trace.py ./csv
"""
import csv, sys, os
from collections import defaultdict

folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "csv")

# Read FALKOR_* from the project-root .env if it exists (variables already set in the shell win).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

missing = [k for k in ("FALKOR_HOST", "FALKOR_PORT", "FALKOR_PASS") if not os.environ.get(k)]
if missing:
    sys.exit(f"Missing environment variables: {missing}. Put them in .env (project root) or set them in this PowerShell window first.")

from falkordb import FalkorDB
print("Connecting to", os.environ["FALKOR_HOST"], os.environ["FALKOR_PORT"], "...")
db = FalkorDB(
    host=os.environ["FALKOR_HOST"],
    port=int(os.environ["FALKOR_PORT"]),
    username=os.environ.get("FALKOR_USER") or None,
    password=os.environ["FALKOR_PASS"],
    socket_timeout=30,
    socket_connect_timeout=15,
)
print("Connected. Loading data from", os.path.abspath(folder))
for _f in ("incidents.csv", "alerts.csv", "devices.csv", "relationships.csv"):
    _p = os.path.join(folder, _f)
    if not os.path.exists(_p):
        sys.exit(f"Missing {_p}. Pass the folder that holds the CSV files, e.g.: python load_trace.py ./csv")
    with open(_p, newline="", encoding="utf-8") as _fh:
        print(f"  {_f}: {sum(1 for _ in _fh) - 1} rows")
g = db.select_graph("ops:default")


def rows(name):
    with open(os.path.join(folder, name), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def clean(r, floats=(), bools=()):
    out = {}
    for k, v in r.items():
        if v == "":
            continue  # skip empty values (e.g. unmanaged device owner)
        if k in floats:
            v = float(v)
        elif k in bools:
            v = v.strip().lower() == "true"
        out[k] = v
    return out


def load_nodes(label, key, data, create_only=False):
    """create_only=True keeps existing nodes untouched (used for Actions so a reload never undoes an approval)."""
    verb = "ON CREATE SET" if create_only else "SET"
    g.query(
        f"UNWIND $rows AS r MERGE (n:{label} {{{key}: r.{key}}}) {verb} n += r",
        params={"rows": data},
    )


def link(src_label, src_key, rel, dst_label, dst_key, pairs):
    """pairs: list of {s, t, props}; rel types can't be parameterized."""
    g.query(
        f"""UNWIND $pairs AS p
            MATCH (a:{src_label} {{{src_key}: p.s}}), (b:{dst_label} {{{dst_key}: p.t}})
            MERGE (a)-[r:{rel}]->(b) SET r += p.props""",
        params={"pairs": pairs},
    )


# ---- indexes ----
for label, prop in [("User","user_id"),("Device","device_id"),("Service","service_id"),
                    ("Alert","alert_id"),("Incident","incident_id"),("Evidence","evidence_id"),
                    ("Memory","memory_id"),("Task","task_id"),("Action","action_id")]:
    try:
        g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
    except Exception:
        pass  # already exists

print("creating indexes done; loading nodes")
# ---- nodes ----
load_nodes("User", "user_id", [clean(r) for r in rows("users.csv")])
load_nodes("Service", "service_id", [clean(r) for r in rows("services.csv")])
load_nodes("Device", "device_id", [clean(r) for r in rows("devices.csv")])
load_nodes("Incident", "incident_id", [clean(r) for r in rows("incidents.csv")])
load_nodes("Alert", "alert_id", [clean(r) for r in rows("alerts.csv")])
load_nodes("Evidence", "evidence_id",
           [clean(r, floats=("confidence",), bools=("verified",)) for r in rows("evidence.csv")])
load_nodes("Memory", "memory_id", [clean(r, floats=("confidence",)) for r in rows("agent_memory.csv")])
load_nodes("Task", "task_id", [clean(r) for r in rows("agent_tasks.csv")])
load_nodes("Action", "action_id", [clean(r, bools=("approved",)) for r in rows("response_actions.csv")], create_only=True)

print("nodes loaded; creating edges")
# ---- structural edges derived from columns ----
link("Alert", "alert_id", "ON_DEVICE", "Device", "device_id",
     [{"s": r["alert_id"], "t": r["device_id"], "props": {}} for r in rows("alerts.csv")])
link("Alert", "alert_id", "PART_OF", "Incident", "incident_id",
     [{"s": r["alert_id"], "t": r["incident_id"], "props": {}} for r in rows("alerts.csv")])
link("Device", "device_id", "OWNED_BY", "User", "user_id",
     [{"s": r["device_id"], "t": r["owner_user_id"], "props": {}}
      for r in rows("devices.csv") if r["owner_user_id"]])
link("Evidence", "evidence_id", "ABOUT", "Alert", "alert_id",
     [{"s": r["evidence_id"], "t": r["alert_id"], "props": {}} for r in rows("evidence.csv")])
link("Memory", "memory_id", "ABOUT", "Incident", "incident_id",
     [{"s": r["memory_id"], "t": r["incident_id"], "props": {}} for r in rows("agent_memory.csv")])
link("Task", "task_id", "FOR", "Incident", "incident_id",
     [{"s": r["task_id"], "t": r["incident_id"], "props": {}} for r in rows("agent_tasks.csv")])
link("Incident", "incident_id", "PROPOSES", "Action", "action_id",
     [{"s": r["incident_id"], "t": r["action_id"], "props": {}} for r in rows("response_actions.csv")])

print("column edges done; loading relationships.csv")
# ---- edges from relationships.csv (grouped by type/labels) ----
groups = defaultdict(list)
for r in rows("relationships.csv"):
    groups[(r["source_type"], r["relationship"], r["target_type"])].append(
        {"s": r["source_id"], "t": r["target_id"], "props": {"evidence_id": r["evidence_id"]}})

ids = {"User": "user_id", "Device": "device_id", "Service": "service_id",
       "Alert": "alert_id", "Incident": "incident_id"}
for (st, rel, tt), pairs in groups.items():
    link(st, ids[st], rel, tt, ids[tt], pairs)

# ---- sanity check ----
print("nodes:", g.query("MATCH (n) RETURN count(n)").result_set[0][0])
print("edges:", g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0])
_ids = [r[0] for r in g.query("MATCH (i:Incident) RETURN i.incident_id ORDER BY i.incident_id").result_set]
print("incidents now in graph:", len(_ids), _ids)
if len(_ids) < len(rows("incidents.csv")):
    print("WARNING: fewer incidents in the graph than in incidents.csv. The load did not complete.")