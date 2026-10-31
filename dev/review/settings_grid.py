"""Shapes under the settings report-timezone and start-of-week. These are global settings, so this script runs alone. A Postgres copy is a second opinion on what Metabase means."""
import json
import sys

import lib
import run_grid as R

mb = lib.Metabase().login()
OUT = open(sys.argv[1], "w")
CRT = "d:orders.created_at"
counts = {}


def record(sid, db, sh, want, tol=1e-9, ordered=False):
    ctx = CTX[db]
    rows, _, err = mb.run(ctx.dq(ctx.inner(sh)))
    if err:
        verdict, diff = "error", err[:300]
    else:
        diff = lib.compare(rows, want, ordered=ordered, tol=tol)
        verdict = "pass" if diff is None else "wrong"
    counts[verdict] = counts.get(verdict, 0) + 1
    OUT.write(json.dumps({"id": sid, "db": db, "verdict": verdict, "diff": diff, "got": (rows or [])[:3], "want": want[:3]}, default=str) + "\n")
    OUT.flush()
    if verdict != "pass":
        print(verdict, sid, db, str(diff)[:260], flush=True)


CTX = {d: R.Ctx(mb, d) for d in ("Cube", "Cube stock")}
T = "orders"
ZONES = ("America/New_York", "Asia/Kathmandu", "Europe/Berlin", "Pacific/Apia")
try:
    for tz in ZONES:
        mb.api("PUT", "/api/setting/report-timezone", {"value": tz})
        L = "toTimeZone(created_at, '%s')" % tz
        BUCKETS = {"hour": "toStartOfHour(%s)", "day": "toDate(%s)", "week": "toStartOfWeek(%s, 0)", "month": "toStartOfMonth(%s)", "quarter": "toStartOfQuarter(%s)", "year": "toStartOfYear(%s)",
                   "hour-of-day": "toHour(%s)", "day-of-week": "if(toDayOfWeek(%s) = 7, 1, toDayOfWeek(%s) + 1)", "month-of-year": "toMonth(%s)", "day-of-month": "toDayOfMonth(%s)", "quarter-of-year": "toQuarter(%s)"}
        MEASURES = {"count": "count()", "revenue": "sum(amount)", "avg_order_value": "avg(toFloat64(amount))", "unique_customers": "uniqExact(user_id)"}
        for unit, e in BUCKETS.items():
            b = e.replace("%s", L)
            for m, sql in MEASURES.items():
                for db in CTX:
                    record("tz/%s/%s/%s" % (tz, unit, m), db, {"table": T, "agg": ["m:orders.%s" % m], "by": [CRT + "@" + unit]}, lib.ch("SELECT %s, %s FROM orders GROUP BY 1" % (b, sql)))
        FILTERS = {
            "between days": (["between", CRT, "2025-03-08", "2025-03-10"], "toDate(%s) BETWEEN '2025-03-08' AND '2025-03-10'" % L),
            "day": (["=", CRT + "@day", "2025-03-09"], "toDate(%s) = '2025-03-09'" % L),
            "month": (["=", CRT + "@month", "2025-03-01"], "toStartOfMonth(%s) = '2025-03-01'" % L),
            "ge time": ([">=", CRT, "2025-06-15T11:00:00"], "created_at >= toDateTime('2025-06-15 11:00:00', '%s')" % tz),
            "lt time": (["<", CRT, "2025-06-15T11:00:00"], "created_at < toDateTime('2025-06-15 11:00:00', '%s')" % tz),
            "gt time": ([">", CRT, "2025-06-15T11:00:00"], "created_at > toDateTime('2025-06-15 11:00:00', '%s')" % tz),
            "offset literal": ([">=", CRT, "2025-06-15T11:00:00Z"], "created_at >= toDateTime('2025-06-15 11:00:00', 'UTC')"),
            "two times": (["and", [">=", CRT, "2025-06-15T11:00:00"], ["<", CRT, "2025-06-16T11:00:00"]], "created_at >= toDateTime('2025-06-15 11:00:00', '%s') AND created_at < toDateTime('2025-06-16 11:00:00', '%s')" % (tz, tz)),
            "two dates": (["and", [">=", CRT, "2025-03-01"], ["<", CRT, "2025-04-01"]], "created_at >= toDateTime('2025-03-01 00:00:00', '%s') AND created_at < toDateTime('2025-04-01 00:00:00', '%s')" % (tz, tz)),
            "one date": ([">=", CRT, "2025-03-01"], "created_at >= toDateTime('2025-03-01 00:00:00', '%s')" % tz),
            "hour of day": (["=", CRT + "@hour-of-day", 5], "toHour(%s) = 5" % L),
        }
        for name, (clause, cond) in FILTERS.items():
            for db in CTX:
                record("tz/%s/filter %s" % (tz, name), db, {"table": T, "agg": ["m:orders.count"], "where": [clause]}, lib.ch("SELECT count() FROM orders WHERE %s" % cond))
                record("tz/%s/filter %s by month" % (tz, name), db, {"table": T, "agg": ["m:orders.revenue"], "by": [CRT + "@month"], "where": [clause]},
                       lib.ch("SELECT toStartOfMonth(%s), sum(amount) FROM orders WHERE %s GROUP BY 1" % (L, cond)))
        # Rolling windows and shifts in the zone.
        WHOLE = [[">=", CRT, "2025-03-01"], ["<", CRT, "2025-05-01"]]
        for db in CTX:
            mstart = "toDateTime('2025-03-01 00:00:00', '%s')" % tz
            mend = "toDateTime('2025-05-01 00:00:00', '%s')" % tz
            record("tz/%s/mtd" % tz, db, {"table": T, "agg": ["m:orders.revenue_mtd"], "by": [CRT + "@month"], "where": WHOLE},
                   lib.ch("SELECT toStartOfMonth(%s), sum(amount) FROM orders WHERE created_at >= %s AND created_at < %s GROUP BY 1" % (L, mstart, mend)))
            record("tz/%s/prior month" % tz, db, {"table": T, "agg": ["m:orders.revenue_prior_month"], "by": [CRT + "@month"], "where": WHOLE},
                   lib.ch("SELECT addMonths(toStartOfMonth(%s), 1) m, sum(amount) FROM orders GROUP BY m HAVING m >= '2025-03-01' AND m < '2025-05-01'" % L))
            # ClickHouse types in the zone.
            X = "toTimeZone(ts_utc, '%s')" % tz
            record("tz/%s/DateTime in New York by day" % tz, db, {"table": "typed_events", "agg": ["m:typed_events.count"], "by": ["d:typed_events.ts_ny@day"]},
                   lib.ch("SELECT toDate(%s), count() FROM typed_events GROUP BY 1" % X))
            record("tz/%s/Date by month" % tz, db, {"table": "typed_events", "agg": ["m:typed_events.count"], "by": ["d:typed_events.day@month"]},
                   lib.ch("SELECT toStartOfMonth(toDateTime(toString(day), '%s')), count() FROM typed_events GROUP BY 1" % tz))
            record("tz/%s/rollup revenue by day big" % tz, db, {"table": "big_orders", "agg": ["m:big_orders.revenue"], "by": ["d:big_orders.created_at@month"]},
                   lib.ch("SELECT toStartOfMonth(toTimeZone(created_at, '%s')), sum(amount) FROM big_orders GROUP BY 1" % tz))
