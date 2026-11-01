"""Cases of three release blockers, with the truth in direct ClickHouse SQL: a list of numbers with a fraction, an aggregation of a joined column, and native SQL on a private member.

A query is JSON with names: `d:orders.status`, `d:orders>products.price` through a foreign key, `d:products.price|P` through the join alias P, `@month` for a bucket.
"""
import re


def fractions(n, base=44.0, step=0.001):
    return [round(base + step * (i + 1), 4) for i in range(n)]


def sql_list(values):
    return ",".join(str(v) for v in values)


def case(case_id, table, truth, refusal=None, parameters=None, ordered=False, tol=1e-6, **query):
    """`refusal` is a text that a clear message may have. With no `refusal`, only the truth passes."""
    out = {"source-table": "t:" + table} if table else {}
    out.update({k.replace("_", "-"): v for k, v in query.items()})
    return {"id": case_id, "query": out, "truth": truth, "refusal": refusal, "parameters": parameters, "ordered": ordered, "tol": tol}


AMOUNT = "d:orders.amount_dim"
SMALL_FRACTIONS = [round(0.25 + 0.00001 * (i + 1), 5) for i in range(7)]


def fraction_cases(stock=False):
    """On stock Cube, a sum of no rows is 0. "Known wrong numbers" in docs/query-compilation.md has this limit."""
    out = []
    empty_sum = "sum(amount)" if stock else "sumOrNull(amount)"
    for n in (1, 7, 8, 9, 30):
        v = fractions(n)
        where = "amount IN (%s)" % sql_list(v)
        out.append(case("fraction/count/%d" % n, "orders", "SELECT count() FROM orders WHERE " + where, aggregation=[["count"]], filter=["=", AMOUNT, *v]))
        out.append(case("fraction/revenue/%d" % n, "orders", "SELECT %s FROM orders WHERE %s" % (empty_sum, where), aggregation=[["sum", "d:orders.revenue"]], filter=["=", AMOUNT, *v]))
    v = fractions(8)
    where = "amount IN (%s)" % sql_list(v)
    thousand = fractions(1000, step=0.0001)
    out += [
        case("fraction/1000", "orders", "SELECT count() FROM orders WHERE amount IN (%s)" % sql_list(thousand), aggregation=[["count"]], filter=["=", AMOUNT, *thousand]),
        case("fraction/and", "orders", "SELECT count() FROM orders WHERE %s AND status = 'completed'" % where, aggregation=[["count"]], filter=["and", ["=", AMOUNT, *v], ["=", "d:orders.status", "completed"]]),
        case("fraction/by_status", "orders", "SELECT toString(status), count() FROM orders WHERE %s GROUP BY 1" % where, aggregation=[["count"]], breakout=["d:orders.status"], filter=["=", AMOUNT, *v]),
        case("fraction/in_or", "orders", "SELECT count() FROM orders WHERE %s OR status = 'no such'" % where, aggregation=[["count"]], filter=["or", ["=", AMOUNT, *v], ["=", "d:orders.status", "no such"]]),
        case("fraction/or_of_lists", "orders", "SELECT count() FROM orders WHERE amount IN (%s)" % sql_list(fractions(16)), aggregation=[["count"]], filter=["or", ["=", AMOUNT, *fractions(8)], ["=", AMOUNT, *fractions(16)[8:]]]),
        case("fraction/not_in", "orders", "SELECT count() FROM orders WHERE amount NOT IN (%s)" % sql_list(v), aggregation=[["count"]], filter=["!=", AMOUNT, *v]),
        case("fraction/not_of_list", "orders", "SELECT count() FROM orders WHERE amount NOT IN (%s)" % sql_list(v), aggregation=[["count"]], filter=["not", ["=", AMOUNT, *v]]),
        case("fraction/count_where", "orders", "SELECT countIf(%s) FROM orders" % where, aggregation=[["count-where", ["=", AMOUNT, *v]]]),
        case("fraction/sum_where", "orders", "SELECT sumIf(amount, %s) FROM orders" % where, aggregation=[["sum-where", AMOUNT, ["=", AMOUNT, *v]]]),
        case("fraction/parameter", "orders", "SELECT count() FROM orders WHERE " + where, aggregation=[["count"]], parameters=[{"type": "number/=", "target": ["dimension", AMOUNT], "value": v}]),
        case("fraction/view", "metric_types", "SELECT count() FROM orders WHERE " + where, aggregation=[["count"]], filter=["=", "d:metric_types.amount", *v]),
        # A value that exists, next to values with more digits than the column.
        case("fraction/decimal_18_4", "typed_events", "SELECT count() FROM typed_events WHERE dec_amount IN (%s)" % sql_list([12.5, *SMALL_FRACTIONS]), aggregation=[["count"]],
             filter=["=", "d:typed_events.dec_amount", 12.5, *SMALL_FRACTIONS]),
        case("fraction/nullable_decimal", "typed_events", "SELECT countIf(n_dec IN (0.25,0.5,0.75,1.5,1.75,2.25,2.5,0.251)) FROM typed_events", aggregation=[["count"]], filter=["=", "d:typed_events.n_dec", 0.25, 0.5, 0.75, 1.5, 1.75, 2.25, 2.5, 0.251]),
        case("fraction/integer_column", "orders", "SELECT count() FROM orders WHERE quantity IN (%s)" % sql_list(i + 1.5 for i in range(8)), aggregation=[["count"]], filter=["=", "d:orders.quantity_dim", *[i + 1.5 for i in range(8)]]),
        case("fraction/float", "typed_events", "SELECT count() FROM typed_events WHERE f32 IN (%s)" % sql_list(0.25 * i for i in range(1, 9)), aggregation=[["count"]], filter=["=", "d:typed_events.f32", *[0.25 * i for i in range(1, 9)]]),
        # These list forms keep their digits at the same length.
        case("list/big_integer", "typed_events", "SELECT count() FROM typed_events WHERE ui64 IN (%s)" % sql_list(9007199254740993 + i for i in range(8)), aggregation=[["count"]], filter=["=", "d:typed_events.ui64", *[9007199254740993 + i for i in range(8)]]),
        case("list/whole_number", "orders", "SELECT count() FROM orders WHERE amount IN (44,56,69,144,45,46,47,48)", aggregation=[["count"]], filter=["=", AMOUNT, 44, 56, 69, 144, 45, 46, 47, 48]),
        case("list/date", "typed_events", "SELECT count() FROM typed_events WHERE day IN (%s)" % ",".join("'2025-03-%02d'" % (i + 1) for i in range(8)), aggregation=[["count"]], filter=["=", "d:typed_events.day", *["2025-03-%02d" % (i + 1) for i in range(8)]]),
        case("list/time", "typed_events", "SELECT count() FROM typed_events WHERE ts_utc IN (%s)" % ",".join("toDateTime('2025-03-01 %02d:00:00','UTC')" % i for i in range(8)), aggregation=[["count"]], filter=["=", "d:typed_events.ts_utc", *["2025-03-01T%02d:00:00" % i for i in range(8)]]),
        case("list/unusual_text", "typed_events", "SELECT countIf(n_str IN ('o\\'brien','a\\\\b','','%','_','s1','s2','ünï')) FROM typed_events", aggregation=[["count"]], filter=["=", "d:typed_events.n_str", "o'brien", "a\\b", "", "%", "_", "s1", "s2", "ünï"]),
        case("list/quotes_and_marks", "typed_events", "SELECT countIf(n_str IN ('s1\\'','s2\\\\','\\\\','\\'','\"x','a,b','(','s3',';--')) FROM typed_events", aggregation=[["count"]], filter=["=", "d:typed_events.n_str", "s1'", "s2\\", "\\", "'", '"x', "a,b", "(", "s3", ";--"]),
    ]
    return out


