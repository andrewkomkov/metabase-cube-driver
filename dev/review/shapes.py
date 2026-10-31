"""The query shapes of the last review. Each shape has a query for Metabase and a truth in direct ClickHouse SQL. The shapes were written
before a read of docs/review/0.1.0.md, so they do not repeat its cases."""
import itertools
import random

# All order columns are qualified, because the truth reads one wide join. The keys are unique, so this join has no fan-out.
BASE = "shop.orders o LEFT JOIN shop.users u ON o.user_id = u.id LEFT JOIN shop.products p ON o.product_id = p.id"
PAID = "o.status IN ('completed','shipped')"

MEASURES = {
    "count": "count()",
    "revenue": "sum(o.amount)",
    "avg_order_value": "avgOrNull(toFloat64(o.amount))",
    "min_amount": "min(o.amount)",
    "max_amount": "max(o.amount)",
    "unique_customers": "uniqExact(o.user_id)",
    "approx_customers": "uniq(o.user_id)",
    "paid_revenue": "sumOrNull(if(%s, o.amount, NULL))" % PAID,
    "paid_share": "toFloat64(sumOrNull(if(%s, o.amount, NULL))) / nullIf(toFloat64(sumOrNull(o.amount)), 0)" % PAID,
    "first_order_at": "min(o.created_at)",
    "last_order_at": "max(o.created_at)",
    "has_orders": "if(count() > 0, 'true', 'false')",
    "statuses": "arrayStringConcat(arraySort(groupUniqArray(o.status)), ',')",
}

BUCKETS = {
    "none": (None, None),
    "hour": ("hour", "toStartOfHour(o.created_at)"),
    "day": ("day", "toDate(o.created_at)"),
    "week": ("week", "toStartOfWeek(o.created_at, 0)"),
    "month": ("month", "toStartOfMonth(o.created_at)"),
    "quarter": ("quarter", "toStartOfQuarter(o.created_at)"),
    "year": ("year", "toStartOfYear(o.created_at)"),
    "minute_of_hour": ("minute-of-hour", "toMinute(o.created_at)"),
    "hour_of_day": ("hour-of-day", "toHour(o.created_at)"),
    "day_of_week": ("day-of-week", "if(toDayOfWeek(o.created_at) = 7, 1, toDayOfWeek(o.created_at) + 1)"),
    "day_of_month": ("day-of-month", "toDayOfMonth(o.created_at)"),
    "day_of_year": ("day-of-year", "toDayOfYear(o.created_at)"),
    "week_of_year": ("week-of-year", "ceiling(toDayOfYear(toStartOfWeek(o.created_at, 0)) / 7)"),
    "month_of_year": ("month-of-year", "toMonth(o.created_at)"),
    "quarter_of_year": ("quarter-of-year", "toQuarter(o.created_at)"),
}

STA, CRT, QTY, AMT = "d:orders.status", "d:orders.created_at", "d:orders.quantity_dim", "d:orders.amount_dim"
CITY = "d:orders>users.city"
CAT = "d:orders>products.category"

FILTERS = {
    "none": (None, "1"),
    "status_eq": (["=", STA, "completed"], "o.status = 'completed'"),
    "status_in": (["=", STA, "shipped", "cancelled"], "o.status IN ('shipped','cancelled')"),
    "status_ne": (["!=", STA, "completed"], "o.status != 'completed'"),
    "qty_ge": ([">=", QTY, 3], "o.quantity >= 3"),
    "qty_between": (["between", QTY, 2, 3], "o.quantity BETWEEN 2 AND 3"),
    "amount_gt_edge": ([">", AMT, 100], "o.amount > 100"),
    "amount_eq_edge": (["=", AMT, 44], "o.amount = 44"),
    "seg_paid": (["segment", "s:orders.paid"], PAID),
    "seg_large": (["segment", "s:orders.large"], "o.quantity >= 3"),
    "two_segs": (["and", ["segment", "s:orders.paid"], ["segment", "s:orders.large"]], "%s AND o.quantity >= 3" % PAID),
    "month_half_open": (["and", [">=", CRT, "2025-04-01"], ["<", CRT, "2025-05-01"]], "o.created_at >= '2025-04-01' AND o.created_at < '2025-05-01'"),
    "between_days": (["between", CRT, "2025-04-15", "2025-05-10"], "toDate(o.created_at) BETWEEN '2025-04-15' AND '2025-05-10'"),
    "between_unit_day": (["between", CRT + "@day", "2025-02-27", "2025-03-01"], "toDate(o.created_at) BETWEEN '2025-02-27' AND '2025-03-01'"),
    "after_time": ([">", CRT, "2025-06-15T11:00:00"], "o.created_at > '2025-06-15 11:00:00'"),
    "before_time": (["<", CRT, "2025-06-15T11:00:00"], "o.created_at < '2025-06-15 11:00:00'"),
    "at_or_before_time": (["<=", CRT, "2025-06-15T11:00:00"], "o.created_at <= '2025-06-15 11:00:00'"),
    "strict_both": (["and", [">", CRT, "2025-06-15T11:00:00"], ["<=", CRT, "2025-06-20T11:00:00"]], "o.created_at > '2025-06-15 11:00:00' AND o.created_at <= '2025-06-20 11:00:00'"),
    "first_second": (["=", CRT, "2025-01-01T00:00:00"], "o.created_at = '2025-01-01 00:00:00'"),
    "last_day": (["=", CRT + "@day", "2025-12-27"], "toDate(o.created_at) = '2025-12-27'"),
    "out_of_data": ([">", CRT, "2030-01-01"], "o.created_at > '2030-01-01 23:59:59'"),
    "empty_value": (["=", STA, "no such status"], "o.status = 'no such status'"),
    "null_col": (["is-null", "d:orders.cancel_status"], "o.status = 'completed'"),
    "not_null_col": (["not-null", "d:orders.cancel_status"], "o.status != 'completed'"),
    "contains": (["contains", STA, "ship"], "o.status LIKE '%ship%'"),
    "starts": (["starts-with", STA, "comp"], "o.status LIKE 'comp%'"),
    "ends": (["ends-with", STA, "ed"], "o.status LIKE '%ed'"),
    "not_contains": (["does-not-contain", STA, "ship"], "o.status NOT LIKE '%ship%'"),
    "or_two": (["or", ["=", STA, "completed"], [">=", QTY, 4]], "o.status = 'completed' OR o.quantity >= 4"),
    "not_group": (["not", ["or", ["=", STA, "completed"], [">=", QTY, 4]]], "NOT (o.status = 'completed' OR o.quantity >= 4)"),
    "bool_true": (["=", "d:orders.is_paid", True], PAID),
    "bool_false": (["=", "d:orders.is_paid", False], "NOT " + PAID),
    "case_dim": (["=", "d:orders.size", "large"], "o.quantity >= 3"),
    "join_city": (["=", CITY, "Austin"], "u.city = 'Austin'"),
    "join_cat": (["=", CAT, "Gadget"], "p.category = 'Gadget'"),
    "join_vip": (["=", "d:orders>users.is_vip", True], "u.is_vip"),
    "relative_400": (["time-interval", CRT, -400, "day"],
                     "o.created_at >= toStartOfDay(today() - 400) AND o.created_at < toStartOfDay(today())"),
    "relative_none": (["time-interval", CRT, -7, "day"],
                      "o.created_at >= toStartOfDay(today() - 7) AND o.created_at < toStartOfDay(today())"),
}


def fan(metric, bucket, flt):
    unit, expr = BUCKETS[bucket]
    clause, cond = FILTERS[flt]
    return {"table": "orders", "agg": ["m:orders.%s" % metric], "by": [CRT + "@" + unit] if unit else [], "where": [clause] if clause else [],
            "truth": "SELECT %s%s FROM %s WHERE %s%s" % (expr + ", " if expr else "", MEASURES[metric], BASE, cond, " GROUP BY 1" if expr else "")}


def grid_core():
    """Each pair of the three axes, and a third axis from a fixed random source. This is about 800 shapes, not the 8000 of the whole cube."""
    rng = random.Random(1508)
    ms, bs, fs = list(MEASURES), list(BUCKETS), list(FILTERS)
    seen, out = set(), []

    def add(m, b, f):
        if (m, b, f) in seen:
            return
        seen.add((m, b, f))
        out.append(dict(fan(m, b, f), id="core/%s/%s/%s" % (m, b, f), tol=0.03 if m == "approx_customers" else 1e-9))

    for m, b in itertools.product(ms, bs):
        add(m, b, rng.choice(fs))
    for m, f in itertools.product(ms, fs):
        add(m, rng.choice(bs), f)
    for b, f in itertools.product(bs, fs):
        add(rng.choice(ms), b, f)
    return out


# The week of year is what Metabase means on Postgres on the same data: the day of year of the start of the week, divided by 7. See finding M2.

# ---------------------------------------------------- rolling windows, time shifts and multi-stage measures

RANGE = "m >= '2025-02-01' AND m < '2025-07-01'"
WHOLE = [[">=", CRT, "2025-02-01"], ["<", CRT, "2025-07-01"]]


def _series(unit, trunc, step, cond, extra=""):
    """A row for each bucket of the range, also with no rows. e is the last second of the bucket."""
    return ("SELECT b, sum(if(%s, a, 0)) FROM (SELECT b, e FROM (SELECT DISTINCT %s AS b, addSeconds(toDateTime(%s + INTERVAL 1 %s), -1) AS e FROM (SELECT toDateTime('2025-02-01 00:00:00') + toIntervalDay(number) AS d FROM numbers(150)))) "
            "AS s CROSS JOIN (SELECT o.created_at AS t, o.amount AS a FROM shop.orders o %s) AS r GROUP BY b ORDER BY b") % (cond, trunc, trunc, step, extra)


