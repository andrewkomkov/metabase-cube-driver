"""Each entry of "Known wrong numbers" in README, and the clock-change hour. Prints what Cube gives, ClickHouse, and Metabase on Postgres where it applies."""
import json

import lib
import run_grid

NY = "America/New_York"
mb = lib.Metabase().login()
cube, stock = run_grid.Ctx(mb, "Cube"), run_grid.Ctx(mb, "Cube stock")
pg_db = [d for d in mb.api("GET", "/api/database")["data"] if d["name"] == "PG"][0]["id"]
pg = {t["name"]: t for t in mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % pg_db)["tables"]}
PF = lambda t, n: ["field", [f for f in pg[t]["fields"] if f["name"] == n][0]["id"], None]  # noqa: E731


def run(db, q):
    res = mb.api("POST", "/api/dataset", dict({"database": db}, **q), ok=(200, 202, 400, 403, 500))
    if res.get("error") or "data" not in res:
        return "ERR " + str(res.get("error"))[:200]
    return res["data"]["rows"][:6]


def mbql(c, table, **kw):
    q = dict({"source-table": c.tables[table]["id"]}, **c.walk(kw))
    return run(c.db, {"type": "query", "query": q})


def nat(c, sql):
    return run(c.db, {"type": "native", "native": {"query": sql, "template-tags": {}}})


def zone(z):
    mb.api("PUT", "/api/setting/report-timezone", {"value": z})


zone(None)
print("== long decimal below 2^53")
print("truth", lib.ch("SELECT id, toString(dec_big) FROM typed_events ORDER BY id LIMIT 3"))
for c, n in ((cube, "dialect"), (stock, "stock")):
    print(n, mbql(c, "typed_events", fields=["d:typed_events.id", "d:typed_events.dec_big", "d:typed_events.dec_big_text"], **{"order-by": [["asc", "d:typed_events.id"]]}, limit=3))
print("== offset in a literal under a report zone")
zone(NY)
for lit, instant in (("2025-06-15T11:00:00Z", "2025-06-15 11:00:00"), ("2025-06-15T11:00:00-05:00", "2025-06-15 16:00:00")):
    print(lit, "truth at the instant", lib.ch("SELECT count() FROM orders WHERE created_at >= toDateTime('%s', 'UTC')" % instant), "truth when read in the zone", lib.ch("SELECT count() FROM orders WHERE created_at >= toDateTime('2025-06-15 11:00:00', '%s')" % NY))
    print("  PG", run(pg_db, {"type": "query", "query": {"source-table": pg["orders"]["id"], "aggregation": [["count"]], "filter": [">=", PF("orders", "created_at"), lit]}}))
    for c, n in ((cube, "dialect"), (stock, "stock")):
        print(" ", n, mbql(c, "orders", aggregation=[["count"]], filter=[">=", "d:orders.created_at", lit]))
