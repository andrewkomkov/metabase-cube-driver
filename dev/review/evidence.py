"""One short repro for each finding of docs/review/0.1.0-final.md: the query, its SQL, the output and the truth. Run it on the review stack."""
import json

import lib
import run_grid as R

mb = lib.Metabase().login()
CTX = {d: R.Ctx(mb, d) for d in ("Cube", "Cube stock")}
CRT = "d:orders.created_at"


def case(fid, table_shape, truth, dbs=("Cube", "Cube stock")):
    print("###", fid)
    for db in dbs:
        ctx = CTX[db]
        q = ctx.dq(ctx.inner(table_shape)) if "native" not in table_shape else {"database": ctx.db, "type": "native", "native": {"query": table_shape["native"]}}
        rows, _, err = mb.run(q)
        if db == dbs[0]:
            sql = mb.api("POST", "/api/dataset/native", q, ok=(200, 202, 400, 500))
            print("sql:", " ".join(str(sql.get("query", sql)).split())[:420])
        print(db + ":", json.dumps(rows[:4] if rows else rows), err[:200] if err else "")
    print("truth:", json.dumps(lib.ch(truth)[:4]))


case("B1 equal time at the start of a month, by month", {"table": "orders", "agg": ["m:orders.count"], "by": [CRT + "@month"], "where": [["=", CRT, "2025-03-01T00:00:00"]]},
     "SELECT toStartOfMonth(created_at), count() FROM orders WHERE created_at = '2025-03-01 00:00:00' GROUP BY 1")
case("B1 the same row, by year", {"table": "orders", "agg": ["m:orders.count"], "by": [CRT + "@year"], "where": [["=", CRT, "2025-01-01T00:00:00"]]},
     "SELECT toStartOfYear(created_at), count() FROM orders WHERE created_at = '2025-01-01 00:00:00' GROUP BY 1")
case("B1 a rollup of days, by hour", {"table": "big_orders", "agg": ["m:big_orders.count"], "by": ["d:big_orders.created_at@hour"], "where": [["=", "d:big_orders.created_at", "2024-03-15T02:00:00"]]},
     "SELECT toStartOfHour(created_at), count() FROM big_orders WHERE created_at = '2024-03-15 02:00:00' GROUP BY 1")
case("B1 a cube with no rollup, by quarter", {"table": "typed_events", "agg": ["m:typed_events.count"], "by": ["d:typed_events.ts_utc@quarter"], "where": [["=", "d:typed_events.ts_utc", "2025-04-01T00:00:00"]]},
     "SELECT toStartOfQuarter(ts_utc), count() FROM typed_events WHERE ts_utc = '2025-04-01 00:00:00' GROUP BY 1")
case("B1 the two bound form gives the truth", {"table": "orders", "agg": ["m:orders.count"], "by": [CRT + "@month"], "where": [[">=", CRT, "2025-03-01T00:00:00"], ["<=", CRT, "2025-03-01T00:00:00"]]},
     "SELECT toStartOfMonth(created_at), count() FROM orders WHERE created_at = '2025-03-01 00:00:00' GROUP BY 1")
case("B2 contains, case sensitive by default", {"table": "orders", "agg": ["m:orders.count"], "where": [["contains", "d:orders.status", "COMP"]]}, "SELECT countIf(status LIKE '%COMP%') FROM orders")
case("B2 does-not-contain", {"table": "orders", "agg": ["m:orders.count"], "where": [["does-not-contain", "d:orders.status", "COMP"]]}, "SELECT countIf(status NOT LIKE '%COMP%') FROM orders")
case("B2 native LIKE", {"native": "SELECT MEASURE(count) FROM orders WHERE status LIKE '%COMP%'"}, "SELECT countIf(status LIKE '%COMP%') FROM orders")
case("B3 native rolling, a function in the filter", {"native": "SELECT DATE_TRUNC('month', created_at), MEASURE(revenue_mtd) FROM orders WHERE DATE_TRUNC('day', created_at) >= '2025-03-15' AND created_at < '2025-05-01' GROUP BY 1"},
     "SELECT toStartOfMonth(created_at), sum(amount) FROM orders WHERE created_at >= '2025-03-15' AND created_at < '2025-05-01' GROUP BY 1 ORDER BY 1")
case("B3 native rolling, interval arithmetic", {"native": "SELECT DATE_TRUNC('month', created_at), MEASURE(revenue_mtd) FROM orders WHERE created_at >= TIMESTAMP '2025-03-01 00:00:00' + INTERVAL '14 day' AND created_at < '2025-05-01' GROUP BY 1"},
     "SELECT toStartOfMonth(created_at), sum(amount) FROM orders WHERE created_at >= '2025-03-15' AND created_at < '2025-05-01' GROUP BY 1 ORDER BY 1")
