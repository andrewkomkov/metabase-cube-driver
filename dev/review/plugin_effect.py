"""A plugin effect on another database. The same queries on Sample Database and on Postgres, on a Metabase with the plugin and on one with no plugin. It compares rows,
compiled SQL, sync metadata and the MCP tools, and it uses the Postgres copy as a second opinion on what Metabase means by a week of year or a text filter."""
import json
import re
import time

import lib

WITH = lib.Metabase("http://127.0.0.1:13300").login()
PLAIN = lib.Metabase("http://127.0.0.1:13301").login()
PG = {"host": "postgres", "port": 5432, "dbname": "shop", "user": "shop", "password": "shop-dev-only", "ssl": False}


def connect(mb):
    have = {d["name"]: d["id"] for d in mb.api("GET", "/api/database")["data"]}
    if "PG" not in have:
        have["PG"] = mb.api("POST", "/api/database", {"engine": "postgres", "name": "PG", "details": PG})["id"]
        while mb.api("GET", "/api/database/%d" % have["PG"]).get("initial_sync_status") != "complete":
            time.sleep(2)
        time.sleep(15)
    return have


W, P = connect(WITH), connect(PLAIN)
results = {"rows_equal": 0, "rows_diff": [], "sql_equal": 0, "sql_diff": []}


def fields(mb, db):
    meta = mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % db)
    return {(t["name"].lower(), f["name"].lower()): f for t in meta["tables"] for f in t["fields"]}, {t["name"].lower(): t for t in meta["tables"]}


def shapes(F, T):
    """F maps a table and a column to a field id. The names are the columns of the Sample Database orders, or the review copy."""
    o, sub, ca = T["orders"]["id"], F[("orders", "total" if ("orders", "total") in F else "amount")]["id"], F[("orders", "created_at")]["id"]
    qty = F[("orders", "quantity")]["id"]
    pid = F[("orders", "product_id")]["id"]
    pcat = F[("products", "category")]["id"]
    out = {}
    for unit in ("hour", "day", "week", "month", "quarter", "year", "hour-of-day", "day-of-week", "day-of-month", "day-of-year", "week-of-year", "month-of-year", "quarter-of-year"):
        out["count by " + unit] = {"source-table": o, "aggregation": [["count"]], "breakout": [["field", ca, {"temporal-unit": unit}]]}
        out["sum by " + unit] = {"source-table": o, "aggregation": [["sum", ["field", sub, None]]], "breakout": [["field", ca, {"temporal-unit": unit}]]}
    t = ["field", ca, None]
    out.update({
        "stages": {"source-query": {"source-table": o, "aggregation": [["count"]], "breakout": [["field", ca, {"temporal-unit": "month"}]]}, "aggregation": [["max", ["field", "count", {"base-type": "type/BigInteger"}]]]},
        "stage filter": {"source-query": {"source-table": o, "aggregation": [["sum", ["field", sub, None]]], "breakout": [["field", qty, None]]}, "filter": [">", ["field", "sum", {"base-type": "type/Float"}], 100]},
        "implicit join": {"source-table": o, "aggregation": [["sum", ["field", sub, None]]], "breakout": [["field", pcat, {"source-field": pid}]]},
        "filter between": {"source-table": o, "aggregation": [["count"]], "filter": ["between", t, "2025-03-01", "2025-03-31"]},
        "filter strict": {"source-table": o, "aggregation": [["count"]], "filter": ["and", [">", t, "2025-03-01T01:00:00"], ["<=", t, "2025-03-02T01:00:00"]]},
        "filter equal time": {"source-table": o, "aggregation": [["count"]], "breakout": [["field", ca, {"temporal-unit": "month"}]], "filter": ["=", t, "2025-03-01T00:00:00"]},
        "cum sum": {"source-table": o, "aggregation": [["cum-sum", ["field", sub, None]]], "breakout": [["field", ca, {"temporal-unit": "year"}]]},
        "median": {"source-table": o, "aggregation": [["median", ["field", sub, None]]]},
        "percentile": {"source-table": o, "aggregation": [["percentile", ["field", sub, None], 0.9]]},
        "distinct": {"source-table": o, "aggregation": [["distinct", ["field", qty, None]]]},
        "datetime add": {"source-table": o, "expressions": {"later": ["datetime-add", t, 1, "month"]}, "fields": [["expression", "later"]], "limit": 3, "order-by": [["asc", ["field", F[("orders", "id")]["id"], None]]]},
        "datetime diff": {"source-table": o, "expressions": {"d": ["datetime-diff", t, "2026-01-01T00:00:00", "month"]}, "aggregation": [["max", ["expression", "d"]]]},
        "bins": {"source-table": o, "aggregation": [["count"]], "breakout": [["field", sub, {"binning": {"strategy": "num-bins", "num-bins": 10}}]]},
        "offset": {"source-table": o, "aggregation": [["offset", {}, ["count"], -1]], "breakout": [["field", ca, {"temporal-unit": "year"}]]},
        "raw limit": {"source-table": o, "limit": 5, "order-by": [["asc", ["field", F[("orders", "id")]["id"], None]]]},
    })
    return out


