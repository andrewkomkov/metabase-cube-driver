"""Tries to read the values of hidden private keys, by each door of Metabase. A value on a door is a blocker. Prints one line for each door."""
import json
import sys
import urllib.parse

import lib
import run_grid

DB = sys.argv[1] if len(sys.argv) > 1 else "Cube"
mb = lib.Metabase().login()
ctx = run_grid.Ctx(mb, DB)
mcp = lib.Mcp(mb)
db = ctx.db
PRIVATE = {"payments": ("id", "payment_id"), "warehouses": ("id", "id"), "promos": ("id", "id"), "shipments": ("order_id", "order_id")}
results = []


def note(door, table, res):
    results.append((door, table, res))
    print("%-34s %-11s %s" % (door, table, res), flush=True)


def summarize(res):
    if isinstance(res, str):
        return "TEXT " + res[:170]
    if res.get("error") or res.get("status") == "failed":
        return "ERR " + str(res.get("error") or res.get("message"))[:150]
    if "data" not in res:
        return "OTHER " + json.dumps(res)[:170]
    cols = [c["name"] for c in res["data"]["cols"]]
    rows = res["data"]["rows"]
    return "ROWS %d cols=%s first=%s" % (len(rows), cols[:5], json.dumps(rows[:3], default=str)[:130])


def ds(door, table, q, url="/api/dataset"):
    note(door, table, summarize(mb.api("POST", url, {"database": db, "type": "query", "query": q}, ok=(200, 202, 400, 403, 404, 500))))


