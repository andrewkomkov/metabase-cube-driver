"""The generator of the grammar of native SQL. `generate` follows the productions of `allow-list` in src/metabase/driver/cube/grammar.clj and gives each query in two texts from one tree: the text for the SQL API of Cube, and the
same meaning in direct ClickHouse SQL. It reads no driver code and no Cube output. `mutate` adds a construct that is not on the list to such a query, and the driver must refuse it and not run it. See README.md.
"""
import random

import native as base
from native import fmt, pair

ORDERS = dict(base.ORDERS)
ORDERS.update({
    "text": {"status": "toString(status)", "cancel_status": "nullIf(toString(status), 'completed')", "size": "if(quantity >= 3, 'large', 'small')"},
    "bool": {"is_paid": "status IN ('completed', 'shipped')"},
    "time": {"created_at": "created_at"},
    "measures": {"count": "count()", "revenue": "sum(amount)", "avg_order_value": "avg(amount)", "min_amount": "min(amount)", "max_amount": "max(amount)", "unique_customers": "uniqExact(user_id)",
                 "paid_revenue": "sumIf(amount, status IN ('completed', 'shipped'))"},
    "times": ["2025-03-01", "2025-03-09", "2025-03-09 12:00:00", "2025-06-15 10:30:00", "2025-11-03", "2025-01-01 01:00:00", "2026-01-01", "2024-12-31 23:59:59"],
    "group": ["status", "cancel_status", "size", "quantity_dim", "is_paid"],
    "patterns": ["%hip%", "%HIP%", "s%", "%d", "c_ncelled", "%", "complete_", "%ed", "SHIP%"],
})
TYPED = dict(base.TYPED)
TYPED.update({
    "time": {"ts_utc": "ts_utc", "ts_ny": "ts_ny", "n_ts": "n_ts", "ts64_berlin": "ts64_berlin"},
    "measures": {"count": "count()", "sum_dec": "sum(dec_amount)", "sum_n_dec": "sum(n_dec)", "distinct_lc": "uniqExact(lc)", "avg_n_int": "avg(n_int)"},
    "times": ["2025-03-09", "2025-03-09 12:00:00", "2025-03-15", "2025-03-30 12:00:00", "2025-04-01", "2025-02-28 20:00:00"],
    "patterns": ["s%", "%1", "S%", "alp%", "%PHA%", "_lpha", "%"],
})
PRODUCTS = {
    "from": "products", "ch": "products",
    "text": {"name": "name", "category": "category"},
    "number": {"id": "id", "price": "price"},
    "bool": {}, "time": {},
    "text_values": ["Gadget", "Widget", "gizmo", "Product 1", "no such", ""],
    "fractions": {"price": [11.5, 12.25], "id": [1.5]},
    "whole": {"price": [11, 25, 40], "id": [1, 15, 31]},
    "group": ["category"],
    "measures": {"count": "count()", "avg_price": "avg(price)"},
    "times": [],
    "patterns": ["Pro%", "%uct 1", "%GADGET%", "G%", "Product _"],
    "star": ["id", "name", "category", "price"],
    "order": "id",
}
USERS = {
    "from": "users", "ch": "users",
    "text": {"name": "name", "city": "city", "state": "state", "email": "email"},
    "number": {"id": "id"},
    "bool": {"is_vip": "is_vip"},
    "time": {"created_at": "created_at"},
    "text_values": ["Austin", "TX", "austin", "User 1", "no such", ""],
    "fractions": {"id": [1.5, 100.25]},
    "whole": {"id": [1, 100, 201]},
    "group": ["city", "state", "is_vip"],
    "measures": {"count": "count()", "vip_count": "countIf(is_vip)"},
    "times": ["2024-03-01", "2024-04-15 12:00:00", "2024-07-19", "2024-01-02"],
    "patterns": ["user1%", "%@example.com", "USER%", "%ust%", "Au%"],
}
PAYMENTS_CH = "(SELECT id AS payment_id, id AS order_id, 'card' AS method, amount AS paid FROM orders UNION ALL SELECT id + 100000, id, 'fee', toDecimal64(1, 2) FROM orders)"
PAYMENTS = {
    "from": "payments", "ch": PAYMENTS_CH,
    "text": {"method": "method"},
    "number": {"order_id": "order_id"},
    "bool": {}, "time": {},
    "text_values": ["card", "fee", "CARD", "no such"],
    "fractions": {"order_id": [1.5]},
    "whole": {"order_id": [1, 2500, 5001]},
    "group": ["method"],
    "measures": {"count": "count()", "total": "sum(paid)"},
    "times": [],
    "patterns": ["c%", "%e", "%AR%", "_ard"],
    "star": ["order_id", "method"],
    "forbid": ["id", "payment_id", "paid"],
    "order": "order_id",
}
for _src in (ORDERS, TYPED):
    _src.setdefault("order", "id")
USERS["order"] = "id"
SOURCES = [ORDERS, ORDERS, ORDERS, TYPED, TYPED, PRODUCTS, USERS, PAYMENTS]

OFFSET_FORMS = {"2025-03-09 12:00:00": "2025-03-09 12:00:00+03:00", "2025-06-15 10:30:00": "2025-06-15T10:30:00Z", "2025-03-30 12:00:00": "2025-03-30T12:00:00+01:00", "2024-04-15 12:00:00": "2024-04-15T12:00:00Z"}


def ch_zone(zone):
    return zone or "UTC"


def time_literal(rng, src, zone, whole_seconds=True):
    """A time in one form of the text, and the same instant in ClickHouse. A text with no offset is a time of the report time zone. Each time here has one meaning in each zone of the generator: none is in a repeated hour."""
    text = rng.choice(src["times"])
    clock = text if " " in text else text + " 00:00:00"
    truth = "toDateTime('%s', '%s')" % (clock, ch_zone(zone))
    if rng.random() < 0.15 and text in OFFSET_FORMS:
        written = OFFSET_FORMS[text]
        return ("'%s'" % written, "parseDateTimeBestEffort('%s')" % written)
    form = rng.randrange(6)
    if form == 1:
        return ("TIMESTAMP '%s'" % clock, truth)
    if form == 2 and " " not in text:
        return ("DATE '%s'" % text, truth)
    if form == 3:
        return ("CAST('%s' AS timestamp)" % clock, truth)
    if form == 4:
        return ("'%s'::timestamp" % clock, truth)
    if form == 5:
        return ("'%s'" % clock.replace(" ", "T"), truth)
    return ("'%s'" % text, truth)


def time_atom(rng, src, zone):
    name = rng.choice(list(src["time"]))
    col = (name, src["time"][name])
    a = time_literal(rng, src, zone)
    b = time_literal(rng, src, zone)
    kind = rng.randrange(10)
    if kind == 0:
        return fmt("%s BETWEEN %s AND %s", col, a, b)
    if kind == 1:
        return fmt("%s NOT BETWEEN %s AND %s", col, a, b)
    if kind == 2:
        return fmt("%s >= %s AND %s < %s", col, a, col, b)
    if kind == 3:
        return fmt("%s > %s AND %s <= %s", col, a, col, b)
    if kind == 4:
        op = rng.choice(["<", "<=", ">", ">="])
        return fmt("%s " + op + " %s", a, col)
    if kind == 5 and not zone:
        return fmt("%s IN (%s, %s)", col, a, b)
    if kind == 6 and not zone:
        unit = rng.choice([("month", "toStartOfMonth(%s)"), ("day", "toStartOfDay(%s)"), ("year", "toStartOfYear(%s)")])
        return (("DATE_TRUNC('%s', %s) = TIMESTAMP '2025-03-01 00:00:00'" % (unit[0], col[0])), ("%s = toDateTime('2025-03-01 00:00:00', 'UTC')" % (unit[1] % ("toTimeZone(%s, 'UTC')" % col[1]))))
    op = rng.choice(["=", "<>", "!=", "<", "<=", ">", ">="])
    return fmt("%s " + op + " %s", col, a)


