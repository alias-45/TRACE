import os
from falkordb import FalkorDB

db = FalkorDB(host=os.environ["FALKOR_HOST"], port=int(os.environ["FALKOR_PORT"]),
              username=os.environ.get("FALKOR_USER") or None,
              password=os.environ["FALKOR_PASS"])
g = db.select_graph("ops:default")

q = """
MATCH p = shortestPath((a:Alert {alert_id:$a})-[*..6]-(b:Alert {alert_id:$b}))
RETURN [n IN nodes(p) | coalesce(n.alert_id, n.device_id, n.user_id, n.service_id)] AS path
"""
print(g.query(q, params={"a": "A007", "b": "A003"}).result_set)