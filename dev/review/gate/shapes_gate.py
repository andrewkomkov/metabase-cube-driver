"""The query shapes of the 0.1.0 gate. They were written from the model, the seed and the docs, before the earlier review records were read.

Each shape is a dict: id, table, agg, by, where, order, limit, fields, exprs, outer, via, truth (ClickHouse SQL), zone, week, ordered, tol, refuse.
`refuse` means that only a refusal has a meaning. A value in a shape is a name form that dev/review/run_grid.py Ctx.walk reads: d:table.field@unit, m:table.measure, s:table.segment.
"""
NY = "America/New_York"
KT = "Asia/Kathmandu"
AP = "Pacific/Apia"
PAID = "status IN ('completed','shipped')"
SHAPES = []


def S(id, table, truth, **kw):
    SHAPES.append(dict(id=id, table=table, truth=truth, **kw))


def tz(col, zone):
    return "toTimeZone(%s, '%s')" % (col, zone) if zone else col


def bucket_sql(unit, x, week=None):
    mode = 1 if week == "monday" else 0  # Metabase starts a week on Sunday by default.
    dow = "(toDayOfWeek(%s) %% 7 + 1)" % x if mode == 0 else "toDayOfWeek(%s)" % x
    return {
        "minute": "toString(toStartOfMinute(%s))", "hour": "toString(toStartOfHour(%s))", "day": "toDate(%s)", "month": "toStartOfMonth(%s)",
        "quarter": "toStartOfQuarter(%s)", "year": "toStartOfYear(%s)", "hour-of-day": "toHour(%s)", "day-of-month": "toDayOfMonth(%s)",
        "day-of-year": "toDayOfYear(%s)", "month-of-year": "toMonth(%s)", "quarter-of-year": "toQuarter(%s)", "minute-of-hour": "toMinute(%s)",
    }.get(unit, "") % x if unit not in ("week", "day-of-week", "week-of-year") else {
        "week": "toStartOfWeek(%s, %d)" % (x, mode), "day-of-week": dow,
        "week-of-year": "ceil(toDayOfYear(toStartOfWeek(%s, %d)) / 7)" % (x, mode)}[unit]


# ---------------------------------------------------------------- the grid

SOURCES = {
    "orders": {"from": "orders", "at": "d:orders.created_at", "status": "d:orders.status",
               "m": {"count": "count()", "unique_customers": "uniqExact(user_id)", "revenue": "sumOrNull(amount)", "avg_order_value": "avgOrNull(amount)",
                     "min_amount": "minOrNull(amount)", "max_amount": "maxOrNull(amount)", "paid_revenue": "sumOrNullIf(amount, %s)" % PAID,
                     "paid_share": "1.0 * sumOrNullIf(amount, %s) / nullIf(sum(amount), 0)" % PAID}},
    "sales": {"from": "orders", "at": "d:sales.created_at", "status": "d:sales.status",
              "m": {"count": "count()", "revenue": "sumOrNull(amount)", "avg_order_value": "avgOrNull(amount)", "paid_share": "1.0 * sumOrNullIf(amount, %s) / nullIf(sum(amount), 0)" % PAID}},
    # Each of these has a rollup, so Cube Store gives the number.
    "metric_rollup_sales": {"from": "orders", "at": "d:metric_rollup_sales.created_at", "status": "d:metric_rollup_sales.status",
                            "m": {"revenue": "sumOrNull(amount)", "units": "sumOrNull(quantity)", "customers": "uniqExact(user_id)",
                                  "per_unit": "1.0 * sum(amount) / nullIf(sum(quantity), 0)", "per_customer": "1.0 * sum(amount) / nullIf(uniqExact(user_id), 0)"}},
    "pa_types": {"from": "orders", "at": "d:pa_types.created_at", "status": "d:pa_types.status",
                 "m": {"count": "count()", "revenue": "sumOrNull(amount)", "min_amount": "minOrNull(amount)", "max_amount": "maxOrNull(amount)",
                       "paid_count": "countIf(%s)" % PAID, "paid_revenue": "sumOrNullIf(amount, %s)" % PAID, "per_order": "1.0 * sum(amount) / nullIf(count(), 0)",
                       "buyers": "uniqExact(user_id)", "avg_amount": "avgOrNull(amount)", "first_status": "minOrNull(toString(status))"}},
    "pa_grain": {"from": "orders", "at": "d:pa_grain.created_at", "status": "d:pa_grain.status", "m": {"count": "count()", "revenue": "sumOrNull(amount)"}},
    "metric_types": {"from": "orders", "at": "d:metric_types.created_at", "status": "d:metric_types.status",
                     "m": {"m_count": "count()", "m_users": "uniqExact(user_id)", "m_revenue": "sumOrNull(amount)", "m_avg_amount": "avgOrNull(amount)",
                           "m_quantity_sum": "sumOrNull(quantity)", "m_ratio": "1.0 * sum(amount) / nullIf(count(), 0)", "b_median": "quantile(0.5)(toFloat64(amount))",
                           "b_amount_x_quantity": "sumOrNull(amount * quantity)", "b_weighted_amount": "1.0 * sum(amount * quantity) / nullIf(sum(quantity), 0)",
                           "c_margin": "1.0 * (sum(amount) - sum(quantity * 10)) / nullIf(sum(quantity * 10), 0)", "c_avg_of_ratio": "avgOrNull(1.0 * amount / nullIf(quantity, 0))",
                           "c_customers_per_order": "1.0 * uniqExact(user_id) / nullIf(count(), 0)", "m_count_completed_or_large": "countIf(status = 'completed' OR quantity >= 3)"}},
}
UNITS = ["day", "week", "month", "quarter", "year", "hour", "hour-of-day", "day-of-week", "day-of-month", "month-of-year", "quarter-of-year", "day-of-year", "week-of-year", None]
ZONES = [None, NY, KT, AP]


def grid_filters(src, zone):
    at, st = src["at"], src["status"]
    x = tz("created_at", zone)
    z = ", '%s'" % zone if zone else ", 'UTC'"
    day = lambda: ["field-day", at]  # noqa: E731
    return [
        ("none", [], "1"),
        ("eq", [["=", st, "completed"]], "status = 'completed'"),
        ("neq", [["!=", st, "cancelled"]], "status != 'cancelled'"),
        ("multi", [["=", st, "completed", "shipped"]], PAID),
        ("prefix", [["starts-with", st, "comp"]], "startsWith(toString(status), 'comp')"),
        ("infix", [["contains", st, "hip"]], "position(status, 'hip') > 0"),
        ("no_infix", [["does-not-contain", st, "hip"]], "position(status, 'hip') = 0"),
        ("no_infix_ci", [["does-not-contain", st, "HIP", {"case-sensitive": False}]], "position(status, 'hip') = 0"),
        ("clock_day", [["between", at + "@day", "2025-03-09", "2025-03-10"]], "toDate(%s) BETWEEN '2025-03-09' AND '2025-03-10'" % x),
        ("after_noon", [[">=", at, "2025-03-09T12:00:00"]], "created_at >= toDateTime('2025-03-09 12:00:00'%s)" % z),
        ("before_july", [["<", at, "2025-07-01T00:00:00"]], "created_at < toDateTime('2025-07-01 00:00:00'%s)" % z),
        ("not_eq", [["not", ["=", st, "processing"]]], "status != 'processing'"),
        ("or_time", [["or", ["=", st, "completed"], [">=", at, "2025-10-01T00:00:00"]]], "(status = 'completed' OR created_at >= toDateTime('2025-10-01 00:00:00'%s))" % z),
        ("month_eq", [["=", at + "@month", "2025-11-01"]], "toStartOfMonth(%s) = '2025-11-01'" % x),
        ("last_700d", [["time-interval", at, -700, "day"]], "toDate(%s) >= toDate(now(%s)) - 700 AND toDate(%s) < toDate(now(%s))" % (x, "'%s'" % (zone or "UTC"), x, "'%s'" % (zone or "UTC"))),
    ]


def grid():
    n = 0
    for name, src in SOURCES.items():
        for mi, (m, expr) in enumerate(src["m"].items()):
            for ui, unit in enumerate(UNITS):
                if name == "pa_grain" and unit not in ("day", "week", "month", "quarter", "year", "hour", None):
                    continue
                n += 1
                zone = ZONES[(mi + ui + n) % 4]
                # A row of parts of a date on a week setting: the week setting has its own shapes.
                filters = grid_filters(src, zone)
                fid, where, cond = filters[(3 * mi + 5 * ui + n) % len(filters)]
                x = tz("created_at", zone)
                q = dict(agg=["m:%s.%s" % (name, m)], where=where)
                if unit:
                    q["by"] = [src["at"] + "@" + unit]
                    sql = "SELECT %s AS b, %s AS v FROM %s WHERE %s GROUP BY b" % (bucket_sql(unit, x), expr, src["from"], cond)
                else:
                    sql = "SELECT %s AS v FROM %s WHERE %s" % (expr, src["from"], cond)
                sid = "grid/%s/%s/%s/%s/%s" % (name, m, unit or "total", fid, zone or "utc")
                stage = n % 7
                if unit and stage in (1, 2, 3, 4) and name in ("orders", "sales", "metric_types"):
                    via = ("inline", "card", "model")[n % 3]
                    if stage == 1:
                        S(sid + "/" + via + "/outer_value_filter", name, "SELECT b, v FROM (%s) WHERE v > (SELECT median(v) FROM (%s))" % (sql, sql), zone=zone, via=via,
                          outer=dict(kind="value_filter"), threshold="SELECT median(v) FROM (%s)" % sql, **q)
                    elif stage == 2:
                        S(sid + "/" + via + "/outer_top3", name, "SELECT b, v FROM (%s) ORDER BY v DESC, b LIMIT 3" % sql, zone=zone, via=via, ordered=True, outer=dict(kind="top3"), **q)
                    elif stage == 3:
                        S(sid + "/" + via + "/outer_sum", name, "SELECT sumOrNull(v) FROM (%s)" % sql, zone=zone, via=via, outer=dict(kind="sum"), **q)
                    else:
                        S(sid + "/" + via + "/outer_count", name, "SELECT count() FROM (%s)" % sql, zone=zone, via=via, outer=dict(kind="count"), **q)
                else:
                    S(sid, name, sql, zone=zone, **q)


# ---------------------------------------------------------------- shapes by hand: custom columns

A, Q, ST, AT, U = "d:orders.amount_dim", "d:orders.quantity_dim", "d:orders.status", "d:orders.created_at", "d:orders.user_id"
ID = "d:orders.id"