def compare(name, db_name):
    fw, tw = fields(WITH, W[db_name])
    fp, tp = fields(PLAIN, P[db_name])
    sw, sp = shapes(fw, tw), shapes(fp, tp)
    for k in sw:
        qw = {"database": W[db_name], "type": "query", "query": sw[k]}
        qp = {"database": P[db_name], "type": "query", "query": sp[k]}
        rw, _, ew = WITH.run(qw)
        rp, _, ep = PLAIN.run(qp)
        same = (ew == ep) if (ew or ep) else lib.compare(rw, rp, ordered=True) is None
        if same:
            results["rows_equal"] += 1
        else:
            results["rows_diff"].append((db_name, k, str(ew or (rw or [])[:2])[:200], str(ep or (rp or [])[:2])[:200]))
        cw = WITH.api("POST", "/api/dataset/native", qw, ok=(200, 202, 400, 500))
        cp = PLAIN.api("POST", "/api/dataset/native", qp, ok=(200, 202, 400, 500))
        tidy = lambda c: re.sub(r"\d+", "N", str(c.get("query") if isinstance(c, dict) else c))
        if tidy(cw) == tidy(cp):
            results["sql_equal"] += 1
        else:
            results["sql_diff"].append((db_name, k, tidy(cw)[:300], tidy(cp)[:300]))


for db in ("Sample Database", "PG"):
    compare("x", db)

# Native SQL, a parameter, and the time zone of a report.
NATIVE = {"Sample Database": "SELECT COUNT(*) FROM ORDERS WHERE TOTAL > 100", "PG": "SELECT COUNT(*) FROM orders WHERE amount > 100"}
for db, sql in NATIVE.items():
    a = WITH.run({"database": W[db], "type": "native", "native": {"query": sql}})[0]
    b = PLAIN.run({"database": P[db], "type": "native", "native": {"query": sql}})[0]
    print("native", db, a, b, "equal" if a == b else "DIFFERENT")

# Metadata that sync wrote.
for db in ("Sample Database", "PG"):
    fw, _ = fields(WITH, W[db])
    fp, _ = fields(PLAIN, P[db])
    keys = ("base_type", "effective_type", "semantic_type", "database_type", "visibility_type", "has_field_values")
    diff = [(k, n, fw[k].get(n), fp[k].get(n)) for k in fw if k in fp for n in keys if fw[k].get(n) != fp[k].get(n)]
    print("metadata", db, "fields", len(fw), len(fp), "diff", diff[:8])
    mw = [m for m in WITH.api("GET", "/api/measure") if m.get("table_id") in {t for t in (fields(WITH, W[db])[1][n]["id"] for n in fields(WITH, W[db])[1])}]
    print("measures on", db, "with the plugin", len(mw))

# The MCP tool that the plugin wraps: between with a time, on another database.
for mb, have, label in ((WITH, W, "with"), (PLAIN, P, "plain")):
    c = lib.Mcp(mb)
    f, t = fields(mb, have["PG"])
    for lo, hi in (("2025-03-01T01:00:00", "2025-03-01T23:00:00"), ("2025-03-01", "2025-03-02"), ("2025-03-01 01:00:00", "2025-03-01 23:00:00")):
        q = {"lib/type": "mbql/query", "database": have["PG"], "stages": [{"lib/type": "mbql.stage/mbql", "source-table": t["orders"]["id"], "aggregation": [["count", {}]],
                                                                          "filters": [["between", {}, ["field", {}, f[("orders", "created_at")]["id"]], lo, hi]]}]}
        res, err = c.call("execute_query", query=q)
        print("mcp between", label, lo, hi, (res or {}).get("rows"), (err or "")[:200])
