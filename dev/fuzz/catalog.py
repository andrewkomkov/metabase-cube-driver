"""What the generator and the oracle know about the demo data. It is written by hand from dev/clickhouse/*.sql and dev/cube/model/*.yml, and it reads no driver code.

A source has one wide FROM of ClickHouse, a map of columns and a map of measures. Each SQL text reads the raw tables, so the truth does not pass through Cube or Metabase.
A column has `sql`, a `kind` (str, int, num, bool, time, date) and `vals`, the boundary values that a filter can pick. `via` names the source of the foreign key of an implicit join.
A measure has `sql` (an aggregate on the same FROM), a `kind`, and optional `tol`, `row` (the value of one row, so that a later stage can add it up), `total` and `window`.
"""

STATUSES = ["completed", "shipped", "processing", "cancelled"]
EDGE_TEXT = ["", "no such", "COMPLETED", "o'brien", "a\\b", "ünï", "%", "_"]
PAID = "o.status IN ('completed','shipped')"
BOOLS = [True, False]


def col(sql, kind, vals=None, nullable=False, ref=None, via=None, group=True):
    return {"sql": sql, "kind": kind, "vals": vals or [], "nullable": nullable, "ref": ref, "via": via, "group": group}


def meas(sql, kind="num", **kw):
    return dict({"sql": sql, "kind": kind}, **kw)


ORDER_TEXT = STATUSES + EDGE_TEXT
QTY = [0, 1, 2, 3, 4, 5, -1, 2147483647]
AMT = [0, 11, 44, 100, 160, 163.99, 164, 0.01, -5, 1000000]
IDS = [0, 1, 2, 2500, 4999, 5000, 5001, -1]

ORDER_COLS = {
    "id": col("o.id", "int", IDS),
    "user_id": col("o.user_id", "int", [1, 2, 199, 200, 201]),
    "product_id": col("o.product_id", "int", [1, 29, 30, 31]),
    "status": col("toString(o.status)", "str", ORDER_TEXT),
    "size": col("if(o.quantity >= 3, 'large', 'small')", "str", ["large", "small", "medium", ""]),
    "quantity_dim": col("o.quantity", "int", QTY),
    "amount_dim": col("o.amount", "num", AMT),
    "is_paid": col("toBool(%s)" % PAID, "bool", BOOLS),
    "cancel_status": col("nullIf(toString(o.status), 'completed')", "str", ORDER_TEXT, nullable=True),
    "status_sorted": col("toString(o.status)", "str", ORDER_TEXT),
    "amount_pct": col("o.amount / 1000", "num", [0.044, 0.1, 0]),
    "created_at": col("o.created_at", "time"),
    "created_label": col("o.created_at", "time"),
}

SIGNUP = "if(u.created_at < toDateTime('2024-04-01 00:00:00', 'UTC'), 'Q1', if(u.created_at < toDateTime('2024-07-01 00:00:00', 'UTC'), 'Q2', 'later'))"
USER_COLS = {
    "id": col("u.id", "int", [0, 1, 200, 201]),
    "name": col("u.name", "str", ["User 1", "User 200", "user 1", ""]),
    "email": col("u.email", "str", ["user1@example.com", ""]),
    "city": col("u.city", "str", ["Austin", "Chicago", "Seattle", "Boston", "austin", ""]),
    "state": col("u.state", "str", ["TX", "IL", "WA", "MA", "tx"]),
    "is_vip": col("toBool(u.is_vip)", "bool", BOOLS),
    "created_at": col("u.created_at", "time"),
    "signup_cohort": col(SIGNUP, "str", ["Q1", "Q2", "later", "q1"]),
}
PRODUCT_COLS = {
    "id": col("p.id", "int", [0, 1, 30, 31]),
    "name": col("p.name", "str", ["Product 1", "Product 30", ""]),
    "category": col("p.category", "str", ["Gadget", "Widget", "Gizmo", "gadget", ""]),
    "price": col("p.price", "num", [11, 20, 40, 40.01, 0]),
}


def via(cols, fk, names):
    return {"%s.%s" % (fk, n): dict(cols[n], via=fk) for n in names}