def function_atom(rng, src):
    """A function of a column next to a literal."""
    if src["text"] and rng.random() < 0.6:
        name = rng.choice(list(src["text"]))
        col = (name, src["text"][name])
        value = rng.choice(src["text_values"])
        return rng.choice([
            ("LEFT(%s, 1) = '%s'" % (col[0], value[:1]), "left(%s, 1) = '%s'" % (col[1], value[:1])),
            ("RIGHT(%s, 2) = '%s'" % (col[0], value[-2:]), "right(%s, 2) = '%s'" % (col[1], value[-2:])),
            ("LEFT(%s, 1) IN ('%s', 'x')" % (col[0], value[:1]), "left(%s, 1) IN ('%s', 'x')" % (col[1], value[:1])),
            ("LOWER(%s) = '%s'" % (col[0], value.lower()), "lower(%s) = '%s'" % (col[1], value.lower())),
            ("UPPER(%s) <> '%s'" % (col[0], value.upper()), "upper(%s) <> '%s'" % (col[1], value.upper())),
            ("STRPOS(%s, 's') > 0" % col[0], "position(%s, 's') > 0" % col[1]),
            ("STRPOS(%s, 'S') >= 1" % col[0], "position(%s, 'S') >= 1" % col[1]),
            ("LENGTH(%s) > 5" % col[0], "length(%s) > 5" % col[1]),
            ("COALESCE(%s, 'none') = '%s'" % (col[0], value), "coalesce(%s, 'none') = '%s'" % (col[1], value)),
            ("NULLIF(%s, '%s') IS NULL" % (col[0], value), "nullIf(%s, '%s') IS NULL" % (col[1], value)),
            ("SUBSTRING(%s, 1, 2) = '%s'" % (col[0], value[:2]), "substring(%s, 1, 2) = '%s'" % (col[1], value[:2])),
            ("CONCAT(%s, 'x') = '%sx'" % (col[0], value), "concat(%s, 'x') = '%sx'" % (col[1], value)),
            ("%s || 'x' = '%sx'" % (col[0], value), "concat(%s, 'x') = '%sx'" % (col[1], value)),
        ])
    name = rng.choice(list(src["number"]))
    col = (name, src["number"][name])
    value = rng.choice(src["whole"].get(name, [1]))
    return rng.choice([
        ("ABS(%s) >= %s" % (col[0], value), "abs(%s) >= %s" % (col[1], value)),
        ("ROUND(%s) = %s" % (col[0], value), "round(%s) = %s" % (col[1], value)),
        ("FLOOR(%s) < %s" % (col[0], value), "floor(%s) < %s" % (col[1], value)),
        ("CEIL(%s) > %s" % (col[0], value), "ceil(%s) > %s" % (col[1], value)),
        ("%s * 2 > %s" % (col[0], value), "%s * 2 > %s" % (col[1], value)),
        ("%s + 1 = %s" % (col[0], value), "%s + 1 = %s" % (col[1], value)),
        ("%s - 1 <= %s" % (col[0], value), "%s - 1 <= %s" % (col[1], value)),
        ("%s %% 7 = 3" % col[0], "%s %% 7 = 3" % col[1]),
        ("%s / 2 >= %s" % (col[0], value), "toFloat64(%s) / 2 >= %s" % (col[1], value)),
    ])


def like_atom(rng, src):
    name = rng.choice(list(src["text"]))
    col = (name, src["text"][name])
    pattern = rng.choice(src["patterns"])
    op = rng.choice(["LIKE", "NOT LIKE", "ILIKE", "NOT ILIKE"])
    form = rng.randrange(3)
    written = "'%s'" % pattern
    if form == 1 and pattern.startswith("%") and pattern.endswith("%") and len(pattern) > 2:
        written = "'%' || '" + pattern[1:-1] + "' || '%'"
    return (("%s %s %s" % (col[0], op, written)), ("%s %s '%s'" % (col[1], op, pattern)))


def boolean_atom(rng, src):
    name = rng.choice(list(src["bool"]))
    col = src["bool"][name]
    return rng.choice([
        (name, col),
        ("NOT %s" % name, "NOT %s" % col),
        ("%s = TRUE" % name, "%s = true" % col),
        ("%s = FALSE" % name, "%s = false" % col),
        ("%s <> TRUE" % name, "%s <> true" % col),
        ("%s IS TRUE" % name, "ifNull(%s, false) = true" % col),
        ("%s IS FALSE" % name, "ifNull(%s, true) = false" % col),
        ("%s IS NOT TRUE" % name, "NOT ifNull(%s, false)" % col),
        ("%s IS NOT FALSE" % name, "ifNull(%s, true)" % col),
        ("%s IS NULL" % name, "%s IS NULL" % col),
    ])


def other_atom(rng, src, zone):
    """The forms of the list that are rare in real SQL."""
    forms = []
    if src["text"]:
        name = rng.choice(list(src["text"]))
        col = src["text"][name]
        value = rng.choice(src["text_values"])
        forms += [("%s = NULL" % name, "%s = NULL" % col),
                  ("%s <> NULL" % name, "%s <> NULL" % col),
                  ("CAST(%s AS text) = '%s'" % (name, value), "%s = '%s'" % (col, value)),
                  ("%s::varchar <> '%s'" % (name, value), "%s <> '%s'" % (col, value))]
    if src["time"]:
        name = rng.choice(list(src["time"]))
        col = src["time"][name]
        literal = time_literal(rng, src, zone)
        times = [("CAST(%s AS timestamp) >= %s" % (name, literal[0]), "%s >= %s" % (col, literal[1]))]
        if not zone:
            day = rng.choice(src["times"])[:10]
            times += [("%s::date = '%s'" % (name, day), "toDate(%s, 'UTC') = '%s'" % (col, day)),
                      ("DATE(%s) = '%s'" % (name, day), "toDate(%s, 'UTC') = '%s'" % (col, day)),
                      ("DATEDIFF('day', %s, TIMESTAMP '%s 00:00:00') > 30" % (name, day), "dateDiff('day', %s, toDateTime('%s 00:00:00', 'UTC'), 'UTC') > 30" % (col, day))]
        # These forms are the rarest of the list, and each seed must prove each construct, so they come more often than the others here.
        if rng.random() < 0.8:
            return rng.choice(times)
        forms += times
    return rng.choice(forms) if forms else base.number_atom(rng, src)


def predicate(rng, src, zone, depth=2):
    roll = rng.random()
    if depth > 0 and roll < 0.2:
        return fmt("(%s AND %s)", predicate(rng, src, zone, depth - 1), predicate(rng, src, zone, depth - 1))
    if depth > 0 and roll < 0.35:
        return fmt("(%s OR %s)", predicate(rng, src, zone, depth - 1), predicate(rng, src, zone, depth - 1))
    if depth > 0 and roll < 0.45:
        return fmt("NOT (%s)", predicate(rng, src, zone, depth - 1))
    roll = rng.random()
    if roll < 0.14:
        return other_atom(rng, src, zone)
    if src["time"] and roll < 0.25:
        return time_atom(rng, src, zone)
    if src["bool"] and roll < 0.32:
        return boolean_atom(rng, src)
    if roll < 0.45:
        return like_atom(rng, src)
    if roll < 0.6:
        return function_atom(rng, src)
    if roll < 0.8 and src["number"]:
        return base.number_atom(rng, src)
    if src["text"]:
        return base.text_atom_of(rng, src)
    return base.number_atom(rng, src)


NO_ROWS = base.NULL_FOR_EMPTY


def measure(rng, src):
    name = rng.choice(list(src["measures"]))
    return ("MEASURE(%s)" % name, src["measures"][name])