print("truth of the time form", lib.ch("SELECT countIf(created_at BETWEEN '2025-03-01 01:00:00' AND '2025-03-01 23:00:00'), countIf(toDate(created_at) BETWEEN '2025-03-01' AND '2025-03-02') FROM orders"))

# The cache: a TTL on the root, on another database, as the wrap of the cache middleware sees it.
for mb, have, label in ((WITH, W, "with"), (PLAIN, P, "plain")):
    mb.api("PUT", "/api/cache", {"model": "root", "model_id": 0, "strategy": {"type": "ttl", "multiplier": 10, "min_duration_ms": 0}}, ok=(200, 204, 400, 402, 403, 404))
    q = {"database": have["PG"], "type": "native", "native": {"query": "SELECT COUNT(*), pg_sleep(0.3) IS NULL FROM orders"}}
    cached = []
    for _ in range(2):
        res = mb.api("POST", "/api/dataset", q)
        cached.append((res.get("cached"), res["data"]["rows"][0][0]))
    print("cache", label, cached)
    mb.api("DELETE", "/api/cache", {"model": "root", "model_id": [0]}, ok=(200, 204, 400, 402, 403, 404))

# The time zone of a report, on both Metabases, on Postgres.
for tz in ("America/New_York", "Asia/Kathmandu"):
    rs = []
    for mb, have in ((WITH, W), (PLAIN, P)):
        mb.api("PUT", "/api/setting/report-timezone", {"value": tz})
        f, t = fields(mb, have["PG"])
        q = {"database": have["PG"], "type": "query", "query": {"source-table": t["orders"]["id"], "aggregation": [["count"]],
                                                                "breakout": [["field", f[("orders", "created_at")]["id"], {"temporal-unit": "day"}]], "limit": 3,
                                                                "order-by": [["asc", ["field", f[("orders", "created_at")]["id"], {"temporal-unit": "day"}]]]}}
        rs.append(mb.run(q)[0])
        mb.api("PUT", "/api/setting/report-timezone", {"value": None})
    print("report tz", tz, "equal" if rs[0] == rs[1] else "DIFFERENT", rs[0])

# Metabase log lines with the name of the driver in a stack trace of another database.
print(json.dumps({"rows_equal": results["rows_equal"], "sql_equal": results["sql_equal"], "rows_diff": results["rows_diff"], "sql_diff": results["sql_diff"]}, indent=0)[:6000])

# The cache on a saved question, where Metabase uses it, and what sync wrote on the database and its tables.
for mb, have, label in ((WITH, W, "with"), (PLAIN, P, "plain")):
    mb.api("PUT", "/api/cache", {"model": "root", "model_id": 0, "strategy": {"type": "ttl", "multiplier": 100, "min_duration_ms": 0}}, ok=(200, 204, 400, 402, 403, 404))
    card = mb.api("POST", "/api/card", {"name": "cache review", "display": "table", "visualization_settings": {}, "type": "question", "collection_id": None,
                                        "dataset_query": {"database": have["PG"], "type": "native", "native": {"query": "SELECT COUNT(*), pg_sleep(1) IS NULL FROM orders"}}})
    seen = []
    for _ in range(3):
        res = mb.api("POST", "/api/card/%d/query" % card["id"], {"parameters": []}, ok=(200, 202))
        seen.append((bool(res.get("cached")), res["data"]["rows"][0][0]))
    print("cache of a saved question", label, seen)
    mb.api("DELETE", "/api/cache", {"model": "root", "model_id": [0]}, ok=(200, 204, 400, 402, 403, 404))
for db in ("Sample Database", "PG"):
    a = WITH.api("GET", "/api/database/%d" % W[db])
    b = PLAIN.api("GET", "/api/database/%d" % P[db])
    ta = {t["name"]: (t.get("description"), t.get("entity_type"), t.get("visibility_type")) for t in WITH.api("GET", "/api/database/%d/metadata" % W[db])["tables"]}
    tb = {t["name"]: (t.get("description"), t.get("entity_type"), t.get("visibility_type")) for t in PLAIN.api("GET", "/api/database/%d/metadata" % P[db])["tables"]}
    print("database", db, "description same", a.get("description") == b.get("description"), "features same", sorted(a.get("features", [])) == sorted(b.get("features", [])), "tables same", ta == tb)
