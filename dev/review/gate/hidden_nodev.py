"""The doors that read a private key by its name, on a Cube with dev mode on and off, as an admin and as a person with the query builder only."""
import json

import lib
import run_grid

mb = lib.Metabase().login()


def show(res):
    if isinstance(res, str):
        return "TEXT " + res[:160]
    if res.get("error") or res.get("status") == "failed" or "data" not in res:
        return "ERR " + str(res.get("error") or res.get("message") or res)[:170]
    return "ROWS %s %s" % ([c["name"] for c in res["data"]["cols"]][:4], json.dumps(res["data"]["rows"][:3], default=str)[:110])


def probe(who, api, db_name, mcp=None):
    ctx = run_grid.Ctx(mb, db_name)
    db = ctx.db
    tid = ctx.tables["payments"]["id"]
    by = ["field", "id", {"base-type": "type/Integer"}]
    for door, q in (("raw", {"source-table": tid, "fields": [by], "limit": 3}), ("max", {"source-table": tid, "aggregation": [["max", by]]}),
                    ("breakout", {"source-table": tid, "breakout": [by], "aggregation": [["count"]], "limit": 3}),
                    ("filter", {"source-table": tid, "filter": [">", by, 100000], "aggregation": [["count"]]}),
                    ("order", {"source-table": tid, "fields": [["field", ctx.field("payments", "method")["id"], None]], "order-by": [["desc", by]], "limit": 2}),
                    ("expression", {"source-table": tid, "expressions": {"x": ["+", by, 0]}, "fields": [["expression", "x"]], "limit": 3}),
                    ("private-dim", {"source-table": ctx.tables["orders"]["id"], "fields": [["field", "internal_note", {"base-type": "type/Text"}]], "limit": 1}),
                    ("private-measure", {"source-table": ctx.tables["orders"]["id"], "aggregation": [["max", ["field", "public_false_measure", {"base-type": "type/Float"}]]]})):
        print(db_name, who, "by-name", door, show(api("POST", "/api/dataset", {"database": db, "type": "query", "query": q}, ok=(200, 202, 400, 403, 500))), flush=True)
    for sql in ("SELECT id FROM payments LIMIT 3", "SELECT MAX(id) FROM payments", "SELECT COUNT(*) FROM secret_audit", "SELECT internal_note FROM orders LIMIT 1"):
        print(db_name, who, "native", sql, show(api("POST", "/api/dataset", {"database": db, "type": "native", "native": {"query": sql, "template-tags": {}}}, ok=(200, 202, 400, 403, 500))), flush=True)
    if mcp:
        for door, stage in (("name-ref fields", {"fields": [["field", {"base-type": "type/Integer"}, "id"]]}), ("name-ref max", {"aggregation": [["max", {}, ["field", {"base-type": "type/Integer"}, "id"]]]})):
            res, err = mcp.call("execute_query", query={"lib/type": "mbql/query", "database": db, "stages": [dict({"lib/type": "mbql.stage/mbql", "source-table": tid}, **stage)]}, row_limit=3)
            print(db_name, who, "mcp", door, ("ERR " + str(err)[:200]) if err else json.dumps(res.get("rows"), default=str)[:130], flush=True)
        res, err = mcp.call("execute_sql", database_id=db, sql="SELECT MAX(id) FROM payments")
        print(db_name, who, "mcp execute_sql", ("ERR " + str(err)[:200]) if err else json.dumps(res.get("rows"), default=str)[:130], flush=True)


for name in ("Cube", "Cube stock", "Cube nodev"):
    probe("admin", mb.api, name, lib.Mcp(mb))

# A person with the query builder and no native SQL. Metabase checks this, and the check of the driver stays above it.
email = "viewer@example.com"
users = mb.api("GET", "/api/user")["data"]
if not any(u["email"] == email for u in users):
    mb.api("POST", "/api/user", {"first_name": "V", "last_name": "V", "email": email, "password": "gate-viewer-1x"})
group = [g for g in mb.api("GET", "/api/permissions/group") if g["name"] == "All Users"][0]["id"]
graph = mb.api("GET", "/api/permissions/graph")
for d in mb.api("GET", "/api/database")["data"]:
    graph["groups"][str(group)].setdefault(str(d["id"]), {})
    graph["groups"][str(group)][str(d["id"])].update({"view-data": "unrestricted", "create-queries": "query-builder"})
mb.api("PUT", "/api/permissions/graph", graph)
viewer = lib.Metabase(email=email, password="gate-viewer-1x").login()
probe("query-builder", viewer.api, "Cube", lib.Mcp(viewer))
probe("query-builder", viewer.api, "Cube nodev", lib.Mcp(viewer))