finally:
    mb.api("PUT", "/api/setting/report-timezone", {"value": None})

# start-of-week, on the driver and on Postgres with the same data.
pg = None
if any(d["name"] == "PG" for d in mb.api("GET", "/api/database")["data"]):
    pg = R.Ctx(mb, "PG")
DAYS = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6}
try:
    for day, n in DAYS.items():
        mb.api("PUT", "/api/setting/start-of-week", {"value": day})
        # Monday is 1 in toDayOfWeek. n days after Sunday.
        sh = "(toDayOfWeek(created_at) - %d + 7) %% 7" % n
        starts = "toDate(created_at) - toIntervalDay(%s)" % sh
        SHAPES = {
            "week": ("week", "toDateTime(%s)" % starts),
            "day-of-week": ("day-of-week", "%s + 1" % sh),
            "week-of-year": ("week-of-year", "ceiling(toDayOfYear(%s) / 7)" % starts),
        }
        for name, (unit, expr) in SHAPES.items():
            for m, sql in (("count", "count()"), ("revenue", "sum(amount)"), ("unique_customers", "uniqExact(user_id)")):
                for db in CTX:
                    record("week/%s/%s/%s" % (day, name, m), db, {"table": T, "agg": ["m:orders.%s" % m], "by": [CRT + "@" + unit]}, lib.ch("SELECT %s, %s FROM orders GROUP BY 1" % (expr, sql)))
            if pg:
                col = pg.field("orders", "created_at")["id"]
                q = {"source-table": pg.tables["orders"]["id"], "aggregation": [["count"]], "breakout": [["field", col, {"temporal-unit": unit}]]}
                prows = mb.run(pg.dq(q))[0]
                want = lib.ch("SELECT %s, count() FROM orders GROUP BY 1" % expr)
                diff = lib.compare(prows, want)
                print("postgres says", day, name, "same as the truth of the review" if diff is None else "DIFFERENT " + diff[:200], flush=True)
                counts["oracle_same" if diff is None else "oracle_diff"] = counts.get("oracle_same" if diff is None else "oracle_diff", 0) + 1
    if pg:
        for label, clause, sql in (("contains is case sensitive", ["contains", ["field", pg.field("orders", "status")["id"], None], "COMP"], "SELECT 0"),
                                   ("starts-with is case sensitive", ["starts-with", ["field", pg.field("orders", "status")["id"], None], "COMP"], "SELECT 0"),
                                   ("does-not-contain is case sensitive", ["does-not-contain", ["field", pg.field("orders", "status")["id"], None], "COMP"], "SELECT 5000")):
            q = {"source-table": pg.tables["orders"]["id"], "aggregation": [["count"]], "filter": clause}
            print("postgres says", label, mb.run(pg.dq(q))[0], "truth", lib.ch(sql), flush=True)
finally:
    mb.api("PUT", "/api/setting/start-of-week", {"value": None})
print("done", counts)