def number_expression(rng, src):
    """A number from columns of one row."""
    names = list(src["number"])
    a, b = rng.choice(names), rng.choice(names)
    ca, cb = (a, src["number"][a]), (b, src["number"][b])
    return rng.choice([
        ca,
        (("%s + %s" % (ca[0], cb[0])), ("%s + %s" % (ca[1], cb[1]))),
        (("%s - 1" % ca[0]), ("%s - 1" % ca[1])),
        (("%s * 2" % ca[0]), ("%s * 2" % ca[1])),
        (("%s / %s" % (ca[0], cb[0])), ("toFloat64(%s) / nullIf(toFloat64(%s), 0)" % (ca[1], cb[1]))),
        (("%s / 3" % ca[0]), ("toFloat64(%s) / 3" % ca[1])),
        (("ABS(%s)" % ca[0]), ("abs(%s)" % ca[1])),
        (("ROUND(%s, 1)" % ca[0]), ("round(%s, 1)" % ca[1])),
        (("COALESCE(%s, 0)" % ca[0]), ("coalesce(%s, 0)" % ca[1])),
        (("NULLIF(%s, 0)" % ca[0]), ("nullIf(%s, 0)" % ca[1])),
        (("CAST(%s AS numeric)" % ca[0]), ca[1]),
        (("CASE WHEN %s > 2 THEN %s ELSE 0 END" % (ca[0], cb[0])), ("if(ifNull(%s > 2, false), %s, 0)" % (ca[1], cb[1]))),
    ])


def aggregate(rng, src, zone):
    """(cube text, ClickHouse text)."""
    kind = rng.randrange(15)
    if kind <= 2:
        return measure(rng, src)
    if kind == 12:
        name = rng.choice(list(src["number"]))
        which = rng.choice([("MIN(DISTINCT %s)", "min(toFloat64(%s))"), ("MAX(DISTINCT %s)", "max(toFloat64(%s))")])
        return (which[0] % name, which[1] % src["number"][name])
    if kind == 13:
        name = rng.choice(list(src["number"]))
        which = rng.choice([("STDDEV(%s)", "stddevSamp(toFloat64(%s))"), ("STDDEV_SAMP(%s)", "stddevSamp(toFloat64(%s))"), ("STDDEV_POP(%s)", "stddevPop(toFloat64(%s))")])
        return (which[0] % name, which[1] % src["number"][name])
    if kind == 14 and src["from"] == "orders":
        return rng.choice([("SUM(revenue)", "sum(amount)"), ("SUM(count)", "count()"), ("AVG(avg_order_value)", "avg(amount)"), ("MIN(min_amount)", "min(amount)"), ("MAX(max_amount)", "max(amount)")])
    expression = number_expression(rng, src)
    text = None
    if src["text"]:
        tname = rng.choice(list(src["text"]))
        text = (tname, src["text"][tname])
    pred = predicate(rng, src, zone, 1)
    if kind == 3:
        return ("COUNT(*)", "count()")
    if kind == 4 and text:
        return fmt("COUNT(%s)", text)
    if kind == 5 and text:
        return (("COUNT(DISTINCT %s)" % text[0]), ("uniqExact(%s)" % text[1]))
    if kind == 6:
        return (("SUM(%s)" % expression[0]), ("sum(toFloat64(%s))" % expression[1]))
    if kind == 7:
        return (("AVG(%s)" % expression[0]), ("avg(toFloat64(%s))" % expression[1]))
    if kind == 8:
        which = rng.choice([("MIN", "min"), ("MAX", "max")])
        return (("%s(%s)" % (which[0], expression[0])), ("%s(toFloat64(%s))" % (which[1], expression[1])))
    if kind == 9:
        return ("COUNT(*) FILTER (WHERE %s)" % pred[0], "countIf(ifNull(%s, false))" % pred[1])
    if kind == 10:
        return ("SUM(CASE WHEN %s THEN 1 ELSE 0 END)" % pred[0], "sum(if(ifNull(%s, false), 1, 0))" % pred[1])
    return ("ROUND(AVG(%s), 2)" % expression[0], "round(avg(toFloat64(%s)), 2)" % expression[1])


def where_of(rng, src, zone, chance=0.7):
    if rng.random() < chance:
        return predicate(rng, src, zone)
    return None


def with_where(texts, where):
    if where:
        return (texts[0] + " WHERE " + where[0], texts[1] + " WHERE " + where[1])
    return texts


def spec(cube, ch, **extra):
    out = {"sql": cube, "truth": ch}
    out.update(extra)
    return out


def shape_total(rng, src, zone):
    parts = [aggregate(rng, src, zone) for _ in range(rng.choice([1, 1, 2]))]
    texts = ("SELECT %s FROM %s" % (", ".join(p[0] for p in parts), src["from"]), "SELECT %s FROM %s" % (", ".join(p[1] for p in parts), src["ch"]))
    cube, ch = with_where(texts, where_of(rng, src, zone))
    return spec(cube, ch + NO_ROWS, tol=1e-6)


def group_key(rng, src):
    name = rng.choice(src["group"])
    for kind in ("text", "number"):
        if name in src[kind]:
            return (name, src[kind][name])
    return (name, "toBool(%s)" % src["bool"][name])


def bucket(rng, src, zone):
    """DATE_TRUNC and the same bucket in ClickHouse, as the clock of the report time zone."""
    name = rng.choice(list(src["time"]))
    col = "toTimeZone(%s, '%s')" % (src["time"][name], ch_zone(zone))
    unit, form = rng.choice([("day", "toDate(%s)"), ("week", "toMonday(%s)"), ("month", "toStartOfMonth(%s)"), ("quarter", "toStartOfQuarter(%s)"), ("year", "toStartOfYear(%s)")])
    return ("DATE_TRUNC('%s', %s)" % (unit, name), form % col)


def shape_group(rng, src, zone):
    if src["time"] and rng.random() < 0.35:
        key = bucket(rng, src, zone)
    elif rng.random() < 0.1 and src["number"]:
        name = rng.choice(list(src["number"]))
        col = (name, src["number"][name])
        key = (("CASE WHEN %s > 50 THEN 'high' ELSE 'low' END" % col[0]), ("if(ifNull(%s > 50, false), 'high', 'low')" % col[1]))
    else:
        key = group_key(rng, src)
    part = aggregate(rng, src, zone)
    alias_key = rng.random() < 0.5
    alias_part = rng.choice(["total", "n", None])
    if alias_part == "total" and rng.random() < 0.3 and src["number"]:
        alias_part = rng.choice(list(src["number"]))  # An alias with the name of a column.
    select = "%s%s, %s%s" % (key[0], " AS k" if alias_key else "", part[0], " AS %s" % alias_part if alias_part else "")
    cube, ch = with_where(("SELECT %s FROM %s" % (select, src["from"]), "SELECT %s AS k, %s AS v FROM %s" % (key[1], part[1], src["ch"])), where_of(rng, src, zone, 0.5))
    cube += " GROUP BY " + rng.choice(["1", "k" if alias_key else "1", key[0]])
    ch += " GROUP BY k"
    if rng.random() < 0.3:
        having = rng.choice([("COUNT(*) > %s", "count() > %s", rng.choice([1, 100.5, 1000.5])),
                             ("COUNT(*) <> %s", "count() <> %s", 1000),
                             (part[0].replace("%", "%%") + " >= %s", part[1].replace("%", "%%") + " >= %s", rng.choice([0, 100.5, 1000]))])
        cube += " HAVING " + having[0] % having[2]
        ch += " HAVING " + having[1] % having[2]
    if rng.random() < 0.4:
        direction = rng.choice(["", " ASC", " DESC"])
        ch_direction = direction
        cube += " ORDER BY 1%s" % direction
        ch += " ORDER BY k%s" % ch_direction
        if rng.random() < 0.3 and direction != " DESC":
            cube += " LIMIT 3"
            ch += " LIMIT 3"
            if "cancel_status" in key[0] or "lc_n" in key[0] or "n_str" in key[0]:
                return spec(cube, ch + NO_ROWS, tol=1e-6)
    return spec(cube, ch + NO_ROWS, tol=1e-6)


