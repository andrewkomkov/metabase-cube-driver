"""A text filter with more than one value, next to Metabase on Postgres. A person gets this from the filter widget with two values."""
import json

import lib
import run_grid

mb = lib.Metabase().login()
cube, stock = run_grid.Ctx(mb, "Cube"), run_grid.Ctx(mb, "Cube stock")
meta = mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % [d for d in mb.api("GET", "/api/database")["data"] if d["name"] == "PG"][0]["id"])
pg_db = meta["id"]
orders = [t for t in meta["tables"] if t["name"] == "orders"][0]
pg_status = ["field", [f for f in orders["fields"] if f["name"] == "status"][0]["id"], None]


def run(db, tid, f, extra=None):
    res = mb.api("POST", "/api/dataset", {"database": db, "type": "query", "query": dict({"source-table": tid, "aggregation": [["count"]], "filter": f}, **(extra or {}))}, ok=(200, 202, 400, 403, 500))
    if res.get("error") or "data" not in res:
        return "ERR " + str(res.get("error"))[:120], None
    return res["data"]["rows"], res["data"].get("native_form", {}).get("query")


CASES = [("contains two", lambda s: ["contains", s, "hip", "pro"], "position(status, 'hip') > 0 OR position(status, 'pro') > 0"),
         ("contains two, no case", lambda s: ["contains", s, "HIP", "PRO", {"case-sensitive": False}], "position(status, 'hip') > 0 OR position(status, 'pro') > 0"),
         ("starts two", lambda s: ["starts-with", s, "ship", "pro"], "status IN ('shipped', 'processing')"),
         ("ends two", lambda s: ["ends-with", s, "ped", "led"], "status IN ('shipped', 'cancelled')"),
         ("not contains two", lambda s: ["does-not-contain", s, "hip", "pro"], "NOT (position(status, 'hip') > 0 OR position(status, 'pro') > 0)"),
         ("contains three", lambda s: ["contains", s, "hip", "pro", "canc"], "status IN ('shipped', 'processing', 'cancelled')"),
         ("not of contains two", lambda s: ["not", ["contains", s, "hip", "pro"]], "NOT (position(status, 'hip') > 0 OR position(status, 'pro') > 0)")]
for name, mk, cond in CASES:
    truth = lib.ch("SELECT count() FROM orders WHERE %s" % cond)[0][0]
    pg = run(pg_db, orders["id"], mk(pg_status))
    c = run(cube.db, cube.tables["orders"]["id"], mk(cube.ref("d:orders.status")))
    s = run(stock.db, stock.tables["orders"]["id"], mk(stock.ref("d:stock.status".replace("stock.", "orders."))))
    print(name, "| truth", truth, "| PG", pg[0], "| dialect", c[0], "| stock", s[0])
    print("   PG SQL:", (pg[1] or "")[:220])
    print("   Cube SQL:", (c[1] or "")[:300])
# A dashboard filter and the MCP tool.
res = mb.api("POST", "/api/dataset", {"database": cube.db, "type": "query", "query": {"source-table": cube.tables["orders"]["id"], "aggregation": [["count"]]},
                                      "parameters": [{"type": "string/contains", "target": ["dimension", cube.ref("d:orders.status")], "value": ["hip", "pro"], "options": {"case-sensitive": False}}]})
print("parameter contains two:", res.get("error") or res["data"]["rows"])
m = lib.Mcp(mb)
out, err = m.call("execute_query", query={"lib/type": "mbql/query", "database": cube.db, "stages": [{"lib/type": "mbql.stage/mbql", "source-table": cube.tables["orders"]["id"], "aggregation": [["count", {}]],
                                          "filters": [["contains", {}, ["field", {}, cube.ref("d:orders.status")[1]], "hip", "pro"]]}]})
print("mcp contains two:", err or out.get("rows"))
print(json.dumps([d for d in cube.tables["users"]["fields"] if "location" in d["name"]][0:3], default=str)[:400] if [d for d in cube.tables["users"]["fields"] if "location" in d["name"]] else "no geo field")
print([f["name"] for f in cube.tables["users"]["fields"]])