def grid_time():
    out = []
    buckets = {"day": ("toDate(d)", "DAY"), "month": ("toStartOfMonth(d)", "MONTH"), "quarter": ("toStartOfQuarter(d)", "QUARTER")}
    windows = {
        "revenue_trailing_30d": "t >= addDays(e, -30) AND t <= e",
        "revenue_mtd": "t >= toStartOfMonth(e) AND t <= e",
        "revenue_ytd": "t >= toStartOfYear(e) AND t <= e",
    }
    filters = {"none": ([], ""), "status": ([["=", STA, "completed"]], "WHERE o.status = 'completed'"), "qty": ([[">=", QTY, 3]], "WHERE o.quantity >= 3")}
    for (m, cond), (bname, (trunc, step)), (fname, (where, extra)) in itertools.product(windows.items(), buckets.items(), filters.items()):
        if bname == "quarter":
            # A quarter does not fit the five months of the range, so this asks for two whole quarters.
            sql = _series(bname, trunc, step, cond, extra).replace("numbers(150)", "numbers(181)").replace("'2025-02-01 00:00:00'", "'2025-01-01 00:00:00'")
            w = [[">=", CRT, "2025-01-01"], ["<", CRT, "2025-07-01"]]
        else:
            sql, w = _series(bname, trunc, step, cond, extra), WHOLE
            if bname == "day":
                sql = sql.replace("numbers(150)", "numbers(150)")
        out.append({"id": "time/%s/%s/%s" % (m, bname, fname), "table": "orders", "agg": ["m:orders.%s" % m], "by": [CRT + "@" + bname], "where": w + where, "truth": sql})
    # A leading window starts at the bucket.
    for fname, (where, extra) in filters.items():
        sql = _series("month", "toStartOfMonth(d)", "MONTH", "t >= b AND t < addDays(b, 7)", extra)
        out.append({"id": "time/revenue_leading_7d/month/%s" % fname, "table": "orders", "agg": ["m:orders.revenue_leading_7d"], "by": [CRT + "@month"], "where": WHOLE + where, "truth": sql})
    # Rolling by a bucket and a second dimension.
    out.append({"id": "time/mtd_by_status", "table": "orders", "agg": ["m:orders.revenue_mtd"], "by": [CRT + "@month", STA], "where": WHOLE,
                "truth": "SELECT toStartOfMonth(o.created_at) m, o.status, sum(o.amount) FROM shop.orders o GROUP BY m, o.status HAVING %s" % RANGE})
    # Other measures next to a rolling measure, and a rolling measure in an expression.
    out.append({"id": "time/mtd_next_to_count", "table": "orders", "agg": ["m:orders.revenue_mtd", "m:orders.count"], "by": [CRT + "@month"], "where": WHOLE,
                "truth": "SELECT toStartOfMonth(o.created_at) m, sum(o.amount), count() FROM shop.orders o GROUP BY m HAVING %s" % RANGE})
    # One bucket of an unbounded window, also from the first and the last month.
    for month, nxt in (("2025-01-01", "2025-02-01"), ("2025-04-01", "2025-05-01"), ("2025-12-01", "2026-01-01")):
        out.append({"id": "time/cumulative_one/%s" % month, "table": "orders", "agg": ["m:orders.cumulative_count"], "by": [CRT + "@month"],
                    "where": [[">=", CRT, month], ["<", CRT, nxt]],
                    "truth": "SELECT toDateTime('%s'), countIf(o.created_at < '%s') FROM shop.orders o" % (month, nxt)})
    # Time shifts, by a month and a quarter, with a filter on the shifted range and a dimension.
    shifts = {"revenue_prior_month": 1, "revenue_next_month": -1}
    for m, n in shifts.items():
        out.append({"id": "time/%s/month" % m, "table": "orders", "agg": ["m:orders.%s" % m], "by": [CRT + "@month"], "where": WHOLE,
                    "truth": "SELECT toStartOfMonth(addMonths(o.created_at, %d)) m, sum(o.amount) FROM shop.orders o GROUP BY m HAVING %s" % (n, RANGE)})
        out.append({"id": "time/%s/month/status" % m, "table": "orders", "agg": ["m:orders.%s" % m], "by": [CRT + "@month", STA], "where": WHOLE,
                    "truth": "SELECT toStartOfMonth(addMonths(o.created_at, %d)) m, o.status, sum(o.amount) FROM shop.orders o GROUP BY m, o.status HAVING %s" % (n, RANGE)})
        out.append({"id": "time/%s/month/filter" % m, "table": "orders", "agg": ["m:orders.%s" % m], "by": [CRT + "@month"], "where": WHOLE + [["=", STA, "shipped"]],
                    "truth": "SELECT toStartOfMonth(addMonths(o.created_at, %d)) m, sum(o.amount) FROM shop.orders o WHERE o.status = 'shipped' GROUP BY m HAVING %s" % (n, RANGE)})
        out.append({"id": "time/%s/month/next_to_revenue" % m, "table": "orders", "agg": ["m:orders.revenue", "m:orders.%s" % m], "by": [CRT + "@month"], "where": WHOLE,
                    "truth": "SELECT a.m, a.r, b.r FROM (SELECT toStartOfMonth(o.created_at) m, sum(o.amount) r FROM shop.orders o GROUP BY m) a LEFT JOIN "
                             "(SELECT toStartOfMonth(addMonths(o.created_at, %d)) m, sum(o.amount) r FROM shop.orders o GROUP BY m) b ON a.m = b.m WHERE a.m >= '2025-02-01' AND a.m < '2025-07-01'" % n})
    # Multi-stage measures.
    size = "if(o.quantity >= 3, 'large', 'small')"
    T = "shop.orders o"
    ms = [
        ("grand_total", "revenue_grand_total", [STA], [], "SELECT o.status, (SELECT sum(amount) FROM shop.orders) FROM %s GROUP BY o.status" % T),
        ("grand_total_filter", "revenue_grand_total", [STA], [[">=", QTY, 3]], "SELECT o.status, (SELECT sum(amount) FROM shop.orders WHERE quantity >= 3) FROM %s WHERE o.quantity >= 3 GROUP BY o.status" % T),
        ("share", "revenue_share_of_total", [STA], [], "SELECT o.status, toFloat64(sum(o.amount)) / (SELECT toFloat64(sum(amount)) FROM shop.orders) FROM %s GROUP BY o.status" % T),
        ("share_month", "revenue_share_of_total", [CRT + "@month"], [], "SELECT toStartOfMonth(o.created_at), toFloat64(sum(o.amount)) / (SELECT toFloat64(sum(amount)) FROM shop.orders) FROM %s GROUP BY 1" % T),
        ("share_filter", "revenue_share_of_total", [STA], [["=", "d:orders.size", "large"]], "SELECT o.status, toFloat64(sum(o.amount)) / (SELECT toFloat64(sum(amount)) FROM shop.orders WHERE quantity >= 3) FROM %s WHERE o.quantity >= 3 GROUP BY o.status" % T),
        ("all_statuses_filter", "revenue_all_statuses", [], [["=", STA, "completed"]], "SELECT sum(o.amount) FROM %s" % T),
        ("all_statuses_two_filters", "revenue_all_statuses", [], [["=", STA, "completed"], [">=", QTY, 3]], "SELECT sum(o.amount) FROM %s WHERE o.quantity >= 3" % T),
        ("keep_status", "revenue_keep_status", [STA, "d:orders.size"], [], "SELECT o.status, %s s, r FROM %s JOIN (SELECT status, sum(amount) r FROM shop.orders GROUP BY status) x ON o.status = x.status GROUP BY o.status, s, r" % (size, T)),
        ("excl_size", "revenue_excl_size", [STA, "d:orders.size"], [], "SELECT o.status, %s s, r FROM %s JOIN (SELECT status, sum(amount) r FROM shop.orders GROUP BY status) x ON o.status = x.status GROUP BY o.status, s, r" % (size, T)),
        ("incl_size_max", "revenue_incl_size_max", [STA], [], "SELECT st, max(r) FROM (SELECT o.status st, %s s, sum(o.amount) r FROM %s GROUP BY st, s) GROUP BY st" % (size, T)),
        ("rank", "revenue_rank", [STA], [], "SELECT o.status, rank() OVER (ORDER BY sum(o.amount) DESC) FROM %s GROUP BY o.status" % T),
        ("p50", "p50_amount", [STA], [], "SELECT o.status, quantile(0.5)(toFloat64(o.amount)) FROM %s GROUP BY o.status" % T),
        ("p50_total", "p50_amount", [], [], "SELECT quantile(0.5)(toFloat64(o.amount)) FROM %s" % T),
        ("payments_per_order", "payments_per_order", [STA], [], "SELECT o.status, 2.0 FROM %s GROUP BY o.status" % T),
        ("in_currency", "revenue_in_currency", ["d:orders.display_currency"], [], "SELECT 'USD', toFloat64(sum(o.amount)) FROM %s UNION ALL SELECT 'EUR', toFloat64(sum(o.amount)) * 0.5 FROM %s" % (T, T)),
    ]
    for name, m, by, where, sql in ms:
        tol = 0.02 if m == "p50_amount" else 1e-9
        out.append({"id": "time/multi/%s" % name, "table": "orders", "agg": ["m:orders.%s" % m], "by": by, "where": where, "truth": sql, "tol": tol})
    return out


# ---------------------------------------------------- outer stages, saved questions and models

ADD = {"count": "count()", "revenue": "sum(o.amount)", "unique_customers": "uniqExact(o.user_id)", "avg_order_value": "avg(toFloat64(o.amount))",
       "paid_revenue": "sumOrNull(if(%s, o.amount, NULL))" % PAID}


def grid_stages():
    """A question of a month and a measure, under a second stage. It runs on the table, on a saved question and on a model."""
    out = []
    for m, source in itertools.product(ADD, ("inline", "question", "model")):
        inner_sql = "SELECT toStartOfMonth(o.created_at) AS b, %s AS v FROM shop.orders o GROUP BY b" % ADD[m]
        inner = {"table": "orders", "agg": ["m:orders.%s" % m], "by": [CRT + "@month"], "where": []}
        for s, (stage, sql, ordered) in {
            "sum": (lambda c: {"aggregation": [["sum", c["v"]]]}, "SELECT sum(v) FROM (%s)", False),
            "max": (lambda c: {"aggregation": [["max", c["v"]]]}, "SELECT max(v) FROM (%s)", False),
            "avg": (lambda c: {"aggregation": [["avg", c["v"]]]}, "SELECT avg(toFloat64(v)) FROM (%s)", False),
            "rows": (lambda c: {"aggregation": [["count"]]}, "SELECT count() FROM (%s)", False),
            "filter_time": (lambda c: {"filter": [">=", c["b"], "2025-07-01"]}, "SELECT b, v FROM (%s) WHERE b >= '2025-07-01'", False),
            "filter_value": (lambda c: {"filter": [">", c["v"], 0]}, "SELECT b, v FROM (%s) WHERE v > 0", False),
            "filter_time_sum": (lambda c: {"filter": ["<", c["b"], "2025-07-01"], "aggregation": [["sum", c["v"]]]}, "SELECT sum(v) FROM (%s) WHERE b < '2025-07-01'", False),
            "coarsen": (lambda c: {"aggregation": [["sum", c["v"]]], "breakout": [c["b@quarter"]]}, "SELECT toStartOfQuarter(b), sum(v) FROM (%s) GROUP BY 1", False),
            "top3": (lambda c: {"order-by": [["desc", c["v"]], ["asc", c["b"]]], "limit": 3}, "SELECT b, v FROM (%s) ORDER BY v DESC, b LIMIT 3", True),
        }.items():
            out.append({"id": "stage/%s/%s/%s" % (source, m, s), "inner": inner, "source": source, "outer": stage, "truth": sql % inner_sql, "ordered": ordered})
    # Three stages.
    q = "SELECT toStartOfMonth(created_at) b, status st, sum(amount) v FROM shop.orders GROUP BY b, st"
    out.append({"id": "stage/three/status_month", "inner": {"table": "orders", "agg": ["m:orders.revenue"], "by": [CRT + "@month", STA], "where": []}, "source": "inline",
                "outer": lambda c: {"aggregation": [["sum", c["v"]]], "breakout": [c["b"]]}, "outer2": lambda c: {"filter": [">", c["v"], 0], "aggregation": [["max", c["v"]]]},
                "truth": "SELECT max(v) FROM (SELECT b, sum(v) v FROM (%s) GROUP BY b) WHERE v > 0" % q})
    return out