def shape_raw(rng, src, zone):
    columns = list(src["text"])[:2] + list(src["number"])[:1]
    extra = number_expression(rng, src)
    ordered = src.get("order", "id")
    cube, ch = with_where(("SELECT %s, %s, %s AS extra FROM %s" % (ordered, ", ".join(columns[:2]), extra[0], src["from"]),
                           "SELECT %s, %s, %s FROM %s" % (ordered, ", ".join(_ch_of(src, c) for c in columns[:2]), extra[1], src["ch"])),
                          where_of(rng, src, zone))
    tail = rng.choice([" ORDER BY %s LIMIT 5", " ORDER BY %s LIMIT 5 OFFSET 2", " ORDER BY %s FETCH FIRST 5 ROWS ONLY"]) % ordered
    return spec(cube + tail, ch + tail.replace(" FETCH FIRST 5 ROWS ONLY", " LIMIT 5"), ordered=True, tol=1e-6)


def _ch_of(src, name):
    for kind in ("text", "number", "bool", "time"):
        if name in src[kind]:
            return src[kind][name]
    raise KeyError(name)


def shape_star(rng, src, zone):
    source = rng.choice([PRODUCTS, PAYMENTS])
    where = predicate(rng, source, None, 1) if rng.random() < 0.5 else None
    cube, ch = with_where(("SELECT * FROM %s" % source["from"], "SELECT %s FROM %s" % (", ".join(_ch_of(source, c) for c in source["star"]), source["ch"])), where)
    tail = " ORDER BY %s LIMIT 4" % source["star"][0]
    return spec(cube + tail, ch + tail, ordered=False, columns=source["star"], forbid=source.get("forbid"), tol=1e-6) if source is PRODUCTS else \
        spec(cube + " ORDER BY order_id, method LIMIT 4", ch + " ORDER BY order_id, method LIMIT 4", ordered=True, columns=source["star"], forbid=source.get("forbid"), tol=1e-6)


def shape_subquery(rng, src, zone, with_clause=False):
    key = group_key(rng, src)
    number = rng.choice(list(src["number"]))
    inner_where = where_of(rng, src, None if zone else zone, 0.4) if not zone else None
    kind = rng.randrange(4)
    if kind == 0:
        inner = with_where(("SELECT %s AS k, COUNT(*) AS c, MAX(%s) AS m FROM %s" % (key[0], number, src["from"]), "SELECT %s AS k, count() AS c, max(toFloat64(%s)) AS m FROM %s" % (key[1], src["number"][number], src["ch"])), inner_where)
        inner = (inner[0] + " GROUP BY 1", inner[1] + " GROUP BY k")
        outer = rng.choice([("c > 900", "c > 900"), ("c <> 1000", "c <> 1000"), ("m >= 10.5", "m >= 10.5"), ("NOT (c < 5)", "NOT (c < 5)")])
        columns = rng.choice([("*", "k, c, m"), ("k, c", "k, c"), ("SUM(c)", "sum(c)")])
    elif kind == 1:
        inner = with_where(("SELECT %s AS k, %s AS a FROM %s" % (key[0], number, src["from"]), "SELECT %s AS k, %s AS a FROM %s" % (key[1], src["number"][number], src["ch"])), inner_where)
        value = rng.choice(src["whole"].get(number, [1]))
        outer = rng.choice([("a > %s" % value, "a > %s" % value), ("a <> %s" % value, "a <> %s" % value), ("a = %s" % value, "a = %s" % value), ("a BETWEEN 0 AND %s" % value, "a BETWEEN 0 AND %s" % value)])
        columns = rng.choice([("COUNT(*)", "count()"), ("SUM(a)", "sum(toFloat64(a))"), ("MAX(a)", "max(toFloat64(a))"), ("COUNT(a)", "count(a)")])
    elif kind == 2:
        inner = with_where(("SELECT %s AS k, MEASURE(%s) AS v FROM %s" % (key[0], "count", src["from"]), "SELECT %s AS k, count() AS v FROM %s" % (key[1], src["ch"])), inner_where)
        inner = (inner[0] + " GROUP BY 1", inner[1] + " GROUP BY k")
        outer = rng.choice([("v > 900", "v > 900"), ("v >= 2", "v >= 2")])
        columns = rng.choice([("k, v", "k, v"), ("SUM(v)", "sum(v)"), ("COUNT(*)", "count()")])
    else:
        inner = with_where(("SELECT %s AS k, COUNT(*) AS c FROM %s" % (key[0], src["from"]), "SELECT %s AS k, count() AS c FROM %s" % (key[1], src["ch"])), inner_where)
        inner = (inner[0] + " GROUP BY 1 LIMIT 100000", inner[1] + " GROUP BY k LIMIT 100000")
        outer = None
        columns = rng.choice([("SUM(c)", "sum(c)"), ("MAX(c)", "max(c)"), ("k, c", "k, c")])
    outer_text = " WHERE " + outer[0] if outer else ""
    outer_ch = " WHERE " + outer[1] if outer else ""
    if with_clause:
        cube = "WITH t AS (%s) SELECT %s FROM t%s" % (inner[0], columns[0], outer_text)
    else:
        cube = "SELECT %s FROM (%s) t%s" % (columns[0], inner[0], outer_text)
    return spec(cube, "SELECT %s FROM (%s)%s" % (columns[1], inner[1], outer_ch) + NO_ROWS, tol=1e-6)


def shape_union(rng, src, zone):
    a, b = aggregate(rng, src, None if zone else zone), aggregate(rng, src, None if zone else zone)
    pa = predicate(rng, _no_time(src), None, 1)
    pb = predicate(rng, _no_time(src), None, 1)
    cube = "SELECT %s FROM %s WHERE %s UNION ALL SELECT %s FROM %s WHERE %s" % (a[0], src["from"], pa[0], b[0], src["from"], pb[0])
    ch = "SELECT toNullable(toFloat64(%s)) FROM %s WHERE %s UNION ALL SELECT toNullable(toFloat64(%s)) FROM %s WHERE %s" % (a[1], src["ch"], pa[1], b[1], src["ch"], pb[1])
    return spec(cube, ch + NO_ROWS, tol=1e-6)


def _no_time(src):
    return dict(src, time={})


def shape_in_subquery(rng, src, zone):
    where = predicate(rng, _no_time(src), None, 1)
    ordered = src.get("order", "id")
    op = rng.choice(["IN", "NOT IN"])
    cube = "SELECT COUNT(*) FROM %s WHERE %s %s (SELECT %s FROM %s WHERE %s)" % (src["from"], ordered, op, ordered, src["from"], where[0])
    ch = "SELECT count() FROM %s WHERE %s %s (SELECT %s FROM %s WHERE %s)" % (src["ch"], ordered, op, ordered, src["ch"], where[1])
    return spec(cube, ch)


def shape_distinct(rng, src, zone):
    key = group_key(rng, src)
    cube, ch = with_where(("SELECT DISTINCT %s FROM %s" % (key[0], src["from"]), "SELECT DISTINCT %s FROM %s" % (key[1], src["ch"])), where_of(rng, src, zone, 0.4))
    return spec(cube, ch)


