"""Random native SQL for the generator. Each query comes in two texts from one tree: the text for the SQL API of Cube, and the same meaning in direct ClickHouse SQL. It reads no driver code and no Cube output.

The constructs are those of "Native SQL" in docs/query-compilation.md: three-valued logic, numbers with a fraction in each form, FILTER, CASE, division, LIKE, HAVING, subqueries, WITH, UNION ALL, IN with a
subquery, window functions over groups and boolean expressions in SELECT. `generate` also gives a share of shapes that must stop with a clear message.
"""
import random

ORDERS = {
    "from": "orders",
    "ch": "orders",
    "text": {"status": "toString(status)", "cancel_status": "nullIf(toString(status), 'completed')"},
    "number": {"id": "id", "quantity_dim": "quantity", "amount_dim": "amount", "user_id": "user_id"},
    "bool": {},
    "text_values": ["completed", "shipped", "processing", "cancelled", "SHIPPED", "no such", ""],
    "fractions": {"amount_dim": [44.001, 43.999, 11.5, 160.01, 0.25], "quantity_dim": [1.5, 2.5], "id": [44.5]},
    "whole": {"amount_dim": [44, 11, 160, 0], "quantity_dim": [1, 2, 4, 0], "id": [1, 2500, 5001], "user_id": [1, 200]},
    "group": ["status", "cancel_status", "quantity_dim"],
}
TYPED = {
    "from": "typed_events",
    "ch": "typed_events",
    "text": {"n_str": "n_str", "lc": "lc", "lc_n": "lc_n"},
    "number": {"id": "id", "n_int": "n_int", "n_dec": "n_dec", "dec_amount": "dec_amount"},
    "bool": {"n_bool": "n_bool", "flag": "flag"},
    "text_values": ["s1", "s2", "alpha", "ALPHA", "no such", ""],
    "fractions": {"n_dec": [0.25, 0.251, 1.75, 2.501], "dec_amount": [12.5, 12.50001], "n_int": [1.5], "id": [3.5]},
    "whole": {"n_dec": [0, 1, 2], "dec_amount": [12, 13], "n_int": [0, 1, 2, 9], "id": [1, 500, 1001]},
    "group": ["lc", "lc_n", "n_str", "n_bool"],
}
SOURCES = [ORDERS, ORDERS, TYPED]


def pair(cube, ch=None):
    return (cube, cube if ch is None else ch)


def fmt(template, *parts):
    """`template` with %s for each part, on each text. A part can hold a % of a pattern, and this does not read it again."""
    return (template % tuple(p[0] for p in parts), template % tuple(p[1] for p in parts))


def column(src, name):
    for kind in ("text", "number", "bool"):
        if name in src[kind]:
            return (name, src[kind][name])
    raise KeyError(name)


def number_literal(rng, value):
    """A number in a form of the text. The truth has the plain number, because it must not go through the same cast."""
    text = repr(value)
    form = rng.randrange(7)
    ch = text
    if form == 1:
        return ("'%s'" % text, ch)
    if form == 2:
        return ("(%s)" % text, ch)
    if form == 3:
        return ("CAST(%s AS numeric)" % text, ch)
    if form == 4:
        return ("%s::numeric" % text, ch)
    if form == 5:
        return ("CAST('%s' AS numeric)" % text, ch)
    return (text, ch)


def number_column(rng, src, name):
    """A number column in a form of the text."""
    cube, ch = column(src, name)
    form = rng.randrange(8)
    if form == 1:
        return ("(%s)" % cube, ch)
    if form == 2:
        return ("CAST(%s AS numeric)" % cube, ch)
    if form == 3:
        return ("%s::numeric" % cube, ch)
    if form == 4:
        return ("%s + 0" % cube, "%s + 0" % ch)
    if form == 5:
        return ("COALESCE(%s, 0)" % cube, "coalesce(%s, 0)" % ch)
    return (cube, ch)


def number_value(rng, src, name):
    if rng.random() < 0.6 and name in src["fractions"]:
        return rng.choice(src["fractions"][name])
    return rng.choice(src["whole"].get(name, [1]))


def number_atom(rng, src):
    name = rng.choice(list(src["number"]))
    col = number_column(rng, src, name)
    value = number_value(rng, src, name)
    other = number_value(rng, src, name)
    kind = rng.randrange(7)
    if kind == 0:
        return fmt("%s BETWEEN %s AND %s", col, number_literal(rng, min(value, other)), number_literal(rng, max(value, other)))
    if kind == 1:
        return fmt("%s NOT BETWEEN %s AND %s", col, number_literal(rng, min(value, other)), number_literal(rng, max(value, other)))
    if kind == 2:
        return fmt("%s IN (%s, %s)", col, number_literal(rng, value), number_literal(rng, other))
    if kind == 3:
        return fmt("%s NOT IN (%s, %s)", col, number_literal(rng, value), number_literal(rng, other))
    op = rng.choice(["=", "<>", "!=", "<", "<=", ">", ">="])
    return fmt("%s " + op + " %s", col, number_literal(rng, value))