def grid_models():
    """Raw rows of a model, and aggregation of a model on top of a filter inside the model."""
    out = []
    F = "o.status = 'completed'"
    inner_filter = [["=", STA, "completed"]]
    fields = ["d:orders.id", STA, CRT, QTY, AMT, "d:orders.count", "d:orders.revenue", "d:orders.unique_customers", "d:orders.avg_order_value"]
    cases = {
        "count_rows": (lambda c: {"aggregation": [["count"]]}, "SELECT count() FROM shop.orders o WHERE %s"),
        "sum_dim": (lambda c: {"aggregation": [["sum", c["quantity_dim"]]]}, "SELECT sum(o.quantity) FROM shop.orders o WHERE %s"),
        "sum_measure": (lambda c: {"aggregation": [["sum", c["revenue"]]]}, "SELECT sum(o.amount) FROM shop.orders o WHERE %s"),
        "sum_count_measure": (lambda c: {"aggregation": [["sum", c["count"]]]}, "SELECT count() FROM shop.orders o WHERE %s"),
        "by_month": (lambda c: {"aggregation": [["sum", c["revenue"]]], "breakout": [c["created_at@month"]]}, "SELECT toStartOfMonth(o.created_at), sum(o.amount) FROM shop.orders o WHERE %s GROUP BY 1"),
        "filter_outer": (lambda c: {"aggregation": [["sum", c["revenue"]]], "filter": [">=", c["quantity_dim"], 3]}, "SELECT sum(o.amount) FROM shop.orders o WHERE %s AND o.quantity >= 3"),
        "filter_time_outer": (lambda c: {"aggregation": [["sum", c["revenue"]]], "filter": ["between", c["created_at"], "2025-03-01", "2025-03-31"]}, "SELECT sum(o.amount) FROM shop.orders o WHERE %s AND toDate(o.created_at) BETWEEN '2025-03-01' AND '2025-03-31'"),
        "max_dim": (lambda c: {"aggregation": [["max", c["amount_dim"]]]}, "SELECT max(o.amount) FROM shop.orders o WHERE %s"),
        "min_time": (lambda c: {"aggregation": [["min", c["created_at"]]]}, "SELECT min(o.created_at) FROM shop.orders o WHERE %s"),
        "distinct_dim": (lambda c: {"aggregation": [["distinct", c["status"]]]}, "SELECT uniqExact(o.status) FROM shop.orders o WHERE %s"),
        "distinct_of_id": (lambda c: {"aggregation": [["distinct", c["id"]]]}, "SELECT uniqExact(o.id) FROM shop.orders o WHERE %s"),
        "avg_dim_by_status": (lambda c: {"aggregation": [["avg", c["amount_dim"]]], "breakout": [c["status"]]}, "SELECT o.status, avg(toFloat64(o.amount)) FROM shop.orders o WHERE %s GROUP BY 1"),
        "expr_ratio": (lambda c: {"aggregation": [["/", ["sum", c["revenue"]], ["sum", c["count"]]]]}, "SELECT toFloat64(sum(o.amount)) / count() FROM shop.orders o WHERE %s"),
    }
    for name, (stage, sql) in cases.items():
        for src, where, cond in (("model", inner_filter, F), ("model_nofilter", [], "1")):
            out.append({"id": "model/%s/%s" % (src, name), "inner": {"table": "orders", "fields": fields, "where": where}, "source": "model", "outer": stage, "truth": sql % cond})
    return out


# ---------------------------------------------------- joins and fan-out

J = "shop.orders o LEFT JOIN shop.users u ON o.user_id = u.id LEFT JOIN shop.products p ON o.product_id = p.id"


def grid_joins():
    def s(i, table, agg, by, where, truth, **kw):
        return dict({"id": "join/" + i, "table": table, "agg": agg, "by": by, "where": where, "truth": truth}, **kw)

    return [
        s("revenue_by_city", "orders", ["m:orders.revenue"], [CITY], [], "SELECT u.city, sum(o.amount) FROM %s GROUP BY 1" % J),
        s("revenue_by_cat", "orders", ["m:orders.revenue"], [CAT], [], "SELECT p.category, sum(o.amount) FROM %s GROUP BY 1" % J),
        s("revenue_by_city_cat", "orders", ["m:orders.revenue", "m:orders.count"], [CITY, CAT], [], "SELECT u.city, p.category, sum(o.amount), count() FROM %s GROUP BY 1, 2" % J),
        s("revenue_by_city_filter_cat", "orders", ["m:orders.revenue"], [CITY], [["=", CAT, "Gizmo"]], "SELECT u.city, sum(o.amount) FROM %s WHERE p.category = 'Gizmo' GROUP BY 1" % J),
        s("avg_by_vip", "orders", ["m:orders.avg_order_value"], ["d:orders>users.is_vip"], [], "SELECT if(u.is_vip, 'true', 'false'), avg(toFloat64(o.amount)) FROM %s GROUP BY 1" % J),
        s("unique_by_state", "orders", ["m:orders.unique_customers"], ["d:orders>users.state"], [], "SELECT u.state, uniqExact(o.user_id) FROM %s GROUP BY 1" % J),
        s("signup_month_join", "orders", ["m:orders.count"], ["d:orders>users.created_at@month"], [], "SELECT toStartOfMonth(u.created_at), count() FROM %s GROUP BY 1" % J),
        s("signup_month_filter_order_month", "orders", ["m:orders.count"], ["d:orders>users.created_at@month"], [["between", CRT, "2025-03-01", "2025-03-31"]],
          "SELECT toStartOfMonth(u.created_at), count() FROM %s WHERE toDate(o.created_at) BETWEEN '2025-03-01' AND '2025-03-31' GROUP BY 1" % J),
        s("users_measure", "users", ["m:users.count"], ["d:users.city"], [], "SELECT city, count() FROM shop.users GROUP BY 1"),
        s("users_vip_count", "users", ["m:users.vip_count"], ["d:users.state"], [], "SELECT state, countIf(is_vip) FROM shop.users GROUP BY 1"),
        s("users_segment_texans", "users", ["m:users.count"], [], [["segment", "s:users.texans"]], "SELECT count() FROM shop.users WHERE state = 'TX'"),
        s("users_segment_vip_city", "users", ["m:users.count"], ["d:users.city"], [["segment", "s:users.vip"]], "SELECT city, count() FROM shop.users WHERE is_vip GROUP BY 1"),
        s("users_cohort", "users", ["m:users.count"], ["d:users.signup_cohort"], [],
          "SELECT if(created_at < '2024-04-01', 'Q1', if(created_at < '2024-07-01', 'Q2', 'later')), count() FROM shop.users GROUP BY 1"),
        s("payments_method", "payments", ["m:payments.count", "m:payments.total"], ["d:payments.method"], [],
          "SELECT 'card', count(), sum(amount) FROM shop.orders UNION ALL SELECT 'fee', count(), toDecimal64(count(), 2) FROM shop.orders"),
        s("view_fanout_orders_count", "extended_sales", ["m:extended_sales.count"], ["d:extended_sales.status"], [], "SELECT status, count() FROM shop.orders GROUP BY 1"),
        s("view_fanout_revenue_method", "extended_sales", ["m:extended_sales.revenue"], ["d:extended_sales.payments_method"], [],
          "SELECT 'card', sum(amount) FROM shop.orders UNION ALL SELECT 'fee', sum(amount) FROM shop.orders"),
        s("view_fanout_total_status", "extended_sales", ["m:extended_sales.payments_total", "m:extended_sales.payments_count"], ["d:extended_sales.status"], [],
          "SELECT status, sum(amount) + toDecimal64(count(), 2), 2 * count() FROM shop.orders GROUP BY 1"),
        s("view_fanout_avg", "extended_sales", ["m:extended_sales.avg_order_value"], ["d:extended_sales.payments_method"], [],
          "SELECT 'card', avg(toFloat64(amount)) FROM shop.orders UNION ALL SELECT 'fee', avg(toFloat64(amount)) FROM shop.orders"),
        s("view_fanout_filter_method", "extended_sales", ["m:extended_sales.count", "m:extended_sales.revenue"], [], [["=", "d:extended_sales.payments_method", "fee"]],
          "SELECT count(), sum(amount) FROM shop.orders"),
        s("view_deep", "deep_extended_sales", ["m:deep_extended_sales.revenue"], ["d:deep_extended_sales.shipments_carrier_mode"], [],
          "SELECT ['air','ground','sea'][1 + id % 3], sum(amount) FROM shop.orders GROUP BY 1"),
        s("shipments_region", "region_sales", ["m:region_sales.revenue", "m:region_sales.count"], ["d:region_sales.destination_region"], [],
          "SELECT multiIf(id % 4 = 0, 'South', id % 4 = 1, 'Midwest', id % 4 = 2, 'West', 'East'), sum(amount), count() FROM shop.orders GROUP BY 1"),
        s("customer_region", "region_sales", ["m:region_sales.revenue"], ["d:region_sales.customer_region"], [],
          "SELECT multiIf(u.state = 'TX', 'South', u.state = 'IL', 'Midwest', u.state = 'WA', 'West', 'East'), sum(o.amount) FROM %s GROUP BY 1" % J),
        s("both_regions", "region_sales", ["m:region_sales.count"], ["d:region_sales.customer_region", "d:region_sales.destination_region"], [],
          "SELECT multiIf(u.state = 'TX', 'South', u.state = 'IL', 'Midwest', u.state = 'WA', 'West', 'East'), multiIf(o.id % 4 = 0, 'South', o.id % 4 = 1, 'Midwest', o.id % 4 = 2, 'West', 'East'), count() FROM " + J + " GROUP BY 1, 2"),
        s("region_customers_count", "region_sales", ["m:region_sales.customer_count"], ["d:region_sales.customer_region"], [],
          "SELECT multiIf(state = 'TX', 'South', state = 'IL', 'Midwest', state = 'WA', 'West', 'East'), uniqExact(id) FROM shop.users WHERE id IN (SELECT user_id FROM shop.orders) GROUP BY 1"),
        s("promo_both", "promo_sales", ["m:promo_sales.count"], ["d:promo_sales.first_promo_code", "d:promo_sales.second_promo_code"], [],
          "SELECT concat('P', toString(id % 10)), concat('P', toString((id + 1) % 10)), count() FROM shop.orders GROUP BY 1, 2"),
        s("promo_filter", "promo_sales", ["m:promo_sales.count"], [], [["=", "d:promo_sales.first_promo_code", "P3"]], "SELECT count() FROM shop.orders WHERE id % 10 = 3"),
        s("orphan_left", "orphan_orders", ["m:orphan_orders.count", "m:orphan_orders.total"], ["d:orphan_orders>orphan_customers.name"], [],
          "SELECT c.name, count(), sum(o.amount) FROM shop.orphan_orders o LEFT JOIN shop.orphan_customers c ON o.customer_id = c.id GROUP BY c.name SETTINGS join_use_nulls = 1"),
        s("orphan_null_filter", "orphan_orders", ["m:orphan_orders.count"], [], [["is-null", "d:orphan_orders>orphan_customers.name"]],
          "SELECT count() FROM shop.orphan_orders o WHERE o.customer_id NOT IN (SELECT id FROM shop.orphan_customers)"),
        s("orphan_not_null_filter", "orphan_orders", ["m:orphan_orders.total"], [], [["not-null", "d:orphan_orders>orphan_customers.name"]],
          "SELECT sum(o.amount) FROM shop.orphan_orders o WHERE o.customer_id IN (SELECT id FROM shop.orphan_customers)"),
        s("sum_price_refused", "orders", [["sum", "d:orders>products.price"]], [STA], [], None, expect="refusal"),
        s("avg_price_refused", "orders", [["avg", "d:orders>products.price"]], [], [], None, expect="refusal"),
        s("sum_price_from_products", "products", [["sum", "d:products.price"]], ["d:products.category"], [], "SELECT category, sum(price) FROM shop.products GROUP BY 1"),
        s("avg_price_measure", "products", ["m:products.avg_price"], ["d:products.category"], [], "SELECT category, avg(toFloat64(price)) FROM shop.products GROUP BY 1"),
        s("min_price_dim", "products", [["min", "d:products.price"]], [], [], "SELECT min(price) FROM shop.products"),
        s("distinct_join_city", "orders", [["distinct", CITY]], [], [], "SELECT uniqExact(u.city) FROM %s" % J),
        s("sum_qty_dim", "orders", [["sum", QTY]], [STA], [], "SELECT status, sum(quantity) FROM shop.orders GROUP BY 1"),
        s("avg_amt_dim_join_city", "orders", [["avg", AMT]], [CITY], [], "SELECT u.city, avg(toFloat64(o.amount)) FROM %s GROUP BY 1" % J),
        s("cum_sum", "orders", [["cum-sum", "d:orders.quantity_dim"]], [CRT + "@month"], [],
          "SELECT b, sum(v) OVER (ORDER BY b) FROM (SELECT toStartOfMonth(created_at) b, sum(quantity) v FROM shop.orders GROUP BY b)"),
        s("cum_count", "orders", [["cum-count"]], [CRT + "@month"], [],
          "SELECT b, sum(v) OVER (ORDER BY b) FROM (SELECT toStartOfMonth(created_at) b, count() v FROM shop.orders GROUP BY b)"),
        s("expr_measures", "orders", [["/", "m:orders.revenue", "m:orders.count"]], [STA], [], "SELECT status, toFloat64(sum(amount)) / count() FROM shop.orders GROUP BY 1"),
        s("count_if", "orders", [["count-where", ["=", STA, "shipped"]]], [CRT + "@quarter"], [], "SELECT toStartOfQuarter(created_at), countIf(status = 'shipped') FROM shop.orders GROUP BY 1"),
        s("sum_if", "orders", [["sum-where", AMT, [">=", QTY, 3]]], [], [], "SELECT sumIf(amount, quantity >= 3) FROM shop.orders"),
        s("share", "orders", [["share", ["=", STA, "completed"]]], [], [], "SELECT countIf(status = 'completed') / count() FROM shop.orders"),
        s("stddev_dim", "orders", [["stddev", AMT]], [], [], "SELECT stddevPop(toFloat64(amount)) FROM shop.orders", tol=1e-6),
        s("bins_amount", "orders", ["m:orders.count"], [AMT + "#width=100"], [], "SELECT floor(toFloat64(amount) / 100) * 100, count() FROM shop.orders GROUP BY 1"),
        s("bins_qty", "orders", ["m:orders.revenue"], [QTY + "#width=2"], [], "SELECT floor(quantity / 2) * 2, sum(amount) FROM shop.orders GROUP BY 1"),
    ]


