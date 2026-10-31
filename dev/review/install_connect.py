"""After the install: the first steps of a new person on a clean Metabase, with the values of the README section Connect. It sends the fields that the form has."""
import os
import time

import lib

mb = lib.Metabase("http://127.0.0.1:13302").login()
readme = {"host": "cube", "port": 15432, "dbname": "cube", "user": "cube", "password": "cube-dev-only", "cube-rest-url": "http://cube:4000"}
st, text, _ = lib.http("POST", mb.url + "/api/database", {"engine": "cube", "name": "Cube", "details": readme}, {"X-Metabase-Session": mb.sid})
print("connect with the values of README, no API secret: HTTP", st, text[:200] if st >= 400 else "")
if st < 400:
    import json
    db = json.loads(text)["id"]
    for _ in range(80):
        d = mb.api("GET", "/api/database/%d" % db)
        if d.get("initial_sync_status") == "complete":
            break
        time.sleep(3)
    meta = mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % db)
    orders = [t for t in meta["tables"] if t["name"] == "orders"][0]
    start = time.time()
    measures = []
    while time.time() - start < 180 and not measures:
        measures = [m for m in mb.api("GET", "/api/measure") if m["table_id"] == orders["id"]]
        time.sleep(3)
    print("sync: tables", len(meta["tables"]), "measures of orders", len(measures), "seconds after the sync status", int(time.time() - start))
    rev = [m for m in measures if m["name"] == "Revenue"][0]
    print("revenue", mb.run({"database": db, "type": "query", "query": {"source-table": orders["id"], "aggregation": [["measure", rev["id"]]]}})[0], "truth", lib.ch("SELECT sum(amount) FROM orders"))
