"""Shapes under the settings report-timezone and start-of-week, through /api/dataset or MCP. These are global settings, so this script runs alone. A Postgres
copy is a second opinion on what a unit means in Metabase.

    python3 settings_grid.py --db Cube --channel dataset --out results.jsonl [--only REGEX]
"""
import argparse
import concurrent.futures
import json
import re
import threading

import lib
import run_grid as R

CRT = "d:orders.created_at"
T = "orders"
ZONES = ("America/New_York", "Asia/Kathmandu", "Europe/Berlin", "Pacific/Apia")
DAYS = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6}


def zone_shapes(tz):
    """Each shape reads the zone `tz`. A literal with no offset is a time in that zone, and each truth writes it so."""
    L = "toTimeZone(created_at, '%s')" % tz

    def at(text):
        return "toDateTime('%s', '%s')" % (text, tz)

    out = []
    buckets = {"hour": "toStartOfHour(%s)", "day": "toDate(%s)", "week": "toStartOfWeek(%s, 0)", "month": "toStartOfMonth(%s)", "quarter": "toStartOfQuarter(%s)", "year": "toStartOfYear(%s)",
               "minute-of-hour": "toMinute(%s)", "hour-of-day": "toHour(%s)", "day-of-week": "if(toDayOfWeek(%s) = 7, 1, toDayOfWeek(%s) + 1)", "month-of-year": "toMonth(%s)", "day-of-month": "toDayOfMonth(%s)",
               "day-of-year": "toDayOfYear(%s)", "week-of-year": "ceiling(toDayOfYear(toStartOfWeek(%s, 0)) / 7)", "quarter-of-year": "toQuarter(%s)"}
    measures = {"count": "count()", "revenue": "sum(amount)", "avg_order_value": "avg(toFloat64(amount))", "unique_customers": "uniqExact(user_id)"}
    for unit, e in buckets.items():
        b = e.replace("%s", L)
        for m, sql in measures.items():
            out.append({"id": "tz/%s/%s/%s" % (tz, unit, m), "table": T, "agg": ["m:orders.%s" % m], "by": [CRT + "@" + unit], "truth": "SELECT %s, %s FROM orders GROUP BY 1" % (b, sql)})
    t1, t2 = "2025-06-15 11:00:00", "2025-06-16 11:00:00"
    i1, i2 = t1.replace(" ", "T"), t2.replace(" ", "T")
    day_cond = "toDate(%s) BETWEEN '2025-03-08' AND '2025-03-10'" % L
    filters = {
        "between days": (["between", CRT, "2025-03-08", "2025-03-10"], day_cond),
        "between times": (["between", CRT, i1, i2], "created_at >= %s AND created_at <= %s" % (at(t1), at(t2))),
        "day": (["=", CRT + "@day", "2025-03-09"], "toDate(%s) = '2025-03-09'" % L),
        "month": (["=", CRT + "@month", "2025-03-01"], "toStartOfMonth(%s) = '2025-03-01'" % L),
        "ge day": ([">=", CRT + "@day", "2025-03-09"], "toDate(%s) >= '2025-03-09'" % L),
        "ge time": ([">=", CRT, i1], "created_at >= %s" % at(t1)),
        "lt time": (["<", CRT, i1], "created_at < %s" % at(t1)),
        "le time": (["<=", CRT, i1], "created_at <= %s" % at(t1)),
        "gt time": ([">", CRT, i1], "created_at > %s" % at(t1)),
        "offset literal": ([">=", CRT, "2025-06-15T11:00:00Z"], "created_at >= toDateTime('2025-06-15 11:00:00', 'UTC')"),
        "two times": (["and", [">=", CRT, i1], ["<", CRT, i2]], "created_at >= %s AND created_at < %s" % (at(t1), at(t2))),
        "strict bound": (["and", [">", CRT, i1], ["<=", CRT, i2]], "created_at > %s AND created_at <= %s" % (at(t1), at(t2))),
        "two dates": (["and", [">=", CRT, "2025-03-01"], ["<", CRT, "2025-04-01"]], "created_at >= %s AND created_at < %s" % (at("2025-03-01 00:00:00"), at("2025-04-01 00:00:00"))),
        "one date": ([">=", CRT, "2025-03-01"], "created_at >= %s" % at("2025-03-01 00:00:00")),
        "bound next to a filter": (["and", [">=", CRT, i1], ["=", "d:orders.status", "shipped"]], "created_at >= %s AND status = 'shipped'" % at(t1)),
        "bound in or": (["or", [">=", CRT, i1], ["=", "d:orders.status", "shipped"]], "created_at >= %s OR status = 'shipped'" % at(t1)),
        "two bounds in or": (["or", ["and", [">=", CRT, i1], ["<", CRT, i2]], ["=", "d:orders.status", "nope"]], "created_at >= %s AND created_at < %s" % (at(t1), at(t2))),
        "not ge": (["not", [">=", CRT, i1]], "NOT created_at >= %s" % at(t1)),
        "not group": (["not", ["or", [">=", CRT, i1], ["=", "d:orders.status", "shipped"]]], "NOT (created_at >= %s OR status = 'shipped')" % at(t1)),
        "not between": (["not", ["between", CRT, i1, i2]], "NOT (created_at >= %s AND created_at <= %s)" % (at(t1), at(t2))),
        "equal": (["=", CRT, "2025-03-15T07:00:00"], "created_at = %s" % at("2025-03-15 07:00:00")),
        "not equal": (["!=", CRT, "2025-03-15T07:00:00"], "created_at != %s" % at("2025-03-15 07:00:00")),
        "hour of day": (["=", CRT + "@hour-of-day", 5], "toHour(%s) = 5" % L),
        "relative": (["time-interval", CRT, -400, "day"], "toDate(%s) >= toDate(now('%s')) - 400 AND toDate(%s) < toDate(now('%s'))" % (L, tz, L, tz)),
    }
    for name, (clause, cond) in filters.items():
        out.append({"id": "tz/%s/filter %s" % (tz, name), "table": T, "agg": ["m:orders.count"], "where": [clause], "truth": "SELECT count() FROM orders WHERE %s" % cond})
        out.append({"id": "tz/%s/filter %s by month" % (tz, name), "table": T, "agg": ["m:orders.revenue"], "by": [CRT + "@month"], "where": [clause],
                    "truth": "SELECT toStartOfMonth(%s), sum(amount) FROM orders WHERE %s GROUP BY 1" % (L, cond)})
        out.append({"id": "tz/%s/filter %s on a view" % (tz, name), "table": "sales", "agg": ["m:sales.count"], "where": [_on_view(clause)], "truth": "SELECT count() FROM orders WHERE %s" % cond})
    # A week next to a filter. The first day of the week here has a custom granularity in the model, and it cannot go next to a filter for the database.
    out.append({"id": "tz/%s/week next to a bound" % tz, "table": T, "agg": ["m:orders.count"], "by": [CRT + "@week"], "where": [[">=", CRT, i1]],
                "truth": "SELECT toStartOfWeek(%s, 0), count() FROM orders WHERE created_at >= %s GROUP BY 1" % (L, at(t1))})
    # An equal time next to a bucket, the shape of finding B1.
    out.append({"id": "tz/%s/equal by the same month" % tz, "table": T, "agg": ["m:orders.count"], "by": [CRT + "@month"], "where": [["=", CRT, "2025-03-15T07:00:00"]],
                "truth": "SELECT toStartOfMonth(%s), count() FROM orders WHERE created_at = %s GROUP BY 1" % (L, at("2025-03-15 07:00:00"))})
    whole = [[">=", CRT, "2025-03-01"], ["<", CRT, "2025-05-01"]]
    # Rolling windows stop, findings B4b and M1.
    for m in ("revenue_mtd", "revenue_ytd", "revenue_trailing_30d", "revenue_leading_7d", "cumulative_count"):
        out.append({"id": "tz/%s/rolling %s" % (tz, m), "table": T, "agg": ["m:orders.%s" % m], "by": [CRT + "@month"], "where": whole, "truth": None, "expect": "refusal"})
    # A time shift reads a date range.
    out.append({"id": "tz/%s/prior month" % tz, "table": T, "agg": ["m:orders.revenue_prior_month"], "by": [CRT + "@month"], "where": whole,
                "truth": "SELECT addMonths(toStartOfMonth(%s), 1) m, sum(amount) FROM orders GROUP BY m HAVING m >= '2025-03-01' AND m < '2025-05-01'" % L})
    out.append({"id": "tz/%s/prior month one bound" % tz, "table": T, "agg": ["m:orders.revenue_prior_month"], "by": [CRT + "@month"], "where": [[">=", CRT, "2025-03-01"]], "truth": None, "expect": "refusal"})
    # Stages. A column of an aggregated stage is a time in the zone, and a column of raw rows goes to the scan.
    inner = {"table": T, "agg": ["m:orders.revenue"], "by": [CRT + "@month"], "where": []}
    q = "SELECT toStartOfMonth(%s) b, sum(amount) v FROM orders GROUP BY b" % L
    out.append({"id": "tz/%s/stage filter on a bucket" % tz, "inner": inner, "source": "inline", "outer": lambda c: {"filter": [">=", c["b"], "2025-07-01"]}, "truth": "SELECT b, v FROM (%s) WHERE b >= '2025-07-01'" % q})
    out.append({"id": "tz/%s/stage equal on a bucket" % tz, "inner": inner, "source": "question", "outer": lambda c: {"filter": ["=", c["b"], "2025-07-01"]}, "truth": "SELECT b, v FROM (%s) WHERE b = '2025-07-01'" % q})
    model = {"table": T, "fields": ["d:orders.id", "d:orders.status", CRT, "d:orders.revenue"], "where": []}
    out.append({"id": "tz/%s/model filter on raw rows" % tz, "inner": model, "source": "model", "outer": lambda c: {"aggregation": [["sum", c["revenue"]]], "filter": [">=", c["created_at"], i1]},
                "truth": "SELECT sum(amount) FROM orders WHERE created_at >= %s" % at(t1)})
    # ClickHouse types in the zone.
    X = "toTimeZone(ts_utc, '%s')" % tz
    out.append({"id": "tz/%s/DateTime in New York by day" % tz, "table": "typed_events", "agg": ["m:typed_events.count"], "by": ["d:typed_events.ts_ny@day"], "truth": "SELECT toDate(%s), count() FROM typed_events GROUP BY 1" % X})
    # A Date with the mark date_only stops on stock Cube, so these have a truth only with the dialect. The runner judges a refusal the same on both.
    out.append({"id": "tz/%s/Date by month" % tz, "table": "typed_events", "agg": ["m:typed_events.count"], "by": ["d:typed_events.day@month"], "truth": "SELECT toStartOfMonth(day), count() FROM typed_events GROUP BY 1"})
    for name, clause, cond in (("equal", ["=", "d:typed_events.day", "2025-03-09"], "day = '2025-03-09'"), ("after", [">", "d:typed_events.day", "2025-03-09"], "day > '2025-03-09'"),
                               ("before", ["<", "d:typed_events.day", "2025-03-09"], "day < '2025-03-09'"), ("between", ["between", "d:typed_events.day", "2025-03-08", "2025-03-10"], "day BETWEEN '2025-03-08' AND '2025-03-10'")):
        out.append({"id": "tz/%s/Date filter %s" % (tz, name), "table": "typed_events", "agg": ["m:typed_events.count"], "where": [clause], "truth": "SELECT count() FROM typed_events WHERE %s" % cond})
    # A stored day stays on its day, and it does not move twice. A time column makes its day in the zone.
    for unit, expr in (("day", "day"), ("week", "toStartOfWeek(day)"), ("quarter", "toStartOfQuarter(day)"), ("year", "toStartOfYear(day)")):
        out.append({"id": "tz/%s/Date by %s" % (tz, unit), "table": "typed_events", "agg": ["m:typed_events.count"], "by": ["d:typed_events.day@%s" % unit], "truth": "SELECT %s, count() FROM typed_events GROUP BY 1" % expr})
    out.append({"id": "tz/%s/Date by day with a Date filter" % tz, "table": "typed_events", "agg": ["m:typed_events.count"], "by": ["d:typed_events.day@day"], "where": [["between", "d:typed_events.day", "2025-03-08", "2025-03-10"]],
                "truth": "SELECT day, count() FROM typed_events WHERE day BETWEEN '2025-03-08' AND '2025-03-10' GROUP BY 1"})
    out.append({"id": "tz/%s/UTC column by day" % tz, "table": "typed_events", "agg": ["m:typed_events.count"], "by": ["d:typed_events.ts_utc@day"], "truth": "SELECT toDate(%s), count() FROM typed_events GROUP BY 1" % X})
    # The same column type with no mark in the model.
    out.append({"id": "tz/%s/Date32 by month" % tz, "table": "typed_events", "agg": ["m:typed_events.count"], "by": ["d:typed_events.day32@month"], "truth": "SELECT toStartOfMonth(day32), count() FROM typed_events GROUP BY 1"})
    out.append({"id": "tz/%s/Date32 filter after" % tz, "table": "typed_events", "agg": ["m:typed_events.count"], "where": [[">", "d:typed_events.day32", "2025-03-09"]], "truth": "SELECT count() FROM typed_events WHERE day32 > '2025-03-09'"})
    out.append({"id": "tz/%s/rollup revenue by month" % tz, "table": "big_orders", "agg": ["m:big_orders.revenue"], "by": ["d:big_orders.created_at@month"],
                "truth": "SELECT toStartOfMonth(toTimeZone(created_at, '%s')), sum(amount) FROM big_orders GROUP BY 1" % tz})
    # Native SQL. Two bounds read the zone, and the scan stops each other form.
    two = "created_at >= '%s' AND created_at < '%s'" % (t1, t2)
    truth_two = "created_at >= %s AND created_at < %s" % (at(t1), at(t2))
    out.append({"id": "tz/%s/native two bounds" % tz, "native": "SELECT MEASURE(count) FROM orders WHERE " + two, "truth": "SELECT count() FROM orders WHERE " + truth_two})
    out.append({"id": "tz/%s/native bucket" % tz, "native": "SELECT DATE_TRUNC('month', created_at), MEASURE(count) FROM orders GROUP BY 1", "truth": "SELECT toStartOfMonth(%s), count() FROM orders GROUP BY 1" % L})
    for name, where in (("one bound", "created_at >= '%s'" % t1), ("equal", "created_at = '%s'" % t1), ("in or", "created_at >= '%s' OR status = 'shipped'" % t1), ("not", "NOT (created_at >= '%s')" % t1),
                        ("bucket in filter", "DATE_TRUNC('month', created_at) >= '2025-07-01'")):
        out.append({"id": "tz/%s/native %s" % (tz, name), "native": "SELECT MEASURE(count) FROM orders WHERE " + where, "truth": None, "expect": "refusal"})
    out.append({"id": "tz/%s/native rolling" % tz, "native": "SELECT DATE_TRUNC('month', created_at), MEASURE(revenue_mtd) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-05-01' GROUP BY 1", "truth": None, "expect": "refusal"})
    return out