case("M1 month to date by a quarter", {"table": "orders", "agg": ["m:orders.revenue_mtd"], "by": [CRT + "@quarter"], "where": [[">=", CRT, "2025-01-01"], ["<", CRT, "2025-07-01"]]},
     "SELECT toStartOfQuarter(created_at), sumIf(amount, toMonth(created_at) IN (3, 6)) FROM orders WHERE created_at < '2025-07-01' GROUP BY 1 ORDER BY 1")
case("M2 week of year, Metabase meaning", {"table": "orders", "agg": ["m:orders.count"], "by": [CRT + "@week-of-year"], "where": [["between", CRT, "2025-01-05", "2025-01-11"]]},
     "SELECT ceiling(toDayOfYear(toStartOfWeek(created_at, 0)) / 7), count() FROM orders WHERE toDate(created_at) BETWEEN '2025-01-05' AND '2025-01-11' GROUP BY 1")
case("m1 week and NOT of OR", {"table": "orders", "agg": ["m:orders.min_amount"], "by": [CRT + "@week"], "where": [["not", ["or", ["=", "d:orders.status", "completed"], [">=", "d:orders.quantity_dim", 4]]]]},
     "SELECT toStartOfWeek(created_at, 0), min(amount) FROM orders WHERE NOT (status = 'completed' OR quantity >= 4) GROUP BY 1 ORDER BY 1")

T, TN = "2025-06-15T11:00:00", "2025-06-15 11:00:00"
try:
    for tz in ("America/New_York", "Asia/Kathmandu"):
        mb.api("PUT", "/api/setting/report-timezone", {"value": tz})
        L = "toTimeZone(created_at, '%s')" % tz
        print("===== report time zone", tz)
        case("B4a one time bound, " + tz, {"table": "orders", "agg": ["m:orders.count"], "where": [[">=", CRT, T]]}, "SELECT count() FROM orders WHERE created_at >= toDateTime('%s', '%s')" % (TN, tz))
        case("B4b month to date, " + tz, {"table": "orders", "agg": ["m:orders.revenue_mtd"], "by": [CRT + "@month"], "where": [[">=", CRT, "2025-03-01"], ["<", CRT, "2025-05-01"]]},
             "SELECT toStartOfMonth(%s), sum(amount) FROM orders WHERE created_at >= toDateTime('2025-03-01 00:00:00', '%s') AND created_at < toDateTime('2025-05-01 00:00:00', '%s') GROUP BY 1 ORDER BY 1" % (L, tz, tz))
        case("B4c hour of day, " + tz, {"table": "orders", "agg": ["m:orders.count"], "by": [CRT + "@hour-of-day"]}, "SELECT toHour(%s), count() FROM orders GROUP BY 1 ORDER BY 1" % L)
        case("M3 Date column by month, " + tz, {"table": "typed_events", "agg": ["m:typed_events.count"], "by": ["d:typed_events.day@month"]}, "SELECT toStartOfMonth(day), count() FROM typed_events GROUP BY 1 ORDER BY 1")
finally:
    mb.api("PUT", "/api/setting/report-timezone", {"value": None})

# A second page of MCP with no row_limit, on Cube and on a Metabase with no plugin.
ctx = CTX["Cube"]
c = lib.Mcp(mb)
res, _ = c.call("execute_query", query=ctx.mbql5({"table": "orders", "fields": ["d:orders.id"], "limit": 2500}), row_limit=2000)
print("mcp page 1 on Cube", len(res["rows"]), res.get("truncated"), bool(res.get("next_cursor")))
res, _ = c.call("execute_query", cursor=res["next_cursor"])
print("mcp page 2 on Cube, no row_limit", len(res["rows"]), res.get("truncated"), bool(res.get("next_cursor")))
plain = lib.Metabase("http://127.0.0.1:13301").login()
c2 = lib.Mcp(plain)
t = [x["id"] for x in plain.api("GET", "/api/database/1/metadata")["tables"] if x["name"].lower() == "orders"][0]
q = {"lib/type": "mbql/query", "database": 1, "stages": [{"lib/type": "mbql.stage/mbql", "source-table": t, "limit": 2500}]}
res, _ = c2.call("execute_query", query=q, row_limit=2000)
print("mcp page 1 on Sample Database, no plugin", len(res["rows"]), res.get("truncated"), bool(res.get("next_cursor")))
res, _ = c2.call("execute_query", cursor=res["next_cursor"])
print("mcp page 2 on Sample Database, no plugin, no row_limit", len(res["rows"]), res.get("truncated"), bool(res.get("next_cursor")))