for table, (name, column) in PRIVATE.items():
    tid = ctx.tables[table]["id"]
    try:
        f = ctx.field(table, name)
    except KeyError:
        note("field-in-metadata", table, "no hidden field")
        continue
    fid = f["id"]
    note("field-json", table, json.dumps({k: f.get(k) for k in ("visibility_type", "semantic_type", "database_type", "fingerprint", "has_field_values")}))
    k = ["field", fid, None]
    by_name = ["field", name, {"base-type": "type/Integer"}]
    other = [x for x in ctx.tables[table]["fields"] if x["id"] != fid and x.get("visibility_type") == "normal"]
    other_ref = ["field", other[0]["id"], None] if other else None
    # /api/dataset: every clause.
    ds("raw-all-fields", table, {"source-table": tid, "limit": 3})
    ds("raw-fields-id", table, {"source-table": tid, "fields": [k], "limit": 3})
    ds("raw-by-name", table, {"source-table": tid, "fields": [by_name], "limit": 3})
    ds("breakout", table, {"source-table": tid, "breakout": [k], "aggregation": [["count"]], "limit": 3})
    ds("breakout-by-name", table, {"source-table": tid, "breakout": [by_name], "aggregation": [["count"]], "limit": 3})
    ds("filter-eq", table, {"source-table": tid, "filter": ["=", k, 1], "aggregation": [["count"]]})
    ds("filter-gt", table, {"source-table": tid, "filter": [">", k, 100000], "aggregation": [["count"]]})
    ds("filter-is-null", table, {"source-table": tid, "filter": ["is-null", k], "aggregation": [["count"]]})
    ds("filter-by-name", table, {"source-table": tid, "filter": [">", by_name, 100000], "aggregation": [["count"]]})
    for agg in ("min", "max", "sum", "avg", "distinct"):
        ds("agg-" + agg, table, {"source-table": tid, "aggregation": [[agg, k]]})
    ds("agg-by-name", table, {"source-table": tid, "aggregation": [["max", by_name]]})
    ds("count-where", table, {"source-table": tid, "aggregation": [["count-where", [">", k, 100000]]]})
    ds("sum-where", table, {"source-table": tid, "aggregation": [["sum-where", k, [">", k, 0]]]})
    ds("share", table, {"source-table": tid, "aggregation": [["share", [">", k, 100000]]]})
    ds("expression-plus", table, {"source-table": tid, "expressions": {"x": ["+", k, 0]}, "fields": [["expression", "x"]], "limit": 3})
    ds("expression-case", table, {"source-table": tid, "expressions": {"x": ["case", [[[">", k, 100000], "hi"]], {"default": "lo"}]}, "breakout": [["expression", "x"]], "aggregation": [["count"]]})
    ds("expression-concat", table, {"source-table": tid, "expressions": {"x": ["concat", k, ""]}, "fields": [["expression", "x"]], "limit": 3})
    ds("expression-coalesce", table, {"source-table": tid, "expressions": {"x": ["coalesce", k, 0]}, "fields": [["expression", "x"]], "limit": 3})
    ds("binning", table, {"source-table": tid, "breakout": [["field", fid, {"binning": {"strategy": "bin-width", "bin-width": 1000}}]], "aggregation": [["count"]]})
    if other_ref:
        ds("order-by-key", table, {"source-table": tid, "fields": [other_ref], "order-by": [["desc", k]], "limit": 3})
        ds("order-by-key-agg", table, {"source-table": tid, "breakout": [other_ref], "aggregation": [["count"]], "order-by": [["asc", k]], "limit": 3})
    ds("cum-sum", table, {"source-table": tid, "aggregation": [["cum-sum", k]]})
    # Stages.
    ds("stage-raw-then-field", table, {"source-query": {"source-table": tid}, "fields": [by_name], "limit": 3})
    ds("stage-raw-then-max", table, {"source-query": {"source-table": tid}, "aggregation": [["max", by_name]]})
    ds("stage-raw-then-filter", table, {"source-query": {"source-table": tid}, "filter": [">", by_name, 100000], "aggregation": [["count"]]})
    ds("stage-fields-id-then-max", table, {"source-query": {"source-table": tid, "fields": [k]}, "aggregation": [["max", by_name]]})
    card = None
    try:
        card = mb.api("POST", "/api/card", {"name": "gate raw " + table, "type": "model", "display": "table", "visualization_settings": {}, "dataset_query": {"database": db, "type": "query", "query": {"source-table": tid}}})["id"]
        cols = [c.get("name") for c in mb.api("POST", "/api/card/%d/query" % card, {})["data"]["cols"]]
        note("model-columns", table, str(cols))
        ds("model-then-id", table, {"source-table": "card__%d" % card, "fields": [by_name], "limit": 3})
        ds("model-then-max", table, {"source-table": "card__%d" % card, "aggregation": [["max", by_name]]})
        ds("model-then-filter", table, {"source-table": "card__%d" % card, "filter": [">", by_name, 100000], "aggregation": [["count"]]})
    except RuntimeError as e:
        note("model", table, "ERR " + str(e)[:170])
    # Export and pivot.
    for url in ("/api/dataset/pivot",):
        ds("pivot", table, {"source-table": tid, "breakout": [k], "aggregation": [["count"]]}, url)
    st, text, _ = lib.http("POST", mb.url + "/api/dataset/csv", {"format_rows": False, "query": json.dumps({"database": db, "type": "query", "query": {"source-table": tid, "fields": [k], "limit": 3}})},
                           {"X-Metabase-Session": mb.sid}, form=True)
    note("csv", table, "%s %s" % (st, text[:120].replace("\n", " | ")))
    # Field endpoints.
    for door, path in (("field-values", "/api/field/%d/values" % fid), ("field-search", "/api/field/%d/search/%d?value=1&limit=5" % (fid, fid)), ("field-summary", "/api/field/%d/summary" % fid),
                       ("field-related", "/api/field/%d/related" % fid), ("field-remapping", "/api/field/%d/remapping/%d?value=1" % (fid, fid))):
        st, text, _ = lib.http("GET", mb.url + path, headers={"X-Metabase-Session": mb.sid})
        note(door, table, "%s %s" % (st, text[:170].replace("\n", " ")))
    # Rescan, then read.
    st, text, _ = lib.http("POST", mb.url + "/api/field/%d/rescan_values" % fid, {}, {"X-Metabase-Session": mb.sid})
    note("field-rescan", table, "%s %s" % (st, text[:90]))
    st, text, _ = lib.http("GET", mb.url + "/api/field/%d/values" % fid, headers={"X-Metabase-Session": mb.sid})
    note("field-values-after-rescan", table, "%s %s" % (st, text[:170]))
    for door, path, body in (("parameter-values", "/api/dataset/parameter/values", {"parameter": {"id": "p", "type": "id", "values_source_type": None}, "field_ids": [fid]}),
                             ("parameter-search", "/api/dataset/parameter/search/1", {"parameter": {"id": "p", "type": "id"}, "field_ids": [fid]})):
        st, text, _ = lib.http("POST", mb.url + path, body, {"X-Metabase-Session": mb.sid})
        note(door, table, "%s %s" % (st, text[:170]))
    # Template tags and parameters that target the key.
    res = mb.api("POST", "/api/dataset", {"database": db, "type": "query", "query": {"source-table": tid, "aggregation": [["count"]]}, "parameters": [{"type": "number/>=", "target": ["dimension", k], "value": [100000]}]}, ok=(200, 202, 400, 403, 500))
    note("parameter-on-key", table, summarize(res))
    tag = {"f": {"id": "f", "name": "f", "display-name": "f", "type": "dimension", "dimension": k, "widget-type": "number/>="}}
    res = mb.api("POST", "/api/dataset", {"database": db, "type": "native", "native": {"query": "SELECT COUNT(*) FROM %s WHERE {{f}}" % table, "template-tags": tag}, "parameters": [{"type": "number/>=", "target": ["dimension", ["template-tag", "f"]], "value": [100000]}]}, ok=(200, 202, 400, 403, 500))
    note("native-field-filter", table, summarize(res))
    # Native SQL.
    for door, sql in (("native-select", "SELECT %s FROM %s LIMIT 3" % (name, table)), ("native-star", "SELECT * FROM %s LIMIT 3" % table), ("native-max", "SELECT MAX(%s) FROM %s" % (name, table)),
                      ("native-where", "SELECT COUNT(*) FROM %s WHERE %s > 100000" % (table, name)), ("native-order", "SELECT 1 AS a FROM %s ORDER BY %s DESC LIMIT 1" % (table, name)),
                      ("native-sub", "SELECT i FROM (SELECT %s AS i FROM %s) t LIMIT 3" % (name, table)), ("native-cte", "WITH t AS (SELECT %s AS i FROM %s) SELECT i FROM t LIMIT 3" % (name, table)),
                      ("native-quoted", 'SELECT "%s" FROM "%s" LIMIT 3' % (name, table)), ("native-upper", "SELECT %s FROM %s LIMIT 3" % (name.upper(), table.upper())),
                      ("native-alias-dot", "SELECT p.%s FROM %s p LIMIT 3" % (name, table)), ("native-group", "SELECT %s, COUNT(*) FROM %s GROUP BY 1 LIMIT 3" % (name, table)),
                      ("native-join-field", "SELECT __cubeJoinField FROM %s LIMIT 3" % table), ("native-comment", "SELECT /*x*/ %s /*y*/ FROM %s LIMIT 3" % (name, table)),
                      ("native-cast", "SELECT CAST(%s AS text) FROM %s LIMIT 3" % (name, table)), ("native-info-schema", "SELECT * FROM information_schema.columns WHERE table_name = '%s' LIMIT 3" % table)):
        res = mb.api("POST", "/api/dataset", {"database": db, "type": "native", "native": {"query": sql, "template-tags": {}}}, ok=(200, 202, 400, 403, 500))
        note(door, table, summarize(res))
    # MCP.
    def mq(door, q):
        res, err = mcp.call("execute_query", query={"lib/type": "mbql/query", "database": db, "stages": [dict({"lib/type": "mbql.stage/mbql", "source-table": tid}, **q)]}, row_limit=3)
        note("mcp-" + door, table, ("ERR " + str(err)[:170]) if err else json.dumps(res.get("rows") if isinstance(res, dict) else res, default=str)[:170])
    mq("raw", {})
    mq("fields", {"fields": [["field", {}, fid]]})
    mq("breakout", {"breakout": [["field", {}, fid]], "aggregation": [["count", {}]]})
    mq("max", {"aggregation": [["max", {}, ["field", {}, fid]]]})
    mq("filter", {"filters": [[">", {}, ["field", {}, fid], 100000]], "aggregation": [["count", {}]]})
    res, err = mcp.call("execute_sql", database_id=db, sql="SELECT %s FROM %s LIMIT 3" % (name, table))
    note("mcp-execute-sql", table, ("ERR " + str(err)[:170]) if err else json.dumps(res.get("rows") if isinstance(res, dict) else res, default=str)[:170])
    res, err = mcp.call("browse_data", action="get_fields", table_ids=[tid], include_hidden=True)
    text = json.dumps(res, default=str) if not err else err
    note("mcp-get-fields-hidden", table, ("ERR " if err else "") + ("names key" if ('"%s"' % name) in text else "no key") + " values:" + str("100001" in text or "field_values" in text))
    res, err = mcp.call("browse_data", action="get_fields", table_ids=[tid])
    text = json.dumps(res, default=str) if not err else err
    note("mcp-get-fields", table, ("ERR " if err else "") + ("names key" if ('"%s"' % name) in text else "no key"))
    res, err = mcp.call("search", term_queries=[table + " " + name])
    note("mcp-search", table, ("ERR " + str(err)[:120]) if err else "ok, 100001 in text: %s" % ("100001" in json.dumps(res, default=str)))
    # X-ray.
    st, text, _ = lib.http("GET", mb.url + "/api/automagic-dashboards/table/%d" % tid, headers={"X-Metabase-Session": mb.sid}, timeout=120)
    note("xray-table", table, "%s len=%d" % (st, len(text)))
    if st == 200:
        dash = json.loads(text)
        seen = shown = 0
        for dc in dash.get("dashcards", []):
            card_ = dc.get("card") or {}
            q = card_.get("dataset_query")
            if not q:
                continue
            seen += 1
            if ("[\"field\",%d" % fid) in json.dumps(q, separators=(",", ":")):
                res = mb.api("POST", "/api/dataset", q, ok=(200, 202, 400, 403, 500))
                shown += 1
                note("xray-card-on-key", table, summarize(res))
        note("xray-cards", table, "cards=%d on key=%d" % (seen, shown))
    st, text, _ = lib.http("GET", mb.url + "/api/automagic-dashboards/field/%d" % fid, headers={"X-Metabase-Session": mb.sid}, timeout=120)
    note("xray-field", table, "%s len=%d" % (st, len(text)))
    if st == 200:
        for dc in json.loads(text).get("dashcards", []):
            q = (dc.get("card") or {}).get("dataset_query")
            if q:
                note("xray-field-card", table, summarize(mb.api("POST", "/api/dataset", q, ok=(200, 202, 400, 403, 500))))
    # A person turns the field on. Admin only.
    if table == "payments":
        mb.api("PUT", "/api/field/%d" % fid, {"visibility_type": "normal", "has_field_values": "list"})
        lib.http("POST", mb.url + "/api/field/%d/rescan_values" % fid, {}, {"X-Metabase-Session": mb.sid})
        st, text, _ = lib.http("GET", mb.url + "/api/field/%d/values" % fid, headers={"X-Metabase-Session": mb.sid})
        note("admin-visible-values", table, "%s %s" % (st, text[:150]))
        ds("admin-visible-raw", table, {"source-table": tid, "fields": [k], "limit": 3})
        ds("admin-visible-max", table, {"source-table": tid, "aggregation": [["max", k]]})
        mb.api("PUT", "/api/field/%d" % fid, {"visibility_type": "hidden", "has_field_values": "none"})