def text_atom(rng, src):
    name = rng.choice(list(src["text"]))
    col = column(src, name)
    value = rng.choice(src["text_values"])
    other = rng.choice(src["text_values"])
    quoted = pair("'%s'" % value)
    pattern = rng.choice(["%hip%", "%HIP%", "s%", "%d", "%1", "s_", "%", "alp%", "%pha%"])
    kind = rng.randrange(13)
    if kind == 0:
        return fmt("%s = %s", col, quoted)
    if kind == 1:
        return fmt("%s <> %s", col, quoted)
    if kind == 2:
        return fmt("%s != %s", col, quoted)
    if kind == 3:
        return fmt("%s IN (%s, '%s')" % ("%s", "%s", other), col, quoted)
    if kind == 4:
        return fmt("%s NOT IN (%s, '%s')" % ("%s", "%s", other), col, quoted)
    if kind == 5:
        return fmt("%s IS NULL", col)
    if kind == 6:
        return fmt("%s IS NOT NULL", col)
    if kind == 7:
        return fmt("%s IS DISTINCT FROM %s", col, quoted)
    if kind == 8:
        return (("%s LIKE '%s'" % (col[0], pattern)), ("%s LIKE '%s'" % (col[1], pattern)))
    if kind == 9:
        return (("%s NOT LIKE '%s'" % (col[0], pattern)), ("%s NOT LIKE '%s'" % (col[1], pattern)))
    if kind == 10:
        return (("%s ILIKE '%s'" % (col[0], pattern)), ("%s ILIKE '%s'" % (col[1], pattern)))
    if kind == 11:
        return (("%s NOT ILIKE '%s'" % (col[0], pattern)), ("%s NOT ILIKE '%s'" % (col[1], pattern)))
    return (("(%s) <> '%s'" % (col[0], value)), ("%s <> '%s'" % (col[1], value)))


def bool_atom(rng, src):
    name = rng.choice(list(src["bool"]))
    cube, ch = column(src, name)
    return rng.choice([(cube, ch),
                       ("NOT %s" % cube, "NOT %s" % ch),
                       ("%s = TRUE" % cube, "%s = true" % ch),
                       ("%s = FALSE" % cube, "%s = false" % ch),
                       ("%s <> TRUE" % cube, "%s <> true" % ch),
                       ("%s IS TRUE" % cube, "ifNull(%s = true, false)" % ch),
                       ("%s IS NOT TRUE" % cube, "NOT ifNull(%s = true, false)" % ch),
                       ("%s IS FALSE" % cube, "ifNull(%s = false, false)" % ch)])


def predicate(rng, src, depth=2):
    roll = rng.random()
    if depth > 0 and roll < 0.25:
        return fmt("(%s AND %s)", predicate(rng, src, depth - 1), predicate(rng, src, depth - 1))
    if depth > 0 and roll < 0.45:
        return fmt("(%s OR %s)", predicate(rng, src, depth - 1), predicate(rng, src, depth - 1))
    if depth > 0 and roll < 0.65:
        return fmt("NOT (%s)", predicate(rng, src, depth - 1))
    if src["bool"] and rng.random() < 0.12:
        return bool_atom(rng, src)
    return number_atom(rng, src) if rng.random() < 0.5 else text_atom(rng, src)


def real(rng, src, name):
    """A number as a Float64 in the truth: Decimal / x keeps the scale of the Decimal on ClickHouse, and SQL does not."""
    cube, ch = column(src, name)
    return (cube, "toFloat64(%s)" % ch)