# ---------------------------------------------------- views, inheritance and rollups

def grid_views():
    def s(i, table, agg, by, where, truth, **kw):
        return dict({"id": "view/" + i, "table": table, "agg": agg, "by": by, "where": where, "truth": truth}, **kw)

    v = "d:sales."
    M = "toStartOfMonth(o.created_at)"
    out = [
        s("sales_revenue", "sales", ["m:sales.revenue"], [], [], "SELECT sum(amount) FROM shop.orders"),
        s("sales_month", "sales", ["m:sales.revenue", "m:sales.count"], [v + "created_at@month"], [], "SELECT %s, sum(o.amount), count() FROM %s GROUP BY 1" % (M, J)),
        s("sales_city_cat", "sales", ["m:sales.revenue"], [v + "users_city", v + "products_category"], [], "SELECT u.city, p.category, sum(o.amount) FROM %s GROUP BY 1, 2" % J),
        s("sales_users_count", "sales", ["m:sales.users_count"], [v + "users_city"], [], "SELECT u.city, uniqExact(u.id) FROM %s GROUP BY 1" % J),
        s("sales_users_count_status", "sales", ["m:sales.users_count"], [v + "status"], [], "SELECT o.status, uniqExact(u.id) FROM %s GROUP BY 1" % J),
        s("sales_paid_share_city", "sales", ["m:sales.paid_share"], [v + "users_city"], [],
          "SELECT u.city, toFloat64(sumIf(o.amount, %s)) / toFloat64(sum(o.amount)) FROM %s GROUP BY 1" % (PAID, J)),
        s("sales_aov_vip_filter", "sales", ["m:sales.avg_order_value"], [v + "status"], [["=", v + "users_is_vip", True]],
          "SELECT o.status, avg(toFloat64(o.amount)) FROM %s WHERE u.is_vip GROUP BY 1" % J),
        s("sales_filter_time_city", "sales", ["m:sales.revenue"], [v + "users_city"], [["between", v + "created_at", "2025-02-28", "2025-03-01"]],
          "SELECT u.city, sum(o.amount) FROM %s WHERE toDate(o.created_at) BETWEEN '2025-02-28' AND '2025-03-01' GROUP BY 1" % J),
        s("sales_raw_top", "sales", None, None, [["=", v + "id", 77]], "SELECT o.id, o.status, o.created_at, u.city, u.state, if(u.is_vip,'true','false'), p.category, p.name FROM %s WHERE o.id = 77" % J,
          fields=[v + "id", v + "status", v + "created_at", v + "users_city", v + "users_state", v + "users_is_vip", v + "products_category", v + "products_name"]),
        s("paid_net", "paid_sales", ["m:paid_sales.net_sales", "m:paid_sales.count"], [], [], "SELECT sum(amount), count() FROM shop.orders WHERE status = 'completed'"),
        s("paid_filter_other_status", "paid_sales", ["m:paid_sales.count"], [], [["=", "d:paid_sales.status", "shipped"]], "SELECT 0"),
        s("paid_by_status", "paid_sales", ["m:paid_sales.count"], ["d:paid_sales.status"], [], "SELECT status, count() FROM shop.orders WHERE status = 'completed' GROUP BY 1"),
        s("paid_rpo", "paid_sales", ["m:paid_sales.revenue_per_order"], ["d:paid_sales.created_at@quarter"], [],
          "SELECT toStartOfQuarter(created_at), toFloat64(sum(amount)) / count() FROM shop.orders WHERE status = 'completed' GROUP BY 1"),
        s("paid_city_vip_seg", "paid_sales", ["m:paid_sales.net_sales"], ["d:paid_sales.users_city"], [["=", "d:paid_sales.users_is_vip", True]],
          "SELECT u.city, sum(o.amount) FROM %s WHERE o.status = 'completed' AND u.is_vip GROUP BY 1" % J),
        s("paid_vip_count_city", "paid_sales", ["m:paid_sales.users_vip_count"], ["d:paid_sales.users_state"], [],
          "SELECT u.state, uniqExactIf(u.id, u.is_vip) FROM %s WHERE o.status = 'completed' GROUP BY 1" % J),
        s("restricted", "restricted_sales", ["m:restricted_sales.count", "m:restricted_sales.revenue"], ["d:restricted_sales.status"], [],
          "SELECT status, count(), sum(amount) FROM shop.orders WHERE status = 'shipped' GROUP BY 1"),
        s("restricted_filter_other", "restricted_sales", ["m:restricted_sales.count"], [], [["=", "d:restricted_sales.status", "completed"]], "SELECT 0"),
        s("level_one_rev", "level_one", ["m:level_one.revenue"], ["d:level_one.status"], [], "SELECT upper(status), sum(amount * 2) FROM shop.orders GROUP BY 1"),
        s("level_one_band", "level_one", ["m:level_one.avg_quantity"], ["d:level_one.quantity_band"], [], "SELECT if(quantity >= 3, 'high', 'low'), avg(quantity) FROM shop.orders GROUP BY 1"),
        s("level_one_filter_upper", "level_one", ["m:level_one.count"], [], [["=", "d:level_one.status", "SHIPPED"]], "SELECT countIf(status = 'shipped') FROM shop.orders"),
        s("level_one_large_seg", "level_one", ["m:level_one.count"], [], [["segment", "s:level_one.large"]], "SELECT countIf(quantity >= 3) FROM shop.orders"),
        s("level_two", "level_two", ["m:level_two.revenue", "m:level_two.max_quantity"], [], [], "SELECT sum(amount * 2), max(quantity) FROM shop.orders WHERE quantity >= 3"),
        s("level_two_view", "level_two_view", ["m:level_two_view.count"], ["d:level_two_view.status"], [], "SELECT upper(status), count() FROM shop.orders WHERE quantity >= 3 GROUP BY 1"),
        s("vip_people", "vip_people", ["m:vip_people.count"], ["d:vip_people.city"], [], "SELECT city, count() FROM shop.users WHERE is_vip GROUP BY 1"),
        s("regular_people", "regular_people", ["m:regular_people.count"], [], [], "SELECT countIf(NOT is_vip) FROM shop.users"),
        s("cheap_products", "cheap_products", ["m:cheap_products.count"], ["d:cheap_products.category"], [], "SELECT category, count() FROM shop.products WHERE price < 20 GROUP BY 1"),
        s("filtered_pushed", "filtered_orders", ["m:filtered_orders.count"], [], [["=", "d:filtered_orders.status", "shipped"]], "SELECT countIf(status = 'shipped') FROM shop.orders"),
        s("filtered_pushed_in", "filtered_orders", ["m:filtered_orders.count"], ["d:filtered_orders.status"], [["=", "d:filtered_orders.status", "shipped", "cancelled"]],
          "SELECT status, count() FROM shop.orders WHERE status IN ('shipped','cancelled') GROUP BY 1"),
        s("filter_group_or", "filter_group_orders", ["m:filter_group_orders.count"], [], [["or", ["=", "d:filter_group_orders.status", "shipped"], ["=", "d:filter_group_orders.quantity", 1]]],
          "SELECT countIf(status = 'shipped' OR quantity = 1) FROM shop.orders"),
        s("filter_group_and", "filter_group_orders", ["m:filter_group_orders.count"], [], [["=", "d:filter_group_orders.status", "shipped"], ["=", "d:filter_group_orders.quantity", 1]],
          "SELECT countIf(status = 'shipped' AND quantity = 1) FROM shop.orders"),
        s("gen_completed", "gen_completed_orders", ["m:gen_completed_orders.count"], [], [], "SELECT countIf(status = 'completed') FROM shop.orders"),
        s("async_cancelled", "async_cancelled_orders", ["m:async_cancelled_orders.count"], [], [], "SELECT countIf(status = 'cancelled') FROM shop.orders"),
        s("second_source", "second_source_tickets", ["m:second_source_tickets.count", "m:second_source_tickets.total_minutes"], ["d:second_source_tickets.priority"], [],
          "SELECT priority, count(), sum(minutes) FROM shop_second.tickets GROUP BY 1"),
        s("explicit_source", "explicit_source", ["m:explicit_source.count"], ["d:explicit_source.category"], [], "SELECT category, count() FROM shop.products GROUP BY 1"),
        s("shipments_mode", "shipments", ["m:shipments.count"], ["d:shipments.carrier_mode"], [], "SELECT ['air','ground','sea'][1 + id % 3], count() FROM shop.orders GROUP BY 1"),
        s("lambda_in_batch", "lambda_sales", ["m:lambda_sales.revenue"], ["d:lambda_sales.status"], [[">=", "d:lambda_sales.created_at", "2025-02-01"], ["<", "d:lambda_sales.created_at", "2025-03-01"]],
          "SELECT status, sum(amount) FROM shop.orders WHERE created_at >= '2025-02-01' AND created_at < '2025-03-01' GROUP BY 1"),
        s("join_sales_state", "join_sales", ["m:join_sales.revenue"], ["d:join_sales>join_customers.state"], [], "SELECT u.state, sum(o.amount) FROM %s GROUP BY 1" % J),
        s("join_sales_status_state", "join_sales", ["m:join_sales.revenue"], ["d:join_sales.status", "d:join_sales>join_customers.state"], [["=", "d:join_sales.status", "shipped"]],
          "SELECT o.status, u.state, sum(o.amount) FROM %s WHERE o.status = 'shipped' GROUP BY 1, 2" % J),
    ]
    B = "shop.big_orders"
    C = "d:big_orders.created_at"
    bigm = {"count": "count()", "revenue": "sum(amount)", "avg_amount": "avg(toFloat64(amount))", "min_amount": "min(amount)", "max_amount": "max(amount)", "unique_users": "uniqExact(user_id)"}
    # A rollup of days with partitions by year. Bounds on a partition, inside a day, and on the last row.
    bounds = {
        "year_edge": ([[">=", C, "2024-12-31T23:59:00"], ["<", C, "2025-01-01T00:01:00"]], "created_at >= '2024-12-31 23:59:00' AND created_at < '2025-01-01 00:01:00'"),
        "whole_day": ([["between", C, "2024-02-29", "2024-02-29"]], "toDate(created_at) = '2024-02-29'"),
        "inside_day": ([[">=", C, "2024-03-01T06:00:00"], ["<", C, "2024-03-01T18:00:00"]], "created_at >= '2024-03-01 06:00:00' AND created_at < '2024-03-01 18:00:00'"),
        "strict": ([[">", C, "2024-03-01T00:00:00"], ["<=", C, "2024-03-01T00:01:00"]], "created_at > '2024-03-01 00:00:00' AND created_at <= '2024-03-01 00:01:00'"),
        "status_month": ([["=", "d:big_orders.status", "shipped"], ["between", C, "2024-06-01", "2024-06-30"]], "status = 'shipped' AND toDate(created_at) BETWEEN '2024-06-01' AND '2024-06-30'"),
    }
    for (bn, (w, cond)), (m, sql) in itertools.product(bounds.items(), bigm.items()):
        out.append(s("big/%s/%s" % (bn, m), "big_orders", ["m:big_orders.%s" % m], [], w, "SELECT %s FROM %s WHERE %s" % (sql, B, cond)))
    # The same shapes by day. A rollup of days must not move a row over midnight.
    out.append(s("big/by_day_month_inside", "big_orders", ["m:big_orders.revenue"], [C + "@day"], [[">=", C, "2024-03-01T06:00:00"], ["<", C, "2024-03-03T06:00:00"]],
                 "SELECT toDate(created_at), sum(amount) FROM %s WHERE created_at >= '2024-03-01 06:00:00' AND created_at < '2024-03-03 06:00:00' GROUP BY 1" % B))
    out.append(s("big/by_quarter", "big_orders", ["m:big_orders.revenue", "m:big_orders.count"], [C + "@quarter"], [], "SELECT toStartOfQuarter(created_at), sum(amount), count() FROM %s GROUP BY 1" % B))
    out.append(s("big/raw_status", "big_orders_raw", ["m:big_orders_raw.unique_users"], ["d:big_orders_raw.status"], [], "SELECT status, uniqExact(user_id) FROM %s GROUP BY 1" % B))
    # A post-processing shape above the default limit of Cube.
    out.append(s("big/distinct_user", "big_orders", [["distinct", "d:big_orders.user_id"]], [], [], "SELECT uniqExact(user_id) FROM %s" % B))
    out.append(s("big/sum_qty_dim", "big_orders_raw", [["sum", "d:big_orders_raw.quantity_dim"]], [], [], "SELECT sum(quantity) FROM %s" % B))
    return out