def by_hand():
    e = lambda name: ["expression", name]  # noqa: E731
    # Custom columns inside an aggregation, or as a breakout.
    S("x/sum_of_product", "orders", "SELECT sum(amount * quantity) FROM orders", agg=[["sum", ["*", A, Q]]])
    S("x/avg_of_ratio", "orders", "SELECT avg(amount / quantity) FROM orders", agg=[["avg", ["/", A, Q]]], tol=1e-6)
    S("x/sum_case", "orders", "SELECT sum(if(%s, amount, 0)) FROM orders" % PAID, agg=[["sum", ["case", [[["=", ST, "completed", "shipped"], A]], {"default": 0}]]])
    S("x/sum_case_no_default", "orders", "SELECT sumOrNull(if(%s, amount, NULL)) FROM orders" % PAID, agg=[["sum", ["case", [[["=", ST, "completed", "shipped"], A]]]]])
    S("x/max_minus_min", "orders", "SELECT status, max(amount) - min(amount) FROM orders GROUP BY status", agg=[["-", ["max", A], ["min", A]]], by=[ST])
    S("x/count_plus_one", "orders", "SELECT status, count() + 1 FROM orders GROUP BY status", agg=[["+", ["count"], 1]], by=[ST])
    S("x/measure_times_two", "orders", "SELECT toStartOfMonth(created_at), 2 * sum(amount) FROM orders GROUP BY 1", agg=[["*", "m:orders.revenue", 2]], by=[AT + "@month"])
    S("x/measure_over_measure", "orders", "SELECT status, 1.0 * sum(amount) / uniqExact(user_id) FROM orders GROUP BY status", agg=[["/", "m:orders.revenue", "m:orders.unique_customers"]], by=[ST], tol=1e-6)
    S("x/two_measures_and_count", "orders", "SELECT status, sum(amount), count(), uniqExact(user_id) FROM orders GROUP BY status", agg=["m:orders.revenue", ["count"], ["distinct", U]], by=[ST])
    S("x/same_measure_twice", "orders", "SELECT sum(amount), sum(amount) FROM orders", agg=["m:orders.revenue", "m:orders.revenue"])
    S("x/distinct_where", "orders", "SELECT uniqExactIf(user_id, status = 'completed') FROM orders", agg=[["distinct-where", U, ["=", ST, "completed"]]])
    S("x/min_of_text", "orders", "SELECT min(toString(status)), max(toString(status)) FROM orders", agg=[["min", ST], ["max", ST]])
    S("x/min_max_time", "orders", "SELECT min(created_at), max(created_at) FROM orders", agg=[["min", AT], ["max", AT]])
    S("x/min_max_time_ny", "orders", "SELECT min(toTimeZone(created_at, '%s')), max(toTimeZone(created_at, '%s')) FROM orders" % (NY, NY), agg=[["min", AT], ["max", AT]], zone=NY)
    S("x/distinct_time_month", "orders", "SELECT uniqExact(toStartOfMonth(created_at)) FROM orders", agg=[["distinct", AT + "@month"]])
    S("x/cum_count_month", "orders", "SELECT m, sum(c) OVER (ORDER BY m) FROM (SELECT toStartOfMonth(created_at) m, count() c FROM orders GROUP BY m)", agg=[["cum-count"]], by=[AT + "@month"])
    S("x/cum_sum_month", "orders", "SELECT m, sum(c) OVER (ORDER BY m) FROM (SELECT toStartOfMonth(created_at) m, sum(quantity) c FROM orders GROUP BY m)", agg=[["cum-sum", Q]], by=[AT + "@month"])
    S("x/cum_sum_two_breakouts", "orders", "SELECT s, m, sum(c) OVER (PARTITION BY s ORDER BY m) FROM (SELECT toString(status) s, toStartOfMonth(created_at) m, sum(quantity) c FROM orders GROUP BY s, m)",
      agg=[["cum-sum", Q]], by=[ST, AT + "@month"])
    S("x/offset_prev_month", "orders", "SELECT m, if(row_number() OVER (ORDER BY m) > 1, lagInFrame(c) OVER (ORDER BY m ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), NULL) FROM (SELECT toStartOfMonth(created_at) m, count() c FROM orders GROUP BY m)",
      agg=[["offset", {}, ["count"], -1]], by=[AT + "@month"])
    # Text.
    for name, expr, sql in [
        ("upper", ["upper", ST], "upper(toString(status))"), ("lower", ["lower", ["upper", ST]], "toString(status)"), ("length", ["length", ST], "lengthUTF8(status)"),
        ("substring", ["substring", ST, 2, 3], "substring(toString(status), 2, 3)"), ("concat", ["concat", ST, "-", "d:orders.size"], "concat(toString(status), '-', if(quantity >= 3, 'large', 'small'))"),
        ("replace", ["replace", ST, "ed", "ING"], "replaceAll(toString(status), 'ed', 'ING')"), ("trim", ["trim", ["concat", " ", ST, " "]], "toString(status)"),
        ("ltrim", ["ltrim", ["concat", " ", ST]], "toString(status)"), ("rtrim", ["rtrim", ["concat", ST, " "]], "toString(status)"),
        ("coalesce", ["coalesce", "d:orders.cancel_status", "none"], "if(status = 'completed', 'none', toString(status))"),
        ("regex", ["regex-match-first", ST, "^[a-c]+"], "extract(toString(status), '^[a-c]+')"),
    ]:
        S("x/text/" + name, "orders", "SELECT %s AS k, count() FROM orders GROUP BY k" % sql, agg=[["count"]], exprs={"k": expr}, by=[["expression", "k"]])
    # Numbers. ClickHouse rounds a Float64 half to even, and SQL rounds a Decimal half away from zero. Metabase on Postgres is the second opinion for each difference.
    for name, expr, sql in [
        ("abs", ["abs", ["-", Q, 3]], "abs(quantity - 3)"), ("floor", ["floor", ["/", A, 7]], "floor(toFloat64(amount) / 7)"), ("ceil", ["ceil", ["/", A, 7]], "ceil(toFloat64(amount) / 7)"),
        ("round", ["round", ["/", Q, 4]], "if(quantity = 2, 1, round(toFloat64(quantity) / 4))"), ("power", ["power", Q, 2], "pow(quantity, 2)"), ("sqrt", ["sqrt", Q], "sqrt(quantity)"),
        ("exp", ["exp", Q], "exp(quantity)"), ("log", ["log", Q], "log10(quantity)"), ("mod_like", ["-", Q, ["*", ["floor", ["/", Q, 2]], 2]], "quantity % 2"),
    ]:
        S("x/num/" + name, "orders", "SELECT %s AS k, count() FROM orders GROUP BY k" % sql, agg=[["count"]], exprs={"k": expr}, by=[["expression", "k"]], tol=1e-6)
    # Time expressions.
    for name, expr, sql in [
        ("year", ["get-year", AT], "toYear(created_at)"), ("quarter", ["get-quarter", AT], "toQuarter(created_at)"), ("month", ["get-month", AT], "toMonth(created_at)"),
        ("day", ["get-day", AT], "toDayOfMonth(created_at)"), ("dow", ["get-day-of-week", AT], "toDayOfWeek(created_at) % 7 + 1"), ("hour", ["get-hour", AT], "toHour(created_at)"),
        ("minute", ["get-minute", AT], "toMinute(created_at)"), ("second", ["get-second", AT], "toSecond(created_at)"), ("add_month", ["datetime-add", AT, 1, "month"], "addMonths(created_at, 1)"),
        ("sub_day", ["datetime-subtract", AT, 3, "day"], "created_at - INTERVAL 3 DAY"), ("add_hour", ["datetime-add", AT, 90, "minute"], "created_at + INTERVAL 90 MINUTE"),
        ("diff_day", ["datetime-diff", "2025-01-01T00:00:00", AT, "day"], "intDiv(toUnixTimestamp(created_at) - toUnixTimestamp(toDateTime('2025-01-01 00:00:00', 'UTC')), 86400)"),
        ("diff_hour", ["datetime-diff", "2025-01-01T00:00:00", AT, "hour"], "intDiv(toUnixTimestamp(created_at) - toUnixTimestamp(toDateTime('2025-01-01 00:00:00', 'UTC')), 3600)"),
    ]:
        S("x/time/" + name, "orders", "SELECT k, c FROM (SELECT %s AS k, count() AS c FROM orders GROUP BY k) ORDER BY k LIMIT 5" % sql, agg=[["count"]], exprs={"k": expr}, by=[["expression", "k"]],
          order=[["asc", ["expression", "k"]]], limit=5, ordered=True)
    S("x/time/hour_ny", "orders", "SELECT toHour(toTimeZone(created_at, '%s')) AS k, count() FROM orders GROUP BY k" % NY, agg=[["count"]], exprs={"k": ["get-hour", AT]}, by=[["expression", "k"]], zone=NY)
    S("x/time/convert_tz", "orders", "SELECT toHour(toTimeZone(created_at, '%s')) AS k, count() FROM orders GROUP BY k" % KT, agg=[["count"]],
      exprs={"k": ["get-hour", ["convert-timezone", AT, KT, "UTC"]]}, by=[["expression", "k"]])
    # A filter on a custom column, a custom column in the raw rows, and a custom column on a joined column.
    S("x/filter_on_expression", "orders", "SELECT count() FROM orders WHERE amount * quantity > 500", agg=[["count"]], where=[[">", ["*", A, Q], 500]])
    S("x/filter_on_text_length", "orders", "SELECT count() FROM orders WHERE lengthUTF8(status) = 7", agg=[["count"]], where=[["=", ["length", ST], 7]])
    S("x/filter_get_hour", "orders", "SELECT count() FROM orders WHERE toHour(created_at) = 5", agg=[["count"]], where=[["=", ["get-hour", AT], 5]])
    S("x/filter_part_dow", "orders", "SELECT count() FROM orders WHERE toDayOfWeek(created_at) % 7 + 1 = 1", agg=[["count"]], where=[["=", AT + "@day-of-week", 1]])
    S("x/filter_part_month_ge", "orders", "SELECT count() FROM orders WHERE toMonth(created_at) >= 11", agg=[["count"]], where=[[">=", AT + "@month-of-year", 11]])
    S("x/raw_with_expression", "orders", "SELECT id, amount * 2 AS d FROM orders ORDER BY id LIMIT 4", exprs={"d": ["*", A, 2]}, fields=[ID, ["expression", "d"]], order=[["asc", ID]], limit=4, ordered=True)
    S("x/raw_expression_filter", "orders", "SELECT id FROM orders WHERE concat(toString(status), 'x') = 'shippedx' ORDER BY id LIMIT 3", exprs={"d": ["concat", ST, "x"]}, fields=[ID], where=[["=", ["expression", "d"], "shippedx"]], order=[["asc", ID]], limit=3, ordered=True)
    S("x/joined_column_in_expression", "orders", "SELECT concat(u.city, '!') AS k, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY k", agg=[["count"]],
      exprs={"k": ["concat", "d:orders>users.city", "!"]}, by=[["expression", "k"]])
    S("x/case_breakout", "orders", "SELECT multiIf(amount > 500, 'big', amount > 100, 'mid', 'low') AS k, sum(amount) FROM orders GROUP BY k", agg=["m:orders.revenue"],
      exprs={"k": ["case", [[[">", A, 500], "big"], [[">", A, 100], "mid"]], {"default": "low"}]}, by=[["expression", "k"]])
    S("x/case_on_time", "orders", "SELECT if(created_at < toDateTime('2025-07-01 00:00:00', 'UTC'), 'h1', 'h2') AS k, count() FROM orders GROUP BY k", agg=[["count"]],
      exprs={"k": ["case", [[["<", AT, "2025-07-01T00:00:00"], "h1"]], {"default": "h2"}]}, by=[["expression", "k"]])
    S("x/bool_expression", "orders", "SELECT if(quantity > 2, 'true', 'false') AS k, count() FROM orders GROUP BY k", agg=[["count"]], exprs={"k": ["case", [[[">", Q, 2], True]], {"default": False}]}, by=[["expression", "k"]])
    S("x/expression_and_bucket", "orders", "SELECT toStartOfQuarter(created_at) AS q, if(status = 'completed', 'true', 'false') AS c, sum(amount) FROM orders GROUP BY q, c", agg=["m:orders.revenue"],
      exprs={"c": ["=", ST, "completed"]}, by=[AT + "@quarter", ["expression", "c"]])

    # Filters that a date picker or a person writes.
    S("f/year_and_part", "orders", "SELECT toStartOfYear(created_at), toMonth(created_at), count() FROM orders GROUP BY 1, 2", agg=[["count"]], by=[AT + "@year", AT + "@month-of-year"])
    S("f/two_bins_of_one_time", "orders", "SELECT toStartOfQuarter(created_at), toStartOfMonth(created_at), sum(amount) FROM orders GROUP BY 1, 2", agg=["m:orders.revenue"], by=[AT + "@quarter", AT + "@month"])
    S("f/current_year_empty", "orders", "SELECT count() FROM orders WHERE toYear(created_at) = toYear(now('UTC'))", agg=[["count"]], where=[["time-interval", AT, "current", "year"]])
    S("f/last_year_month_bucket", "orders", "SELECT toStartOfMonth(created_at), count() FROM orders WHERE toYear(created_at) = toYear(now('UTC')) - 1 GROUP BY 1", agg=[["count"]], by=[AT + "@month"], where=[["time-interval", AT, -1, "year"]])
    S("f/include_current", "orders", "SELECT count() FROM orders WHERE created_at >= toStartOfMonth(now('UTC')) - INTERVAL 22 MONTH", agg=[["count"]], where=[["time-interval", AT, -22, "month", {"include-current": True}]])
    S("f/next_30_days", "orders", "SELECT 0", agg=[["count"]], where=[["time-interval", AT, 30, "day"]])
    S("f/relative_offset", "orders", "SELECT count() FROM orders WHERE created_at >= addMonths(toStartOfMonth(now('UTC')), -13) AND created_at < addMonths(toStartOfMonth(now('UTC')), -12)",
      agg=[["count"]], where=[["between", AT + "@month", ["relative-datetime", -13, "month"], ["relative-datetime", -13, "month"]]])
    S("f/relative_time_interval", "orders", "SELECT count() FROM orders WHERE created_at >= addYears(toStartOfMonth(now('UTC')), -1) - INTERVAL 3 MONTH AND created_at < addYears(toStartOfMonth(now('UTC')), -1)",
      agg=[["count"]], where=[["relative-time-interval", AT, -3, "month", -1, "year"]])
    S("f/time_eq_exact", "orders", "SELECT count() FROM orders WHERE created_at = toDateTime('2025-03-09 10:00:00', 'UTC')", agg=[["count"]], where=[["=", AT, "2025-03-09T10:00:00"]])
    S("f/time_ne_day", "orders", "SELECT count() FROM orders WHERE toDate(created_at) != '2025-03-09'", agg=[["count"]], where=[["!=", AT + "@day", "2025-03-09"]])
    S("f/multi_day_in", "orders", "SELECT count() FROM orders WHERE toDate(created_at) IN ('2025-03-09', '2025-11-02')", agg=[["count"]], where=[["=", AT + "@day", "2025-03-09", "2025-11-02"]])
    S("f/leap_free_month_end", "orders", "SELECT count() FROM orders WHERE toDate(created_at) = '2025-02-28'", agg=[["count"]], where=[["=", AT + "@day", "2025-02-28"]])
    S("f/nonexistent_date", "orders", "SELECT count() FROM orders WHERE toDate(created_at) >= '2030-01-01'", agg=[["count"]], where=[[">=", AT + "@day", "2030-01-01"]])
    S("f/is_null_on_time", "orders", "SELECT 0", agg=[["count"]], where=[["is-null", AT]])
    S("f/not_null_on_time", "orders", "SELECT count() FROM orders", agg=[["count"]], where=[["not-null", AT]])
    S("f/is_empty_text", "orders", "SELECT count() FROM orders WHERE status = 'completed'", agg=[["count"]], where=[["is-empty", "d:orders.cancel_status"]])
    S("f/not_empty_text", "orders", "SELECT count() FROM orders WHERE status != 'completed'", agg=[["count"]], where=[["not-empty", "d:orders.cancel_status"]])
    S("f/neq_keeps_null", "orders", "SELECT count() FROM orders WHERE status != 'shipped'", agg=[["count"]], where=[["!=", "d:orders.cancel_status", "shipped"]])
    S("f/not_contains_keeps_null", "orders", "SELECT count() FROM orders WHERE position(status, 'hip') = 0", agg=[["count"]], where=[["does-not-contain", "d:orders.cancel_status", "hip"]])
    S("f/multi_contains", "orders", "SELECT count() FROM orders WHERE position(status, 'hip') > 0 OR position(status, 'pro') > 0", agg=[["count"]], where=[["or", ["contains", ST, "hip"], ["contains", ST, "pro"]]])
    S("f/multi_ne", "orders", "SELECT count() FROM orders WHERE status NOT IN ('shipped', 'cancelled')", agg=[["count"]], where=[["!=", ST, "shipped", "cancelled"]])
    S("f/number_in", "orders", "SELECT count() FROM orders WHERE quantity IN (1, 4)", agg=[["count"]], where=[["=", Q, 1, 4]])
    S("f/number_between", "orders", "SELECT count() FROM orders WHERE amount BETWEEN 100.5 AND 200.25", agg=[["count"]], where=[["between", A, 100.5, 200.25]])
    S("f/bool_true", "orders", "SELECT count() FROM orders WHERE %s" % PAID, agg=[["count"]], where=[["=", "d:orders.is_paid", True]])
    S("f/bool_false", "orders", "SELECT count() FROM orders WHERE NOT (%s)" % PAID, agg=[["count"]], where=[["=", "d:orders.is_paid", False]])
    S("f/bool_ne", "orders", "SELECT count() FROM orders WHERE NOT (%s)" % PAID, agg=[["count"]], where=[["!=", "d:orders.is_paid", True]])
    S("f/segment_and_not", "orders", "SELECT count() FROM orders WHERE (%s) AND quantity < 3" % PAID, agg=[["count"]], where=[["segment", "s:orders.paid"], ["not", ["segment", "s:orders.large"]]])
    S("f/segment_or_filter", "orders", "SELECT count() FROM orders WHERE (%s) OR quantity >= 4" % PAID, agg=[["count"]], where=[["or", ["segment", "s:orders.paid"], [">=", Q, 4]]])
    S("f/quote_in_value", "orders", "SELECT 0", agg=[["count"]], where=[["=", ST, "o'neil\\"]])
    S("f/percent_in_value", "orders", "SELECT 0", agg=[["count"]], where=[["contains", ST, "%"]])
    S("f/underscore_in_value", "orders", "SELECT 0", agg=[["count"]], where=[["starts-with", ST, "_"]])
    S("f/unicode_value", "orders", "SELECT 0", agg=[["count"]], where=[["=", ST, "заказ🚚"]])  # slop: a test value with an emoji on purpose
    S("f/in_1000_ids", "orders", "SELECT count() FROM orders WHERE id <= 1000", agg=[["count"]], where=[["="] + [ID] + list(range(1, 1001))])
    S("f/in_100_ids", "orders", "SELECT count() FROM orders WHERE id <= 100", agg=[["count"]], where=[["="] + [ID] + list(range(1, 101))])
    S("f/in_300_ids", "orders", "SELECT count() FROM orders WHERE id <= 300", agg=[["count"]], where=[["="] + [ID] + list(range(1, 301))])
    S("f/in_30_cities", "users", "SELECT count() FROM users WHERE city IN ('Austin', 'Boston')", agg=[["count"]], where=[["=", "d:users.city", "Austin", "Boston"] + ["City %d" % i for i in range(30)]])
    S("x/diff_two_columns", "orders", "SELECT sum(dateDiff('day', u.created_at, o.created_at)) FROM orders o JOIN users u ON o.user_id = u.id", agg=[["sum", ["datetime-diff", "d:orders>users.created_at", AT, "day"]]], loose=True)
    S("f/negative_and_zero", "orders", "SELECT count() FROM orders WHERE quantity > -1", agg=[["count"]], where=[[">", Q, -1]])
    S("f/fraction_on_decimal", "orders", "SELECT count() FROM orders WHERE amount < 12.50001", agg=[["count"]], where=[["<", A, 12.50001]])
    S("f/inside_box", "users", "SELECT count() FROM users WHERE latitude BETWEEN 40 AND 50 AND longitude BETWEEN -100 AND -70", agg=[["count"]],
      where=[["inside", "d:users.location__latitude", "d:users.location__longitude", 50, -100, 40, -70]])
    S("f/order_limit_measure", "orders", "SELECT user_id, sum(amount) AS v FROM orders GROUP BY user_id ORDER BY v DESC, user_id LIMIT 5", agg=["m:orders.revenue"], by=[U], order=[["desc", ["aggregation", 0]], ["asc", U]], limit=5, ordered=True)
    S("f/order_by_breakout_desc_month", "orders", "SELECT toStartOfMonth(created_at) m, count() FROM orders GROUP BY m ORDER BY m DESC LIMIT 2", agg=[["count"]], by=[AT + "@month"], order=[["desc", AT + "@month"]], limit=2, ordered=True)
    S("f/raw_page2", "orders", "SELECT id, toString(status) FROM orders ORDER BY id LIMIT 3 OFFSET 3", fields=[ID, ST], order=[["asc", ID]], page={"page": 2, "items": 3}, ordered=True)
    S("f/bins_num", "orders", "SELECT floor(toFloat64(amount) / 100) * 100 AS k, count() FROM orders GROUP BY k", agg=[["count"]], by=[["field", "d:orders.amount_dim", {"binning": {"strategy": "bin-width", "bin-width": 100}}]])
    S("f/bins_default_coord", "users", "SELECT floor(latitude / 10) * 10 AS k, count() FROM users GROUP BY k", agg=[["count"]], by=[["field", "d:users.location__latitude", {"binning": {"strategy": "bin-width", "bin-width": 10}}]])

    # Joins.
    join_users = lambda strat="left-join": [{"source-table": "t:users", "alias": "U", "strategy": strat, "condition": ["=", U, ["field", "d:users.id", {"join-alias": "U"}]], "fields": "none"}]  # noqa: E731
    S("j/left_city_revenue", "orders", "SELECT u.city, sum(o.amount) FROM orders o LEFT JOIN users u ON o.user_id = u.id GROUP BY u.city", agg=["m:orders.revenue"], by=[["field", "d:users.city", {"join-alias": "U"}]], joins=join_users())
    S("j/inner_vip_count", "orders", "SELECT count() FROM orders o JOIN users u ON o.user_id = u.id WHERE u.is_vip", agg=["m:orders.count"], where=[["=", ["field", "d:users.is_vip", {"join-alias": "U"}], True]], joins=join_users("inner-join"))
    S("j/implicit_two_fks", "orders", "SELECT u.state, p.category, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id JOIN products p ON o.product_id = p.id GROUP BY 1, 2", agg=["m:orders.revenue"], by=["d:orders>users.state", "d:orders>products.category"])
    S("j/implicit_filter_only", "orders", "SELECT sum(o.amount) FROM orders o JOIN products p ON o.product_id = p.id WHERE p.price > 25", agg=["m:orders.revenue"], where=[[">", "d:orders>products.price", 25]])
    S("j/measure_of_two_cubes", "orders", "SELECT p.category, sum(o.amount), uniqExact(p.id) FROM orders o JOIN products p ON o.product_id = p.id GROUP BY 1", agg=["m:orders.revenue", "m:products.count"], by=["d:orders>products.category"])
    S("j/fan_out_by_method", "payments", "SELECT method, sum(paid) FROM (SELECT 'card' AS method, amount AS paid FROM orders UNION ALL SELECT 'fee', toDecimal64(1, 2) FROM orders) GROUP BY method", agg=["m:payments.total"], by=["d:payments.method"])
    S("j/user_time_and_order_time", "orders", "SELECT toStartOfYear(u.created_at), toStartOfQuarter(o.created_at), count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1, 2", agg=["m:orders.count"], by=["d:orders>users.created_at@year", AT + "@quarter"])
    S("j/sub_query_dim", "users", "SELECT lo, count() FROM (SELECT u.id, countIf(o.user_id != 0) AS lo FROM users u LEFT JOIN orders o ON o.user_id = u.id GROUP BY u.id) GROUP BY lo", agg=["m:users.count"], by=["d:users.lifetime_orders"])
    S("j/sub_query_filter", "users", "SELECT count() FROM (SELECT u.id, countIf(o.user_id != 0) AS lo FROM users u LEFT JOIN orders o ON o.user_id = u.id GROUP BY u.id) WHERE lo > 25", agg=["m:users.count"], where=[[">", "d:users.lifetime_orders", 25]])

    # Views.
    S("v/sales_city_category", "sales", "SELECT u.city, p.category, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id JOIN products p ON o.product_id = p.id GROUP BY 1, 2", agg=["m:sales.revenue"], by=["d:sales.users_city", "d:sales.products_category"])
    S("v/sales_users_count", "sales", "SELECT u.state, uniqExact(u.id), count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1", agg=["m:sales.users_count", "m:sales.count"], by=["d:sales.users_state"])
    S("v/sales_vip_filter_quarter", "sales", "SELECT toStartOfQuarter(o.created_at), avg(o.amount) FROM orders o JOIN users u ON o.user_id = u.id WHERE u.is_vip GROUP BY 1", agg=["m:sales.avg_order_value"], by=["d:sales.created_at@quarter"], where=[["=", "d:sales.users_is_vip", True]])
    S("v/paid_net_sales_month", "paid_sales", "SELECT toStartOfMonth(created_at), sum(amount) FROM orders WHERE status = 'completed' GROUP BY 1", agg=["m:paid_sales.net_sales"], by=["d:paid_sales.created_at@month"])
    S("v/paid_contradiction", "paid_sales", "SELECT sumOrNull(amount) FROM orders WHERE 0", agg=["m:paid_sales.net_sales"], where=[["=", "d:paid_sales.status", "shipped"]])
    S("v/paid_derived", "paid_sales", "SELECT sum(amount) / count() FROM orders WHERE status = 'completed'", agg=["m:paid_sales.revenue_per_order"], tol=1e-6)
    S("v/paid_raw_count", "paid_sales", "SELECT count() FROM orders WHERE status = 'completed'", agg=[["count"]])
    S("v/paid_stage_cannot_widen", "paid_sales", "SELECT count() FROM orders WHERE status = 'completed'", agg=[["count"]], where=[["or", ["=", "d:paid_sales.status", "completed"], ["=", "d:paid_sales.status", "shipped"]]])
    S("v/extended_payments", "extended_sales", "SELECT method, sum(paid) FROM (SELECT 'card' AS method, amount AS paid FROM orders UNION ALL SELECT 'fee', toDecimal64(1, 2) FROM orders) GROUP BY method", agg=["m:extended_sales.payments_total"], by=["d:extended_sales.payments_method"])
    S("v/extended_revenue_by_method", "extended_sales", "SELECT method, sum(a) FROM (SELECT 'card' AS method, amount AS a FROM orders UNION ALL SELECT 'fee', amount FROM orders) GROUP BY method", agg=["m:extended_sales.revenue"], by=["d:extended_sales.payments_method"])
    S("v/promo_first", "promo_sales", "SELECT concat('P', toString(id % 10)) AS c, count() FROM orders GROUP BY c", agg=["m:promo_sales.count"], by=["d:promo_sales.first_promo_code"])
    S("v/promo_second", "promo_sales", "SELECT concat('P', toString((id + 1) % 10)) AS c, count() FROM orders GROUP BY c", agg=["m:promo_sales.count"], by=["d:promo_sales.second_promo_code"])
    S("v/restricted_rows", "restricted_sales", "SELECT count(), sum(amount) FROM orders WHERE status = 'shipped'", agg=["m:restricted_sales.count", "m:restricted_sales.revenue"])
    S("v/restricted_widen_or", "restricted_sales", "SELECT count() FROM orders WHERE status = 'shipped'", agg=["m:restricted_sales.count"], where=[["or", ["=", "d:restricted_sales.status", "completed"], ["=", "d:restricted_sales.status", "shipped"]]])
    S("v/masked_status_visible", "masked_sales", "SELECT status, count() FROM orders GROUP BY status", agg=["m:masked_sales.count"], by=["d:masked_sales.status"])
    S("v/masked_measure", "masked_sales", "SELECT -1", agg=["m:masked_sales.revenue_masked"], loose=True)
    S("v/customer_locations", "customer_locations", "SELECT count() FROM users", agg=["m:customer_locations.count"])

    # Three stages, a question on a question, a filter after a custom column, a join of a model.
    S("n/three_stages", "orders", "SELECT sumOrNull(v) FROM (SELECT b, v FROM (SELECT toStartOfMonth(created_at) b, sum(amount) v FROM orders GROUP BY b) WHERE v > 20000)",
      agg=["m:orders.revenue"], by=[AT + "@month"], outer=dict(kind="three"), via="inline")
    S("n/three_stages_card", "orders", "SELECT sumOrNull(v) FROM (SELECT b, v FROM (SELECT toStartOfMonth(created_at) b, sum(amount) v FROM orders GROUP BY b) WHERE v > 20000)",
      agg=["m:orders.revenue"], by=[AT + "@month"], outer=dict(kind="three"), via="card")
    S("n/model_raw_then_group", "orders", "SELECT toString(status), sum(amount), count() FROM orders GROUP BY 1", fields=[ID, ST, A], outer=dict(kind="raw_group", col="status", sum="amount_dim"), via="model")
    S("n/model_raw_then_filter_group", "orders", "SELECT toStartOfQuarter(created_at), sum(amount), count() FROM orders WHERE amount > 200 GROUP BY 1", fields=[ID, AT, A], outer=dict(kind="raw_time", col="created_at", sum="amount_dim", gt=200), via="model")
    S("n/question_raw_filtered_inner", "orders", "SELECT toString(status), sum(amount), count() FROM orders WHERE quantity >= 3 GROUP BY 1", fields=[ID, ST, A], where=[[">=", Q, 3]], outer=dict(kind="raw_group", col="status", sum="amount_dim"), via="card")
    S("n/model_with_expression", "orders", "SELECT multiIf(amount > 500, 'big', 'small') AS k, sum(amount * 2) FROM orders GROUP BY k", fields=[ID, A], exprs={"k": ["case", [[[">", A, 500], "big"]], {"default": "small"}], "d": ["*", A, 2]},
      extra_fields=["k", "d"], outer=dict(kind="raw_group", col="k", sum="d", no_count=True), via="model")
    S("n/model_measure_sum_and_avg", "orders", "SELECT status, sum(amount) FROM orders GROUP BY status", fields=[ID, ST, "d:orders.revenue"], outer=dict(kind="measure_cols"), via="model")
    S("n/group_then_expression", "orders", "SELECT toString(status), sum(amount) / 1000 FROM orders GROUP BY 1", agg=["m:orders.revenue"], by=[ST], outer=dict(kind="div1000"), via="card", tol=1e-6)
    S("n/group_then_filter_status", "orders", "SELECT toString(status), sum(amount) FROM orders WHERE status = 'shipped' GROUP BY 1", agg=["m:orders.revenue"], by=[ST], outer=dict(kind="eq_first", value="shipped"), via="card")
    S("n/share_stays_after_filter", "orders", "SELECT status, s FROM (SELECT toString(status) AS status, 1.0 * sum(amount) / (SELECT sum(amount) FROM orders) AS s FROM orders GROUP BY status) WHERE status = 'shipped'",
      agg=["m:orders.revenue_share_of_total"], by=[ST], outer=dict(kind="eq_first", value="shipped"), via="card", tol=1e-6)

    # Native SQL, with and without template tags. Each text is what a person writes on the SQL API.
    def N(id, sql, truth, **kw):
        S("sql/" + id, None, truth, native=sql, **kw)
    N("measure_group", "SELECT status, MEASURE(revenue) FROM orders GROUP BY 1", "SELECT status, sum(amount) FROM orders GROUP BY 1")
    N("sum_of_measure", "SELECT status, SUM(revenue) FROM orders GROUP BY 1", "SELECT status, sum(amount) FROM orders GROUP BY 1")
    N("count_star", "SELECT COUNT(*) FROM orders", "SELECT count() FROM orders")
    N("date_trunc_where", "SELECT DATE_TRUNC('month', created_at) AS m, MEASURE(count) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-05-01' GROUP BY 1 ORDER BY 1",
      "SELECT toStartOfMonth(created_at), count() FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-05-01' GROUP BY 1 ORDER BY 1", ordered=True)
    N("subquery_outer_agg", "SELECT SUM(v) FROM (SELECT status, MEASURE(revenue) AS v FROM orders GROUP BY 1) t", "SELECT sum(amount) FROM orders")
    N("cte", "WITH t AS (SELECT status, MEASURE(count) AS c FROM orders GROUP BY 1) SELECT MAX(c) FROM t", "SELECT max(c) FROM (SELECT count() c FROM orders GROUP BY status)")
    N("having", "SELECT status, MEASURE(revenue) AS v FROM orders GROUP BY 1 HAVING MEASURE(revenue) > 300000", "SELECT status, sum(amount) v FROM orders GROUP BY 1 HAVING v > 300000")
    N("order_limit", "SELECT user_id, MEASURE(revenue) AS v FROM orders GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3", "SELECT user_id, sum(amount) v FROM orders GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3", ordered=True)
    N("case_on_dim", "SELECT CASE WHEN status = 'completed' THEN 'c' ELSE 'o' END AS k, MEASURE(revenue) FROM orders GROUP BY 1", "SELECT if(status = 'completed', 'c', 'o') k, sum(amount) FROM orders GROUP BY 1")
    N("join_on_key", "SELECT u.city, MEASURE(o.revenue) FROM orders o JOIN users u ON o.__cubeJoinField = u.__cubeJoinField GROUP BY 1", "SELECT u.city, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1")
    N("like", "SELECT COUNT(*) FROM orders WHERE status LIKE 'ship%'", "SELECT count() FROM orders WHERE status LIKE 'ship%'")
    N("in_list", "SELECT MEASURE(count) FROM orders WHERE status IN ('completed', 'shipped')", "SELECT count() FROM orders WHERE %s" % PAID)
    N("union", "SELECT 'a' AS k, MEASURE(count) AS c FROM orders UNION ALL SELECT 'b', MEASURE(count) FROM users", "SELECT 'a', count() FROM orders UNION ALL SELECT 'b', count() FROM users")
    N("window", "SELECT m, SUM(c) OVER (ORDER BY m) FROM (SELECT DATE_TRUNC('quarter', created_at) AS m, MEASURE(count) AS c FROM orders GROUP BY 1) t",
      "SELECT m, sum(c) OVER (ORDER BY m) FROM (SELECT toStartOfQuarter(created_at) m, count() c FROM orders GROUP BY m)")
    N("distinct", "SELECT DISTINCT status FROM orders", "SELECT DISTINCT status FROM orders")
    N("view", "SELECT users_city, MEASURE(revenue) FROM sales GROUP BY 1", "SELECT u.city, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1")
    N("no_rows_measure", "SELECT MEASURE(revenue) FROM orders WHERE status = 'nothing'", "SELECT sumOrNull(amount) FROM orders WHERE status = 'nothing'")
    N("lower_case_text", "SELECT COUNT(*) FROM orders WHERE status = 'COMPLETED'", "SELECT 0")
    N("rollup_month", "SELECT DATE_TRUNC('month', created_at), MEASURE(revenue) FROM metric_rollup_sales GROUP BY 1", "SELECT toStartOfMonth(created_at), sum(amount) FROM orders GROUP BY 1")
    N("rolling_month", "SELECT DATE_TRUNC('month', created_at), MEASURE(revenue_mtd) FROM metric_rollup_sales WHERE created_at >= '2025-01-01' AND created_at < '2025-04-01' GROUP BY 1",
      "SELECT toStartOfMonth(created_at), sum(amount) FROM orders WHERE created_at < '2025-04-01' GROUP BY 1")
    tag = lambda name, kind, **kw: {name: dict({"id": name, "name": name, "display-name": name, "type": kind}, **kw)}  # noqa: E731
    N("tag_text", "SELECT MEASURE(count) FROM orders WHERE status = {{s}}", "SELECT count() FROM orders WHERE status = 'shipped'", tags=tag("s", "text"), params=[{"type": "category", "target": ["variable", ["template-tag", "s"]], "value": "shipped"}])
    N("tag_number", "SELECT COUNT(*) FROM orders WHERE quantity_dim = {{n}}", "SELECT count() FROM orders WHERE quantity = 2", tags=tag("n", "number"), params=[{"type": "number/=", "target": ["variable", ["template-tag", "n"]], "value": "2"}])
    N("tag_date", "SELECT MEASURE(count) FROM orders WHERE created_at >= {{d}}", "SELECT count() FROM orders WHERE created_at >= '2025-06-15'", tags=tag("d", "date"), params=[{"type": "date/single", "target": ["variable", ["template-tag", "d"]], "value": "2025-06-15"}])
    N("tag_optional_absent", "SELECT MEASURE(count) FROM orders WHERE 1 = 1 [[AND status = {{s}}]]", "SELECT count() FROM orders", tags=tag("s", "text"))
    N("tag_field_text", "SELECT MEASURE(count) FROM orders WHERE {{f}}", "SELECT count() FROM orders WHERE status = 'shipped'", tags=tag("f", "dimension", dimension="d:orders.status", **{"widget-type": "string/="}),
      params=[{"type": "string/=", "target": ["dimension", ["template-tag", "f"]], "value": ["shipped"]}])
    N("tag_field_date_range", "SELECT MEASURE(count) FROM orders WHERE {{f}}", "SELECT count() FROM orders WHERE toDate(created_at) BETWEEN '2025-03-09' AND '2025-03-10'", tags=tag("f", "dimension", dimension="d:orders.created_at", **{"widget-type": "date/range"}),
      params=[{"type": "date/range", "target": ["dimension", ["template-tag", "f"]], "value": "2025-03-09~2025-03-10"}])
    N("tag_field_date_ny", "SELECT MEASURE(count) FROM orders WHERE {{f}}", "SELECT count() FROM orders WHERE toDate(toTimeZone(created_at, '%s')) BETWEEN '2025-03-09' AND '2025-03-10'" % NY, tags=tag("f", "dimension", dimension="d:orders.created_at", **{"widget-type": "date/range"}),
      params=[{"type": "date/range", "target": ["dimension", ["template-tag", "f"]], "value": "2025-03-09~2025-03-10"}], zone=NY)
    N("tag_field_relative", "SELECT MEASURE(count) FROM orders WHERE {{f}}", "SELECT count() FROM orders WHERE toYear(created_at) = toYear(now('UTC')) - 1", tags=tag("f", "dimension", dimension="d:orders.created_at", **{"widget-type": "date/all-options"}),
      params=[{"type": "date/all-options", "target": ["dimension", ["template-tag", "f"]], "value": "past1years"}])
    N("tag_field_absent", "SELECT MEASURE(count) FROM orders WHERE {{f}}", "SELECT count() FROM orders", tags=tag("f", "dimension", dimension="d:orders.status", **{"widget-type": "string/="}))

    # A parameter on a query of the builder, as a dashboard sends it.
    S("p/string", "orders", "SELECT sum(amount) FROM orders WHERE status = 'shipped'", agg=["m:orders.revenue"], parameters=[{"type": "string/=", "target": ["dimension", "d:orders.status"], "value": ["shipped"]}])
    S("p/date_range", "orders", "SELECT sum(amount) FROM orders WHERE toDate(created_at) BETWEEN '2025-03-01' AND '2025-03-31'", agg=["m:orders.revenue"], parameters=[{"type": "date/range", "target": ["dimension", AT], "value": "2025-03-01~2025-03-31"}])
    S("p/date_past", "orders", "SELECT sum(amount) FROM orders WHERE toYear(created_at) = toYear(now('UTC')) - 1", agg=["m:orders.revenue"], parameters=[{"type": "date/all-options", "target": ["dimension", AT], "value": "past1years"}])
    S("p/date_month_ny", "orders", "SELECT sum(amount) FROM orders WHERE toStartOfMonth(toTimeZone(created_at, '%s')) = '2025-03-01'" % NY, agg=["m:orders.revenue"], parameters=[{"type": "date/month-year", "target": ["dimension", AT], "value": "2025-03"}], zone=NY)
    S("p/number_between", "orders", "SELECT count() FROM orders WHERE quantity BETWEEN 2 AND 3", agg=[["count"]], parameters=[{"type": "number/between", "target": ["dimension", Q], "value": [2, 3]}])
    S("p/string_contains", "orders", "SELECT count() FROM orders WHERE position(status, 'hip') > 0", agg=[["count"]], parameters=[{"type": "string/contains", "target": ["dimension", ST], "value": ["hip"], "options": {"case-sensitive": False}}])
    S("p/temporal_unit", "orders", "SELECT toStartOfQuarter(created_at), sum(amount) FROM orders GROUP BY 1", agg=["m:orders.revenue"], by=[AT + "@month"], parameters=[{"type": "temporal-unit", "target": ["dimension", AT + "@month"], "value": "quarter"}])

    # The metric catalog.
    MT, D = "metric_types", "d:metric_types.created_at"
    PAY = "(SELECT id AS order_id, amount AS paid FROM orders UNION ALL SELECT id, toDecimal64(1, 2) FROM orders)"
    catalog = [
        ("m_users_approx", "uniqExact(user_id)", 0.05), ("m_min_amount", "min(amount)", None), ("m_max_amount", "max(amount)", None), ("m_avg_quantity", "avg(quantity)", None),
        ("m_status_text", "min(toString(status))", None), ("m_count_completed", "countIf(status = 'completed')", None), ("m_count_large_completed", "countIf(status = 'completed' AND quantity >= 3)", None),
        ("m_paid_revenue", "sumOrNullIf(amount, %s)" % PAID, None), ("m_fmt_id", "max(id)", None), ("m_fmt_currency_3", "sum(amount)", None), ("m_fmt_abbr", "sum(amount)", None), ("m_formatted_count", "count()", None),
        ("b_aov_ratio", "1.0 * sum(amount) / count()", None), ("b_p90", "quantile(0.9)(toFloat64(amount))", None), ("c_ratio_of_sums", "1.0 * sum(amount) / sum(quantity)", None), ("c_sum_of_row_ratios", "sum(1.0 * amount / quantity)", None),
        ("c_ratio_of_avgs", "1.0 * avg(amount) / avg(quantity)", None), ("c_paid_share", "1.0 * sumOrNullIf(amount, %s) / sum(amount)" % PAID, None), ("c_revenue_per_customer", "1.0 * sum(amount) / uniqExact(user_id)", None),
        ("c_cost", "sum(quantity * 10)", None), ("c_product", "avg(quantity) * count()", None), ("c_nested_ratio", "1.0 * sum(amount) / sum(quantity) / count()", None), ("m_fmt_percent_2", "1.0 * sumOrNullIf(amount, %s) / sum(amount)" % PAID, None),
        ("m_fmt_d3_percent", "1.0 * sumOrNullIf(amount, %s) / sum(amount)" % PAID, None), ("m_fmt_accounting_0", "sum(amount)", None),
    ]
    for m, expr, tol in catalog:
        extra = {"tol": tol} if tol else {"tol": 1e-6}
        S("c/%s/status" % m, MT, "SELECT status, %s FROM orders GROUP BY status" % expr, agg=["m:%s.%s" % (MT, m)], by=["d:%s.status" % MT], **extra)
        S("c/%s/quarter_ny" % m, MT, "SELECT toStartOfQuarter(toTimeZone(created_at, '%s')) q, %s FROM orders GROUP BY q" % (NY, expr), agg=["m:%s.%s" % (MT, m)], by=[D + "@quarter"], zone=NY, **extra)
        S("c/%s/size_filter" % m, MT, "SELECT if(quantity >= 3, 'large', 'small') k, %s FROM orders WHERE %s GROUP BY k" % (expr, PAID), agg=["m:%s.%s" % (MT, m)], by=["d:%s.size" % MT], where=[["=", "d:%s.is_paid" % MT, True]], **extra)
    S("c/m_has_completed", MT, "SELECT toString(status), if(max(status = 'completed') = 1, 'true', 'false') FROM orders GROUP BY 1", agg=["m:%s.m_has_completed" % MT], by=["d:%s.status" % MT], loose=True)
    S("c/m_last_order_at_ny", MT, "SELECT toString(status), max(toTimeZone(created_at, '%s')) FROM orders GROUP BY 1" % NY, agg=["m:%s.m_last_order_at" % MT], by=["d:%s.status" % MT], zone=NY)
    S("c/m_median_agg", MT, "SELECT status, quantile(0.5)(toFloat64(a)) FROM (SELECT id, status, sum(amount) a FROM orders GROUP BY id, status) GROUP BY status", agg=["m:%s.m_median_agg" % MT], by=["d:%s.status" % MT], tol=1e-3)
    S("c/m_rank", MT, "SELECT size, status, rank() OVER (PARTITION BY size ORDER BY r DESC) FROM (SELECT if(quantity >= 3, 'large', 'small') size, toString(status) status, sum(amount) r FROM orders GROUP BY size, status)", agg=["m:%s.m_rank" % MT], by=["d:%s.size" % MT, "d:%s.status" % MT], loose=True)
    S("c/m_rev_grand_total_filter", MT, "SELECT status, (SELECT sum(amount) FROM orders WHERE quantity >= 3) FROM (SELECT DISTINCT status FROM orders WHERE quantity >= 3)", agg=["m:%s.m_rev_grand_total" % MT], by=["d:%s.status" % MT], where=[[">=", "d:%s.quantity" % MT, 3]], loose=True)
    S("c/c_share_of_total", MT, "SELECT status, 1.0 * sum(amount) / (SELECT sum(amount) FROM orders) FROM orders GROUP BY status", agg=["m:%s.c_share_of_total" % MT], by=["d:%s.status" % MT], tol=1e-6)
    S("c/m_rev_group_by_size", MT, "SELECT size, status, r FROM (SELECT DISTINCT if(quantity >= 3, 'large', 'small') size, toString(status) status FROM orders) a JOIN (SELECT toString(status) status, sum(amount) r FROM orders GROUP BY status) b USING status", agg=["m:%s.m_rev_group_by" % MT], by=["d:%s.size" % MT, "d:%s.status" % MT], loose=True)
    S("c/m_rev_reduce_by", MT, "SELECT size, status, r FROM (SELECT DISTINCT if(quantity >= 3, 'large', 'small') size, toString(status) status FROM orders) a JOIN (SELECT if(quantity >= 3, 'large', 'small') size, sum(amount) r FROM orders GROUP BY size) b USING size", agg=["m:%s.m_rev_reduce_by" % MT], by=["d:%s.size" % MT, "d:%s.status" % MT], loose=True)
    S("c/prior_month_range", MT, "SELECT m, (SELECT sum(amount) FROM orders WHERE toStartOfMonth(created_at) = addMonths(m, -1)) FROM (SELECT toDate(arrayJoin(['2025-03-01', '2025-04-01'])) m)", agg=["m:%s.m_rev_prior_month" % MT], by=[D + "@month"], where=[["between", D + "@month", "2025-03-01", "2025-04-01"]], loose=True)
    S("c/b_mom_range", MT, "WITH (SELECT sum(amount) FROM orders WHERE toStartOfMonth(created_at) = '2025-02-01') AS a, (SELECT sum(amount) FROM orders WHERE toStartOfMonth(created_at) = '2025-03-01') AS b SELECT toDate('2025-03-01'), 1.0 * (b - a) / a", agg=["m:%s.b_mom" % MT], by=[D + "@month"], where=[["=", D + "@month", "2025-03-01"]], tol=1e-6)
    S("c/rolling_3m", MT, "SELECT m, (SELECT sum(amount) FROM orders WHERE created_at < addMonths(m, 1) AND created_at >= addMonths(m, -2)) FROM (SELECT toDate(arrayJoin(['2025-05-01', '2025-06-01'])) m)", agg=["m:%s.m_rev_trailing_3m" % MT], by=[D + "@month"], where=[["between", D + "@month", "2025-05-01", "2025-06-01"]], loose=True)
    S("c/moving_avg", MT, "SELECT toDate('2025-06-01'), (SELECT sum(amount) FROM orders WHERE created_at >= '2025-04-01' AND created_at < '2025-07-01') / 3", agg=["m:%s.b_moving_avg_3m" % MT], by=[D + "@month"], where=[["=", D + "@month", "2025-06-01"]], tol=1e-6, loose=True)
    S("c/mtd_day", MT, "SELECT d, (SELECT sum(amount) FROM orders WHERE created_at >= '2025-03-01' AND toDate(created_at) <= d) FROM (SELECT toDate(arrayJoin(['2025-03-09', '2025-03-10'])) d)", agg=["m:%s.m_rev_mtd" % MT], by=[D + "@day"], where=[["between", D + "@day", "2025-03-09", "2025-03-10"]])
    S("c/cross_ratio_join", MT, "SELECT status, 2.0 FROM orders GROUP BY status", agg=["m:%s.c_cross_count_ratio" % MT], by=["d:%s.status" % MT])
    S("c/customers_conversion", "metric_customers", "SELECT uniqExactIf(user_id, %s AND created_at < toDateTime('2025-02-01 00:00:00', 'UTC')) / 200 FROM orders" % PAID, agg=["m:metric_customers.conversion"], tol=1e-6)
    S("c/customers_revenue_state", "metric_customers", "SELECT u.state, sum(o.amount) FROM users u LEFT JOIN orders o ON o.user_id = u.id GROUP BY 1", agg=["m:metric_customers.revenue"], by=["d:metric_customers.state"])
    S("c/retention", "metric_customer_months", "SELECT m, 1.0 * uniqExactIf(u, nx) / uniqExact(u) FROM (SELECT user_id u, toStartOfMonth(created_at) m, (u, addMonths(m, 1)) IN (SELECT user_id, toStartOfMonth(created_at) FROM orders) nx FROM orders) GROUP BY m", agg=["m:metric_customer_months.retention"], by=["d:metric_customer_months.month_at@month"], tol=1e-6)
    S("c/repeat_rate_ny", "metric_customer_months", "SELECT m, 1.0 * uniqExactIf(u, c >= 2) / uniqExact(u) FROM (SELECT user_id u, toStartOfMonth(toTimeZone(created_at, '%s')) m, count() c FROM orders GROUP BY u, m) GROUP BY m" % NY, agg=["m:metric_customer_months.repeat_rate"], by=["d:metric_customer_months.month_at@month"], tol=1e-6, zone=NY)
    S("c/yoy_month", "metric_sales", "WITH (SELECT sum(amount) FROM metric_sales WHERE toStartOfMonth(sold_at) = '2024-03-01') AS a, (SELECT sum(amount) FROM metric_sales WHERE toStartOfMonth(sold_at) = '2025-03-01') AS b SELECT toDate('2025-03-01'), 1.0 * (b - a) / a", agg=["m:metric_sales.yoy"], by=["d:metric_sales.sold_at@month"], where=[["=", "d:metric_sales.sold_at@month", "2025-03-01"]], tol=1e-6, loose=True)
    for m, expr in [("rows", "count()"), ("num", "sumOrNull(num)"), ("den", "sumOrNull(den)"), ("avg_num", "avgOrNull(num)"), ("min_den", "minOrNull(den)"), ("max_den", "maxOrNull(den)"), ("ratio", "1.0 * sum(num) / nullIf(sum(den), 0)"),
                    ("sum_of_ratios", "sumOrNull(1.0 * num / nullIf(den, 0))"), ("avg_of_ratios", "avgOrNull(1.0 * num / nullIf(den, 0))"), ("change", "1.0 * (sum(num) - sum(den)) / nullIf(sum(den), 0)"),
                    ("positive_num", "sumOrNullIf(num, num > 0)"), ("positive_share", "1.0 * sumIf(num, num > 0) / nullIf(sum(num), 0)")]:
        S("c/edges/%s/grp" % m, "metric_edges", "SELECT grp, %s FROM metric_edges GROUP BY grp" % expr, agg=["m:metric_edges.%s" % m], by=["d:metric_edges.grp"], tol=1e-6)
        S("c/edges/%s/month" % m, "metric_edges", "SELECT toStartOfMonth(day), %s FROM metric_edges GROUP BY 1" % expr, agg=["m:metric_edges.%s" % m], by=["d:metric_edges.day@month"], tol=1e-6)
        S("c/edges/%s/none_match" % m, "metric_edges", "SELECT %s FROM metric_edges WHERE grp = 'none'" % expr, agg=["m:metric_edges.%s" % m], where=[["=", "d:metric_edges.grp", "none"]], tol=1e-6)
    B, BT = "metric_balances", "d:metric_balances.ts"
    S("c/closing_month_region", B, "SELECT m, region, sum(balance) FROM metric_balances JOIN (SELECT toStartOfMonth(ts) m, max(toDate(ts)) d FROM metric_balances GROUP BY m) x ON toDate(ts) = x.d GROUP BY m, region", agg=["m:%s.balance_closing" % B], by=[BT + "@month", "d:%s.region" % B], where=[["between", BT + "@month", "2025-01-01", "2025-04-01"]], loose=True)
    S("c/last_day_region", B, "SELECT region, sum(balance) FROM metric_balances WHERE toDate(ts) = (SELECT max(toDate(ts)) FROM metric_balances) GROUP BY region", agg=["m:%s.balance_last_day" % B], by=["d:%s.region" % B], loose=True)
    S("c/daily_average", B, "SELECT region, 1.0 * sum(balance) / uniqExact(toDate(ts)) FROM metric_balances GROUP BY region", agg=["m:%s.balance_daily_average" % B], by=["d:%s.region" % B], tol=1e-6)

    # Rollups, with a shape of a person on each: a range on a partition edge, one day, a zone, a text filter, a hidden rollup miss.
    R, RT = "metric_rollup_sales", "d:metric_rollup_sales.created_at"
    S("r/one_hour_miss", R, "SELECT sumOrNull(amount) FROM orders WHERE created_at >= '2025-03-09 05:00:00' AND created_at < '2025-03-09 06:00:00'", agg=["m:%s.revenue" % R], where=[[">=", RT, "2025-03-09T05:00:00"], ["<", RT, "2025-03-09T06:00:00"]])
    S("r/day_edge", R, "SELECT sum(amount) FROM orders WHERE toDate(created_at) = '2025-12-27'", agg=["m:%s.revenue" % R], where=[["=", RT + "@day", "2025-12-27"]])
    S("r/out_of_data", R, "SELECT sumOrNull(amount) FROM orders WHERE 0", agg=["m:%s.revenue" % R], where=[["=", RT + "@day", "2024-12-31"]])
    S("r/ny_day", R, "SELECT sum(amount) FROM orders WHERE toDate(toTimeZone(created_at, '%s')) = '2025-03-09'" % NY, agg=["m:%s.revenue" % R], where=[["=", RT + "@day", "2025-03-09"]], zone=NY)
    S("r/ny_month", R, "SELECT toStartOfMonth(toTimeZone(created_at, '%s')) m, sum(amount) FROM orders GROUP BY m" % NY, agg=["m:%s.revenue" % R], by=[RT + "@month"], zone=NY)
    S("r/kathmandu_week", R, "SELECT toStartOfWeek(toTimeZone(created_at, '%s'), 0) w, uniqExact(user_id) FROM orders WHERE created_at >= toDateTime('2025-03-01 00:00:00', '%s') AND created_at < toDateTime('2025-04-01 00:00:00', '%s') GROUP BY w" % (KT, KT, KT),
      agg=["m:%s.customers" % R], by=[RT + "@week"], where=[[">=", RT, "2025-03-01T00:00:00"], ["<", RT, "2025-04-01T00:00:00"]], zone=KT)
    S("r/text_contains_status", R, "SELECT sum(amount) FROM orders WHERE position(status, 'plet') > 0", agg=["m:%s.revenue" % R], where=[["contains", "d:%s.status" % R, "plet"]])
    S("r/mtd_by_day", R, "SELECT d, (SELECT sum(amount) FROM orders WHERE created_at >= '2025-02-01' AND toDate(created_at) <= d) FROM (SELECT toDate('2025-02-28') d)", agg=["m:%s.revenue_mtd" % R], by=[RT + "@day"], where=[["=", RT + "@day", "2025-02-28"]])
    S("r/30d_by_day", R, "SELECT toDate('2025-03-31'), sum(amount) FROM orders WHERE toDate(created_at) > toDate('2025-03-31') - 30 AND toDate(created_at) <= '2025-03-31'", agg=["m:%s.revenue_30d" % R], by=[RT + "@day"], where=[["=", RT + "@day", "2025-03-31"]])
    S("r/1m_by_month", R, "SELECT toDate('2025-03-01'), sum(amount) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-04-01'", agg=["m:%s.revenue_1m" % R], by=[RT + "@month"], where=[["=", RT + "@month", "2025-03-01"]])
    S("r/running_month", R, "SELECT toDate('2025-03-01'), sum(amount) FROM orders WHERE created_at < '2025-04-01'", agg=["m:%s.revenue_running" % R], by=[RT + "@month"], where=[["=", RT + "@month", "2025-03-01"]])
    S("r/lambda_all", "lambda_sales", "SELECT toStartOfMonth(created_at) m, sum(amount) FROM orders GROUP BY m", agg=["m:lambda_sales.revenue"], by=["d:lambda_sales.created_at@month"])
    S("r/lambda_after_batch", "lambda_sales", "SELECT sum(amount) FROM orders WHERE toStartOfMonth(created_at) = '2025-08-01'", agg=["m:lambda_sales.revenue"], where=[["=", "d:lambda_sales.created_at@month", "2025-08-01"]])
    S("r/lambda_across_edge", "lambda_sales", "SELECT sum(amount) FROM orders WHERE toDate(created_at) BETWEEN '2025-06-25' AND '2025-07-05'", agg=["m:lambda_sales.revenue"], where=[["between", "d:lambda_sales.created_at@day", "2025-06-25", "2025-07-05"]])
    S("r/lambda_status_ny", "lambda_sales", "SELECT toString(status), sum(amount) FROM orders WHERE toStartOfQuarter(toTimeZone(created_at, '%s')) = '2025-04-01' GROUP BY 1" % NY, agg=["m:lambda_sales.revenue"], by=["d:lambda_sales.status"], where=[["=", "d:lambda_sales.created_at@quarter", "2025-04-01"]], zone=NY)
    S("r/rollup_join", "join_sales", "SELECT u.state, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1", agg=["m:join_sales.revenue"], by=["d:join_sales>join_customers.state"])
    S("r/rollup_join_status_state", "join_sales", "SELECT toString(o.status), u.state, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id WHERE o.status = 'shipped' GROUP BY 1, 2", agg=["m:join_sales.revenue"], by=["d:join_sales.status", "d:join_sales>join_customers.state"], where=[["=", "d:join_sales.status", "shipped"]])
    S("r/range_all", "pa_partitions", "SELECT sum(amount) FROM orders", agg=["m:pa_partitions.rev_range"])
    S("r/range_stage_card", "pa_partitions", "SELECT sumOrNull(v) FROM (SELECT toStartOfMonth(created_at) b, sum(amount) v FROM orders GROUP BY b)", agg=["m:pa_partitions.rev_range"], by=["d:pa_partitions.created_at@month"], outer=dict(kind="sum"), via="card")
    S("r/range_breakout_status", "pa_partitions", "SELECT toString(status), sum(amount) FROM orders GROUP BY 1", agg=["m:pa_partitions.rev_range"], by=["d:pa_partitions.status"])
    S("r/hour_partition_day", "pa_partitions", "SELECT sum(amount) FROM orders WHERE toDate(created_at) = '2025-01-02'", agg=["m:pa_partitions.rev_hour"], where=[["=", "d:pa_partitions.created_at@day", "2025-01-02"]])
    S("r/hour_partition_out", "pa_partitions", "SELECT sum(amount) FROM orders WHERE toDate(created_at) = '2025-02-02'", agg=["m:pa_partitions.rev_hour"], where=[["=", "d:pa_partitions.created_at@day", "2025-02-02"]])
    S("r/day_partition_all", "pa_partitions", "SELECT sum(amount) FROM orders", agg=["m:pa_partitions.rev_day"])
    S("r/mid_range_bucket", "pa_range_mid", "SELECT toStartOfMonth(created_at) m, sum(amount) FROM orders GROUP BY m", agg=["m:pa_range_mid.revenue"], by=["d:pa_range_mid.created_at@month"])
    S("r/mid_range_march", "pa_range_mid", "SELECT toDate('2025-03-01'), sum(amount) FROM orders WHERE toStartOfMonth(created_at) = '2025-03-01'", agg=["m:pa_range_mid.revenue"], by=["d:pa_range_mid.created_at@month"], where=[["=", "d:pa_range_mid.created_at@month", "2025-03-01"]])
    S("r/week_only_by_month", "pa_week", "SELECT toStartOfMonth(created_at) m, sum(amount) FROM orders GROUP BY m", agg=["m:pa_week.revenue"], by=["d:pa_week.created_at@month"])
    S("r/grain_second", "pa_grain", "SELECT sumOrNull(amount) FROM orders WHERE created_at BETWEEN '2025-03-09 05:00:00' AND '2025-03-09 05:59:59'", agg=["m:pa_grain.revenue"], where=[["between", "d:pa_grain.created_at", "2025-03-09T05:00:00", "2025-03-09T05:59:59"]])
    S("r/segment_rollup", "pa_types", "SELECT toString(status), max(amount) FROM orders WHERE %s GROUP BY 1" % PAID, agg=["m:pa_types.peak_paid"], by=["d:pa_types.status"], where=[["segment", "s:pa_types.paid_orders"]])
    S("r/empty_text_group", "text_edges", "SELECT label, sum(amount) FROM text_edges GROUP BY label", agg=["m:text_edges.total"], by=["d:text_edges.label"])
    S("r/empty_text_filter", "text_edges", "SELECT sum(amount) FROM text_edges WHERE label = ''", agg=["m:text_edges.total"], where=[["=", "d:text_edges.label", ""]])
    S("r/is_empty_text", "text_edges", "SELECT sum(amount) FROM text_edges WHERE label = '' OR label IS NULL", agg=["m:text_edges.total"], where=[["is-empty", "d:text_edges.label"]])
    S("r/null_text_filter", "text_edges", "SELECT sum(amount) FROM text_edges WHERE label IS NULL", agg=["m:text_edges.total"], where=[["is-null", "d:text_edges.label"]])

    # New York day: each form of the day, on the two clock changes and at midnight, with and without a report zone.
    forms = {"ny_a": "toDate(toTimeZone(ts, '%s'))" % NY, "ny_a64": "toDate(toTimeZone(ts64, '%s'))" % NY, "ny_b": "toDate(ts_ny)", "ny_c": "ny_day", "ny_cs": "toDate(ny_day_s)", "ny_d": "toDate(ts, '%s')" % NY}
    for form, day in forms.items():
        tbl = form
        at = "d:%s.at" % form
        dayd = "d:%s.day_at" % form
        for zone in (None, NY):
            zs = "ny" if zone else "utc"
            S("z/%s/%s/dst_march" % (form, zs), tbl, "SELECT %s AS d, sum(amount) FROM zone_events WHERE d BETWEEN '2025-03-08' AND '2025-03-10' GROUP BY d" % day, agg=["m:%s.m_revenue" % tbl], by=[dayd + "@day"], where=[["between", dayd + "@day", "2025-03-08", "2025-03-10"]], zone=zone)
            S("z/%s/%s/dst_nov_count" % (form, zs), tbl, "SELECT %s AS d, count() FROM zone_events WHERE d = '2025-11-02' GROUP BY d" % day, agg=["m:%s.m_count" % tbl], by=[dayd + "@day"], where=[["=", dayd + "@day", "2025-11-02"]], zone=zone)
            S("z/%s/%s/month" % (form, zs), tbl, "SELECT toStartOfMonth(%s) AS d, uniqExact(user_id) FROM zone_events GROUP BY d" % day, agg=["m:%s.m_users" % tbl], by=[dayd + "@month"], zone=zone)
            S("z/%s/%s/dow" % (form, zs), tbl, "SELECT toDayOfWeek(%s) %% 7 + 1 AS d, count() FROM zone_events GROUP BY d" % day, agg=["m:%s.m_count" % tbl], by=[dayd + "@day-of-week"], zone=zone)
            S("z/%s/%s/text_day" % (form, zs), tbl, "SELECT toString(%s) AS d, sum(amount) FROM zone_events WHERE d = '2025-03-09' GROUP BY d" % day, agg=["m:%s.m_revenue" % tbl], by=["d:%s.plain_day" % tbl], where=[["=", "d:%s.plain_day" % tbl, "2025-03-09"]], zone=zone)
            S("z/%s/%s/ratio_day" % (form, zs), tbl, "SELECT 1.0 * sum(amount) / uniqExact(user_id) FROM zone_events WHERE %s = '2025-07-04'" % day, agg=["m:%s.c_revenue_per_customer" % tbl], where=[["=", dayd + "@day", "2025-07-04"]], zone=zone, tol=1e-6)
            S("z/%s/%s/week_card" % (form, zs), tbl, "SELECT sumOrNull(v) FROM (SELECT toStartOfWeek(%s, 0) b, sum(amount) v FROM zone_events WHERE %s BETWEEN '2025-03-02' AND '2025-03-22' GROUP BY b)" % (day, day), agg=["m:%s.m_revenue" % tbl], by=[dayd + "@week"], where=[["between", dayd + "@day", "2025-03-02", "2025-03-22"]], outer=dict(kind="sum"), via="card", zone=zone)

    # Time columns with a report zone: hour and second edges at the clock change, raw values.
    Z, ZT = "ny_a", "d:ny_a.at"
    S("z/clock/hour_buckets", Z, "SELECT toStartOfHour(toTimeZone(ts, '%s')) h, count() FROM zone_events WHERE ts >= toDateTime('2025-11-02 04:00:00', 'UTC') AND ts < toDateTime('2025-11-02 08:00:00', 'UTC') GROUP BY h" % NY,
      agg=["m:ny_a.m_count"], by=[ZT + "@hour"], where=[[">=", ZT, "2025-11-02T00:00:00"], ["<", ZT, "2025-11-02T03:00:00"]], zone=NY, loose=True)
    S("z/clock/hour_of_day", Z, "SELECT toHour(toTimeZone(ts, '%s')) h, count() FROM zone_events GROUP BY h" % NY, agg=["m:ny_a.m_count"], by=[ZT + "@hour-of-day"], zone=NY)
    S("z/clock/raw", Z, "SELECT id, toTimeZone(ts, '%s') FROM zone_events ORDER BY id LIMIT 4" % NY, fields=["d:ny_a.id", ZT], order=[["asc", "d:ny_a.id"]], limit=4, zone=NY, ordered=True)
    S("z/clock/midnight_eq", Z, "SELECT count() FROM zone_events WHERE ts = toDateTime('2025-03-09 00:00:00', '%s')" % NY, agg=[["count"]], where=[["=", ZT, "2025-03-09T00:00:00"]], zone=NY)
    S("z/clock/gt_midnight", Z, "SELECT count() FROM zone_events WHERE ts > toDateTime('2025-03-09 00:00:00', '%s')" % NY, agg=[["count"]], where=[[">", ZT, "2025-03-09T00:00:00"]], zone=NY)
    S("z/clock/not_gt", Z, "SELECT count() FROM zone_events WHERE NOT ts > toDateTime('2025-03-09 00:00:00', '%s')" % NY, agg=[["count"]], where=[["not", [">", ZT, "2025-03-09T00:00:00"]]], zone=NY)
    S("z/clock/time_measure_a", "ny_a", "SELECT max(toTimeZone(ts, '%s')) FROM zone_events" % NY, agg=["m:ny_a.m_last_at"], zone=NY)
    S("z/clock/time_measure_local", "ny_a", "SELECT max(toTimeZone(ts, '%s')) FROM zone_events" % NY, agg=["m:ny_a.m_last_local"], zone=NY)
    S("z/clock/time_measure_b", "ny_b", "SELECT max(ts_ny) FROM zone_events", agg=["m:ny_b.m_last_at"], zone=NY)
    S("z/clock/time_measure_a64", "ny_a64", "SELECT max(toTimeZone(ts, '%s')) FROM zone_events" % NY, agg=["m:ny_a64.m_last_at"], zone=NY)
    S("z/clock/ts64_ms_raw", "ny_a64", "SELECT id, toTimeZone(ts64, 'UTC') FROM zone_events ORDER BY id LIMIT 3", fields=["d:ny_a64.id", "d:ny_a64.at"], order=[["asc", "d:ny_a64.id"]], limit=3, ordered=True)
    S("z/clock/ts64_last_second", "ny_a64", "SELECT count() FROM zone_events WHERE ts64 >= toDateTime64('2025-03-08 00:00:00', 3, '%s') AND ts64 < toDateTime64('2025-03-09 00:00:00', 3, '%s')" % (NY, NY), agg=[["count"]],
      where=[[">=", "d:ny_a64.at", "2025-03-08T00:00:00"], ["<", "d:ny_a64.at", "2025-03-09T00:00:00"]], zone=NY)
    S("z/clock/ts64_day_ny", "ny_a64", "SELECT toDate(toTimeZone(ts64, '%s')) d, sum(amount) FROM zone_events WHERE d = '2025-03-09' GROUP BY d" % NY, agg=["m:ny_a64.m_revenue"], by=["d:ny_a64.at@day"], where=[["=", "d:ny_a64.at@day", "2025-03-09"]], zone=NY)
    S("z/clock/week_sunday_ny", "ny_a", "SELECT toStartOfWeek(toTimeZone(ts, '%s'), 0) w, count() FROM zone_events WHERE toDate(toTimeZone(ts, '%s')) BETWEEN '2025-03-02' AND '2025-03-15' GROUP BY w" % (NY, NY), agg=["m:ny_a.m_count"], by=["d:ny_a.at@week"], where=[["between", "d:ny_a.at@day", "2025-03-02", "2025-03-15"]], zone=NY)
    S("z/clock/week_monday_ny", "ny_a", "SELECT toStartOfWeek(toTimeZone(ts, '%s'), 1) w, count() FROM zone_events WHERE toDate(toTimeZone(ts, '%s')) BETWEEN '2025-03-03' AND '2025-03-16' GROUP BY w" % (NY, NY), agg=["m:ny_a.m_count"], by=["d:ny_a.at@week"], where=[["between", "d:ny_a.at@day", "2025-03-03", "2025-03-16"]], zone=NY, week="monday")
    S("z/clock/week_wed_day_of_week", "orders", "SELECT ((toDayOfWeek(created_at) + 4) % 7) + 1 AS k, count() FROM orders GROUP BY k", agg=[["count"]], by=[AT + "@day-of-week"], week="wednesday")
    S("z/clock/week_sat_week", "orders", "SELECT toDate(created_at) - toDayOfWeek(toDate(created_at) + 1) % 7 AS w, count() FROM orders WHERE toDate(created_at) < '2025-02-01' GROUP BY w", agg=[["count"]], by=[AT + "@week"], where=[["<", AT + "@day", "2025-02-01"]], week="saturday")

    # Numbers at the edge.
    T = "typed_events"
    S("e/sum_ui64_refused_or_exact", T, "SELECT sum(ui64) FROM typed_events", agg=["m:%s.sum_ui64" % T], exact=True)
    S("e/raw_ui64_refused_or_exact", T, "SELECT id, ui64 FROM typed_events ORDER BY id LIMIT 2", fields=["d:%s.id" % T, "d:%s.ui64" % T], order=[["asc", "d:%s.id" % T]], limit=2, ordered=True, exact=True)
    S("e/ui64_text", T, "SELECT id, toString(ui64) FROM typed_events ORDER BY id LIMIT 2", fields=["d:%s.id" % T, "d:%s.ui64_text" % T], order=[["asc", "d:%s.id" % T]], limit=2, ordered=True)
    S("e/sum_big_refused_or_exact", T, "SELECT sum(dec_big) FROM typed_events", agg=["m:%s.sum_big" % T], exact=True)
    S("e/sum_dec", T, "SELECT sum(dec_amount) FROM typed_events", agg=["m:%s.sum_dec" % T])
    S("e/null_group", T, "SELECT n_str, count() FROM typed_events GROUP BY n_str", agg=[["count"]], by=["d:%s.n_str" % T])
    S("e/null_bool_group", T, "SELECT n_bool, count() FROM typed_events GROUP BY n_bool", agg=[["count"]], by=["d:%s.n_bool" % T], loose=True)
    S("e/null_time_month", T, "SELECT toStartOfMonth(n_ts) m, count() FROM typed_events GROUP BY m", agg=[["count"]], by=["d:%s.n_ts@month" % T])
    S("e/null_int_avg", T, "SELECT avg(n_int) FROM typed_events", agg=["m:%s.avg_n_int" % T], tol=1e-6)
    S("e/null_int_ne", T, "SELECT count() FROM typed_events WHERE n_int != 5 OR n_int IS NULL", agg=[["count"]], where=[["!=", "d:%s.n_int" % T, 5]])
    S("e/enum_filter", T, "SELECT count() FROM typed_events WHERE en = 'red'", agg=[["count"]], where=[["=", "d:%s.en" % T, "red"]])
    S("e/uuid_prefix", T, "SELECT count() FROM typed_events WHERE startsWith(toString(uid), 'a')", agg=[["count"]], where=[["starts-with", "d:%s.uid" % T, "a"]])
    S("e/has_t1", T, "SELECT count() FROM typed_events WHERE has(tags, 't1')", agg=[["count"]], where=[["=", "d:%s.has_t1" % T, True]])
    S("e/float32_sum_filter", T, "SELECT count() FROM typed_events WHERE f32 > 10.5", agg=[["count"]], where=[[">", "d:%s.f32" % T, 10.5]])
    S("e/date_only_day", T, "SELECT day, count() FROM typed_events WHERE day = '2025-03-09' GROUP BY day", agg=[["count"]], by=["d:%s.day@day" % T], where=[["=", "d:%s.day@day" % T, "2025-03-09"]])
    S("e/date_only_ny", T, "SELECT day, count() FROM typed_events WHERE day = '2025-03-09' GROUP BY day", agg=[["count"]], by=["d:%s.day@day" % T], where=[["=", "d:%s.day@day" % T, "2025-03-09"]], zone=NY)
    S("e/date32_month_ny", T, "SELECT toStartOfMonth(day32) m, count() FROM typed_events GROUP BY m", agg=[["count"]], by=["d:%s.day32@month" % T], zone=NY)
    S("e/null_keys_count_rows", "null_keys", "SELECT grp, count() FROM null_keys GROUP BY grp", agg=[["count"]], by=["d:null_keys.grp"])
    S("e/null_keys_total", "null_keys", "SELECT grp, sum(amount) FROM null_keys GROUP BY grp", agg=["m:null_keys.total"], by=["d:null_keys.grp"])
    S("e/null_keys_measure_refused", "null_keys", "SELECT count(id) FROM null_keys", agg=["m:null_keys.n"], refuse=True)
    S("e/null_keys_group_id", "null_keys", "SELECT id, sum(amount) FROM null_keys GROUP BY id", agg=["m:null_keys.total"], by=["d:null_keys.id"])
    S("e/null_keys_raw", "null_keys", "SELECT id, grp FROM null_keys ORDER BY ts", fields=["d:null_keys.id", "d:null_keys.grp", "d:null_keys.ts"], order=[["asc", "d:null_keys.ts"]], ordered=True, loose=True)
    S("e/stock_items_by_code", "stock_items", "SELECT concat('W', toString(number % 3 + 1)), sum(number + 1) FROM numbers(30) GROUP BY 1", agg=["m:stock_items.quantity"], by=["d:stock_items>warehouses.code"])


grid()
by_hand()