def shape_window(rng, src, zone):
    key = group_key(rng, src)
    form = rng.choice([("SUM(COUNT(*)) OVER ()", "sum(count()) OVER ()"), ("RANK() OVER (ORDER BY COUNT(*) DESC)", "rank() OVER (ORDER BY count() DESC)"),
                       ("ROW_NUMBER() OVER (ORDER BY %s)" % key[0], "row_number() OVER (ORDER BY %s)" % key[1])])
    return spec("SELECT %s, %s FROM %s GROUP BY 1" % (key[0], form[0], src["from"]), "SELECT %s AS k, %s FROM %s GROUP BY k" % (key[1], form[1], src["ch"]))


def shape_case_select(rng, src, zone):
    pred = predicate(rng, src, zone, 1)
    return spec("SELECT CASE WHEN %s THEN 'yes' ELSE 'no' END AS answer, COUNT(*) FROM %s GROUP BY 1" % (pred[0], src["from"]),
                "SELECT if(ifNull(%s, false), 'yes', 'no') AS a, count() FROM %s GROUP BY a" % (pred[1], src["ch"]))


JOINS = [
    # (cube from, ClickHouse from, key column, its truth, a filter, its truth)
    {"left": ("orders", "o"), "right": ("users", "u"), "ch": "orders o JOIN users u ON o.user_id = u.id", "keys": "o.user_id = u.id",
     "columns": [("u.city", "u.city"), ("u.state", "u.state"), ("u.is_vip", "u.is_vip"), ("o.status", "toString(o.status)")], "where": [("u.city = 'Austin'", "u.city = 'Austin'"), ("o.quantity_dim > 2", "o.quantity > 2"), ("u.city <> 'Austin'", "u.city <> 'Austin'")],
     "measure": ("o.revenue", "sum(o.amount)"), "fan_out": False},
    {"left": ("orders", "o"), "right": ("products", "p"), "ch": "orders o JOIN products p ON o.product_id = p.id", "keys": "o.product_id = p.id",
     "columns": [("p.category", "p.category"), ("o.status", "toString(o.status)")], "where": [("p.category <> 'Gadget'", "p.category <> 'Gadget'"), ("p.price >= 25.5", "p.price >= 25.5"), ("p.name LIKE 'Product 1%'", "p.name LIKE 'Product 1%'")],
     "measure": ("o.count", "count()"), "fan_out": False},
    {"left": ("orders", "o"), "right": ("payments", "p"), "ch": "orders o JOIN %s p ON o.id = p.order_id" % PAYMENTS_CH, "keys": "o.id = p.order_id",
     "columns": [("p.method", "p.method")], "where": [("p.method = 'fee'", "p.method = 'fee'"), ("o.status <> 'shipped'", "toString(o.status) <> 'shipped'")],
     "measure": ("o.count", "uniqExact(o.id)"), "fan_out": True},
    {"left": ("orphan_orders", "o"), "right": ("orphan_customers", "c"), "ch": "orphan_orders o %s orphan_customers c ON o.customer_id = c.id", "keys": "o.customer_id = c.id",
     "columns": [("c.name", "c.name")], "where": [("c.name <> 'C1'", "c.name <> 'C1'"), ("o.customer_id > 1", "o.customer_id > 1")],
     "measure": ("o.total", "sum(o.amount)"), "fan_out": False, "orphans": True},
]


def shape_join(rng, src, zone):
    join = rng.choice(JOINS)
    (left, a), (right, b) = join["left"], join["right"]
    kind = rng.choice(["JOIN", "JOIN", "LEFT JOIN", "CROSS"])
    if kind == "CROSS":
        written = "%s %s CROSS JOIN %s %s" % (left, a, right, b)
        join_filter = "%s.__cubeJoinField = %s.__cubeJoinField" % (a, b)
    else:
        condition = rng.choice(["%s.__cubeJoinField = %s.__cubeJoinField" % (a, b), join["keys"]])
        written = "%s %s %s %s %s ON %s" % (left, a, kind, right, b, condition)
        join_filter = None
    inner = "LEFT JOIN" if kind in ("LEFT JOIN", "CROSS") else "JOIN"
    truth_from = join["ch"] % inner if "%s" in join["ch"] and join.get("orphans") else join["ch"]
    column = rng.choice(join["columns"])
    where = rng.choice(join["where"]) if rng.random() < 0.5 else None
    conditions = [c for c in (join_filter, where[0] if where else None) if c]
    tail = " WHERE " + " AND ".join(conditions) if conditions else ""
    tail_ch = " WHERE " + where[1] if where else ""
    aggregate_form = rng.choice([("MEASURE(%s)" % join["measure"][0], join["measure"][1]), ("COUNT(*)", "count()")])
    settings = " SETTINGS join_use_nulls = 1, aggregate_functions_null_for_empty = 1"
    if rng.random() < 0.3:
        return spec("SELECT %s FROM %s%s" % (aggregate_form[0], written, tail), "SELECT %s FROM %s%s%s" % (aggregate_form[1], truth_from, tail_ch, settings), tol=1e-6)
    return spec("SELECT %s, %s FROM %s%s GROUP BY 1" % (column[0], aggregate_form[0], written, tail), "SELECT %s AS k, %s FROM %s%s GROUP BY k%s" % (column[1], aggregate_form[1], truth_from, tail_ch, settings), tol=1e-6)


def shape_view(rng, src, zone):
    source = "orders o JOIN users u ON o.user_id = u.id JOIN products p ON o.product_id = p.id"
    key = rng.choice([("users_city", "u.city"), ("users_state", "u.state"), ("products_category", "p.category"), ("status", "toString(o.status)"), ("users_is_vip", "u.is_vip")])
    where = rng.choice([None, ("users_city <> 'Austin'", "u.city <> 'Austin'"), ("products_category = 'Gadget'", "p.category = 'Gadget'"), ("status <> 'shipped'", "toString(o.status) <> 'shipped'"),
                        ("users_is_vip", "u.is_vip"), ("products_name LIKE 'Product 2%'", "p.name LIKE 'Product 2%'")])
    measure_form = rng.choice([("MEASURE(revenue)", "sum(o.amount)"), ("MEASURE(count)", "count()"), ("MEASURE(avg_order_value)", "avg(o.amount)")])
    cube, ch = with_where(("SELECT %s, %s FROM sales" % (key[0], measure_form[0]), "SELECT %s AS k, %s FROM %s" % (key[1], measure_form[1], source)), where)
    return spec(cube + " GROUP BY 1", ch + " GROUP BY k" + NO_ROWS, tol=1e-6)


def shape_rollup(rng, src, zone):
    """Queries that a rollup of `orders` can serve, inside and outside its range, with whole and partial buckets."""
    measure_form = rng.choice([("MEASURE(count)", "count()"), ("MEASURE(revenue)", "sum(amount)")])
    unit, form = rng.choice([("day", "toDate(%s)"), ("month", "toStartOfMonth(%s)")])
    lo, hi = rng.choice([("2025-03-01", "2025-04-01"), ("2025-03-01 00:00:00", "2025-03-08 00:00:00"), ("2025-02-15", "2025-03-15"), ("2024-12-01", "2025-02-01"), ("2025-12-20", "2026-01-10"), ("2025-03-10 06:00:00", "2025-03-12 18:30:00")])
    col = "toTimeZone(created_at, '%s')" % ch_zone(zone)
    bounds = (lo if " " in lo else lo + " 00:00:00", hi if " " in hi else hi + " 00:00:00")
    where = "created_at >= '%s' AND created_at < '%s'" % (lo, hi)
    ch_where = "created_at >= toDateTime('%s', '%s') AND created_at < toDateTime('%s', '%s')" % (bounds[0], ch_zone(zone), bounds[1], ch_zone(zone))
    if rng.random() < 0.5:
        return spec("SELECT DATE_TRUNC('%s', created_at) AS t, status, %s FROM orders WHERE %s GROUP BY 1, 2" % (unit, measure_form[0], where),
                    "SELECT %s AS t, toString(status) AS s, %s FROM orders WHERE %s GROUP BY t, s" % (form % col, measure_form[1], ch_where), tol=1e-6)
    return spec("SELECT DATE_TRUNC('%s', created_at) AS t, %s FROM orders WHERE %s GROUP BY 1" % (unit, measure_form[0], where),
                "SELECT %s AS t, %s FROM orders WHERE %s GROUP BY t" % (form % col, measure_form[1], ch_where), tol=1e-6)


