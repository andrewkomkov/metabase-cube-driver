"""The real user of the last review. An agent gets a question and builds a dashboard with filters through MCP tools only, with no knowledge of Cube.
It finds the ids from the words of the question. REST runs a dashcard with a parameter only for the check, because no MCP tool does this."""
import json
import sys

import lib

QUESTION = "How does monthly revenue by product category develop, and which customer cities bring the most? I want to filter by city, order status and date."

mb = lib.Metabase().login()
c = lib.Mcp(mb)
log = []


def call(tool, **a):
    res, err = c.call(tool, **a)
    log.append({"tool": tool, "ok": not err, "error": (err or "")[:300]})
    if err:
        print("TOOL ERROR", tool, json.dumps(a)[:400], err[:800])
        sys.exit(1)
    return res


SEARCH = {k: None for k in ["semantic_queries", "recent", "type", "collection_id", "created_by", "archived", "limit", "offset", "response_format", "fields"]}

# 1. Find the data from the words of the question.
found = call("search", **dict(SEARCH, term_queries=["revenue product category city"], type=["table"]))
print("search:", [(d["name"], d["id"]) for d in found["data"]][:8])
dbs = call("browse_data", action="list_databases")
cube = [d for d in dbs["data"] if d["name"] == "Cube"][0]["id"]
tables = call("browse_data", action="list_tables", database_id=cube, search="sales")
sales = [t for t in tables["data"] if t["name"] == "sales"][0]
print("table:", sales["id"], sales.get("description", "")[:200])
fields_res = call("browse_data", action="get_fields", table_ids=[sales["id"]])
tbl = (fields_res.get("tables") or fields_res.get("data"))[0]
print("keys:", sorted(tbl.keys()))
fields = {f["name"]: f["id"] for f in tbl["fields"]}
measures = {m["name"]: m["id"] for m in tbl.get("measures", [])}
print("fields:", sorted(fields))
print("measures:", sorted(measures))
print("segments:", [s["name"] for s in tbl.get("segments", [])])


def field(name, **opts):
    return ["field", opts, fields[name]]


def measure(name):
    return ["measure", {}, measures[name]]


def query(agg, by, **extra):
    stage = dict({"lib/type": "mbql.stage/mbql", "source-table": sales["id"], "aggregation": agg}, **extra)
    if by:
        stage["breakout"] = by
    return {"lib/type": "mbql/query", "database": cube, "stages": [stage]}


# 2. Three questions. Each comes from a query that ran first, and is saved by its handle.
SPEC = {
    "Monthly revenue by category": (query([measure("Revenue")], [field("created_at", **{"temporal-unit": "month"}), field("products_category")]), "line"),
    "Revenue by city": (query([measure("Revenue"), measure("Order count")], [field("users_city")]), "bar"),
    "Revenue and AOV": (query([measure("Revenue"), measure("AOV")], []), "table"),
}
cards = {}
for name, (q, display) in SPEC.items():
    res = call("execute_query", query=q, row_limit=200)
    print(name, "rows", len(res["rows"]), res["rows"][:2])
    card = call("question_write", method="create", name=name, query_handle=res["query_handle"], display=display, collection_id="root")
    cards[name] = card["id"]

# 3. A dashboard with three filters, each wired to all cards.
ops = []
for i, (name, cid) in enumerate(cards.items()):
    ops.append({"op": "add_card", "id": -(i + 1), "card_id": cid})
ops += [
    {"op": "add_parameter", "parameter_id": "city", "name": "City", "type": "string/=", "sectionId": "string"},
    {"op": "add_parameter", "parameter_id": "status", "name": "Status", "type": "string/=", "sectionId": "string"},
    {"op": "add_parameter", "parameter_id": "date", "name": "Date", "type": "date/all-options", "sectionId": "date"},
]
for i in range(len(cards)):
    for pid, fname in (("city", "users_city"), ("status", "status"), ("date", "created_at")):
        ops.append({"op": "wire_parameter", "parameter_id": pid, "dashcard_id": -(i + 1), "target_field": fields[fname]})
dash = call("dashboard_write", method="create", name="Revenue review", description=QUESTION, collection_id="root", ops=ops)
did = dash["id"]
print("dashboard", did)