ORDER_MEASURES = {
    "count": meas("count()", "int", row="1"),
    "revenue": meas("sumOrNull(o.amount)", row="o.amount"),
    "avg_order_value": meas("avgOrNull(toFloat64(o.amount))"),
    "min_amount": meas("minOrNull(o.amount)"),
    "max_amount": meas("maxOrNull(o.amount)"),
    "unique_customers": meas("uniqExact(o.user_id)", "int"),
    "approx_customers": meas("uniq(o.user_id)", "int", tol=0.05),
    "paid_revenue": meas("sumOrNull(if(%s, o.amount, NULL))" % PAID),
    "paid_share": meas("toFloat64(sumOrNull(if(%s, o.amount, NULL))) / nullIf(toFloat64(sumOrNull(o.amount)), 0)" % PAID),
    "first_order_at": meas("minOrNull(o.created_at)", "time"),
    "last_order_at": meas("maxOrNull(o.created_at)", "time"),
    "has_orders": meas("toBool(count() > 0)", "bool"),
    "statuses": meas("arrayStringConcat(arraySort(groupUniqArray(toString(o.status))), ',')", "str"),
    "revenue_abbr": meas("sumOrNull(o.amount)"),
    "revenue_meta": meas("sumOrNull(o.amount)"),
    "revenue_grand_total": meas("sumOrNull(o.amount)", total=True),
    "revenue_share_of_total": meas("toFloat64(sumOrNull(o.amount)) / nullIf(toFloat64(%(total)s), 0)", total="sumOrNull(o.amount)"),
    "revenue_mtd": meas("sumOrNull(%s)", window={"from": "toStartOfMonth(e)", "to": "t <= e", "size": 1}),
    "revenue_ytd": meas("sumOrNull(%s)", window={"from": "toStartOfYear(e)", "to": "t <= e", "size": 12}),
    "revenue_trailing_30d": meas("sumOrNull(%s)", window={"from": "addDays(e, -30)", "to": "t <= e", "size": 1}),
    "revenue_leading_7d": meas("sumOrNull(%s)", window={"from": "toDateTime(b, 'UTC')", "to": "t < addDays(toDateTime(b, 'UTC'), 7)", "size": 0}),
    "cumulative_count": meas("countIf(%s)", window={"from": None, "to": "t <= e", "size": 99}),
    "revenue_prior_month": meas("sumOrNull(o.amount)", shift=1),
    "revenue_next_month": meas("sumOrNull(o.amount)", shift=-1),
}

METRIC_MEASURES = {
    "m_count": meas("count()", "int", row="1"),
    "m_users": meas("uniqExact(o.user_id)", "int"),
    "m_users_approx": meas("uniq(o.user_id)", "int", tol=0.05),
    "m_revenue": meas("sumOrNull(o.amount)", row="o.amount"),
    "m_avg_amount": meas("avgOrNull(toFloat64(o.amount))"),
    "m_min_amount": meas("minOrNull(o.amount)"),
    "m_max_amount": meas("maxOrNull(o.amount)"),
    "m_quantity_sum": meas("sumOrNull(o.quantity)", row="o.quantity"),
    "m_avg_quantity": meas("avgOrNull(toFloat64(o.quantity))"),
    "m_ratio": meas("toFloat64(sumOrNull(o.amount)) / nullIf(toFloat64(count()), 0)"),
    "m_status_text": meas("minOrNull(toString(o.status))", "str"),
    "m_last_order_at": meas("maxOrNull(o.created_at)", "time"),
    "m_has_completed": meas("toBool(maxOrNull(o.status = 'completed'))", "bool"),
    "m_count_completed": meas("countIf(o.status = 'completed')", "int"),
    "m_count_large_completed": meas("countIf(o.status = 'completed' AND o.quantity >= 3)", "int"),
    "m_count_completed_or_large": meas("countIf(o.status = 'completed' OR o.quantity >= 3)", "int"),
    "m_paid_revenue": meas("sumOrNull(if(%s, o.amount, NULL))" % PAID),
    "m_paid_january": meas("countIf(%s AND o.created_at < toDateTime('2025-02-01 00:00:00', 'UTC'))" % PAID, "int"),
    "m_rev_grand_total": meas("sumOrNull(o.amount)", total=True),
    "c_share_of_total": meas("toFloat64(sumOrNull(o.amount)) / nullIf(toFloat64(%(total)s), 0)", total="sumOrNull(o.amount)"),
    "m_rev_mtd": meas("sumOrNull(%s)", window={"from": "toStartOfMonth(e)", "to": "t <= e", "size": 1}),
    "m_rev_ytd": meas("sumOrNull(%s)", window={"from": "toStartOfYear(e)", "to": "t <= e", "size": 12}),
    "m_rev_prior_month": meas("sumOrNull(o.amount)", shift=1),
    "m_fmt_percent": meas("toFloat64(sumOrNull(if(%s, o.amount, NULL))) / nullIf(toFloat64(sumOrNull(o.amount)), 0)" % PAID),
    "m_fmt_currency": meas("sumOrNull(o.amount)"),
    "m_fmt_id": meas("maxOrNull(o.id)", "int"),
    "b_aov_ratio": meas("toFloat64(sumOrNull(o.amount)) / nullIf(toFloat64(count()), 0)"),
    "b_median": meas("quantile(0.5)(toFloat64(o.amount))", tol=1e-6),
    "b_p90": meas("quantile(0.9)(toFloat64(o.amount))", tol=1e-6),
    "b_amount_x_quantity": meas("sumOrNull(o.amount * o.quantity)"),
    "b_weighted_amount": meas("toFloat64(sumOrNull(o.amount * o.quantity)) / nullIf(toFloat64(sumOrNull(o.quantity)), 0)"),
    "c_ratio_of_sums": meas("toFloat64(sumOrNull(o.amount)) / nullIf(toFloat64(sumOrNull(o.quantity)), 0)"),
    "c_sum_of_row_ratios": meas("sumOrNull(toFloat64(o.amount) / nullIf(toFloat64(o.quantity), 0))"),
    "c_avg_of_ratio": meas("avgOrNull(toFloat64(o.amount) / nullIf(toFloat64(o.quantity), 0))"),
    "c_ratio_of_avgs": meas("avgOrNull(toFloat64(o.amount)) / nullIf(avgOrNull(toFloat64(o.quantity)), 0)"),
    "c_paid_share": meas("toFloat64(sumOrNull(if(%s, o.amount, NULL))) / nullIf(toFloat64(sumOrNull(o.amount)), 0)" % PAID),
    "c_customers_per_order": meas("toFloat64(uniqExact(o.user_id)) / nullIf(toFloat64(count()), 0)"),
    "c_revenue_per_customer": meas("toFloat64(sumOrNull(o.amount)) / nullIf(toFloat64(uniqExact(o.user_id)), 0)"),
    "c_cost": meas("sumOrNull(o.quantity * 10)"),
    "c_margin": meas("(toFloat64(sumOrNull(o.amount)) - toFloat64(sumOrNull(o.quantity * 10))) / nullIf(toFloat64(sumOrNull(o.quantity * 10)), 0)"),
    "c_product": meas("avgOrNull(toFloat64(o.quantity)) * count()"),
}