def aggregate(rng, src):
    """(cube text, ClickHouse text, kind)."""
    name = rng.choice(list(src["number"]))
    col = real(rng, src, name)
    tname = rng.choice(list(src["text"]))
    tcol = column(src, tname)
    kind = rng.randrange(14)
    pred = predicate(rng, src, 1)
    if kind == 0:
        return ("COUNT(*)", "count()")
    if kind == 1:
        return fmt("COUNT(%s)", tcol)
    if kind == 2:
        return (("COUNT(DISTINCT %s)" % tcol[0]), ("uniqExact(%s)" % tcol[1]))
    if kind == 3:
        return fmt("SUM(%s)", col)
    if kind == 4:
        return fmt("AVG(%s)", col)
    if kind == 5:
        return fmt("MIN(%s)", col)
    if kind == 6:
        return fmt("MAX(%s)", col)
    if kind == 7:
        return ("COUNT(*) FILTER (WHERE %s)" % pred[0], "countIf(ifNull(%s, false))" % pred[1])
    if kind == 8:
        return ("SUM(%s) FILTER (WHERE %s)" % (col[0], pred[0]), "sumIf(%s, ifNull(%s, false))" % (col[1], pred[1]))
    if kind == 9:
        return ("MAX(%s) FILTER (WHERE %s)" % (col[0], pred[0]), "maxIf(%s, ifNull(%s, false))" % (col[1], pred[1]))
    if kind == 10:
        return ("COUNT(%s) FILTER (WHERE %s)" % (tcol[0], pred[0]), "countIf(%s, ifNull(%s, false))" % (tcol[1], pred[1]))
    if kind == 11:
        return ("SUM(CASE WHEN %s THEN 1 ELSE 0 END)" % pred[0], "sum(if(ifNull(%s, false), 1, 0))" % pred[1])
    if kind == 12:
        divisor = rng.choice([3, 7, 0.5])
        return ("MAX(%s / %s)" % (col[0], divisor), "max(%s / %s)" % (col[1], divisor))
    return ("SUM(%s) / COUNT(*)" % col[0], "sum(%s) / nullIf(toFloat64(count()), 0)" % col[1])


NULL_FOR_EMPTY = " SETTINGS aggregate_functions_null_for_empty = 1"


def group_key(rng, src):
    name = rng.choice(src["group"])
    return column(src, name)


def shape_total(rng, src):
    parts = [aggregate(rng, src) for _ in range(rng.choice([1, 1, 2]))]
    where = predicate(rng, src) if rng.random() < 0.7 else None
    cube = "SELECT %s FROM %s" % (", ".join(p[0] for p in parts), src["from"])
    ch = "SELECT %s FROM %s" % (", ".join(p[1] for p in parts), src["ch"])
    if where:
        cube += " WHERE " + where[0]
        ch += " WHERE " + where[1]
    return {"sql": cube, "truth": ch + NULL_FOR_EMPTY}


def shape_group(rng, src):
    key = group_key(rng, src)
    part = aggregate(rng, src)
    where = predicate(rng, src, 1) if rng.random() < 0.5 else None
    cube = "SELECT %s, %s FROM %s" % (key[0], part[0], src["from"])
    ch = "SELECT %s AS k, %s FROM %s" % (key[1], part[1], src["ch"])
    if where:
        cube += " WHERE " + where[0]
        ch += " WHERE " + where[1]
    cube += " GROUP BY 1"
    ch += " GROUP BY k"
    if rng.random() < 0.5:
        having = rng.choice([("COUNT(*) > %s", "count() > %s", rng.choice([1, 100.5, 1000.5, 1999.5])),
                             ("SUM(%s) >= %%s" % "id", "sum(id) >= %s", rng.choice([100.5, 1000000.25])),
                             ("%s <> 'shipped'" % key[0], "k <> 'shipped'", None)])
        if having[2] is not None:
            cube += " HAVING " + having[0] % having[2]
            ch += " HAVING " + having[1] % having[2]
        elif key[0] in src["text"]:
            cube += " HAVING " + having[0]
            ch += " HAVING " + having[1]
    return {"sql": cube, "truth": ch + NULL_FOR_EMPTY}


def shape_subquery(rng, src, with_clause=False):
    name = rng.choice(list(src["number"]))
    col = column(src, name)
    tname = rng.choice(list(src["text"]))
    tcol = column(src, tname)
    inner_where = predicate(rng, src, 1) if rng.random() < 0.4 else None
    value = number_value(rng, src, name)
    outer = rng.choice([
        (("a %s %s" % (rng.choice(["=", "<>", ">=", "<"]), value)), None),
        ("(a) >= '%s'" % value, "a >= %s" % value),
        ("b <> 'shipped'", None),
        ("NOT (b = 'shipped')", "NOT (b = 'shipped')"),
        ("b NOT IN ('shipped', 'no such')", None),
    ])
    outer_ch = outer[1] or outer[0]
    agg = rng.choice([("COUNT(*)", "count()"), ("COUNT(a)", "count(a)"), ("SUM(a)", "sum(toFloat64(a))"), ("MAX(a)", "max(toFloat64(a))"),
                      ("COUNT(*) FILTER (WHERE b <> 'shipped')", "countIf(ifNull(b <> 'shipped', false))")])
    inner_cube = "SELECT %s AS a, %s AS b FROM %s" % (col[0], tcol[0], src["from"])
    inner_ch = "SELECT %s AS a, %s AS b FROM %s" % (col[1], tcol[1], src["ch"])
    if inner_where:
        inner_cube += " WHERE " + inner_where[0]
        inner_ch += " WHERE " + inner_where[1]
    if with_clause:
        cube = "WITH t AS (%s) SELECT %s FROM t WHERE %s" % (inner_cube, agg[0], outer[0])
    else:
        cube = "SELECT %s FROM (%s) t WHERE %s" % (agg[0], inner_cube, outer[0])
    ch = "SELECT %s FROM (%s) WHERE %s" % (agg[1], inner_ch, outer_ch)
    return {"sql": cube, "truth": ch + NULL_FOR_EMPTY}


