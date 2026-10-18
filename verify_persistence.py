"""Read-only inspection of a saved Action record in FalkorDB.

Examples:
    python verify_persistence.py --action-id ACT003
    python verify_persistence.py --action-id ACT003 --expected-status approved

Run after the API has been restarted. This directly queries FalkorDB; it does not
change an action or execute a real-world response.
"""
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from falkordb import FalkorDB

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "backend" / ".env")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--action-id", required=True, help="Action id, e.g. ACT003")
parser.add_argument("--expected-status", choices=["proposed", "awaiting_approval", "approved", "rejected"],
                    help="Optional expected saved status; exits non-zero if it differs")
args = parser.parse_args()

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

graph_name = os.getenv("FALKOR_GRAPH", "ops:default")
graph = FalkorDB(**kwargs).select_graph(graph_name)
rows = graph.query(
    "MATCH (a:Action {action_id:$id}) RETURN properties(a)",
    params={"id": args.action_id},
).result_set
if not rows:
    raise SystemExit(f"FAIL: Action {args.action_id} was not found in graph {graph_name!r}.")

record = rows[0][0]
if hasattr(record, "properties"):
    record = record.properties
if not isinstance(record, dict):
    record = dict(record)
status = str(record.get("status", "")).lower()
print(f"Graph: {graph_name}")
print(f"Action: {args.action_id}")
print(f"Incident: {record.get('incident_id', '(missing)')}")
print(f"Saved status: {status or '(missing)'}")
print(f"Approved: {record.get('approved', '(missing)')}")
for key in ("approved_at", "rejected_at", "approval_record", "rejection_record"):
    if record.get(key) is not None:
        print(f"{key}: {record[key]}")

if args.expected_status and status != args.expected_status:
    raise SystemExit(f"FAIL: expected status {args.expected_status!r}, found {status!r}.")
print("PASS: action state read directly from FalkorDB.")