# ---------------------------------------------------- boundary values and ClickHouse types

def grid_boundary():
    def s(i, agg, by, where, truth, **kw):
        return dict({"id": "edge/" + i, "table": "typed_events", "agg": agg, "by": by, "where": where, "truth": truth}, **kw)

    d = "d:typed_events."
    T = "shop.typed_events"
    c = "m:typed_events.count"
    out = [
        s("sum_dec", ["m:typed_events.sum_dec"], [], [], "SELECT sum(dec_amount) FROM %s" % T),
        s("sum_dec_lc", ["m:typed_events.sum_dec"], [d + "lc"], [], "SELECT lc, sum(dec_amount) FROM %s GROUP BY 1" % T),
        s("sum_n_dec", ["m:typed_events.sum_n_dec"], [], [], "SELECT sum(n_dec) FROM %s" % T),
        s("sum_n_dec_all_null", ["m:typed_events.sum_n_dec"], [], [["is-null", d + "n_dec"]], "SELECT sumOrNull(n_dec) FROM %s WHERE n_dec IS NULL" % T),
        s("avg_n_int", ["m:typed_events.avg_n_int"], [], [], "SELECT avg(n_int) FROM %s" % T),
        s("distinct_lc", ["m:typed_events.distinct_lc"], [], [], "SELECT uniqExact(lc) FROM %s" % T),
        s("distinct_n_str", ["m:typed_events.distinct_n_str"], [], [], "SELECT uniqExact(n_str) FROM %s" % T),
        s("tags_total", ["m:typed_events.tags_total"], [d + "has_t1"], [], "SELECT if(has(tags, 't1'), 'true', 'false'), sum(length(tags)) FROM %s GROUP BY 1" % T),
        s("eq_null_str", [c], [], [["=", d + "n_str", "s1"]], "SELECT countIf(n_str = 's1') FROM %s" % T),
        s("ne_null_str", [c], [], [["!=", d + "n_str", "s1"]], "SELECT count() FROM %s WHERE n_str != 's1' OR n_str IS NULL" % T, note="Metabase reads != with NULL as true"),
        s("null_str", [c], [], [["is-null", d + "n_str"]], "SELECT countIf(n_str IS NULL) FROM %s" % T),
        s("not_null_str", [c], [], [["not-null", d + "n_str"]], "SELECT countIf(n_str IS NOT NULL) FROM %s" % T),
        s("empty_str", [c], [], [["is-empty", d + "n_str"]], "SELECT countIf(n_str IS NULL OR n_str = '') FROM %s" % T),
        s("not_empty_str", [c], [], [["not-empty", d + "n_str"]], "SELECT countIf(n_str IS NOT NULL AND n_str != '') FROM %s" % T),
        s("group_null_str", [c], [d + "n_str"], [], "SELECT n_str, count() FROM %s GROUP BY 1" % T),
        s("group_null_int", ["m:typed_events.sum_dec"], [d + "n_int"], [], "SELECT n_int, sum(dec_amount) FROM %s GROUP BY 1" % T),
        s("gt_null_int", [c], [], [[">", d + "n_int", 5]], "SELECT countIf(n_int > 5) FROM %s" % T),
        s("le_null_int", [c], [], [["<=", d + "n_int", 0]], "SELECT countIf(n_int <= 0) FROM %s" % T),
        s("eq_zero", [c], [], [["=", d + "n_int", 0]], "SELECT countIf(n_int = 0) FROM %s" % T),
        s("not_in_null", [c], [], [["!=", d + "n_int", 1, 2]], "SELECT count() FROM %s WHERE n_int NOT IN (1, 2) OR n_int IS NULL" % T, note="Metabase reads != with NULL as true"),
        s("bool_true", [c], [], [["=", d + "n_bool", True]], "SELECT countIf(n_bool = true) FROM %s" % T),
        s("bool_false", [c], [], [["=", d + "n_bool", False]], "SELECT countIf(n_bool = false) FROM %s" % T),
        s("bool_null", [c], [], [["is-null", d + "n_bool"]], "SELECT countIf(n_bool IS NULL) FROM %s" % T),
        s("bool_group", [c], [d + "n_bool"], [], "SELECT if(n_bool IS NULL, NULL, if(n_bool, 'true', 'false')), count() FROM %s GROUP BY 1" % T),
        s("enum_eq", [c], [], [["=", d + "en", "green"]], "SELECT countIf(en = 'green') FROM %s" % T),
        s("enum_group", ["m:typed_events.sum_dec"], [d + "en"], [], "SELECT toString(en), sum(dec_amount) FROM %s GROUP BY 1" % T),
        s("lc_n_group", [c], [d + "lc_n"], [], "SELECT lc_n, count() FROM %s GROUP BY 1" % T),
        s("lc_n_null", [c], [], [["is-null", d + "lc_n"]], "SELECT countIf(lc_n IS NULL) FROM %s" % T),
        s("uuid_eq", [c], [], [["=", d + "uid", "00000000-0000-0000-0000-000000000007"]], "SELECT 1"),
        s("dec_eq_edge", [c], [], [["=", d + "dec_amount", 0.125]], "SELECT countIf(dec_amount = 0.125) FROM %s" % T),
        s("dec_between_edge", [c], [], [["between", d + "dec_amount", 0.125, 0.375]], "SELECT countIf(dec_amount BETWEEN 0.125 AND 0.375) FROM %s" % T),
        s("float_eq_edge", [c], [], [["=", d + "f32", 0.25]], "SELECT countIf(f32 = 0.25) FROM %s" % T),
        s("float_tenth", [c], [], [[">", d + "dec_amount", 0.1]], "SELECT countIf(dec_amount > 0.1) FROM %s" % T),
        s("negative_bound", [c], [], [[">", d + "n_int", -1]], "SELECT countIf(n_int > -1) FROM %s" % T),
        s("huge_bound", [c], [], [["<", d + "id", 9007199254740993]], "SELECT count() FROM %s" % T),
        s("id_edge_low", [c], [], [["<=", d + "id", 1]], "SELECT 1"),
        s("id_edge_high", [c], [], [[">=", d + "id", 1000]], "SELECT 1"),
        s("array_len", [["sum", d + "nums_len"]], [d + "lc"], [], "SELECT lc, sum(length(nums)) FROM %s GROUP BY 1" % T),
        s("date_eq", [c], [], [["=", d + "day", "2025-03-09"]], "SELECT countIf(day = '2025-03-09') FROM %s" % T),
        s("date_gt", [c], [], [[">", d + "day", "2025-03-09"]], "SELECT countIf(day > '2025-03-09') FROM %s" % T),
        s("date_lt", [c], [], [["<", d + "day", "2025-03-09"]], "SELECT countIf(day < '2025-03-09') FROM %s" % T),
        s("date_between", [c], [], [["between", d + "day", "2025-03-08", "2025-03-10"]], "SELECT countIf(day BETWEEN '2025-03-08' AND '2025-03-10') FROM %s" % T),
        s("date32_eq", [c], [], [["=", d + "day32", "2025-03-09"]], "SELECT countIf(day32 = '2025-03-09') FROM %s" % T),
        s("date32_month", [c], [d + "day32@month"], [], "SELECT toStartOfMonth(day32), count() FROM %s GROUP BY 1" % T),
        s("date_week", [c], [d + "day@week"], [], "SELECT toStartOfWeek(day, 0), count() FROM %s GROUP BY 1" % T),
        s("ny_day", [c], [d + "ts_ny@day"], [], "SELECT toDate(toTimeZone(ts_ny, 'UTC')), count() FROM %s GROUP BY 1" % T),
        s("ny_month", [c], [d + "ts_ny@month"], [], "SELECT toStartOfMonth(toTimeZone(ts_ny, 'UTC')), count() FROM %s GROUP BY 1" % T),
        s("ny_hour_of_day", [c], [d + "ts_ny@hour-of-day"], [], "SELECT toHour(toTimeZone(ts_ny, 'UTC')), count() FROM %s GROUP BY 1" % T),
        s("ny_dst_gap", [c], [], [[">=", d + "ts_ny", "2025-03-09T06:00:00"], ["<", d + "ts_ny", "2025-03-09T08:00:00"]],
          "SELECT countIf(ts_utc >= '2025-03-09 06:00:00' AND ts_utc < '2025-03-09 08:00:00') FROM %s" % T),
        s("berlin_day", [c], [d + "ts64_berlin@day"], [], "SELECT toDate(toTimeZone(ts64_berlin, 'UTC')), count() FROM %s GROUP BY 1" % T),
        s("berlin_lt_whole", [c], [], [["<", d + "ts64_berlin", "2025-03-01T02:00:00"]], "SELECT countIf(ts64_berlin < toDateTime64('2025-03-01 02:00:00', 3, 'UTC')) FROM %s" % T),
        s("berlin_le_whole", [c], [], [["<=", d + "ts64_berlin", "2025-03-01T02:00:00"]], "SELECT countIf(ts64_berlin <= toDateTime64('2025-03-01 02:00:00', 3, 'UTC')) FROM %s" % T),
        s("berlin_half_open", [c], [], [[">=", d + "ts64_berlin", "2025-03-01T02:00:00"], ["<", d + "ts64_berlin", "2025-03-01T03:00:00"]],
          "SELECT countIf(ts64_berlin >= toDateTime64('2025-03-01 02:00:00', 3, 'UTC') AND ts64_berlin < toDateTime64('2025-03-01 03:00:00', 3, 'UTC')) FROM %s" % T),
        s("berlin_gt", [c], [], [[">", d + "ts64_berlin", "2025-03-01T02:00:00"], ["<=", d + "ts64_berlin", "2025-03-01T04:00:00"]],
          "SELECT countIf(ts64_berlin > toDateTime64('2025-03-01 02:00:00', 3, 'UTC') AND ts64_berlin <= toDateTime64('2025-03-01 04:00:00', 3, 'UTC')) FROM %s" % T),
        s("berlin_fraction", [c], [], [["<", d + "ts64_berlin", "2025-03-01T02:00:00.500"]], "SELECT countIf(ts64_berlin < toDateTime64('2025-03-01 02:00:00.500', 3, 'UTC')) FROM %s" % T),
        s("berlin_not_gt", [c], [], [["not", [">", d + "ts64_berlin", "2025-03-01T02:00:00"]]], "SELECT countIf(NOT ts64_berlin > toDateTime64('2025-03-01 02:00:00', 3, 'UTC')) FROM %s" % T),
        s("n_ts_group", [c], [d + "n_ts@month"], [], "SELECT if(n_ts IS NULL, NULL, toStartOfMonth(n_ts)), count() FROM %s GROUP BY 1" % T),
        s("n_ts_null", [c], [], [["is-null", d + "n_ts"]], "SELECT countIf(n_ts IS NULL) FROM %s" % T),
        s("n_ts_gt", [c], [], [[">", d + "n_ts", "2025-03-15T00:00:00"]], "SELECT countIf(n_ts > '2025-03-15 00:00:00') FROM %s" % T),
        s("raw_decimal_row", None, None, [["=", d + "id", 8]], "SELECT id, dec_amount, f32, n_int, n_str, lc FROM %s WHERE id = 8" % T,
          fields=[d + "id", d + "dec_amount", d + "f32", d + "n_int", d + "n_str", d + "lc"]),
        s("raw_times", None, None, [["=", d + "id", 7]], "SELECT id, toTimeZone(ts_ny, 'UTC'), day, day32 FROM %s WHERE id = 7" % T, fields=[d + "id", d + "ts_ny", d + "day", d + "day32"]),
        s("raw_ts64", None, None, [["=", d + "id", 7]], "SELECT id, toTimeZone(ts64_berlin, 'UTC') FROM %s WHERE id = 7" % T, fields=[d + "id", d + "ts64_berlin"], expect="stock_whole_seconds"),
        s("raw_ui64_big", None, None, [["=", d + "id", 2]], None, fields=[d + "id", d + "ui64"], expect="refusal"),
        s("raw_ui64_text", None, None, [["=", d + "id", 2]], "SELECT id, toString(ui64) FROM %s WHERE id = 2" % T, fields=[d + "id", d + "ui64_text"]),
        s("sum_ui64_big", ["m:typed_events.sum_ui64"], [], [], None, expect="refusal"),
        s("raw_dec_big", None, None, [["=", d + "id", 1]], "SELECT id, toString(dec_big) FROM %s WHERE id = 1" % T, fields=[d + "id", d + "dec_big"], known="README: a long decimal below 2^53"),
        s("sum_big", ["m:typed_events.sum_big"], [], [], None, expect="refusal"),
    ]
    # Strings with characters that must stay literal.
    O = "shop.orders"
    S = STA
    for i, v in enumerate(["O'Brien", "a\\b", "100%", "a_b", "", " ", "Ж", "'; DROP TABLE orders; --", "a\"b", "completed "]):
        esc = v.replace("\\", "\\\\").replace("'", "\\'")
        out.append({"id": "edge/str_eq_%d" % i, "table": "orders", "agg": ["m:orders.count"], "by": [], "where": [["=", S, v]], "truth": "SELECT countIf(status = '%s') FROM %s" % (esc, O)})
    # LIKE characters in a string filter. "%" and "_" are data for Metabase, so "c_mpleted" matches no row, and "comp%" matches no row.
    for i, (op, v, n) in enumerate([("contains", "_", 0), ("contains", "%", 0), ("starts-with", "c_mp", 0), ("contains", "COMP", 0), ("ends-with", "ED", 0)]):
        out.append({"id": "edge/like_%d" % i, "table": "orders", "agg": ["m:orders.count"], "by": [], "where": [[op, S, v]], "truth": "SELECT %d" % n})
    out.append({"id": "edge/contains_ignore_case", "table": "orders", "agg": ["m:orders.count"], "by": [], "where": [["contains", S, "COMP", {"case-sensitive": False}]], "truth": "SELECT countIf(status = 'completed') FROM %s" % O})
    # Limits and orders.
    out += [
        {"id": "edge/order_limit", "table": "orders", "agg": ["m:orders.revenue"], "by": [STA], "where": [], "order": [["desc", ["aggregation", 0]]], "limit": 2, "ordered": True,
         "truth": "SELECT status, sum(amount) r FROM %s GROUP BY 1 ORDER BY r DESC LIMIT 2" % O},
        {"id": "edge/order_dim_limit", "table": "orders", "agg": ["m:orders.count"], "by": [CRT + "@month"], "where": [], "order": [["desc", CRT + "@month"]], "limit": 1, "ordered": True,
         "truth": "SELECT toStartOfMonth(created_at) m, count() FROM %s GROUP BY 1 ORDER BY m DESC LIMIT 1" % O},
        {"id": "edge/limit_one_total", "table": "orders", "agg": ["m:orders.count"], "by": [], "where": [], "limit": 1, "truth": "SELECT count() FROM %s" % O},
        {"id": "edge/raw_limit", "table": "orders", "fields": ["d:orders.id"], "where": [[">", "d:orders.id", 4990]], "order": [["asc", "d:orders.id"]], "limit": 3, "ordered": True,
         "truth": "SELECT id FROM %s WHERE id > 4990 ORDER BY id LIMIT 3" % O},
        {"id": "edge/raw_1999_rows", "table": "orders", "fields": ["d:orders.id", "d:orders.amount_dim"], "where": [], "order": [["asc", "d:orders.id"]], "limit": 1999, "ordered": True,
         "truth": "SELECT id, amount FROM %s ORDER BY id LIMIT 1999" % O},
        {"id": "edge/custom_granularity_half_day", "table": "orders", "agg": ["m:orders.count"], "by": ["d:orders.created_half_day@default"], "where": [[">=", CRT, "2025-03-01"], ["<", CRT, "2025-03-03"]],
         "truth": "SELECT toStartOfInterval(created_at, INTERVAL 12 HOUR), count() FROM %s WHERE created_at >= '2025-03-01' AND created_at < '2025-03-03' GROUP BY 1" % O},
        {"id": "edge/custom_week_sunday", "table": "orders", "agg": ["m:orders.revenue"], "by": ["d:orders.created_week_sunday"], "where": [[">=", CRT, "2025-03-02"], ["<", CRT, "2025-03-16"]],
         "truth": "SELECT toStartOfWeek(created_at, 0), sum(amount) FROM %s WHERE created_at >= '2025-03-02' AND created_at < '2025-03-16' GROUP BY 1" % O},
        {"id": "edge/custom_fiscal_quarter", "table": "orders", "agg": ["m:orders.count"], "by": ["d:orders.created_fiscal_quarter"], "where": [],
         "truth": "SELECT addMonths(toStartOfQuarter(addMonths(created_at, -1)), 1), count() FROM %s GROUP BY 1" % O},
        {"id": "edge/custom_fiscal_year", "table": "orders", "agg": ["m:orders.count"], "by": ["d:orders.created_fiscal_year"], "where": [],
         "truth": "SELECT addMonths(toStartOfYear(addMonths(created_at, -3)), 3), count() FROM %s GROUP BY 1" % O},
        {"id": "edge/proxy_month", "table": "orders", "agg": ["m:orders.count"], "by": ["d:orders.created_month_proxy"], "where": [],
         "truth": "SELECT toStartOfMonth(created_at), count() FROM %s GROUP BY 1" % O},
    ]
    return out