JOINED = "shop.orders o LEFT JOIN shop.users u ON o.user_id = u.id LEFT JOIN shop.products p ON o.product_id = p.id"
TYPED_TEXT = ["s0", "s1", "s4", "s9", "alpha", "beta", "gamma", "low", "high", "red", "green", "", "t0,t1", "t0"]
TYPED_INT = [0, 1, 5, 9, 10, 1000, 1001, -1]
TYPED_NUM = [0, 0.125, 0.25, 12.5, 12.50001, 62.5, 125, 250, 249.75, -1]

SOURCES = {
    "orders": {
        "weight": 30, "from": JOINED, "table": "orders", "rows": 5000,
        "cols": dict(ORDER_COLS, **via(USER_COLS, "users", ["city", "state", "is_vip", "created_at", "signup_cohort", "name"]), **via(PRODUCT_COLS, "products", ["category", "name", "price"])),
        "measures": ORDER_MEASURES,
        "segments": {"paid": PAID, "large": "o.quantity >= 3"},
        "joins": {"users": {"on": "user_id", "cols": USER_COLS, "table": "shop.users"}, "products": {"on": "product_id", "cols": PRODUCT_COLS, "table": "shop.products"}},
    },
    "metric_types": {
        "weight": 16, "from": JOINED, "table": "metric_types", "rows": 5000,
        "cols": {
            "id": ORDER_COLS["id"], "status": ORDER_COLS["status"], "quantity": ORDER_COLS["quantity_dim"], "amount": ORDER_COLS["amount_dim"], "user_id": ORDER_COLS["user_id"],
            "product_id": ORDER_COLS["product_id"], "is_paid": ORDER_COLS["is_paid"], "created_at": ORDER_COLS["created_at"], "size": ORDER_COLS["size"],
        },
        "measures": METRIC_MEASURES,
    },
    "sales": {
        "weight": 10, "from": JOINED, "table": "sales", "rows": 5000,
        "cols": {"id": ORDER_COLS["id"], "status": ORDER_COLS["status"], "created_at": ORDER_COLS["created_at"], "users_city": USER_COLS["city"], "users_state": USER_COLS["state"],
                 "users_is_vip": USER_COLS["is_vip"], "products_category": PRODUCT_COLS["category"], "products_name": PRODUCT_COLS["name"]},
        "measures": {"count": ORDER_MEASURES["count"], "revenue": ORDER_MEASURES["revenue"], "avg_order_value": ORDER_MEASURES["avg_order_value"],
                     "paid_share": ORDER_MEASURES["paid_share"], "users_count": meas("uniqExact(u.id)", "int")},
    },
    "paid_sales": {
        "weight": 6, "from": JOINED, "table": "paid_sales", "rows": 2000, "always": "o.status = 'completed'",
        "cols": {"status": ORDER_COLS["status"], "created_at": ORDER_COLS["created_at"], "users_city": USER_COLS["city"], "users_state": USER_COLS["state"], "users_is_vip": USER_COLS["is_vip"]},
        "measures": {"count": ORDER_MEASURES["count"], "net_sales": ORDER_MEASURES["revenue"], "users_count": meas("uniqExact(u.id)", "int"),
                     "users_vip_count": meas("uniqExactIf(u.id, u.is_vip)", "int")},
    },
    "users": {
        "weight": 5, "from": "shop.users u", "table": "users", "rows": 200,
        "cols": dict(USER_COLS),
        "measures": {"count": meas("count()", "int", row="1"), "vip_count": meas("countIf(u.is_vip)", "int")},
        "segments": {"vip": "u.is_vip", "texans": "u.state = 'TX'"},
    },
    "products": {
        "weight": 3, "from": "shop.products p", "table": "products", "rows": 30,
        "cols": dict(PRODUCT_COLS),
        "measures": {"count": meas("count()", "int", row="1"), "avg_price": meas("avgOrNull(toFloat64(p.price))")},
    },
    "typed_events": {
        "weight": 16, "from": "shop.typed_events t", "table": "typed_events", "rows": 1000,
        "cols": {
            "id": col("t.id", "int", TYPED_INT),
            "dec_amount": col("t.dec_amount", "num", TYPED_NUM),
            "f32": col("toFloat64(t.f32)", "num", TYPED_NUM),
            "ts_utc": col("t.ts_utc", "time"),
            "ts_ny": col("t.ts_ny", "time"),
            "ts64_berlin": col("t.ts64_berlin", "time"),
            "day": col("t.day", "date"),
            "day32": col("t.day32", "date"),
            "n_int": col("t.n_int", "int", TYPED_INT, nullable=True),
            "n_str": col("t.n_str", "str", TYPED_TEXT, nullable=True),
            "n_dec": col("t.n_dec", "num", TYPED_NUM, nullable=True),
            "n_ts": col("t.n_ts", "time", nullable=True),
            "n_bool": col("t.n_bool", "bool", BOOLS, nullable=True),
            "lc": col("toString(t.lc)", "str", TYPED_TEXT),
            "lc_n": col("toNullable(toString(t.lc_n))", "str", TYPED_TEXT, nullable=True),
            "flag": col("t.flag", "bool", BOOLS),
            "en": col("toString(t.en)", "str", TYPED_TEXT),
            "tags_text": col("arrayStringConcat(t.tags, ',')", "str", TYPED_TEXT),
            "nums_len": col("toInt64(length(t.nums))", "int", [0, 1, 3, 4]),
            "has_t1": col("toBool(has(t.tags, 't1'))", "bool", BOOLS),
        },
        "measures": {
            "count": meas("count()", "int", row="1"),
            "sum_dec": meas("sumOrNull(t.dec_amount)", row="t.dec_amount"),
            "avg_n_int": meas("avgOrNull(t.n_int)"),
            "sum_n_dec": meas("sumOrNull(t.n_dec)"),
            "distinct_lc": meas("uniqExact(toString(t.lc))", "int"),
            "distinct_n_str": meas("uniqExact(t.n_str)", "int"),
            "tags_total": meas("sumOrNull(length(t.tags))"),
        },
    },
    "metric_edges": {
        "weight": 6, "from": "shop.metric_edges e", "table": "metric_edges", "rows": 22,
        "cols": {"id": col("e.id", "int", [0, 1, 22, 23]), "grp": col("toString(e.grp)", "str", ["plain", "zero_den", "null_den", "null_num", "neg_num", "cancels", "mixed", "PLAIN", ""]), "day": col("e.day", "time")},
        "measures": {
            "rows": meas("count()", "int", row="1"),
            "num": meas("sumOrNull(e.num)"),
            "den": meas("sumOrNull(e.den)"),
            "avg_num": meas("avgOrNull(e.num)"),
            "min_den": meas("minOrNull(e.den)"),
            "max_den": meas("maxOrNull(e.den)"),
            "ratio": meas("sumOrNull(e.num) / nullIf(sumOrNull(e.den), 0)"),
            "sum_of_ratios": meas("sumOrNull(e.num / nullIf(e.den, 0))"),
            "avg_of_ratios": meas("avgOrNull(e.num / nullIf(e.den, 0))"),
            "change": meas("(sumOrNull(e.num) - sumOrNull(e.den)) / nullIf(sumOrNull(e.den), 0)"),
            "positive_num": meas("sumOrNull(if(e.num > 0, e.num, NULL))"),
            "positive_share": meas("sumOrNull(if(e.num > 0, e.num, NULL)) / nullIf(sumOrNull(e.num), 0)"),
        },
    },
    "orphan_orders": {
        "weight": 3, "from": "shop.orphan_orders o LEFT JOIN shop.orphan_customers c ON o.customer_id = c.id", "table": "orphan_orders", "rows": 10,
        "cols": {"id": col("o.id", "int", [0, 1, 10, 11]), "customer_id": col("o.customer_id", "int", [0, 1, 3, 4, 5]),
                 "orphan_customers.name": col("c.name", "str", ["C1", "C3", "C9", ""], nullable=True, via="orphan_customers")},
        "measures": {"count": meas("count()", "int", row="1"), "total": meas("sumOrNull(o.amount)", row="o.amount")},
        "joins": {"orphan_customers": {"on": "customer_id", "table": "shop.orphan_customers", "cols": {"name": col("c.name", "str", ["C1", "C3", "C9", ""], nullable=True)}}},
    },
    "big_orders": {
        "weight": 5, "from": "shop.big_orders b", "table": "big_orders", "rows": 10000000, "big": True,
        "cols": {"status": col("toString(b.status)", "str", ORDER_TEXT), "quantity_dim": col("b.quantity", "int", QTY), "created_at": col("b.created_at", "time")},
        "measures": {
            "count": meas("count()", "int"),
            "revenue": meas("sumOrNull(b.amount)"),
            "avg_amount": meas("avgOrNull(toFloat64(b.amount))"),
            "min_amount": meas("minOrNull(b.amount)"),
            "max_amount": meas("maxOrNull(b.amount)"),
            "unique_users": meas("uniqExact(b.user_id)", "int"),
        },
    },
}