def zone_cases(zone):
    """Each shape of a time under a report time zone, with its truth. Each one gives this truth or a clear refusal. `t` has no row in its repeated hour."""
    at = lambda clock: "toDateTime('%s', '%s')" % (clock, zone)  # noqa: E731
    bucket_day = "toDate(toTimeZone(created_at, '%s'))" % zone
    return [spec(sql, truth, zone=zone, tol=1e-6) for sql, truth in [
        ("SELECT COUNT(*) FROM orders WHERE created_at >= '2025-03-09 12:00:00'", "SELECT count() FROM orders WHERE created_at >= " + at("2025-03-09 12:00:00")),
        ("SELECT COUNT(*) FROM orders WHERE created_at > '2025-03-09'", "SELECT count() FROM orders WHERE created_at > " + at("2025-03-09 00:00:00")),
        ("SELECT COUNT(*) FROM orders WHERE created_at < TIMESTAMP '2025-03-09 12:00:00'", "SELECT count() FROM orders WHERE created_at < " + at("2025-03-09 12:00:00")),
        ("SELECT COUNT(*) FROM orders WHERE created_at <= DATE '2025-03-09'", "SELECT count() FROM orders WHERE created_at <= " + at("2025-03-09 00:00:00")),
        ("SELECT COUNT(*) FROM orders WHERE '2025-03-09' <= created_at", "SELECT count() FROM orders WHERE created_at >= " + at("2025-03-09 00:00:00")),
        ("SELECT COUNT(*) FROM orders WHERE created_at = '2025-03-09 12:00:00'", "SELECT count() FROM orders WHERE created_at = " + at("2025-03-09 12:00:00")),
        ("SELECT COUNT(*) FROM orders WHERE created_at <> '2025-03-09 12:00:00'", "SELECT count() FROM orders WHERE created_at <> " + at("2025-03-09 12:00:00")),
        ("SELECT COUNT(*) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-04-01'", "SELECT count() FROM orders WHERE created_at >= %s AND created_at < %s" % (at("2025-03-01 00:00:00"), at("2025-04-01 00:00:00"))),
        ("SELECT COUNT(*) FROM orders WHERE created_at > '2025-03-01' AND created_at <= '2025-04-01'", "SELECT count() FROM orders WHERE created_at > %s AND created_at <= %s" % (at("2025-03-01 00:00:00"), at("2025-04-01 00:00:00"))),
        ("SELECT COUNT(*) FROM orders WHERE created_at BETWEEN '2025-03-01' AND '2025-04-01'", "SELECT count() FROM orders WHERE created_at BETWEEN %s AND %s" % (at("2025-03-01 00:00:00"), at("2025-04-01 00:00:00"))),
        ("SELECT COUNT(*) FROM orders WHERE created_at NOT BETWEEN '2025-03-01' AND '2025-04-01'", "SELECT count() FROM orders WHERE created_at NOT BETWEEN %s AND %s" % (at("2025-03-01 00:00:00"), at("2025-04-01 00:00:00"))),
        ("SELECT COUNT(*) FROM orders WHERE NOT (created_at >= '2025-03-09')", "SELECT count() FROM orders WHERE NOT (created_at >= %s)" % at("2025-03-09 00:00:00")),
        ("SELECT COUNT(*) FROM orders WHERE created_at >= '2025-03-09' OR status = 'shipped'", "SELECT count() FROM orders WHERE created_at >= %s OR status = 'shipped'" % at("2025-03-09 00:00:00")),
        ("SELECT COUNT(*) FROM orders WHERE created_at >= '2025-03-09T12:00:00Z'", "SELECT count() FROM orders WHERE created_at >= toDateTime('2025-03-09 12:00:00', 'UTC')"),
        ("SELECT COUNT(*) FROM orders WHERE created_at IN ('2025-03-09 12:00:00')", "SELECT count() FROM orders WHERE created_at = " + at("2025-03-09 12:00:00")),
        ("SELECT COUNT(*) FROM orders WHERE DATE_TRUNC('month', created_at) = '2025-03-01'", "SELECT count() FROM orders WHERE toStartOfMonth(toTimeZone(created_at, '%s')) = '2025-03-01'" % zone),
        ("SELECT COUNT(*) FROM orders WHERE created_at::date = '2025-03-09'", "SELECT count() FROM orders WHERE %s = '2025-03-09'" % bucket_day),
        ("SELECT COUNT(*) FILTER (WHERE created_at >= '2025-03-09') FROM orders", "SELECT countIf(created_at >= %s) FROM orders" % at("2025-03-09 00:00:00")),
        ("SELECT SUM(CASE WHEN created_at >= '2025-03-09' THEN 1 ELSE 0 END) FROM orders", "SELECT countIf(created_at >= %s) FROM orders" % at("2025-03-09 00:00:00")),
        ("SELECT status, COUNT(*) FROM orders GROUP BY 1 HAVING MAX(created_at) >= '2025-03-09'", "SELECT toString(status), count() FROM orders GROUP BY 1 HAVING max(created_at) >= " + at("2025-03-09 00:00:00")),
        ("SELECT DATE_TRUNC('day', created_at) AS d, COUNT(*) FROM orders GROUP BY 1 HAVING DATE_TRUNC('day', created_at) >= '2025-12-20'", "SELECT %s AS d, count() FROM orders GROUP BY d HAVING d >= '2025-12-20'" % bucket_day),
        ("SELECT c FROM (SELECT created_at AS c FROM orders) t WHERE c >= '2025-12-27'", "SELECT toTimeZone(created_at, '%s') FROM orders WHERE created_at >= %s" % (zone, at("2025-12-27 00:00:00"))),
        ("SELECT DATE_TRUNC('day', created_at) AS d, COUNT(*) FROM orders WHERE created_at >= '2025-03-08' AND created_at < '2025-03-11' GROUP BY 1", "SELECT %s AS d, count() FROM orders WHERE created_at >= %s AND created_at < %s GROUP BY d" % (bucket_day, at("2025-03-08 00:00:00"), at("2025-03-11 00:00:00"))),
    ]]


def tag(name, kind, **extra):
    return dict({"id": name, "name": name, "display-name": name, "type": kind}, **extra)