NATIVE_FRACTIONS = [
    ("SELECT COUNT(*) FROM orders WHERE amount_dim = 44.001", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE 44.001 = amount_dim", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders o WHERE o.amount_dim = 44.001", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim IN (44.001, 44.002, 44.003)", "SELECT count() FROM orders WHERE amount IN (44.001, 44.002, 44.003)"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim IN (%s)" % sql_list(fractions(8)), "SELECT count() FROM orders WHERE amount IN (%s)" % sql_list(fractions(8))),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim NOT IN (44.001, 44.002, 44.003)", "SELECT count() FROM orders WHERE amount NOT IN (44.001, 44.002, 44.003)"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim < 44.001", "SELECT count() FROM orders WHERE amount < 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim <> 44.001", "SELECT count() FROM orders WHERE amount <> 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim BETWEEN 44.001 AND 44.009", "SELECT count() FROM orders WHERE amount BETWEEN 44.001 AND 44.009"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim BETWEEN 43 AND 44.001", "SELECT count() FROM orders WHERE amount BETWEEN 43 AND 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE quantity_dim = 1.5", "SELECT count() FROM orders WHERE quantity = 1.5"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim = 44.0", "SELECT count() FROM orders WHERE amount = 44"),
    ("SELECT status, COUNT(*) FROM orders WHERE amount_dim > 44.5 GROUP BY 1", "SELECT toString(status), count() FROM orders WHERE amount > 44.5 GROUP BY 1"),
    ("SELECT COUNT(*) FROM orders WHERE status = 'o.5' OR amount_dim = 44.5", "SELECT count() FROM orders WHERE status = 'o.5' OR amount = 44.5"),
]