def _on_view(x):
    if isinstance(x, list):
        return [_on_view(i) for i in x]
    if isinstance(x, str) and x.startswith("d:orders."):
        return "d:sales." + x[len("d:orders."):]
    return x


def week_shapes(day, n):
    sh = "(toDayOfWeek(created_at) - %d + 7) %% 7" % n
    starts = "toDate(created_at) - toIntervalDay(%s)" % sh
    units = {"week": "toDateTime(%s)" % starts, "day-of-week": "%s + 1" % sh, "week-of-year": "ceiling(toDayOfYear(%s) / 7)" % starts}
    out = []
    for unit, expr in units.items():
        for m, sql in (("count", "count()"), ("revenue", "sum(amount)"), ("unique_customers", "uniqExact(user_id)")):
            out.append({"id": "week/%s/%s/%s" % (day, unit, m), "table": T, "agg": ["m:orders.%s" % m], "by": [CRT + "@" + unit], "truth": "SELECT %s, %s FROM orders GROUP BY 1" % (expr, sql)})
    # A leap year before, the only part of the rule that needs the length of the year: 2025 starts on a Wednesday, and 2024 has 366 days.
    out.append({"id": "week/%s/week-of-year on the first days" % day, "table": T, "agg": ["m:orders.count"], "by": [CRT + "@week-of-year"], "where": [["<", CRT, "2025-01-08"]],
                "truth": "SELECT ceiling(toDayOfYear(%s) / 7), count() FROM orders WHERE created_at < '2025-01-08' GROUP BY 1" % starts})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="Cube")
    ap.add_argument("--channel", default="dataset")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    mb = lib.Metabase().login()
    ctx = R.Ctx(mb, a.db)
    pg = None
    if a.channel == "dataset" and any(d["name"] == "PG" for d in mb.api("GET", "/api/database")["data"]):
        pg = R.Ctx(mb, "PG")
    local = threading.local()

    def job(sh):
        if a.channel == "mcp" and not getattr(local, "c", None):
            local.c = lib.Mcp(lib.Metabase().login())
        return R.one(ctx, getattr(local, "c", None), sh, a.channel)

    def chosen(items):
        return [s for s in items if not a.only or re.search(a.only, s["id"])]

    counts = {}
    with open(a.out, "w") as f, concurrent.futures.ThreadPoolExecutor(a.workers) as ex:
        def write(r):
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
            f.write(json.dumps(r, default=str) + "\n")
            f.flush()

        try:
            for tz in ZONES:
                mb.api("PUT", "/api/setting/report-timezone", {"value": tz})
                for r in ex.map(job, chosen(zone_shapes(tz))):
                    write(r)
        finally:
            mb.api("PUT", "/api/setting/report-timezone", {"value": None})
        try:
            for day, n in DAYS.items():
                mb.api("PUT", "/api/setting/start-of-week", {"value": day})
                shapes = chosen(week_shapes(day, n))
                for r in ex.map(job, shapes):
                    write(r)
                if pg:
                    # The truth of a unit must be what Metabase gives on Postgres with the same data.
                    col = pg.field("orders", "created_at")["id"]
                    for unit in ("week", "day-of-week", "week-of-year"):
                        sh = next((s for s in shapes if s["id"] == "week/%s/%s/count" % (day, unit)), None)
                        if not sh:
                            continue
                        rows = mb.run(pg.dq({"source-table": pg.tables["orders"]["id"], "aggregation": [["count"]], "breakout": [["field", col, {"temporal-unit": unit}]]}))[0]
                        diff = lib.compare(rows, lib.ch(sh["truth"]))
                        write({"id": "postgres/%s/%s" % (day, unit), "channel": "postgres", "verdict": "pass" if diff is None else "wrong", "diff": diff})
        finally:
            mb.api("PUT", "/api/setting/start-of-week", {"value": None})
    print("done", a.db, a.channel, counts, flush=True)


if __name__ == "__main__":
    main()