zone(None)
print("== native SQL that the scan may not read")
truth_mtd = lib.ch("SELECT sum(amount) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-03-10'")
print("truth of the month to 03-09", truth_mtd, "total", lib.ch("SELECT sum(amount) FROM orders"))
for sql in ("SELECT MEASURE(revenue_mtd) FROM orders", "SELECT revenue_mtd FROM orders", 'SELECT MEASURE("revenue_mtd") FROM "orders"', "SELECT x FROM (SELECT MEASURE(revenue_mtd) AS x FROM orders) t",
            "WITH t AS (SELECT MEASURE(revenue_mtd) AS x FROM orders) SELECT x FROM t", "SELECT /* d */ MEASURE( revenue_mtd ) FROM orders", "SELECT MEASURE(o.revenue_mtd) FROM orders o",
            "SELECT status, MEASURE(revenue_trailing_30d) FROM orders GROUP BY 1", "SELECT DATE_TRUNC('month', created_at) m, MEASURE(revenue_mtd) x FROM orders WHERE created_at >= '2025-03-05' AND created_at < '2025-03-20' GROUP BY 1",
            "SELECT DATE_TRUNC('day', created_at) m, MEASURE(revenue_trailing_30d) x FROM orders WHERE created_at BETWEEN '2025-03-05' AND '2025-03-06' GROUP BY 1",
            "SELECT MEASURE(revenue_prior_month) FROM orders", "SELECT DATE_TRUNC('month', created_at), MEASURE(revenue_prior_month) FROM orders GROUP BY 1 LIMIT 2",
            "SELECT MEASURE(n) FROM null_keys", "SELECT MEASURE(n_of_a) FROM null_keys WHERE ts >= '2025-01-01'", "SELECT COUNT(*) FROM null_keys", "SELECT x FROM (SELECT MEASURE(n) AS x FROM null_keys) t", "SELECT MEASURE(rev_range) FROM pa_partitions",
            "SELECT x FROM (SELECT MEASURE(rev_range) AS x FROM pa_partitions) t", "WITH t AS (SELECT MEASURE(rev_range) AS x FROM pa_partitions) SELECT x FROM t", "SELECT status, MEASURE(rev_range) FROM pa_partitions GROUP BY 1",
            "SELECT DATE_TRUNC('month', created_at), MEASURE(rev_range) FROM pa_partitions GROUP BY 1", "SELECT MEASURE(revenue) FROM lambda_sales WHERE created_at >= '2025-08-01' AND created_at < '2025-09-01'",
            "SELECT MEASURE(balance_closing) FROM metric_balances", "SELECT MEASURE(revenue) FROM orders WHERE created_at = '2025-03-09 10:00:00'", "SELECT DATE_TRUNC('day', created_at), MEASURE(count) FROM orders WHERE created_at = '2025-03-09 10:00:00' GROUP BY 1",
            "SELECT count(*) FROM orders WHERE LOWER(status) = 'SHIPPED'", "SELECT count(*) FROM orders WHERE created_at BETWEEN '2025-03-09' AND '2025-03-09'", "SELECT count(*) FROM orders WHERE status NOT LIKE '%ip%'",
            "SELECT count(*) FROM orders WHERE status ~ 'ship'", "SELECT count(*) FROM orders WHERE status SIMILAR TO 'ship%'", "SELECT count(*) FROM orders WHERE SUBSTR(status, 1, 4) = 'ship'", "SELECT count(*) FROM orders WHERE status ILIKE 'SHIP%'"):
    print(sql[:96], "=>", json.dumps(nat(cube, sql), default=str)[:150], flush=True)
print("truth null_keys", lib.ch("SELECT count(), count(id), countIf(grp='a') FROM null_keys"), "orders at the hour", lib.ch("SELECT sum(amount), count() FROM orders WHERE created_at = '2025-03-09 10:00:00'"), "lambda aug", lib.ch("SELECT sum(amount) FROM orders WHERE created_at >= '2025-08-01' AND created_at < '2025-09-01'"), "rev_range total", lib.ch("SELECT sum(amount) FROM orders"))
print("truth text", lib.ch("SELECT countIf(lower(status)='SHIPPED'), countIf(toDate(created_at)='2025-03-09' AND created_at<='2025-03-09'), countIf(position(status,'ip')=0), countIf(status='shipped') FROM orders"), "day", lib.ch("SELECT count() FROM orders WHERE toDate(created_at)='2025-03-09'"))
print("== on stock with no dialect")
zone(NY)
print("Date32 by month, truth", lib.ch("SELECT toStartOfMonth(day32) m, count() FROM typed_events GROUP BY m ORDER BY m"), "stock", mbql(stock, "typed_events", aggregation=[["count"]], breakout=["d:typed_events.day32@month"]), "dialect", mbql(cube, "typed_events", aggregation=[["count"]], breakout=["d:typed_events.day32@month"]))
print("Date marked date_only, stock", mbql(stock, "typed_events", aggregation=[["count"]], breakout=["d:typed_events.day@month"]))
zone(None)
sql = "SELECT COUNT(*) FROM ny_a64 WHERE at >= TIMESTAMP '2025-03-08 00:00:00' AND at < TIMESTAMP '2025-03-09 00:00:00'"
print("native half-open, truth", lib.ch("SELECT count() FROM zone_events WHERE ts64 >= '2025-03-08 00:00:00' AND ts64 < '2025-03-09 00:00:00'"), "stock", nat(stock, sql), "dialect", nat(cube, sql))
print("builder half-open, stock", mbql(stock, "ny_a64", aggregation=[["count"]], filter=["and", [">=", "d:ny_a64.at", "2025-03-08T00:00:00"], ["<", "d:ny_a64.at", "2025-03-09T00:00:00"]]))
print("raw ts64, truth", lib.ch("SELECT toString(ts64) FROM zone_events ORDER BY id LIMIT 2"), "stock", mbql(stock, "ny_a64", fields=["d:ny_a64.at"], **{"order-by": [["asc", "d:ny_a64.id"]]}, limit=2), "dialect", mbql(cube, "ny_a64", fields=["d:ny_a64.at"], **{"order-by": [["asc", "d:ny_a64.id"]]}, limit=2))
print("rollup empty text, truth", lib.ch("SELECT label, sum(amount) FROM text_edges GROUP BY label"), "stock", mbql(stock, "text_edges", aggregation=[["measure", stock.measures[("text_edges", "total")]]], breakout=["d:text_edges.label"]), "dialect", mbql(cube, "text_edges", aggregation=[["measure", cube.measures[("text_edges", "total")]]], breakout=["d:text_edges.label"]))
print("== the repeated hour of 2025-11-02 in New York")
zone(NY)
for table, m in (("orders", "revenue"), ("pa_grain", "revenue"), ("pa_types", "revenue"), ("sales", "revenue"), ("metric_types", "m_revenue"), ("metric_rollup_sales", "revenue")):
    res = mb.api("POST", "/api/dataset", {"database": cube.db, "type": "query", "query": {"source-table": cube.tables[table]["id"], "aggregation": [["measure", cube.measures[(table, m)]]], "breakout": [cube.ref("d:%s.created_at@hour" % table)]}})
    near = [r for r in res["data"]["rows"] if r[0].startswith("2025-11-02T0")]
    print(table, "rows", len(res["data"]["rows"]), "near", near[:6])