PRICE = "d:orders>products.price"
JOINED = "orders o LEFT JOIN products p ON o.product_id = p.id"
EXPLICIT = [{"source-table": "t:products", "alias": "P", "strategy": "left-join", "condition": ["=", "d:orders.product_id", "d:products.id|P"]}]
# Cube has no form of these from groups, so a clear message is the right answer.
NO_FORM = "Cube cannot"


def aggregations(x):
    """(name, clause, SQL over the join, a refusal that may stand for the number)."""
    return [
        ("sum", ["sum", x], "sumOrNull(toFloat64(p.price))", None),
        ("avg", ["avg", x], "avgOrNull(toFloat64(p.price))", None),
        ("min", ["min", x], "minOrNull(toFloat64(p.price))", None),
        ("max", ["max", x], "maxOrNull(toFloat64(p.price))", None),
        ("distinct", ["distinct", x], "uniqExact(p.price)", None),
        ("count", ["count", x], "count(p.price)", None),
        ("median", ["median", x], "quantileExact(0.5)(toFloat64(p.price))", NO_FORM),
        ("percentile", ["percentile", x, 0.9], "quantileExact(0.9)(toFloat64(p.price))", NO_FORM),
        ("stddev", ["stddev", x], "stddevPop(toFloat64(p.price))", NO_FORM),
        ("var", ["var", x], "varPop(toFloat64(p.price))", NO_FORM),
        ("count_where", ["count-where", [">", x, 50]], "countIf(p.price > 50)", None),
        ("sum_where", ["sum-where", x, [">", x, 50]], "sumIf(toFloat64(p.price), p.price > 50)", None),
        ("share", ["share", [">", x, 50]], "countIf(p.price > 50) / count()", None),
    ]


def clause_of(name, x):
    return next(a for a in aggregations(x) if a[0] == name)[1]