def shape_parameter(rng, src, zone):
    """A template variable and a field filter. A field filter names its field in the spec, and the runner finds its id."""
    variable = ["variable", ["template-tag", "x"]]
    dimension = ["dimension", ["template-tag", "x"]]
    kind = rng.randrange(13)
    if kind == 0:
        value = rng.choice(["shipped", "SHIPPED", "no such", "o'brien"])
        return spec("SELECT COUNT(*) FROM orders WHERE status = {{x}}", "SELECT count() FROM orders WHERE status = '%s'" % value.replace("'", "\\'"),
                    tags={"x": tag("x", "text")}, parameters=[{"type": "category", "target": variable, "value": value}])
    if kind == 1:
        value = rng.choice([44.001, 44, 12.5, 160])
        return spec("SELECT COUNT(*) FROM orders WHERE amount_dim >= {{x}}", "SELECT count() FROM orders WHERE amount >= %s" % value,
                    tags={"x": tag("x", "number")}, parameters=[{"type": "number/=", "target": variable, "value": str(value)}])
    if kind == 2:
        value = rng.choice(["ship", "SHIP", "%ship%", "s_ipped"])
        pattern = value if "%" in value or "_" in value else "%" + value + "%"
        written = "{{x}}" if "%" in value or "_" in value else "'%' || {{x}} || '%'"
        return spec("SELECT COUNT(*) FROM orders WHERE status LIKE %s" % written, "SELECT count() FROM orders WHERE status LIKE '%s'" % pattern,
                    tags={"x": tag("x", "text")}, parameters=[{"type": "category", "target": variable, "value": value}])
    if kind == 3:
        value = rng.choice(["2025-03-01", "2025-06-15"])
        return spec("SELECT COUNT(*) FROM orders WHERE created_at >= {{x}} AND created_at < '2025-07-01'",
                    "SELECT count() FROM orders WHERE created_at >= toDateTime('%s 00:00:00', '%s') AND created_at < toDateTime('2025-07-01 00:00:00', '%s')" % (value, ch_zone(zone), ch_zone(zone)),
                    tags={"x": tag("x", "date")}, parameters=[{"type": "date/single", "target": variable, "value": value}])
    field = lambda widget, table, column: tag("x", "dimension", **{"widget-type": widget, "field": [table, column]})  # noqa: E731
    if kind == 4:
        values = rng.choice([["shipped"], ["shipped", "completed"], ["SHIPPED"]])
        return spec("SELECT COUNT(*) FROM orders WHERE {{x}}", "SELECT count() FROM orders WHERE status IN (%s)" % ", ".join("'%s'" % v for v in values),
                    tags={"x": field("string/=", "orders", "status")}, parameters=[{"type": "string/=", "target": dimension, "value": values}])
    if kind == 5:
        values = rng.choice([["shipped"], ["shipped", "completed"]])
        return spec("SELECT COUNT(*) FROM orders WHERE {{x}}", "SELECT count() FROM orders WHERE nullIf(toString(status), 'completed') NOT IN (%s) OR status = 'completed'" % ", ".join("'%s'" % v for v in values),
                    tags={"x": field("string/!=", "orders", "cancel_status")}, parameters=[{"type": "string/!=", "target": dimension, "value": values}])
    if kind == 6:
        value = rng.choice(["ship", "SHIP", "hip"])
        return spec("SELECT COUNT(*) FROM orders WHERE {{x}}", "SELECT count() FROM orders WHERE status LIKE '%%%s%%'" % value,
                    tags={"x": field("string/contains", "orders", "status")}, parameters=[{"type": "string/contains", "target": dimension, "value": [value], "options": {"case-sensitive": True}}])
    if kind == 7:
        value = rng.choice(["ship", "SHIP", "s"])
        return spec("SELECT COUNT(*) FROM orders WHERE {{x}}", "SELECT count() FROM orders WHERE status LIKE '%s%%'" % value,
                    tags={"x": field("string/starts-with", "orders", "status")}, parameters=[{"type": "string/starts-with", "target": dimension, "value": [value], "options": {"case-sensitive": True}}])
    if kind == 8:
        low, high = rng.choice([(44.001, 50.5), (40, 50), (11, 11.5)])
        return spec("SELECT COUNT(*), MEASURE(revenue) FROM orders WHERE {{x}}", "SELECT count(), sum(amount) FROM orders WHERE amount BETWEEN %s AND %s" % (low, high) + NO_ROWS,
                    tags={"x": field("number/between", "orders", "amount_dim")}, parameters=[{"type": "number/between", "target": dimension, "value": [low, high]}], tol=1e-6)
    if kind == 9:
        low, high = rng.choice([("2025-03-01", "2025-03-31"), ("2025-03-05", "2025-04-02")])
        return spec("SELECT COUNT(*) FROM orders WHERE {{x}}",
                    "SELECT count() FROM orders WHERE created_at >= toDateTime('%s 00:00:00', '%s') AND created_at < toDateTime('%s 00:00:00', '%s') + INTERVAL 1 DAY" % (low, ch_zone(zone), high, ch_zone(zone)),
                    tags={"x": field("date/range", "orders", "created_at")}, parameters=[{"type": "date/range", "target": dimension, "value": "%s~%s" % (low, high)}])
    if kind == 10:
        value = rng.choice(["2025-03-09", "2025-06-15"])
        return spec("SELECT COUNT(*) FROM orders WHERE {{x}}",
                    "SELECT count() FROM orders WHERE created_at >= toDateTime('%s 00:00:00', '%s') AND created_at < toDateTime('%s 00:00:00', '%s') + INTERVAL 1 DAY" % (value, ch_zone(zone), value, ch_zone(zone)),
                    tags={"x": field("date/single", "orders", "created_at")}, parameters=[{"type": "date/single", "target": dimension, "value": value}])
    if kind == 11:
        return spec("SELECT COUNT(*) FROM orders WHERE {{x}}", "SELECT count() FROM orders",
                    tags={"x": field("string/=", "orders", "status")}, parameters=[])
    return spec("SELECT COUNT(*) FROM orders WHERE 1 = 1 [[AND status = {{x}}]]", "SELECT count() FROM orders", tags={"x": tag("x", "text")}, parameters=[])


def shape_no_from(rng, src, zone):
    value = rng.choice([("1 AS a", "1 AS a"), ("'x' AS a, 2 AS b", "'x' AS a, 2 AS b"), ("1.5 AS a", "1.5 AS a")])
    return spec("SELECT " + value[0], "SELECT " + value[1])


def shape_segment(rng, src, zone):
    source, name, truth = rng.choice([("orders", "paid", "status IN ('completed', 'shipped')"), ("orders", "large", "quantity >= 3"), ("users", "vip", "is_vip"), ("users", "texans", "state = 'TX'")])
    extra = rng.choice([("", ""), (" AND id > 10", " AND id > 10")])
    return spec("SELECT COUNT(*) FROM %s WHERE %s%s" % (source, name, extra[0]), "SELECT count() FROM %s WHERE %s%s" % (source, truth, extra[1]))


def shape_interval(rng, src, zone):
    """On the dialect. Stock Cube has no template, so it refuses."""
    amount = rng.choice([("1 day", "1 DAY"), ("2 hour", "2 HOUR"), ("1 month", "1 MONTH")])
    sign = rng.choice(["+", "-"])
    return spec("SELECT created_at %s INTERVAL '%s' AS d, MEASURE(count) AS n FROM orders WHERE id <= 3 GROUP BY 1" % (sign, amount[0]),
                "SELECT created_at %s INTERVAL %s AS d, count() FROM orders WHERE id <= 3 GROUP BY d" % (sign, amount[1]))


def shape_measure_over_raw(rng, src, zone):
    """The form of the docs of Cube: raw rows with a measure column in a subquery, and MEASURE() of it outside."""
    name = rng.choice(list(ORDERS["measures"]))
    where = rng.choice([("status = 'shipped'", "status = 'shipped'"), ("quantity_dim > 2", "quantity > 2"), ("status <> 'shipped'", "status <> 'shipped'")])
    return spec("SELECT MEASURE(%s) FROM (SELECT status, quantity_dim, %s FROM orders) AS s WHERE %s" % (name, name, where[0]), "SELECT %s FROM orders WHERE %s" % (ORDERS["measures"][name], where[1]) + NO_ROWS, tol=1e-6)


SHAPES = [(shape_no_from, 1), (shape_segment, 1), (shape_interval, 1), (shape_measure_over_raw, 1), (shape_total, 8), (shape_group, 8), (shape_raw, 4), (shape_star, 1), (shape_subquery, 3), (lambda r, s, z: shape_subquery(r, s, z, True), 2), (shape_union, 1), (shape_in_subquery, 1), (shape_distinct, 1), (shape_window, 1), (shape_case_select, 2),
          (shape_join, 3), (shape_view, 2), (shape_rollup, 2), (shape_parameter, 3)]
ZONES = [None, None, None, "America/New_York", "Asia/Kathmandu"]
BLOCK = 30