# ---------------------------------------------------- native SQL

def grid_native():
    O = "shop.orders"

    def n(i, sql, truth, **kw):
        return dict({"id": "native/" + i, "native": sql, "truth": truth}, **kw)

    return [
        n("measure", "SELECT status, MEASURE(revenue) FROM orders GROUP BY 1", "SELECT status, sum(amount) FROM %s GROUP BY 1" % O),
        n("month", "SELECT DATE_TRUNC('month', created_at), MEASURE(count) FROM orders GROUP BY 1", "SELECT toStartOfMonth(created_at), count() FROM %s GROUP BY 1" % O),
        n("half_open", "SELECT MEASURE(count) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-04-01'", "SELECT countIf(created_at >= '2025-03-01' AND created_at < '2025-04-01') FROM %s" % O),
        n("alias_scan", "SELECT MEASURE(count) c FROM orders o WHERE o.created_at >= TIMESTAMP '2025-03-01 00:00:00' AND o.created_at < TIMESTAMP '2025-04-01 00:00:00'", "SELECT countIf(created_at >= '2025-03-01' AND created_at < '2025-04-01') FROM %s" % O),
        n("strict_both", "SELECT MEASURE(count) FROM orders WHERE created_at > '2025-03-01 00:00:00' AND created_at <= '2025-03-02 00:00:00'", "SELECT countIf(created_at > '2025-03-01 00:00:00' AND created_at <= '2025-03-02 00:00:00') FROM %s" % O, soft=True),
        n("strict_both_cast", "SELECT MEASURE(count) FROM orders WHERE CAST(created_at AS timestamp) > CAST('2025-03-01 00:00:00' AS timestamp) AND created_at <= '2025-03-02 00:00:00'", "SELECT countIf(created_at > '2025-03-01 00:00:00' AND created_at <= '2025-03-02 00:00:00') FROM %s" % O, soft=True),
        n("strict_both_reversed", "SELECT MEASURE(count) FROM orders WHERE '2025-03-01 00:00:00' < created_at AND '2025-03-02 00:00:00' >= created_at", "SELECT countIf(created_at > '2025-03-01 00:00:00' AND created_at <= '2025-03-02 00:00:00') FROM %s" % O, soft=True),
        n("between_date_end", "SELECT MEASURE(count) FROM orders WHERE created_at BETWEEN '2025-03-01' AND '2025-03-02'", "SELECT countIf(created_at BETWEEN '2025-03-01 00:00:00' AND '2025-03-02 00:00:00') FROM %s" % O, soft=True),
        n("between_cast_end", "SELECT MEASURE(count) FROM orders WHERE created_at BETWEEN CAST('2025-03-01' AS date) AND CAST('2025-03-02' AS date)", "SELECT countIf(created_at BETWEEN '2025-03-01 00:00:00' AND '2025-03-02 00:00:00') FROM %s" % O, soft=True),
        n("subquery_outer_filter", "SELECT * FROM (SELECT status, MEASURE(revenue) r FROM orders GROUP BY 1) t WHERE status = 'shipped'", "SELECT status, sum(amount) FROM %s WHERE status = 'shipped' GROUP BY 1" % O, soft=True),
        n("cte_outer_filter_time", "WITH m AS (SELECT DATE_TRUNC('month', created_at) b, MEASURE(revenue) r FROM orders GROUP BY 1) SELECT SUM(r) FROM m WHERE b >= '2025-07-01'", "SELECT sum(amount) FROM %s WHERE toStartOfMonth(created_at) >= '2025-07-01'" % O, soft=True),
        n("cte_share", "WITH m AS (SELECT status, MEASURE(revenue) r FROM orders GROUP BY 1 LIMIT 100) SELECT status, r / (SELECT SUM(r) FROM m) FROM m WHERE status = 'shipped'",
          "SELECT status, toFloat64(sum(amount)) / (SELECT toFloat64(sum(amount)) FROM %s) FROM %s WHERE status = 'shipped' GROUP BY 1" % (O, O), soft=True),
        n("count_star_empty", "SELECT COUNT(*) FROM orders WHERE status = 'nope'", "SELECT 0", known="README: native COUNT(*) on no rows"),
        n("sum_empty", "SELECT MEASURE(revenue) FROM orders WHERE status = 'nope'", "SELECT sumOrNull(amount) FROM %s WHERE status = 'nope'" % O),
        n("having", "SELECT status, MEASURE(count) c FROM orders GROUP BY 1 HAVING MEASURE(count) > 1500", "SELECT status, count() c FROM %s GROUP BY 1 HAVING c > 1500" % O),
        n("order_limit", "SELECT status, MEASURE(revenue) r FROM orders GROUP BY 1 ORDER BY 2 DESC LIMIT 1", "SELECT status, sum(amount) r FROM %s GROUP BY 1 ORDER BY r DESC LIMIT 1" % O, ordered=True),
        n("rolling_no_trunc", "SELECT MEASURE(revenue_mtd) FROM orders", None, expect="refusal"),
        n("rolling_mid_bucket", "SELECT DATE_TRUNC('month', created_at), MEASURE(revenue_mtd) FROM orders WHERE created_at >= '2025-03-15' AND created_at < '2025-05-01' GROUP BY 1", None, expect="refusal"),
        n("rolling_comment_trunc", "SELECT /* DATE_TRUNC('month', created_at) */ MEASURE(revenue_mtd) FROM orders", None, expect="refusal"),
        n("rolling_string_trunc", "SELECT 'DATE_TRUNC(month, created_at)' x, MEASURE(revenue_mtd) FROM orders GROUP BY 1", None, expect="refusal"),
        n("rolling_aliased_bucket", "SELECT b, MEASURE(revenue_mtd) FROM (SELECT DATE_TRUNC('month', created_at) b, revenue_mtd FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-05-01') t GROUP BY 1",
          "SELECT toStartOfMonth(created_at), sum(amount) FROM %s WHERE created_at >= '2025-03-01' AND created_at < '2025-05-01' GROUP BY 1" % O, soft=True),
        n("rolling_ok", "SELECT DATE_TRUNC('month', created_at), MEASURE(revenue_mtd) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-05-01' GROUP BY 1",
          "SELECT toStartOfMonth(created_at), sum(amount) FROM %s WHERE created_at >= '2025-03-01' AND created_at < '2025-05-01' GROUP BY 1" % O),
        n("rolling_between_mid", "SELECT DATE_TRUNC('month', created_at), MEASURE(revenue_mtd) FROM orders WHERE created_at BETWEEN TIMESTAMP '2025-03-15 00:00:00' AND TIMESTAMP '2025-03-31 23:59:59' GROUP BY 1", None, expect="refusal"),
        n("rolling_day_trunc_in", "SELECT DATE_TRUNC('month', created_at), MEASURE(revenue_mtd) FROM orders WHERE DATE_TRUNC('day', created_at) >= '2025-03-15' AND created_at < '2025-05-01' GROUP BY 1", None, expect="refusal"),
        n("rolling_hidden_upper_case", "select date_trunc('month', CREATED_AT), measure(revenue_mtd) from orders where CREATED_AT >= '2025-03-15' and CREATED_AT < '2025-05-01' group by 1", None, expect="refusal"),
        n("switch_star", "SELECT status, display_currency FROM orders WHERE id = 1", "SELECT status, NULL FROM %s WHERE id = 1" % O, known="README: raw select of a switch dimension"),
        n("select_star_rows", "SELECT id FROM orders WHERE id = 1", "SELECT 1"),
        n("star", "SELECT * FROM orders WHERE id = 1 LIMIT 100", None, kind="row_count", want_rows=1, known="README: native SELECT *"),
        n("sum_price_join", "SELECT o.status, SUM(p.price) FROM orders o JOIN products p ON o.__cubeJoinField = p.__cubeJoinField GROUP BY 1",
          "SELECT o.status, sum(p.price) FROM %s o JOIN shop.products p ON o.product_id = p.id GROUP BY 1" % O, known="docs: native sum of a joined dimension"),
        n("cross_join", "SELECT MEASURE(o.revenue), u.city FROM orders o CROSS JOIN users u GROUP BY 2", "SELECT sum(o.amount), u.city FROM %s GROUP BY 2" % J),
        n("sum_decimal_dim", "SELECT SUM(amount_dim) FROM orders", "SELECT sum(amount) FROM %s" % O),
        n("avg_measure_col", "SELECT AVG(revenue) FROM orders", None, soft=True),
        n("sub_query_equal", "SELECT lifetime_orders, lifetime_paid_orders, MEASURE(count) FROM users WHERE city = 'Austin' GROUP BY 1, 2 ORDER BY 1 LIMIT 1", None, soft=True),
        n("param_text", "SELECT MEASURE(count) FROM orders WHERE status = {{s}}", "SELECT countIf(status = 'shipped') FROM %s" % O,
          tags={"s": {"id": "a", "name": "s", "display-name": "s", "type": "text"}}, parameters=[{"type": "category", "target": ["variable", ["template-tag", "s"]], "value": "shipped"}]),
        n("param_date", "SELECT MEASURE(count) FROM orders WHERE created_at >= {{d}} AND created_at < '2025-04-01'", "SELECT countIf(created_at >= '2025-03-15' AND created_at < '2025-04-01') FROM %s" % O,
          tags={"d": {"id": "b", "name": "d", "display-name": "d", "type": "date"}}, parameters=[{"type": "date/single", "target": ["variable", ["template-tag", "d"]], "value": "2025-03-15"}]),
        n("param_number", "SELECT MEASURE(count) FROM orders WHERE quantity_dim >= {{q}}", "SELECT countIf(quantity >= 3) FROM %s" % O,
          tags={"q": {"id": "c", "name": "q", "display-name": "q", "type": "number"}}, parameters=[{"type": "number/=", "target": ["variable", ["template-tag", "q"]], "value": "3"}]),
        n("hidden_cube_refused", "SELECT MEASURE(count) FROM secret_audit", "SELECT count() FROM %s" % O, known="docs: hidden members in dev mode"),
        n("timezone_set", "SELECT DATE_TRUNC('day', created_at), MEASURE(count) FROM orders WHERE id = 1 GROUP BY 1", "SELECT toDate(created_at), count() FROM %s WHERE id = 1 GROUP BY 1" % O),
        n("union", "SELECT MEASURE(count) FROM orders WHERE status = 'shipped' UNION ALL SELECT MEASURE(count) FROM orders WHERE status = 'cancelled'",
          "SELECT countIf(status = 'shipped') FROM %s UNION ALL SELECT countIf(status = 'cancelled') FROM %s" % (O, O)),
        n("case_expr", "SELECT CASE WHEN status = 'shipped' THEN 'S' ELSE 'O' END x, MEASURE(count) FROM orders GROUP BY 1", "SELECT if(status = 'shipped', 'S', 'O'), count() FROM %s GROUP BY 1" % O),
        n("distinct", "SELECT DISTINCT status FROM orders", "SELECT DISTINCT status FROM %s" % O),
        n("count_distinct_dim", "SELECT COUNT(DISTINCT user_id) FROM orders", "SELECT uniqExact(user_id) FROM %s" % O),
        n("window_fn", "SELECT status, MEASURE(revenue) r, RANK() OVER (ORDER BY MEASURE(revenue) DESC) FROM orders GROUP BY 1", "SELECT status, sum(amount) r, rank() OVER (ORDER BY sum(amount) DESC) FROM %s GROUP BY 1" % O, soft=True),
        n("limit_in_cte_filter", "WITH m AS (SELECT status, MEASURE(revenue) r FROM orders GROUP BY 1 LIMIT 100) SELECT * FROM m WHERE r > 0", "SELECT status, sum(amount) r FROM %s GROUP BY 1" % O),
        n("double_quoted", "SELECT \"status\", MEASURE(\"revenue\") FROM \"orders\" GROUP BY 1", "SELECT status, sum(amount) FROM %s GROUP BY 1" % O),
        n("schema_qualified", "SELECT MEASURE(count) FROM public.orders", "SELECT count() FROM %s" % O, soft=True),
        n("multi_statement", "SELECT 1; SELECT MEASURE(count) FROM orders", None, soft=True),
    ]