def join_cases():
    out = []
    for name, clause, sql, refusal in aggregations(PRICE):
        raw = {"source-table": "t:orders", "fields": ["d:orders.id", "d:products.price|P"], "joins": [dict(EXPLICIT[0], fields="none")]}
        out += [
            case("join/%s/total" % name, "orders", "SELECT %s FROM %s" % (sql, JOINED), refusal, aggregation=[clause]),
            case("join/%s/by_status" % name, "orders", "SELECT toString(o.status), %s FROM %s GROUP BY 1" % (sql, JOINED), refusal, aggregation=[clause], breakout=["d:orders.status"]),
            case("join/%s/by_joined_column" % name, "orders", "SELECT p.category, %s FROM %s GROUP BY 1" % (sql, JOINED), refusal, aggregation=[clause], breakout=["d:orders>products.category"]),
            case("join/%s/by_month" % name, "orders", "SELECT toStartOfMonth(o.created_at), %s FROM %s GROUP BY 1" % (sql, JOINED), refusal, aggregation=[clause], breakout=["d:orders.created_at@month"]),
            case("join/%s/with_filter" % name, "orders", "SELECT %s FROM %s WHERE o.status = 'completed'" % (sql, JOINED), refusal, aggregation=[clause], filter=["=", "d:orders.status", "completed"]),
            case("join/%s/custom_column" % name, "orders", "SELECT %s FROM %s" % (sql, JOINED), refusal, aggregation=[clause_of(name, ["expression", "e"])], expressions={"e": ["*", PRICE, 1]}),
            case("join/%s/explicit" % name, "orders", "SELECT %s FROM %s" % (sql, JOINED), refusal, aggregation=[clause_of(name, "d:products.price|P")], joins=EXPLICIT),
            case("join/%s/later_stage" % name, None, "SELECT %s FROM %s" % (sql, JOINED), refusal, source_query=raw, aggregation=[clause_of(name, ["field", "P__price", {"base-type": "type/Float"}])]),
        ]
    month = "SELECT m, sum(v) OVER (ORDER BY m) FROM (SELECT toStartOfMonth(o.created_at) m, %s v FROM " + JOINED + " GROUP BY 1) ORDER BY m"
    status = "SELECT s, sum(v) OVER (ORDER BY s) FROM (SELECT toString(o.status) s, %s v FROM " + JOINED + " GROUP BY 1) ORDER BY s"
    total = "toFloat64(sum(p.price))"
    counted = "count(p.price)"
    out += [
        case("join/cum_sum/total", "orders", "SELECT %s FROM %s" % (total, JOINED), aggregation=[["cum-sum", PRICE]]),
        case("join/cum_sum/by_month", "orders", month % total, ordered=True, aggregation=[["cum-sum", PRICE]], breakout=["d:orders.created_at@month"]),
        case("join/cum_sum/by_status", "orders", status % total, ordered=True, aggregation=[["cum-sum", PRICE]], breakout=["d:orders.status"]),
        case("join/cum_sum/explicit", "orders", "SELECT %s FROM %s" % (total, JOINED), aggregation=[["cum-sum", "d:products.price|P"]], joins=EXPLICIT),
        case("join/cum_count/total", "orders", "SELECT %s FROM %s" % (counted, JOINED), aggregation=[["cum-count", PRICE]]),
        case("join/cum_count/by_month", "orders", month % counted, ordered=True, aggregation=[["cum-count", PRICE]], breakout=["d:orders.created_at@month"]),
        case("join/cum_count/rows_by_month", "orders", month % "count()", ordered=True, aggregation=[["cum-count"]], breakout=["d:orders.created_at@month"]),
        case("join/count/two_aggregations", "orders", "SELECT count(p.price), toFloat64(sum(p.price)) FROM %s" % JOINED, aggregation=[["count", PRICE], ["sum", PRICE]]),
        case("join/count/in_an_expression", "orders", "SELECT count(p.price) + 1 FROM %s" % JOINED, aggregation=[["+", ["count", PRICE], 1]]),
        case("join/count/text_column", "orders", "SELECT count(p.name) FROM %s" % JOINED, aggregation=[["count", "d:orders>products.name"]]),
        # A row with no match counts in the rows and not in the column.
        case("join/count/no_match", "orphan_orders", "SELECT count(c.name) FROM orphan_orders o LEFT JOIN orphan_customers c ON o.customer_id = c.id SETTINGS join_use_nulls = 1", aggregation=[["count", "d:orphan_orders>orphan_customers.name"]]),
        case("join/own_column/count", "orders", "SELECT count(amount) FROM orders", aggregation=[["count", AMOUNT]]),
        case("join/own_column/cum_sum", "orders", "SELECT m, sum(v) OVER (ORDER BY m) FROM (SELECT toStartOfMonth(created_at) m, sum(amount) v FROM orders GROUP BY 1) ORDER BY m", ordered=True, aggregation=[["cum-sum", AMOUNT]], breakout=["d:orders.created_at@month"]),
        case("join/later_stage_over_groups", None, "SELECT count() FROM %s" % JOINED, source_query={"source-table": "t:orders", "breakout": ["d:orders>products.price"], "aggregation": [["count"]]},
             aggregation=[["sum", ["field", "count", {"base-type": "type/Integer"}]]]),
    ]
    view = "orders o LEFT JOIN users u ON o.user_id = u.id"
    city = "d:sales.users_city"
    for name, clause, sql in (("count", ["count", city], "count(u.city)"), ("distinct", ["distinct", city], "uniqExact(u.city)"), ("min", ["min", city], "min(u.city)"),
                              ("max", ["max", city], "max(u.city)"), ("count_where", ["count-where", ["=", city, "Austin"]], "countIf(u.city = 'Austin')"),
                              ("share", ["share", ["=", city, "Austin"]], "countIf(u.city = 'Austin') / count()"),
                              ("sum_where", ["sum-where", "d:sales.id", ["=", city, "Austin"]], "sumIf(o.id, u.city = 'Austin')")):
        out.append(case("view/%s/total" % name, "sales", "SELECT %s FROM %s" % (sql, view), aggregation=[clause]))
        out.append(case("view/%s/by_status" % name, "sales", "SELECT toString(o.status), %s FROM %s GROUP BY 1" % (sql, view), aggregation=[clause], breakout=["d:sales.status"]))
    out.append(case("view/cum_count/total", "sales", "SELECT count(u.city) FROM %s" % view, aggregation=[["cum-count", city]]))
    out.append(case("view/cum_count/by_status", "sales", "SELECT s, sum(v) OVER (ORDER BY s) FROM (SELECT toString(o.status) s, count(u.city) v FROM %s GROUP BY 1) ORDER BY s" % view, ordered=True,
                    aggregation=[["cum-count", city]], breakout=["d:sales.status"]))
    return out


