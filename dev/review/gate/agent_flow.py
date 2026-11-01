"""A person asks an agent: which states bring the paid revenue, and how does it move by month, with a filter by date and by status.
The agent has only MCP and reads the names from MCP. The last part reads the dashboard through REST as the browser does, and compares each number with ClickHouse."""
import json
import sys

import lib

mb = lib.Metabase().login()
m = lib.Mcp(mb)
DB_NAME = sys.argv[1] if len(sys.argv) > 1 else "Cube"
problems = []


def call(tool, **a):
    res, err = m.call(tool, **a)
    print("%s(%s) -> %s" % (tool, json.dumps(a)[:150], ("ERROR " + str(err)[:400]) if err else json.dumps(res, default=str)[:260]))
    if err:
        problems.append((tool, str(err)[:300]))
    return res


dbs = call("browse_data", action="list_databases")
db = [d for d in dbs["data" if "data" in dbs else "databases"] if d["name"] == DB_NAME][0]["id"]
found = call("search", term_queries=["revenue", "paid sales"])
tables = call("browse_data", action="list_tables", database_id=db, search="sales")
tid = [t for t in tables.get("tables", tables.get("data", [])) if t["name"] == "sales"][0]["id"]
fields = call("browse_data", action="get_fields", table_ids=[tid])
text = json.dumps(fields)
print("description in fields:", "Start here" in text or "Curated" in text)
tbl = fields["tables"][0] if "tables" in fields else fields
cols = {f["name"]: f for f in tbl.get("fields", [])}
print("visible fields:", sorted(cols))
ms = tbl.get("measures") or []
print("measures:", [x.get("name") for x in ms][:12])
want = {"Revenue": None}
measure = next((x for x in ms if x.get("name") in ("Revenue", "revenue")), None)
if not measure:
    problems.append(("get_fields", "no measure Revenue on sales"))
    sys.exit("no measure")

state, status, at = cols["users_state"]["id"], cols["status"]["id"], cols["created_at"]["id"]
stage = {"lib/type": "mbql.stage/mbql", "source-table": tid, "aggregation": [["measure", {}, measure["id"]]]}
q1 = {"lib/type": "mbql/query", "database": db, "stages": [dict(stage, breakout=[["field", {}, state]], **{"order-by": [["desc", {}, ["aggregation", {}, 0]]]})]}
q2 = {"lib/type": "mbql/query", "database": db, "stages": [dict(stage, breakout=[["field", {"temporal-unit": "month"}, at]])]}
r1 = call("execute_query", query=q1)
r2 = call("execute_query", query=q2)
c1 = call("question_write", method="create", name="Gate revenue by state", query_handle=r1["query_handle"], display="bar", collection_id="root")
c2 = call("question_write", method="create", name="Gate revenue by month", query_handle=r2["query_handle"], display="line", collection_id="root")
dash = call("dashboard_write", method="create", name="Gate revenue", description="Revenue by state and by month, with a date and a status filter.", collection_id="root",
            ops=[{"op": "add_card", "card_id": c1["id"], "id": -1}, {"op": "add_card", "card_id": c2["id"], "id": -2},
                 {"op": "add_parameter", "parameter_id": "when", "name": "Order date", "type": "date/all-options", "sectionId": "date"},
                 {"op": "add_parameter", "parameter_id": "st", "name": "Status", "type": "string/=", "sectionId": "string"},
                 {"op": "wire_parameter", "parameter_id": "when", "dashcard_id": -1, "target_field": at, "autowire": True},
                 {"op": "wire_parameter", "parameter_id": "st", "dashcard_id": -1, "target_field": status, "autowire": True}])
did = dash["id"]
back = call("get_content", items=[{"type": "dashboard", "id": did, "fields": None}])
print(json.dumps(back, default=str)[:900])
values = call("get_parameter_values", target="dashboard", id=did, parameter_id="st")
print("status values:", json.dumps(values, default=str)[:300])
saved = call("run_saved_question", id=c1["id"])
truth1 = lib.ch("SELECT u.state, sum(o.amount) v FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1")
d = lib.compare(saved["rows"], truth1)
print("saved question 1:", d or "equal")
if d:
    problems.append(("run_saved_question", d))

# The browser: a dashcard with both filters.
full = mb.api("GET", "/api/dashboard/%d" % did)
for dc in full["dashcards"]:
    print("dashcard", dc["id"], dc["card_id"], "mappings", json.dumps(dc.get("parameter_mappings"))[:260])
cases = [("no filter", [], "1"),
         ("month and status", [{"id": "when", "type": "date/all-options", "value": "2025-03-01~2025-03-31"}, {"id": "st", "type": "string/=", "value": ["shipped"]}], "toDate(o.created_at) BETWEEN '2025-03-01' AND '2025-03-31' AND o.status = 'shipped'"),
         ("last year", [{"id": "when", "type": "date/all-options", "value": "past1years"}], "toYear(o.created_at) = toYear(now('UTC')) - 1"),
         ("two statuses", [{"id": "st", "type": "string/=", "value": ["shipped", "cancelled"]}], "o.status IN ('shipped', 'cancelled')")]
for zone in (None, "America/New_York"):
    mb.api("PUT", "/api/setting/report-timezone", {"value": zone})
    for name, params, cond in cases:
        if zone:
            cond = cond.replace("o.created_at", "toTimeZone(o.created_at, '%s')" % zone)
        for dc, sql in zip(full["dashcards"], ("SELECT u.state, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id WHERE %s GROUP BY 1", "SELECT toStartOfMonth(%s), sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id WHERE %%s GROUP BY 1" % (("toTimeZone(o.created_at, '%s')" % zone) if zone else "o.created_at"))):
            res = mb.api("POST", "/api/dashboard/%d/dashcard/%d/card/%d/query" % (did, dc["id"], dc["card_id"]), {"parameters": params}, ok=(200, 202, 400, 500))
            if res.get("error") or "data" not in res:
                print(zone, name, dc["id"], "ERROR", str(res.get("error"))[:300])
                problems.append((name, str(res.get("error"))[:300]))
                continue
            t = lib.ch(sql % cond)
            diff = lib.compare(res["data"]["rows"], t)
            print(zone, name, dc["id"], "rows", len(t), diff or "equal")
            if diff:
                problems.append((name, diff))
mb.api("PUT", "/api/setting/report-timezone", {"value": None})
print("PROBLEMS", json.dumps(problems, default=str))
