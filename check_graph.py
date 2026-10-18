"""Show what the FalkorDB server in .env actually contains, without printing the password.

    python check_graph.py
"""
import os
from dotenv import load_dotenv

here = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(here, ".env"))
backend_env = os.path.join(here, "backend", ".env")
if os.path.exists(backend_env):
    print("WARNING: backend/.env exists; the backend also reads it. Delete it unless it is intentional.")

from falkordb import FalkorDB

host = os.environ["FALKOR_HOST"]
print("server:", host[:12] + "..." + host[-24:], "port", os.environ["FALKOR_PORT"])
db = FalkorDB(host=host, port=int(os.environ["FALKOR_PORT"]),
              username=os.environ.get("FALKOR_USER") or None,
              password=os.environ["FALKOR_PASS"], socket_connect_timeout=15, socket_timeout=30)
wanted = os.getenv("FALKOR_GRAPH", "ops:default")
try:
    names = db.list_graphs()
except Exception:
    names = [wanted]
print("graphs on this server:", names)
for name in names:
    g = db.select_graph(name)
    ids = [r[0] for r in g.query("MATCH (i:Incident) RETURN i.incident_id ORDER BY i.incident_id").result_set]
    n = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
    mark = "  <-- the backend reads this one" if name == wanted else ""
    print(f"  {name}: {n} nodes, {len(ids)} incidents {ids}{mark}")
print("expected after a full load: graph 'ops:default' with 9 incidents I001..I009")