def grid_followup():
    """Shapes that came after the first run, around its findings."""
    out = []
    O = "shop.orders"
    # An equality on a time, at the start of a bucket of the same column. Each cube and each unit.
    cases = {
        "orders": ("orders", "m:orders.count", CRT, "shop.orders", "created_at"),
        "sales": ("sales", "m:sales.count", "d:sales.created_at", "shop.orders", "created_at"),
        "paid_sales": ("paid_sales", "m:paid_sales.count", "d:paid_sales.created_at", "shop.orders", "created_at"),
        "big_orders": ("big_orders", "m:big_orders.count", "d:big_orders.created_at", "shop.big_orders", "created_at"),
        "big_raw": ("big_orders_raw", "m:big_orders_raw.count", "d:big_orders_raw.created_at", "shop.big_orders", "created_at"),
        "typed": ("typed_events", "m:typed_events.count", "d:typed_events.ts_utc", "shop.typed_events", "ts_utc"),
        "users": ("users", "m:users.count", "d:users.created_at", "shop.users", "created_at"),
    }
    for name, (t, m, col, src, c) in cases.items():
        cond = " AND status = 'completed'" if name == "paid_sales" else ""
        for unit, moment in (("year", "2025-01-01T00:00:00"), ("quarter", "2025-04-01T00:00:00"), ("month", "2025-03-01T00:00:00"), ("day", "2025-03-15T00:00:00"), ("hour", "2025-03-15T02:00:00")):
            if name in ("big_orders", "big_raw"):
                moment = moment.replace("2025", "2024")
            if name == "users":
                moment = moment.replace("2025", "2024")
            lit = moment.replace("T", " ")
            out.append({"id": "follow/eq/%s/%s" % (name, unit), "table": t, "agg": [m], "by": [col + "@" + unit], "where": [["=", col, moment]],
                        "truth": "SELECT toStartOf%s(%s), count() FROM %s WHERE %s = '%s'%s GROUP BY 1" % (unit.capitalize(), c, src, c, lit, cond)})
    # Case in a text filter, on each operator and through a saved question.
    for op, v, sql in [("contains", "COMP", "status LIKE '%COMP%'"), ("starts-with", "COMP", "status LIKE 'COMP%'"), ("ends-with", "ED", "status LIKE '%ED'"), ("does-not-contain", "COMP", "status NOT LIKE '%COMP%'"),
                       ("contains", "comp", "status LIKE '%comp%'")]:
        out.append({"id": "follow/case/%s/%s" % (op, v), "table": "orders", "agg": ["m:orders.count"], "by": [], "where": [[op, STA, v]], "truth": "SELECT countIf(%s) FROM %s" % (sql, O)})
        out.append({"id": "follow/case_group/%s/%s" % (op, v), "table": "orders", "agg": ["m:orders.revenue"], "by": [CRT + "@quarter"], "where": [[op, STA, v]],
                    "truth": "SELECT toStartOfQuarter(created_at), sum(amount) FROM %s WHERE %s GROUP BY 1" % (O, sql)})
    out.append({"id": "follow/case/users_name", "table": "users", "agg": ["m:users.count"], "by": [], "where": [["contains", "d:users.name", "USER 1"]], "truth": "SELECT countIf(name LIKE '%USER 1%') FROM shop.users"})
    out.append({"id": "follow/case/view", "table": "sales", "agg": ["m:sales.count"], "by": [], "where": [["contains", "d:sales.status", "SHIP"]], "truth": "SELECT countIf(status LIKE '%SHIP%') FROM shop.orders"})
    # Native forms of the same text filter.
    for name, cond, tr in [("like", "status LIKE '%COMP%'", "status LIKE '%COMP%'"), ("ilike", "status ILIKE '%COMP%'", "status ILIKE '%COMP%'"), ("not_like", "status NOT LIKE '%COMP%'", "status NOT LIKE '%COMP%'")]:
        out.append({"id": "follow/case_native/" + name, "native": "SELECT MEASURE(count) FROM orders WHERE " + cond, "truth": "SELECT countIf(%s) FROM %s" % (tr, O)})
    # Rolling measures in native SQL, around the scan. Each filter starts inside a bucket, so the number does not agree with the filter.
    M = "DATE_TRUNC('month', created_at), MEASURE(revenue_mtd)"
    for name, where in [
        ("trunc_day_left", "DATE_TRUNC('day', created_at) >= '2025-03-15' AND created_at < '2025-05-01'"),
        ("trunc_hour_left", "DATE_TRUNC('hour', created_at) >= TIMESTAMP '2025-03-15 00:00:00' AND created_at < '2025-05-01'"),
        ("cast_date", "CAST(created_at AS date) >= DATE '2025-03-15' AND created_at < '2025-05-01'"),
        ("in_list", "created_at IN (TIMESTAMP '2025-03-15 01:00:00', TIMESTAMP '2025-03-16 01:00:00')"),
        ("extract_day", "EXTRACT(DAY FROM created_at) >= 15 AND created_at >= '2025-03-01' AND created_at < '2025-05-01'"),
        ("quoted_column", "\"created_at\" >= '2025-03-15' AND \"created_at\" < '2025-05-01'"),
        ("table_alias", "orders.created_at >= '2025-03-15' AND orders.created_at < '2025-05-01'"),
        ("lower_function_literal", "created_at >= TO_TIMESTAMP('2025-03-15', 'YYYY-MM-DD') AND created_at < '2025-05-01'"),
        ("interval_literal", "created_at >= TIMESTAMP '2025-03-01 00:00:00' + INTERVAL '14 day' AND created_at < '2025-05-01'"),
        ("comment_split", "created_at /* x */ >= /* y */ '2025-03-15' AND created_at < '2025-05-01'"),
        ("newline_split", "created_at\n>=\n'2025-03-15' AND created_at < '2025-05-01'"),
    ]:
        out.append({"id": "follow/rolling_native/" + name, "native": "SELECT %s FROM orders WHERE %s GROUP BY 1" % (M, where), "truth": None, "expect": "refusal"})
    # Native bounds, around the scan of a strict lower bound. The truth is SQL.
    B = "SELECT countIf(created_at > '2025-03-01 01:00:00' AND created_at <= '2025-03-02 01:00:00') FROM %s" % O
    for name, where in [
        ("function_literal", "created_at > TO_TIMESTAMP('2025-03-01 01:00:00', 'YYYY-MM-DD HH24:MI:SS') AND created_at <= '2025-03-02 01:00:00'"),
        ("comment_split", "created_at /* x */ > '2025-03-01 01:00:00' AND created_at <= '2025-03-02 01:00:00'"),
        ("not_le", "NOT (created_at <= '2025-03-01 01:00:00') AND created_at <= '2025-03-02 01:00:00'"),
        ("quoted", "\"created_at\" > '2025-03-01 01:00:00' AND \"created_at\" <= '2025-03-02 01:00:00'"),
    ]:
        out.append({"id": "follow/bound_native/" + name, "native": "SELECT MEASURE(count) FROM orders WHERE " + where, "truth": B})
    # Subquery filters, around the scan.
    R = "SELECT sum(amount) FROM %s WHERE toStartOfMonth(created_at) >= '2025-07-01'" % O
    for name, sql in [
        ("nested_parens", "SELECT SUM(r) FROM ((SELECT DATE_TRUNC('month', created_at) b, MEASURE(revenue) r FROM orders GROUP BY 1)) t WHERE b >= '2025-07-01'"),
        ("comment_limit", "SELECT SUM(r) FROM (SELECT DATE_TRUNC('month', created_at) b, MEASURE(revenue) r FROM orders GROUP BY 1 /* LIMIT 100 */) t WHERE b >= '2025-07-01'"),
        ("two_level", "SELECT SUM(r) FROM (SELECT b, r FROM (SELECT DATE_TRUNC('month', created_at) b, MEASURE(revenue) r FROM orders GROUP BY 1) a) t WHERE b >= '2025-07-01'"),
        ("join_having", "SELECT SUM(r) FROM (SELECT DATE_TRUNC('month', created_at) b, MEASURE(revenue) r FROM orders GROUP BY 1) t HAVING MIN(b) IS NOT NULL"),
    ]:
        out.append({"id": "follow/subquery_native/" + name, "native": sql, "truth": R if name != "join_having" else "SELECT sum(amount) FROM %s" % O})
    return out


def shapes():
    return grid_core() + grid_time() + grid_stages() + grid_models() + grid_joins() + grid_views() + grid_boundary() + grid_native() + grid_followup()


if __name__ == "__main__":
    import collections

    all_shapes = shapes()
    print(len(all_shapes), collections.Counter(s["id"].split("/")[0] for s in all_shapes))