print("truth hour by instant", lib.ch("SELECT toString(toStartOfHour(toTimeZone(created_at, '%s'))), toString(toStartOfHour(created_at)), sum(amount) FROM orders WHERE toDate(toTimeZone(created_at, '%s')) = '2025-11-02' GROUP BY 1, 2 ORDER BY 2" % (NY, NY))[:6])
rows = run(pg_db, {"type": "query", "query": {"source-table": pg["tz_events"]["id"], "aggregation": [["count"]], "breakout": [["field", [f for f in pg["tz_events"]["fields"] if f["name"] == "ts"][0]["id"], {"temporal-unit": "hour"}]], "filter": ["between", ["field", [f for f in pg["tz_events"]["fields"] if f["name"] == "ts"][0]["id"], {"temporal-unit": "day"}], "2025-11-01", "2025-11-03"], "order-by": [["asc", ["field", [f for f in pg["tz_events"]["fields"] if f["name"] == "ts"][0]["id"], {"temporal-unit": "hour"}]]]}})
print("PG hour buckets on the day", json.dumps(rows, default=str)[:400])
res = mb.api("POST", "/api/dataset", {"database": cube.db, "type": "query", "query": {"source-table": cube.tables["ny_a"]["id"], "aggregation": [["count"]], "breakout": [cube.ref("d:ny_a.at@hour")], "filter": ["between", cube.ref("d:ny_a.at@day"), "2025-11-01", "2025-11-03"], "order-by": [["asc", cube.ref("d:ny_a.at@hour")]]}})
print("Cube hour buckets on the day", json.dumps(res["data"]["rows"][:6], default=str)[:400], "of", len(res["data"]["rows"]))
print("truth", lib.ch("SELECT toString(toStartOfHour(toTimeZone(ts, '%s'))) h, count() FROM zone_events WHERE toDate(toTimeZone(ts, '%s')) BETWEEN '2025-11-01' AND '2025-11-03' GROUP BY h ORDER BY h" % (NY, NY))[:6])
zone(None)
print("== rolling on one bucket, the item of PR 3")
print(mbql(cube, "orders", aggregation=[["measure", cube.measures[("orders", "cumulative_count")]]], breakout=["d:orders.created_at@month"], filter=["=", "d:orders.created_at@month", "2025-03-01"]), lib.ch("SELECT count() FROM orders WHERE created_at < '2025-04-01'"))
print(mbql(cube, "orders", aggregation=[["measure", cube.measures[("orders", "cumulative_count")]]], breakout=["d:orders.created_at@month"], filter=["between", "d:orders.created_at@month", "2025-03-01", "2025-05-01"]), lib.ch("SELECT toStartOfMonth(created_at) m, count() FROM orders GROUP BY m ORDER BY m")[:6])