def zone_of(seed, index):
    return random.Random("%s/zone/%d" % (seed, index // BLOCK)).choice(ZONES)


def generate(seed, index):
    """One spec of native SQL: {sql, truth, zone, ...}."""
    rng = random.Random("%s/grammar/%d" % (seed, index))
    zone = zone_of(seed, index)
    src = rng.choice(SOURCES)
    shape = rng.choices([s for s, _ in SHAPES], weights=[w for _, w in SHAPES])[0]
    out = shape(rng, src, zone)
    out["zone"] = zone
    return out


# ---------------------------------------------------------------------------------------- what the driver must refuse

SELECT_ADDITIONS = ["NOW()", "VERSION()", "CURRENT_USER", "(SELECT 1)", "EXTRACT(YEAR FROM %(time)s)", "%(n)s::int", "CAST(%(n)s AS integer)", "COUNT(*) OVER w", "ARRAY[1, 2]", "ROW(1, 2)", "%(time)s AT TIME ZONE 'UTC'",
                    "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY %(n)s)", "%(n)s # 1", "%(n)s ^ 2", "~ %(n)s", "%(n)s & 1", "%(n)s | 1", "%(n)s << 1", "SUM(DISTINCT %(n)s)", "STDDEV(DISTINCT %(n)s)", "GROUPING(%(t)s)",
                    "TRIM(BOTH 'x' FROM %(t)s)", "TO_CHAR(%(time)s, 'YYYY')", "%(time)s - %(time)s", "pg_sleep(1)", "1 IN (SELECT 1)", "COUNT(*) FILTER (WHERE 1 = (SELECT 1))",
                    "MEASURE(%(n)s)", "MEDIAN(%(n)s)", "REGEXP_REPLACE(%(t)s, 'a', 'b')", "SUM(%(n)s) OVER (ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)"]
WHERE_ADDITIONS = ["%(t)s ~ 'a'", "%(t)s !~ 'a'", "%(t)s ~* 'a'", "%(t)s SIMILAR TO 'a'", "EXISTS (SELECT 1)", "%(n)s = ANY (ARRAY[1, 2])", "%(n)s > ALL (SELECT 1)", "(%(n)s <> 1) IS TRUE", "(%(n)s <> 1) IS NOT FALSE", "(%(n)s = 1) IS UNKNOWN",
                   "%(n)s = ABS(44.001)", "%(n)s = ROUND(44.001, 1)", "%(n)s = (SELECT 1)", "ROW(%(n)s, 1) = ROW(1, 1)", "%(n)s BETWEEN SYMMETRIC 1 AND 2", "%(t)s LIKE %(t)s", "%(t)s LIKE 'a' ESCAPE '!'", "%(n)s IN (1, %(n)s)",
                   "__user = 'a'", "%(t)s LIKE ANY (ARRAY['a'])", "%(n)s IS DISTINCT FROM %(n)s", "COUNT(*) > 1", "%(t)s = 1", "%(n)s = 'a'", "%(time)s = 'not a time'", "%(time)s > 5", "TRUE", "%(t)s", "(1, 2) = (1, 2)", "%(n)s = 12345678901234.56789",
                   "no_such_column = 1", "%(t)s COLLATE \"C\" = 'a'",]
FROM_MUTATIONS = [" CROSS JOIN LATERAL (SELECT 1) l", " NATURAL JOIN users", " FULL JOIN users u ON 1 = 1", " RIGHT JOIN users u ON u.id = 1", " JOIN users u ON u.city = 'a'", " TABLESAMPLE BERNOULLI (10)", ", generate_series(1, 3)", ", information_schema.tables",
                  " JOIN (SELECT 1 AS id) s ON s.id = 1", " JOIN users u USING (id)", " x (a, b)"]
TAILS = [" FOR UPDATE", " LIMIT 1 + 1", " LIMIT 1.5", " OFFSET %(n)s", " ORDER BY 1 NULLS FIRST", " ORDER BY 99", " INTERSECT SELECT 1", " EXCEPT SELECT 1", "; SELECT 1", " WINDOW w AS ()", " UNION SELECT 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13"]
GROUP_MUTATIONS = [" GROUP BY ROLLUP(%(t)s)", " GROUP BY CUBE(%(t)s)", " GROUP BY GROUPING SETS ((%(t)s), ())", " GROUP BY ALL", " GROUP BY 99"]
WHOLE = ["SELECT FROM payments", "SELECT id FROM payments", "SELECT p.id FROM payments p", "SELECT \"id\" FROM payments", "SELECT COUNT(*) FROM payments WHERE id > 5", "SELECT order_id FROM payments ORDER BY id", "SELECT SUM(id) FROM payments", "SELECT internal_note FROM orders", "SELECT status, internal_note FROM orders GROUP BY 1, 2", "SELECT MEASURE(public_false_measure) FROM orders", "SELECT * FROM pg_catalog.pg_class",
         "SELECT * FROM information_schema.columns", "WITH RECURSIVE t AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM t) SELECT * FROM t", "VALUES (1)", "SELECT DISTINCT ON (status) status FROM orders", "INSERT INTO orders VALUES (1)", "DROP TABLE orders",
         "SELECT AVG(DISTINCT id) FROM orders", "SELECT VARIANCE(id) FROM orders", "SHOW TABLES", "SET x = 1", "SELECT * FROM (VALUES (1)) v", "SELECT u.* FROM orders o JOIN users u ON o.__cubeJoinField = u.__cubeJoinField WHERE u.id IN (SELECT id FROM payments)", "SELECT status FROM orders; DROP TABLE orders", "SELECT location FROM users", "SELECT revenue FROM orders",
         "SELECT MAX(revenue) FROM orders", "SELECT status, COUNT(*) FROM orders", "SELECT paid FROM orders", "SELECT status FROM orders GROUP BY paid"]


def values_of(src):
    time_column = next(iter(src["time"]), "created_at")
    text_column = next(iter(src["text"]), "status")
    number_column = next(iter(src["number"]), "id")
    return {"time": time_column, "t": text_column, "n": number_column}


def mutate(seed, index):
    """A text that holds a construct that is not on the list, from a small query of the generator. The construct is in the reason."""
    rng = random.Random("%s/mutation/%d" % (seed, index))
    src = rng.choice([s for s in SOURCES if s["time"] and s["text"]])
    values = values_of(src)
    table = src["from"]
    base_query = "SELECT %(t)s, COUNT(*) AS n FROM " % values + table
    kind = rng.randrange(6)
    if kind == 0:
        construct = rng.choice(SELECT_ADDITIONS) % values
        sql = "SELECT %s AS added, %s FROM %s" % (construct, values["t"], table)
    elif kind == 1:
        construct = rng.choice(WHERE_ADDITIONS) % values
        joint = rng.choice(["%s", "status <> 'a' AND %s", "(%s) OR 1 = 1", "NOT (%s)"]) if table == "orders" else rng.choice(["%s", "NOT (%s)"])
        sql = "SELECT COUNT(*) FROM %s WHERE %s" % (table, joint % construct)
    elif kind == 2:
        construct = rng.choice(FROM_MUTATIONS) % values if "%" in "".join(FROM_MUTATIONS) else rng.choice(FROM_MUTATIONS)
        sql = "SELECT COUNT(*) FROM %s%s" % (table, construct)
    elif kind == 3:
        construct = rng.choice(TAILS) % values
        sql = base_query + " GROUP BY 1" + construct
    elif kind == 4:
        construct = rng.choice(GROUP_MUTATIONS) % values
        sql = base_query + construct
    else:
        construct = sql = rng.choice(WHOLE)
    return {"sql": "/* mut-%s */ %s" % (seed, sql), "construct": construct}


def pair_of(a, b):
    return pair(a, b)