# Each of these reads a private member, or tries to. A Cube in dev mode gives the value if the scan lets the text through.
PRIVATE_SQL = [
    "SELECT ALL * FROM payments LIMIT 1",
    "SELECT all * FROM payments LIMIT 1",
    "SELECT ALL payments.* FROM payments LIMIT 1",
    "SELECT DISTINCT * FROM payments LIMIT 1",
    "SELECT DISTINCT ON (order_id) * FROM payments LIMIT 1",
    "SELECT * FROM payments, orders LIMIT 1",
    'SELECT x.* FROM payments "x" LIMIT 1',
    'SELECT MAX("limit".id) FROM payments AS "limit"',
    'SELECT MAX("where".id) FROM payments "where"',
    'SELECT MAX("order".id) FROM payments "order"',
    'SELECT MAX("group"."id") FROM payments "group"',
    'SELECT MAX("from"."id") FROM payments AS "from"',
    'SELECT MAX("ID") FROM payments',
    'SELECT MAX("id") FROM public.payments',
    'SELECT MAX(public.payments.id) FROM public.payments',
    "SELECT E'\\'' AS a, MAX(id) FROM payments -- '",
    "SELECT e'\\'' AS a, MAX(id) FROM payments -- '",
    "SELECT E'\\\\' AS a, MAX(id) FROM payments -- '",
    "SELECT E'a\\'' 'b' AS a, MAX(id) FROM payments -- '",
    "SELECT $$'$$ AS a, MAX(id) FROM payments -- '",
    "SELECT $t$'$t$ AS a, MAX(id) FROM payments -- '",
    "SELECT 'a\\' AS a, MAX(id) FROM payments -- '",
    "SELECT /* /* nested */ ' */ MAX(id) AS x FROM payments --'",
    "SELECT /* a */ MAX(/* b */ id) FROM payments",
    "SELECT 1 -- a\r, MAX(id) FROM payments\n",
    'SELECT U&"i\\0064" FROM payments LIMIT 1',
    "SELECT MAX(p.id) FROM payments p WHERE EXISTS (SELECT 1 FROM orders p)",
    "SELECT MAX(p.id) FROM orders p, payments p",
    "SELECT orders.id FROM payments orders LIMIT 1",
    "SELECT COUNT(*) FROM (SELECT NULL AS id, method FROM payments) t WHERE t.id > 100000",
    "SELECT COUNT(*) FROM (SELECT NULL AS id, method FROM payments) t WHERE id > 100000",
    'SELECT COUNT(*) FROM (SELECT NULL AS "ID", method FROM payments) t WHERE t."ID" > 100000',
    'SELECT COUNT(*) FROM (SELECT NULL AS id, method FROM payments) "where" WHERE "where".id > 100000',
    "SELECT COUNT(*) FROM (SELECT NULL id, method FROM payments) t WHERE t.id > 100000",
    "SELECT COUNT(*) FROM (SELECT CAST(NULL AS int) AS id, method FROM payments) t WHERE t.id > 100000",
    "WITH t AS (SELECT NULL AS id, method FROM payments) SELECT COUNT(*) FROM t WHERE t.id > 100000",
    "SELECT COUNT(*) FROM (SELECT NULL AS internal_note, status FROM orders) x WHERE x.internal_note = 'x'",
    "SELECT MAX(x) FROM (SELECT id AS x FROM payments) t",
    "SELECT t.id FROM (SELECT id FROM payments) t LIMIT 1",
    "SELECT p.order_id FROM payments p ORDER BY id LIMIT 1",
    "SELECT MAX(internal_note) FROM orders",
    "SELECT MEASURE(public_false_measure) FROM orders",
    'SELECT MEASURE("o".public_false_measure) FROM orders "o"',
    "SELECT COUNT(*) FROM secret_audit",
    'SELECT COUNT(*) FROM public."SECRET_AUDIT"',
    "SELECT COUNT(*) FROM orders WHERE EXISTS (SELECT 1 FROM secret_audit)",
    "SELECT MAX(id FROM payments",
]