# The zones have a clock change on each side of the equator, an offset of part of an hour, and an offset above 12 hours.
ZONES = [None, None, None, None, "America/New_York", "America/New_York", "Europe/Berlin", "Asia/Kathmandu", "Pacific/Apia", "America/St_Johns", "Australia/Sydney"]
WEEK_DAYS = [None, None, None, "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_NUMBER = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6}

DATE_VALUES = {
    "common": ["2025-01-01", "2025-03-09", "2025-03-31", "2025-04-01", "2025-06-15", "2025-11-02", "2025-12-27", "2025-12-28", "2024-12-31", "2026-01-01", "2030-01-01", "2024-02-29"],
    "typed": ["2025-03-01", "2025-03-09", "2025-03-10", "2025-03-31", "2025-04-11", "2025-04-12", "2024-12-31", "2030-01-01"],
}
TIME_VALUES = {
    "common": ["2025-01-01T00:00:00", "2025-03-09T07:00:00", "2025-06-15T11:00:00", "2025-06-15T11:00:01", "2025-11-02T05:00:00", "2025-12-27T22:00:00", "2024-12-31T23:59:59", "2026-01-01T00:00:00", "2030-01-01T00:00:00"],
    "typed": ["2025-03-01T01:00:00", "2025-03-09T06:00:00", "2025-03-09T07:00:00", "2025-03-20T10:00:00", "2025-04-11T16:00:00", "2025-04-11T15:59:59", "2030-01-01T00:00:00"],
}
TRUNC_UNITS = ["minute", "hour", "day", "week", "month", "quarter", "year"]
PART_UNITS = ["minute-of-hour", "hour-of-day", "day-of-week", "day-of-month", "day-of-year", "week-of-year", "month-of-year", "quarter-of-year"]
PART_VALUES = {"minute-of-hour": [0, 30, 59], "hour-of-day": [0, 5, 23], "day-of-week": [1, 4, 7], "day-of-month": [1, 15, 31], "day-of-year": [1, 60, 365], "week-of-year": [1, 10, 53], "month-of-year": [1, 3, 12], "quarter-of-year": [1, 4]}