# 4. Read it back through MCP.
back = call("get_content", items=[{"type": "dashboard", "id": did, "fields": None}], include=["parameters", "layout"], response_format=None)
item = back["items"][0] if isinstance(back, dict) and "items" in back else back
print("read back:", json.dumps(item)[:1800])
for pid, want in (("city", {"Austin", "Boston", "Chicago", "Seattle"}), ("status", {"cancelled", "completed", "processing", "shipped"})):
    vals = call("get_parameter_values", target="dashboard", id=did, parameter_id=pid)
    got = {v[0] if isinstance(v, list) else v for v in vals.get("values", vals.get("data", []))}
    print("values", pid, sorted(map(str, got)), "OK" if got == want else "DIFFERENT")

# 5. Each card under each filter, compared with ClickHouse.
J = "shop.orders o LEFT JOIN shop.users u ON o.user_id = u.id LEFT JOIN shop.products p ON o.product_id = p.id"
TRUTH = {
    "Monthly revenue by category": "SELECT toStartOfMonth(o.created_at), p.category, sum(o.amount) FROM %s WHERE %s GROUP BY 1, 2",
    "Revenue by city": "SELECT u.city, sum(o.amount), count() FROM %s WHERE %s GROUP BY 1",
    "Revenue and AOV": "SELECT sum(o.amount), avg(toFloat64(o.amount)) FROM %s WHERE %s",
}
pmeta = {"city": ("string/=", ["Austin"]), "status": ("string/=", ["shipped"])}
FILTERS = [
    ("no filter", {}, "1"),
    ("city", {"city": ["Austin"]}, "u.city = 'Austin'"),
    ("two cities", {"city": ["Austin", "Boston"]}, "u.city IN ('Austin','Boston')"),
    ("status", {"status": ["shipped"]}, "o.status = 'shipped'"),
    ("month range", {"date": "2025-03-01~2025-03-31"}, "toDate(o.created_at) BETWEEN '2025-03-01' AND '2025-03-31'"),
    ("mid month range", {"date": "2025-03-10~2025-04-20"}, "toDate(o.created_at) BETWEEN '2025-03-10' AND '2025-04-20'"),
    ("one day", {"date": "2025-03-09"}, "toDate(o.created_at) = '2025-03-09'"),
    ("month", {"date": "2025-03"}, "toStartOfMonth(o.created_at) = '2025-03-01'"),
    ("quarter", {"date": "Q2-2025"}, "toStartOfQuarter(o.created_at) = '2025-04-01'"),
    ("open end", {"date": "2025-12-20~"}, "toDate(o.created_at) > '2025-12-20'"),
    ("open start", {"date": "~2025-01-05"}, "toDate(o.created_at) < '2025-01-05'"),
    ("relative", {"date": "past500days"}, "toDate(o.created_at) >= today() - 500 AND toDate(o.created_at) < today()"),
    ("exclude quarters", {"date": "exclude-quarters-1"}, "toQuarter(o.created_at) != 1"),
    ("all", {"city": ["Seattle"], "status": ["completed"], "date": "2025-06-01~2025-08-31"},
     "u.city = 'Seattle' AND o.status = 'completed' AND toDate(o.created_at) BETWEEN '2025-06-01' AND '2025-08-31'"),
    ("no rows", {"city": ["Atlantis"]}, "u.city = 'Atlantis'"),
]
detail = mb.api("GET", "/api/dashboard/%d" % did)
TYPES = {p["id"]: p["type"] for p in detail["parameters"]}
passed = total = 0
for label, values, cond in FILTERS:
    for dc in detail["dashcards"]:
        name = dc["card"]["name"]
        params = []
        for pid, v in values.items():
            pm = [m for m in dc["parameter_mappings"] if m["parameter_id"] == pid][0]
            params.append({"id": pid, "type": TYPES[pid], "value": v, "target": pm["target"]})
        res = mb.api("POST", "/api/dashboard/%d/dashcard/%d/card/%d/query" % (did, dc["id"], dc["card_id"]), {"parameters": params}, ok=(200, 202))
        want = lib.ch(TRUTH[name] % (J, cond))
        total += 1
        if res.get("error"):
            print("ERROR", label, name, str(res["error"])[:300])
            continue
        diff = lib.compare(res["data"]["rows"], want, tol=1e-6)
        if diff and not (label == "no rows" and res["data"]["rows"] in ([[None, None]], [[0, None]]) and name == "Revenue and AOV"):
            print("WRONG", label, name, diff[:300])
        else:
            passed += 1
print("dashboard cells: %d of %d equal ClickHouse" % (passed, total))
print("tools:", log)