# Ordinary native SQL on the same cubes. Each must run and give its truth.
ORDINARY_SQL = [
    ("SELECT method, COUNT(*) FROM payments GROUP BY 1", "SELECT 'card', 5000 UNION ALL SELECT 'fee', 5000"),
    ('SELECT "method", COUNT(*) FROM "payments" GROUP BY 1', "SELECT 'card', 5000 UNION ALL SELECT 'fee', 5000"),
    ("SELECT p.method, COUNT(*) FROM payments p GROUP BY 1", "SELECT 'card', 5000 UNION ALL SELECT 'fee', 5000"),
    ("SELECT COUNT(*), 2 * 3 FROM orders", "SELECT count(), 6 FROM orders"),
    ("SELECT MAX(o.id) FROM orders o", "SELECT max(id) FROM orders"),
    ("SELECT MAX(o.id) AS id FROM orders o", "SELECT max(id) FROM orders"),
    ("SELECT MAX(id) FROM orders", "SELECT max(id) FROM orders"),
    ("SELECT COUNT(id) FROM orders", "SELECT count() FROM orders"),
    ("SELECT t.status, COUNT(*) FROM (SELECT status FROM orders) t GROUP BY 1", "SELECT toString(status), count() FROM orders GROUP BY 1"),
    ("SELECT * FROM (SELECT status, COUNT(*) AS c FROM orders GROUP BY 1) t", "SELECT toString(status), count() FROM orders GROUP BY 1"),
    ("WITH t AS (SELECT status, COUNT(*) AS c FROM orders GROUP BY 1) SELECT status, c FROM t", "SELECT toString(status), count() FROM orders GROUP BY 1"),
    ("SELECT COUNT(*) FROM orders WHERE status = E'shipped'", "SELECT count() FROM orders WHERE status = 'shipped'"),
    ("SELECT COUNT(*) FROM orders WHERE status = 'it''s -- not a comment'", "SELECT count() FROM orders WHERE status = 'no such'"),
    ("SELECT COUNT(*) /* a comment with id */ FROM orders -- and internal_note\n WHERE status = 'shipped'", "SELECT count() FROM orders WHERE status = 'shipped'"),
    ("SELECT DATE_TRUNC('month', created_at), COUNT(*) FROM orders GROUP BY 1", "SELECT toStartOfMonth(created_at), count() FROM orders GROUP BY 1"),
    ("SELECT MIN(users_city), MAX(users_city) FROM sales", "SELECT min(u.city), max(u.city) FROM orders o JOIN users u ON o.user_id = u.id"),
    ("SELECT category, COUNT(*) FROM products GROUP BY 1", "SELECT category, count() FROM products GROUP BY 1"),
    ("SELECT id FROM users ORDER BY id LIMIT 3", "SELECT id FROM users ORDER BY id LIMIT 3"),
    ("SELECT id, city FROM users ORDER BY id LIMIT 3", "SELECT id, city FROM users ORDER BY id LIMIT 3"),
]

# An aggregation of a joined column in native SQL. Native SQL has no stage to write, so a clear message stands for the number.
JOINED_SQL = [
    "SELECT COUNT(p.price) FROM orders o CROSS JOIN products p WHERE o.__cubeJoinField = p.__cubeJoinField",
    "SELECT COUNT(price) FROM orders o CROSS JOIN products p WHERE o.__cubeJoinField = p.__cubeJoinField",
    "SELECT COUNT(ALL p.price) FROM orders o CROSS JOIN products p WHERE o.__cubeJoinField = p.__cubeJoinField",
    "SELECT SUM(p.price) FROM orders o CROSS JOIN products p WHERE o.__cubeJoinField = p.__cubeJoinField",
    "SELECT AVG(price) FROM orders o CROSS JOIN products p WHERE o.__cubeJoinField = p.__cubeJoinField",
    "SELECT COUNT(users_city) FROM sales",
    "SELECT o.status, COUNT(p.price) FROM orders o CROSS JOIN products p WHERE o.__cubeJoinField = p.__cubeJoinField GROUP BY 1",
]