# A join reaches the key through its foreign key.
fk = ctx.field("stock_items", "warehouse_id")
key = fk.get("fk_target_field_id")
note("fk", "stock_items", "fk to %s" % key)
tid = ctx.tables["stock_items"]["id"]
if key:
    ds("implicit-breakout", "stock_items", {"source-table": tid, "breakout": [["field", key, {"source-field": fk["id"]}]], "aggregation": [["count"]]})
    ds("implicit-max", "stock_items", {"source-table": tid, "aggregation": [["max", ["field", key, {"source-field": fk["id"]}]]]})
    ds("implicit-filter", "stock_items", {"source-table": tid, "filter": ["=", ["field", key, {"source-field": fk["id"]}], 1], "aggregation": [["count"]]})
    ds("explicit-join-all", "stock_items", {"source-table": tid, "joins": [{"source-table": ctx.tables["warehouses"]["id"], "alias": "W", "strategy": "left-join", "fields": "all",
       "condition": ["=", ["field", fk["id"], None], ["field", key, {"join-alias": "W"}]]}], "limit": 3})
    ds("explicit-join-key", "stock_items", {"source-table": tid, "joins": [{"source-table": ctx.tables["warehouses"]["id"], "alias": "W", "strategy": "left-join", "fields": [["field", key, {"join-alias": "W"}]],
       "condition": ["=", ["field", fk["id"], None], ["field", key, {"join-alias": "W"}]]}], "limit": 3})
    ds("explicit-join-max", "stock_items", {"source-table": tid, "joins": [{"source-table": ctx.tables["warehouses"]["id"], "alias": "W", "strategy": "left-join", "fields": "none",
       "condition": ["=", ["field", fk["id"], None], ["field", key, {"join-alias": "W"}]]}], "aggregation": [["max", ["field", key, {"join-alias": "W"}]]]})
    ds("explicit-join-on-other", "warehouses", {"source-table": ctx.tables["warehouses"]["id"], "joins": [{"source-table": tid, "alias": "S", "strategy": "left-join", "fields": "none",
       "condition": ["=", ["field", key, None], ["field", fk["id"], {"join-alias": "S"}]]}], "aggregation": [["count"]]})