def shape_union(rng, src):
    a, b = aggregate(rng, src), aggregate(rng, src)
    pa, pb = predicate(rng, src, 1), predicate(rng, src, 1)
    cube = "SELECT %s FROM %s WHERE %s UNION ALL SELECT %s FROM %s WHERE %s" % (a[0], src["from"], pa[0], b[0], src["from"], pb[0])
    ch = "SELECT toNullable(toFloat64(%s)) FROM %s WHERE %s UNION ALL SELECT toNullable(toFloat64(%s)) FROM %s WHERE %s" % (a[1], src["ch"], pa[1], b[1], src["ch"], pb[1])
    return {"sql": cube, "truth": ch + NULL_FOR_EMPTY, "tol": 1e-6}


def shape_boolean(rng, src):
    pred = predicate(rng, src, 1)
    cube = "SELECT %s AS b, COUNT(*) FROM %s GROUP BY 1" % (pred[0], src["from"])
    ch = "SELECT toBool(%s) AS b, count() FROM %s GROUP BY b" % (pred[1], src["ch"])
    return {"sql": cube, "truth": ch}


def shape_raw(rng, src):
    col = column(src, rng.choice(list(src["text"])))
    where = predicate(rng, src, 1)
    cube = "SELECT id, %s FROM %s WHERE %s ORDER BY id LIMIT 5" % (col[0], src["from"], where[0])
    ch = "SELECT id, %s FROM %s WHERE %s ORDER BY id LIMIT 5" % (col[1], src["ch"], where[1])
    return {"sql": cube, "truth": ch, "ordered": True}


def shape_limited(rng, src):
    key = group_key(rng, src)
    cube = "SELECT SUM(c) FROM (SELECT %s AS k, COUNT(*) AS c FROM %s GROUP BY 1 LIMIT 100000) t" % (key[0], src["from"])
    ch = "SELECT sum(c) FROM (SELECT %s AS k, count() AS c FROM %s GROUP BY k)" % (key[1], src["ch"])
    return {"sql": cube, "truth": ch}


def shape_window(rng, src):
    key = group_key(rng, src)
    cube = "SELECT %s, SUM(COUNT(*)) OVER () FROM %s GROUP BY 1" % (key[0], src["from"])
    ch = "SELECT %s AS k, sum(count()) OVER () FROM %s GROUP BY k" % (key[1], src["ch"])
    return {"sql": cube, "truth": ch}


def shape_in_subquery(rng, src):
    where = predicate(rng, src, 1)
    op = rng.choice(["IN", "NOT IN"])
    cube = "SELECT COUNT(*) FROM %s WHERE id %s (SELECT id FROM %s WHERE %s)" % (src["from"], op, src["from"], where[0])
    ch = "SELECT count() FROM %s WHERE id %s (SELECT id FROM %s WHERE %s)" % (src["ch"], op, src["ch"], where[1])
    return {"sql": cube, "truth": ch}


def shape_refused(rng, src):
    name = rng.choice(list(src["number"]))
    key = rng.choice(src["group"])
    return {"sql": rng.choice([
        "SELECT %s, COUNT(*) FROM %s GROUP BY ROLLUP(%s)" % (key, src["from"], key),
        "SELECT %s, COUNT(*) FROM %s GROUP BY CUBE(%s)" % (key, src["from"], key),
        "SELECT %s, COUNT(*) FROM %s GROUP BY GROUPING SETS ((%s), ())" % (key, src["from"], key),
        "SELECT SUM(DISTINCT %s) FROM %s" % (name, src["from"]),
        "SELECT COUNT(*) FROM %s a CROSS JOIN %s b" % (src["from"], src["from"]),
        "SELECT %s FROM %s ORDER BY 1 NULLS FIRST" % (key, src["from"]),
        "SELECT %s / 0 FROM %s LIMIT 1" % (name, src["from"]),
    ]), "expect": "refusal", "truth": None}


SHAPES = [(shape_total, 6), (shape_group, 4), (shape_subquery, 3), (lambda rng, src: shape_subquery(rng, src, True), 2), (shape_union, 2), (shape_boolean, 2), (shape_raw, 2),
          (shape_limited, 1), (shape_window, 1), (shape_in_subquery, 2), (shape_refused, 1)]


def generate(seed, index):
    """One native spec. The caller gives the settings of the block."""
    rng = random.Random("%s/native/%d" % (seed, index))
    src = rng.choice(SOURCES)
    shape = rng.choices([s for s, _ in SHAPES], weights=[w for _, w in SHAPES])[0]
    spec = {"native": shape(rng, src)}
    return spec