JOINED_SQL_OK = [
    ("SELECT MIN(p.price), MAX(p.price) FROM orders o CROSS JOIN products p WHERE o.__cubeJoinField = p.__cubeJoinField", "SELECT min(p.price), max(p.price) FROM orders o JOIN products p ON o.product_id = p.id"),
    ("SELECT COUNT(DISTINCT p.price) FROM orders o CROSS JOIN products p WHERE o.__cubeJoinField = p.__cubeJoinField", "SELECT uniqExact(p.price) FROM orders o JOIN products p ON o.product_id = p.id"),
]


def resolve(x, field_id, fk_id, table_id):
    """`x` with ids in place of names."""
    if isinstance(x, str) and x.startswith("t:"):
        return table_id(x[2:])
    if isinstance(x, str) and re.match(r"^d:[a-z_]", x):
        body, opts = x[2:], {}
        if "@" in body:
            body, opts["temporal-unit"] = body.split("@")
        if "|" in body:
            body, opts["join-alias"] = body.split("|")
        path, name = body.split(".")
        if ">" in path:
            source, path = path.split(">")
            opts["source-field"] = fk_id(source, path)
        return ["field", field_id(path, name), opts or None]
    if isinstance(x, list):
        return [resolve(i, field_id, fk_id, table_id) for i in x]
    if isinstance(x, dict):
        return {k: resolve(v, field_id, fk_id, table_id) for k, v in x.items()}
    return x


def run(cases, execute, differ, field_id, fk_id, table_id):
    """`execute(query, parameters)` gives (rows, error), and `differ(rows, truth, ordered, tol)` gives a text or None. Returns (a count of each result, the problems)."""
    counts = {"pass": 0, "refused": 0}
    problems = []
    for c in cases:
        query = resolve(c["query"], field_id, fk_id, table_id)
        parameters = resolve(c["parameters"], field_id, fk_id, table_id) if c["parameters"] else None
        rows, error = execute(query, parameters)
        if error:
            if c["refusal"] and c["refusal"] in error:
                counts["refused"] += 1
            else:
                problems.append("%s: error: %s" % (c["id"], error[:200].replace("\n", " ")))
            continue
        diff = differ(rows, c["truth"], c["ordered"], c["tol"])
        if diff:
            problems.append("%s: %s" % (c["id"], diff[:200]))
        else:
            counts["pass"] += 1
    return counts, problems


def shows_private_value(rows):
    """With dev mode off, Cube has no private member. So `*` gives the public columns, and a filter on `NULL AS id` finds no row. A value of the private key of payments starts at 100001."""
    numbers = [v for row in rows for v in row if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return any(v > 100000 for v in numbers) or (len(rows) == 1 and len(rows[0]) == 1 and rows[0][0] not in (0, None))


def run_native(execute, differ, dev_mode=True):
    """`execute(sql)` gives (rows, error). Returns (a count of each group, the problems)."""
    problems = []
    for sql, truth in NATIVE_FRACTIONS + ORDINARY_SQL + JOINED_SQL_OK:
        rows, error = execute(sql)
        if error:
            problems.append("refused %r: %s" % (sql, error[:200].replace("\n", " ")))
        else:
            diff = differ(rows, truth, False, 1e-6)
            if diff:
                problems.append("wrong %r: %s" % (sql, diff[:200]))
    for sql in PRIVATE_SQL + JOINED_SQL:
        rows, error = execute(sql)
        if error or (sql in PRIVATE_SQL and not dev_mode and not shows_private_value(rows)):
            continue
        problems.append("no refusal %r: %s" % (sql, str(rows)[:120]))
    counts = {"rows": len(NATIVE_FRACTIONS) + len(ORDINARY_SQL) + len(JOINED_SQL_OK), "refused": len(PRIVATE_SQL) + len(JOINED_SQL)}
    return counts, problems
