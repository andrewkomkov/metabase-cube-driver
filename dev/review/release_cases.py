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


CS = "nullIf(toString(status), 'completed')"
INTERVAL = "cannot add an interval"

# Native SQL that the SQL API of Cube reads with another meaning than SQL, on each construct of "Native SQL" in docs/query-compilation.md. Each row has the text, the truth in direct ClickHouse SQL, and
# a text of a clear message that may stand for the number. With no such text, only the truth passes.
NATIVE_SEMANTICS = [
    # FILTER with each aggregation.
    ("SELECT COUNT(*) FILTER (WHERE status = 'completed') FROM orders", "SELECT countIf(status = 'completed') FROM orders"),
    ("SELECT COUNT(*) FILTER (WHERE status = 'completed') AS c, COUNT(*) FROM orders", "SELECT countIf(status = 'completed'), count() FROM orders"),
    ("SELECT status, COUNT(*) FILTER (WHERE quantity_dim = 1) FROM orders GROUP BY 1", "SELECT toString(status), countIf(quantity = 1) FROM orders GROUP BY 1"),
    ("SELECT SUM(amount_dim) FILTER (WHERE status = 'completed') FROM orders", "SELECT sumIf(amount, status = 'completed') FROM orders"),
    ("SELECT AVG(amount_dim) FILTER (WHERE status = 'completed') FROM orders", "SELECT avgIf(amount, status = 'completed') FROM orders"),
    ("SELECT MIN(amount_dim) FILTER (WHERE status = 'completed') FROM orders", "SELECT minIf(amount, status = 'completed') FROM orders"),
    ("SELECT MAX(amount_dim) FILTER (WHERE status = 'completed') FROM orders", "SELECT maxIf(amount, status = 'completed') FROM orders"),
    ("SELECT COUNT(amount_dim) FILTER (WHERE status = 'completed') FROM orders", "SELECT countIf(status = 'completed') FROM orders"),
    ("SELECT COUNT(cancel_status) FILTER (WHERE quantity_dim = 1) FROM orders", "SELECT countIf(%s, quantity = 1) FROM orders" % CS),
    ("SELECT COUNT(DISTINCT user_id) FILTER (WHERE status = 'completed') FROM orders", "SELECT uniqExactIf(user_id, status = 'completed') FROM orders"),
    ("SELECT STDDEV(amount_dim) FILTER (WHERE status = 'completed') FROM orders", "SELECT stddevSampIf(amount, status = 'completed') FROM orders"),
    ("SELECT COUNT(*) FILTER (WHERE cancel_status <> 'shipped') FROM orders", "SELECT countIf(%s <> 'shipped') FROM orders" % CS),
    ("SELECT COUNT(*) FILTER (WHERE amount_dim = 44.001) FROM orders", "SELECT countIf(amount = 44.001) FROM orders"),
    ("SELECT status, SUM(COUNT(*) FILTER (WHERE quantity_dim = 1)) OVER () FROM orders GROUP BY 1", "SELECT toString(status), sum(countIf(quantity = 1)) OVER () FROM orders GROUP BY 1"),
    ("SELECT COUNT(*) FILTER (WHERE status = 'completed') FROM (SELECT status FROM orders) t", "SELECT countIf(status = 'completed') FROM orders"),
    ("WITH t AS (SELECT status FROM orders) SELECT COUNT(*) FILTER (WHERE status = 'completed') FROM t", "SELECT countIf(status = 'completed') FROM orders"),
    # NULL in a filter: `<>`, NOT IN, NOT, NOT LIKE, through each form of a column, in WHERE and HAVING and in a subquery.
    ("SELECT COUNT(*) FROM orders WHERE cancel_status <> 'shipped'", "SELECT count() FROM orders WHERE %s <> 'shipped'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status != 'shipped'", "SELECT count() FROM orders WHERE %s <> 'shipped'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE 'shipped' <> cancel_status", "SELECT count() FROM orders WHERE %s <> 'shipped'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE (cancel_status) <> 'shipped'", "SELECT count() FROM orders WHERE %s <> 'shipped'" % CS),
    ("SELECT COUNT(*) FROM orders o WHERE o.cancel_status <> 'shipped'", "SELECT count() FROM orders WHERE %s <> 'shipped'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE \"cancel_status\" <> 'shipped' AND status <> 'cancelled'", "SELECT count() FROM orders WHERE %s <> 'shipped' AND status <> 'cancelled'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status <> 'shipped' OR status = 'completed'", "SELECT count() FROM orders WHERE %s <> 'shipped' OR status = 'completed'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status NOT IN ('shipped', 'processing')", "SELECT count() FROM orders WHERE %s NOT IN ('shipped', 'processing')" % CS),
    ("SELECT COUNT(*) FROM orders WHERE NOT (cancel_status = 'shipped')", "SELECT count() FROM orders WHERE NOT (%s = 'shipped')" % CS),
    ("SELECT COUNT(*) FROM orders WHERE NOT (cancel_status IN ('shipped'))", "SELECT count() FROM orders WHERE NOT (%s IN ('shipped'))" % CS),
    ("SELECT COUNT(*) FROM orders WHERE NOT (cancel_status <> 'shipped')", "SELECT count() FROM orders WHERE NOT (%s <> 'shipped')" % CS),
    ("SELECT COUNT(*) FROM orders WHERE NOT (cancel_status = 'shipped' OR quantity_dim = 1)", "SELECT count() FROM orders WHERE NOT (%s = 'shipped' OR quantity = 1)" % CS),
    ("SELECT COUNT(*) FROM orders WHERE NOT (cancel_status = 'shipped' AND quantity_dim = 1)", "SELECT count() FROM orders WHERE NOT (%s = 'shipped' AND quantity = 1)" % CS),
    ("SELECT COUNT(*) FROM orders WHERE NOT (NOT (cancel_status = 'shipped'))", "SELECT count() FROM orders WHERE %s = 'shipped'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status NOT ILIKE '%SHIP%'", "SELECT count() FROM orders WHERE %s NOT ILIKE '%%SHIP%%'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status NOT LIKE '%ship%'", "SELECT count() FROM orders WHERE %s NOT LIKE '%%ship%%'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status NOT LIKE 'shipped'", "SELECT count() FROM orders WHERE %s NOT LIKE 'shipped'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status NOT IN ('shipped', NULL)", "SELECT toUInt64(0)"),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status IN ('shipped', NULL)", "SELECT count() FROM orders WHERE %s = 'shipped'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status = NULL", "SELECT toUInt64(0)"),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status <> NULL", "SELECT toUInt64(0)"),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status IS DISTINCT FROM 'shipped'", "SELECT count() FROM orders WHERE %s IS DISTINCT FROM 'shipped'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status IS NOT DISTINCT FROM 'shipped'", "SELECT count() FROM orders WHERE %s IS NOT DISTINCT FROM 'shipped'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE NOT (cancel_status IS NULL)", "SELECT count() FROM orders WHERE %s IS NOT NULL" % CS),
    ("SELECT cancel_status, COUNT(*) c FROM orders GROUP BY 1 HAVING cancel_status <> 'shipped'", "SELECT %s s, count() c FROM orders GROUP BY 1 HAVING s <> 'shipped'" % CS),
    ("SELECT cancel_status, COUNT(*) c FROM orders GROUP BY 1 HAVING NOT (cancel_status = 'shipped')", "SELECT %s s, count() c FROM orders GROUP BY 1 HAVING s <> 'shipped'" % CS),
    ("SELECT status, COUNT(*) FROM orders GROUP BY 1 HAVING COUNT(*) <> 2000", "SELECT toString(status), count() c FROM orders GROUP BY 1 HAVING c <> 2000"),
    ("SELECT SUM(CASE WHEN cancel_status <> 'shipped' THEN 1 ELSE 0 END) FROM orders", "SELECT countIf(%s <> 'shipped') FROM orders" % CS),
    ("SELECT SUM(CASE WHEN NOT (cancel_status = 'shipped') THEN 1 ELSE 0 END) FROM orders", "SELECT countIf(NOT (%s = 'shipped')) FROM orders" % CS),
    ("SELECT SUM(CASE WHEN cancel_status NOT IN ('shipped') THEN 1 ELSE 0 END) FROM orders", "SELECT countIf(%s NOT IN ('shipped')) FROM orders" % CS),
    ("SELECT cancel_status <> 'shipped' AS b, COUNT(*) FROM orders GROUP BY 1", "SELECT toBool(%s <> 'shipped') b, count() FROM orders GROUP BY 1" % CS),
    ("SELECT COUNT(*) FROM (SELECT cancel_status FROM orders) t WHERE cancel_status <> 'shipped'", "SELECT count() FROM orders WHERE %s <> 'shipped'" % CS),
    ("SELECT COUNT(*) FROM (SELECT cancel_status AS x FROM orders) t WHERE x <> 'shipped'", "SELECT count() FROM orders WHERE %s <> 'shipped'" % CS),
    ("SELECT COUNT(*) FROM (SELECT cancel_status AS x FROM orders) t WHERE t.x NOT IN ('shipped')", "SELECT count() FROM orders WHERE %s <> 'shipped'" % CS),
    ("WITH t AS (SELECT cancel_status AS x FROM orders) SELECT COUNT(*) FROM t WHERE x <> 'shipped'", "SELECT count() FROM orders WHERE %s <> 'shipped'" % CS),
    ("SELECT COUNT(*) FROM orders o CROSS JOIN users u WHERE o.__cubeJoinField = u.__cubeJoinField AND o.cancel_status <> 'shipped'", "SELECT count() FROM orders WHERE %s <> 'shipped'" % CS),
    ("SELECT COUNT(*) FROM orders o WHERE o.id IN (SELECT id FROM orders WHERE cancel_status <> 'shipped')", "SELECT count() FROM orders WHERE %s <> 'shipped'" % CS),
    ("SELECT COUNT(*) FROM orders o WHERE o.id NOT IN (SELECT id FROM orders WHERE cancel_status = 'shipped')", "SELECT count() FROM orders WHERE id NOT IN (SELECT id FROM orders WHERE %s = 'shipped')" % CS),
    ("SELECT COUNT(*) FROM typed_events WHERE n_int <> 1", "SELECT count() FROM typed_events WHERE n_int <> 1"),
    ("SELECT COUNT(*) FROM typed_events WHERE NOT (n_int = 1)", "SELECT count() FROM typed_events WHERE NOT (n_int = 1)"),
    ("SELECT COUNT(*) FROM typed_events WHERE n_int NOT BETWEEN 1 AND 2", "SELECT count() FROM typed_events WHERE n_int NOT BETWEEN 1 AND 2"),
    ("SELECT COUNT(*) FROM typed_events WHERE NOT (n_int BETWEEN 1 AND 2)", "SELECT count() FROM typed_events WHERE n_int NOT BETWEEN 1 AND 2"),
    ("SELECT COUNT(*) FROM typed_events WHERE NOT (n_int > 1)", "SELECT count() FROM typed_events WHERE NOT (n_int > 1)"),
    ("SELECT COUNT(*) FROM typed_events WHERE n_dec <> 0.25", "SELECT count() FROM typed_events WHERE n_dec <> 0.25"),
    ("SELECT COUNT(*) FROM typed_events WHERE n_dec <> 0.251", "SELECT count() FROM typed_events WHERE n_dec <> 0.251"),
    ("SELECT COUNT(*) FROM typed_events WHERE n_str <> 's1'", "SELECT count() FROM typed_events WHERE n_str <> 's1'"),
    ("SELECT COUNT(*) FROM typed_events WHERE n_ts <> TIMESTAMP '2025-03-01 00:00:00'", "SELECT count() FROM typed_events WHERE n_ts <> toDateTime('2025-03-01 00:00:00', 'UTC')"),
    ("SELECT COUNT(*) FROM typed_events WHERE NOT n_bool", "SELECT count() FROM typed_events WHERE NOT n_bool"),
    ("SELECT COUNT(*) FROM typed_events WHERE n_bool = FALSE", "SELECT count() FROM typed_events WHERE n_bool = false"),
    ("SELECT COUNT(*) FROM typed_events WHERE n_bool <> TRUE", "SELECT count() FROM typed_events WHERE n_bool <> true"),
    ("SELECT COUNT(*) FROM typed_events WHERE n_bool IS NOT TRUE", "SELECT count() FROM typed_events WHERE NOT ifNull(n_bool, false)"),
    ("SELECT COUNT(*) FROM typed_events WHERE n_bool IS FALSE", "SELECT count() FROM typed_events WHERE ifNull(n_bool = false, false)"),
    ("SELECT COUNT(*) FROM typed_events WHERE n_bool IS TRUE", "SELECT count() FROM typed_events WHERE ifNull(n_bool, false)"),
    ("SELECT COUNT(*) FROM typed_events WHERE n_bool IS NOT FALSE", "SELECT count() FROM typed_events WHERE NOT ifNull(n_bool = false, false)"),
    ("SELECT COUNT(*) FROM typed_events WHERE NOT (n_bool IS TRUE)", "SELECT count() FROM typed_events WHERE NOT ifNull(n_bool, false)"),
    ("SELECT COUNT(*) FROM typed_events WHERE (dec_amount) <> 12.50001 AND n_bool IS NOT TRUE", "SELECT count() FROM typed_events WHERE dec_amount <> 12.50001 AND NOT ifNull(n_bool, false)"),
    ("SELECT COUNT(*) FROM typed_events WHERE (dec_amount) <> 12.50001 AND n_bool IS NOT FALSE", "SELECT count() FROM typed_events WHERE dec_amount <> 12.50001 AND NOT ifNull(n_bool = false, false)"),
    ("SELECT SUM(CASE WHEN n_bool IS NOT TRUE THEN 1 ELSE 0 END) FROM typed_events", "SELECT countIf(NOT ifNull(n_bool, false)) FROM typed_events"),
    ("SELECT SUM(CASE WHEN n_bool IS NOT FALSE THEN 1 ELSE 0 END) FROM typed_events", "SELECT countIf(NOT ifNull(n_bool = false, false)) FROM typed_events"),
    ("SELECT COUNT(n_str) FROM typed_events WHERE NOT ((n_bool IS FALSE AND dec_amount NOT IN ((12.5), 12.5)))", "SELECT count(n_str) FROM typed_events WHERE NOT (ifNull(n_bool = false, false) AND dec_amount NOT IN (12.5, 12.5))"),
    ("SELECT COUNT(*) FROM orders WHERE is_paid", "SELECT count() FROM orders WHERE status IN ('completed', 'shipped')"),
    ("SELECT COUNT(*) FROM orders WHERE NOT is_paid", "SELECT count() FROM orders WHERE status NOT IN ('completed', 'shipped')"),
    ("SELECT COUNT(*) FROM orders WHERE is_paid = TRUE", "SELECT count() FROM orders WHERE status IN ('completed', 'shipped')"),
    ("SELECT COUNT(*) FROM orders WHERE is_paid = FALSE", "SELECT count() FROM orders WHERE status NOT IN ('completed', 'shipped')"),
    ("SELECT COUNT(*) FROM orders WHERE is_paid IS NOT TRUE", "SELECT count() FROM orders WHERE status NOT IN ('completed', 'shipped')"),
    ("SELECT COUNT(*) FROM orders WHERE is_paid IS TRUE", "SELECT count() FROM orders WHERE status IN ('completed', 'shipped')"),
    ("SELECT SUM(CASE WHEN is_paid THEN 1 ELSE 0 END) FROM orders", "SELECT countIf(status IN ('completed', 'shipped')) FROM orders"),
    # A number with a fraction: each wrapper, each form of the literal, each operator.
    ("SELECT COUNT(*) FROM orders WHERE CAST(amount_dim AS numeric) = 44.001", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE CAST(amount_dim AS double precision) = 44.001", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim::numeric = 44.001", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE (amount_dim) = 44.001", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE ((amount_dim)) = (44.001)", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim = '44.001'", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE '44.001' = amount_dim", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim = 44.001::numeric", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim = CAST(44.001 AS numeric)", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim = CAST('44.001' AS numeric)", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim = 44001e-3", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim = 44.0 + 0.001", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim + 0 = 44.001", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE ABS(amount_dim) = 44.001", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE COALESCE(amount_dim, 0) = 44.001", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim IN ('44.001')", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim IN (44.001, '44.002', 44)", "SELECT count() FROM orders WHERE amount = 44"),
    ("SELECT COUNT(*) FROM orders WHERE (amount_dim) IN (44.001, 44.002)", "SELECT count() FROM orders WHERE amount IN (44.001, 44.002)"),
    ("SELECT COUNT(*) FROM orders WHERE CAST(amount_dim AS numeric) NOT IN (44.001, 44.002)", "SELECT count() FROM orders WHERE amount NOT IN (44.001, 44.002)"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim BETWEEN '44.001' AND '44.009'", "SELECT count() FROM orders WHERE amount BETWEEN 44.001 AND 44.009"),
    ("SELECT COUNT(*) FROM orders WHERE (amount_dim) BETWEEN 44.001 AND 44.009", "SELECT count() FROM orders WHERE amount BETWEEN 44.001 AND 44.009"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim NOT BETWEEN 43.999 AND 44.001", "SELECT count() FROM orders WHERE amount NOT BETWEEN 43.999 AND 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE (amount_dim) >= 44.001", "SELECT count() FROM orders WHERE amount >= 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE (amount_dim) > 43.999", "SELECT count() FROM orders WHERE amount > 43.999"),
    ("SELECT COUNT(*) FROM orders WHERE (amount_dim) < 44.001", "SELECT count() FROM orders WHERE amount < 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE (amount_dim) <= 43.999", "SELECT count() FROM orders WHERE amount <= 43.999"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim > '43.999'", "SELECT count() FROM orders WHERE amount > 43.999"),
    ("SELECT COUNT(*) FROM orders WHERE 44.001 <= amount_dim", "SELECT count() FROM orders WHERE amount >= 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE NOT (amount_dim = 44.001)", "SELECT count() FROM orders WHERE NOT (amount = 44.001)"),
    ("SELECT COUNT(*) FROM orders WHERE NOT ((amount_dim) >= 44.001)", "SELECT count() FROM orders WHERE amount < 44.001"),
    ("SELECT SUM(CASE WHEN amount_dim = 44.001 THEN 1 ELSE 0 END) FROM orders", "SELECT countIf(amount = 44.001) FROM orders"),
    ("SELECT SUM(CASE WHEN (amount_dim) >= 44.001 THEN 1 ELSE 0 END) FROM orders", "SELECT countIf(amount >= 44.001) FROM orders"),
    ("SELECT SUM(CASE amount_dim WHEN 44.001 THEN 1 ELSE 0 END) FROM orders", "SELECT countIf(amount = 44.001) FROM orders"),
    ("SELECT CASE WHEN amount_dim > 43.999 THEN 'a' ELSE 'b' END, COUNT(*) FROM orders GROUP BY 1", "SELECT if(amount > 43.999, 'a', 'b'), count() FROM orders GROUP BY 1"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim = 44.0", "SELECT count() FROM orders WHERE amount = 44"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim = '44.00'", "SELECT count() FROM orders WHERE amount = 44"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim = '44'", "SELECT count() FROM orders WHERE amount = 44"),
    ("SELECT COUNT(*) FROM (SELECT amount_dim AS a FROM orders) t WHERE a = 44.001", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM (SELECT amount_dim AS a FROM orders) t WHERE t.a >= 44.001", "SELECT count() FROM orders WHERE amount >= 44.001"),
    ("SELECT COUNT(*) FROM (SELECT amount_dim AS a FROM orders) t WHERE a = '44.001'", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM (SELECT amount_dim FROM orders) t WHERE amount_dim = 44.001", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("WITH t AS (SELECT amount_dim AS a FROM orders) SELECT COUNT(*) FROM t WHERE a = 44.001", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM orders WHERE status = 'shipped' AND (amount_dim) = 44.001", "SELECT count() FROM orders WHERE status = 'shipped' AND amount = 44.001"),
    ("SELECT status, SUM(amount_dim) s FROM orders GROUP BY 1 HAVING SUM(amount_dim) >= 64950.001", "SELECT toString(status), sum(amount) s FROM orders GROUP BY 1 HAVING s >= 64950.001"),
    ("SELECT status, SUM(amount_dim) s FROM orders GROUP BY 1 HAVING SUM(amount_dim) = 64950.001", "SELECT toString(status), sum(amount) s FROM orders GROUP BY 1 HAVING s = 64950.001"),
    ("SELECT status, MIN(amount_dim) m FROM orders GROUP BY 1 HAVING MIN(amount_dim) >= 11.001", "SELECT toString(status), min(amount) m FROM orders GROUP BY 1 HAVING m >= 11.001"),
    ("SELECT status, MAX(amount_dim) m FROM orders GROUP BY 1 HAVING MAX(amount_dim) >= 144.001", "SELECT toString(status), max(amount) m FROM orders GROUP BY 1 HAVING m >= 144.001"),
    ("SELECT status, AVG(amount_dim) a FROM orders GROUP BY 1 HAVING AVG(amount_dim) > 64.93", "SELECT toString(status), avg(amount) a FROM orders GROUP BY 1 HAVING a > 64.93"),
    ("SELECT status, COUNT(*) c FROM orders GROUP BY 1 HAVING COUNT(*) > 1000.5", "SELECT toString(status), count() c FROM orders GROUP BY 1 HAVING c > 1000.5"),
    ("SELECT status, MEASURE(revenue) r FROM orders GROUP BY 1 HAVING MEASURE(revenue) >= 119940.001", "SELECT 'x', toFloat64(0) WHERE 0"),
    ("SELECT status, MEASURE(revenue) r FROM orders GROUP BY 1 HAVING r >= 119940.001", "SELECT 'x', toFloat64(0) WHERE 0"),
    ("SELECT status, r FROM (SELECT status, SUM(amount_dim) AS r FROM orders GROUP BY 1 LIMIT 100) t WHERE r >= 64950.001", "SELECT toString(status), sum(amount) s FROM orders GROUP BY 1 HAVING s >= 64950.001"),
    ("SELECT COUNT(*) FROM orders WHERE (quantity_dim) = 1.5", "SELECT count() FROM orders WHERE quantity = 1.5"),
    ("SELECT COUNT(*) FROM orders WHERE quantity_dim > '1.5'", "SELECT count() FROM orders WHERE quantity > 1.5"),
    ("SELECT COUNT(*) FROM orders WHERE id > 44.5", "SELECT count() FROM orders WHERE id > 44.5"),
    ("SELECT COUNT(*) FROM typed_events WHERE dec_amount = 12.50001", "SELECT count() FROM typed_events WHERE dec_amount = 12.50001"),
    ("SELECT COUNT(*) FROM typed_events WHERE (n_dec) = 0.251", "SELECT count() FROM typed_events WHERE n_dec = 0.251"),
    ("SELECT COUNT(*) FROM typed_events WHERE (n_dec) <> 0.251", "SELECT count() FROM typed_events WHERE n_dec <> 0.251"),
    ("SELECT COUNT(*) FROM typed_events WHERE n_dec NOT IN (0.25, 0.251)", "SELECT count() FROM typed_events WHERE n_dec NOT IN (0.25, 0.251)"),
    ("SELECT COUNT(*) FROM typed_events WHERE (f32) = 0.25", "SELECT count() FROM typed_events WHERE f32 = 0.25"),
    ("SELECT COUNT(*) FROM metric_types WHERE (amount) = 44.001", "SELECT count() FROM orders WHERE amount = 44.001"),
    ("SELECT COUNT(*) FROM sales WHERE (id) > 44.5", "SELECT count() FROM orders WHERE id > 44.5"),
    # Division: real division on each path.
    ("SELECT SUM(amount_dim / 3) FROM orders", "SELECT sum(toFloat64(amount) / 3) FROM orders"),
    ("SELECT MAX(amount_dim / 7) FROM orders", "SELECT max(toFloat64(amount) / 7) FROM orders"),
    ("SELECT amount_dim / 7 x, COUNT(*) FROM orders GROUP BY 1", "SELECT toFloat64(amount) / 7 x, count() FROM orders GROUP BY 1"),
    ("SELECT SUM(quantity_dim / 2) FROM orders", "SELECT sum(quantity / 2) FROM orders"),
    ("SELECT quantity_dim / 2 x, COUNT(*) FROM orders GROUP BY 1", "SELECT quantity / 2 x, count() FROM orders GROUP BY 1"),
    ("SELECT COUNT(*) FROM orders WHERE quantity_dim / 2 = 1", "SELECT count() FROM orders WHERE quantity / 2 = 1"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim / 3 > 43.5", "SELECT count() FROM orders WHERE toFloat64(amount) / 3 > 43.5"),
    ("SELECT 7 / 2, COUNT(*) FROM orders", "SELECT 3.5, count() FROM orders"),
    ("SELECT SUM(amount_dim) / COUNT(*) FROM orders", "SELECT sum(toFloat64(amount)) / count() FROM orders"),
    ("SELECT COUNT(*) / 7 FROM orders", "SELECT count() / 7 FROM orders"),
    ("SELECT status, COUNT(*) / 7 FROM orders GROUP BY 1", "SELECT toString(status), count() / 7 FROM orders GROUP BY 1"),
    ("SELECT MEASURE(count) / 7 FROM orders", "SELECT count() / 7 FROM orders"),
    ("SELECT MEASURE(revenue) / MEASURE(count) FROM orders", "SELECT sum(toFloat64(amount)) / count() FROM orders"),
    ("SELECT CAST(COUNT(*) AS float) / 7 FROM orders", "SELECT count() / 7 FROM orders"),
    ("SELECT MAX(amount_dim / NULLIF(quantity_dim - quantity_dim, 0)) FROM orders", "SELECT toNullable(toFloat64(NULL))"),
    ("SELECT MAX(amount_dim / (quantity_dim - quantity_dim)) FROM orders", "SELECT toNullable(toFloat64(NULL))"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim / (quantity_dim - quantity_dim) > 0", "SELECT toUInt64(0)"),
    ("SELECT MIN(quantity_dim / (quantity_dim - 1)) FROM orders", "SELECT min(quantity / nullIf(quantity - 1, 0)) FROM orders"),
    ("SELECT quantity_dim / (quantity_dim - 1) x, COUNT(*) FROM orders GROUP BY 1", "SELECT toFloat64(quantity) / nullIf(quantity - 1, 0) x, count() FROM orders GROUP BY 1"),
    ("SELECT MAX(quantity_dim % 2) FROM orders", "SELECT max(quantity % 2) FROM orders"),
    ("SELECT SUM(amount_dim * 1.005) FROM orders", "SELECT sum(toFloat64(amount) * 1.005) FROM orders"),
    ("SELECT ROUND(AVG(amount_dim), 1) FROM orders", "SELECT round(avg(amount), 1) FROM orders"),
    # LIKE keeps its case and its NULL.
    ("SELECT COUNT(*) FROM orders WHERE status LIKE '%hip%'", "SELECT count() FROM orders WHERE status LIKE '%hip%'"),
    ("SELECT COUNT(*) FROM orders WHERE status LIKE '%HIP%'", "SELECT count() FROM orders WHERE status LIKE '%HIP%'"),
    ("SELECT COUNT(*) FROM orders WHERE status LIKE 's_ipped'", "SELECT count() FROM orders WHERE status LIKE 's_ipped'"),
    ("SELECT COUNT(*) FROM orders WHERE status LIKE 'S_IPPED'", "SELECT count() FROM orders WHERE status LIKE 'S_IPPED'"),
    ("SELECT COUNT(*) FROM orders WHERE status LIKE '%'", "SELECT count() FROM orders WHERE status LIKE '%'"),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status LIKE '%ship%'", "SELECT count() FROM orders WHERE %s LIKE '%%ship%%'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status LIKE '%SHIP%'", "SELECT count() FROM orders WHERE %s LIKE '%%SHIP%%'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status LIKE '%'", "SELECT count() FROM orders WHERE %s LIKE '%%'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status NOT LIKE '%'", "SELECT count() FROM orders WHERE %s NOT LIKE '%%'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status ILIKE '%SHIP%'", "SELECT count() FROM orders WHERE %s ILIKE '%%SHIP%%'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status LIKE 'shipped'", "SELECT count() FROM orders WHERE %s LIKE 'shipped'" % CS),
    ("SELECT SUM(CASE WHEN cancel_status LIKE '%ship%' THEN 1 ELSE 0 END) FROM orders", "SELECT countIf(%s LIKE '%%ship%%') FROM orders" % CS),
    ("SELECT cancel_status LIKE '%ship%' b, COUNT(*) FROM orders GROUP BY 1", "SELECT toBool(%s LIKE '%%ship%%') b, count() FROM orders GROUP BY 1" % CS),
    ("SELECT COUNT(*) FROM orders WHERE LOWER(cancel_status) LIKE '%ship%'", "SELECT count() FROM orders WHERE lower(%s) LIKE '%%ship%%'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status ~ 'ship'", "SELECT count() FROM orders WHERE match(%s, 'ship')" % CS),
    # Window functions.
    ("SELECT status, SUM(COUNT(*)) OVER () FROM orders GROUP BY 1", "SELECT toString(status), sum(count()) OVER () FROM orders GROUP BY 1"),
    ("SELECT status, COUNT(*) OVER (PARTITION BY status) FROM orders GROUP BY status", "SELECT toString(status), count() OVER (PARTITION BY status) FROM orders GROUP BY status"),
    ("SELECT status, RANK() OVER (ORDER BY COUNT(*) DESC, status) FROM orders GROUP BY 1", "SELECT toString(status), rank() OVER (ORDER BY count() DESC, toString(status)) FROM orders GROUP BY status"),
    ("SELECT id, ROW_NUMBER() OVER (ORDER BY id) r FROM typed_events ORDER BY id LIMIT 3", "SELECT id, row_number() OVER (ORDER BY id) r FROM typed_events ORDER BY id LIMIT 3"),
    ("SELECT id, SUM(n_dec) OVER (ORDER BY id ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) s FROM typed_events ORDER BY id LIMIT 3", "SELECT id, sum(n_dec) OVER (ORDER BY id ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) s FROM typed_events ORDER BY id LIMIT 3"),
    ("SELECT id, COUNT(*) FILTER (WHERE id > 1) OVER () c FROM typed_events ORDER BY id LIMIT 2", "SELECT id, countIf(id > 1) OVER () c FROM typed_events ORDER BY id LIMIT 2"),
    ("SELECT id, ROW_NUMBER() OVER (ORDER BY id) r FROM orders ORDER BY id LIMIT 3", "SELECT id, row_number() OVER (ORDER BY id) r FROM orders ORDER BY id LIMIT 3", "over the rows of a cube with a rolling window"),
    # DISTINCT.
    ("SELECT DISTINCT status FROM orders", "SELECT DISTINCT toString(status) FROM orders"),
    ("SELECT DISTINCT status, quantity_dim FROM orders", "SELECT DISTINCT toString(status), quantity FROM orders"),
    ("SELECT COUNT(DISTINCT status), COUNT(*) FROM orders", "SELECT uniqExact(status), count() FROM orders"),
    ("SELECT COUNT(DISTINCT cancel_status) FROM orders", "SELECT uniqExact(%s) FROM orders" % CS),
    ("SELECT AVG(DISTINCT quantity_dim) FROM orders", "SELECT avg(DISTINCT quantity) FROM orders"),
    ("SELECT MIN(DISTINCT quantity_dim), MAX(DISTINCT quantity_dim) FROM orders", "SELECT min(quantity), max(quantity) FROM orders"),
    ("SELECT DISTINCT ON (lc) lc, id FROM typed_events ORDER BY lc, id LIMIT 2", "SELECT lc, min(id) FROM typed_events GROUP BY lc ORDER BY lc LIMIT 2"),
    # COALESCE and NULLIF.
    ("SELECT COALESCE(cancel_status, 'none') s, COUNT(*) FROM orders GROUP BY 1", "SELECT coalesce(%s, 'none') s, count() FROM orders GROUP BY 1" % CS),
    ("SELECT NULLIF(status, 'shipped') s, COUNT(*) FROM orders GROUP BY 1", "SELECT nullIf(toString(status), 'shipped') s, count() FROM orders GROUP BY 1"),
    ("SELECT COUNT(*) FROM orders WHERE COALESCE(cancel_status, 'x') <> 'shipped'", "SELECT count() FROM orders WHERE coalesce(%s, 'x') <> 'shipped'" % CS),
    ("SELECT COUNT(*) FROM orders WHERE NULLIF(status, 'shipped') <> 'completed'", "SELECT count() FROM orders WHERE nullIf(toString(status), 'shipped') <> 'completed'"),
    ("SELECT COUNT(*) FROM orders WHERE NULLIF(status, 'shipped') IS NULL", "SELECT count() FROM orders WHERE nullIf(toString(status), 'shipped') IS NULL"),
    ("SELECT SUM(COALESCE(n_int, 0)) FROM typed_events", "SELECT sum(coalesce(n_int, 0)) FROM typed_events"),
    ("SELECT COUNT(COALESCE(n_int, 0)) FROM typed_events", "SELECT count() FROM typed_events"),
    ("SELECT SUM(NULLIF(n_int, 1)) FROM typed_events", "SELECT sum(nullIf(n_int, 1)) FROM typed_events"),
    ("SELECT COUNT(NULLIF(n_int, 1)) FROM typed_events", "SELECT count(nullIf(n_int, 1)) FROM typed_events"),
    ("SELECT COUNT(n_int), AVG(n_int) FROM typed_events", "SELECT count(n_int), avg(n_int) FROM typed_events"),
    # ORDER BY and NULL. SQL does not fix where NULL goes with no NULLS. Explicit NULLS LAST must hold.
    ("SELECT cancel_status, COUNT(*) FROM orders GROUP BY 1 ORDER BY 1 LIMIT 1", "SELECT 'cancelled', toUInt64(1000)"),
    ("SELECT cancel_status, COUNT(*) FROM orders GROUP BY 1 ORDER BY 1 DESC NULLS LAST LIMIT 1", "SELECT 'shipped', toUInt64(1000)"),
    ("SELECT id, cancel_status FROM orders ORDER BY cancel_status DESC NULLS LAST, id LIMIT 1", "SELECT min(id), 'shipped' FROM orders WHERE status = 'shipped'"),
    ("SELECT id, cancel_status FROM orders ORDER BY cancel_status NULLS LAST, id LIMIT 1", "SELECT min(id), 'cancelled' FROM orders WHERE status = 'cancelled'"),
    # LIMIT in a subquery.
    ("SELECT COUNT(*) FROM (SELECT id FROM orders LIMIT 10) t", "SELECT toUInt64(10)"),
    ("SELECT SUM(a) FROM (SELECT amount_dim a FROM orders ORDER BY id LIMIT 10) t", "SELECT sum(amount) FROM (SELECT amount FROM orders ORDER BY id LIMIT 10)"),
    ("SELECT SUM(c) FROM (SELECT status, COUNT(*) c FROM orders GROUP BY 1 ORDER BY 1 LIMIT 2) t", "SELECT sum(c) FROM (SELECT status, count() c FROM orders GROUP BY 1 ORDER BY toString(status) LIMIT 2)"),
    ("SELECT MAX(id) FROM (SELECT id FROM orders ORDER BY id LIMIT 10 OFFSET 5) t", "SELECT toInt32(15)"),
    ("SELECT id FROM orders ORDER BY id LIMIT 2 OFFSET 2", "SELECT id FROM orders ORDER BY id LIMIT 2 OFFSET 2"),
    ("SELECT status, c FROM (SELECT status, COUNT(*) c FROM orders GROUP BY 1 LIMIT 10) t ORDER BY c DESC LIMIT 1", "SELECT 'completed', 2000"),
    # CTE.
    ("WITH t AS (SELECT status, COUNT(*) c FROM orders GROUP BY 1) SELECT SUM(c) FROM t", "SELECT count() FROM orders"),
    ("WITH t AS (SELECT status, COUNT(*) c FROM orders GROUP BY 1) SELECT MAX(c) FROM t", "SELECT toUInt64(2000)"),
    ("WITH t AS (SELECT status, COUNT(*) c FROM orders GROUP BY 1) SELECT AVG(c) FROM t", "SELECT 1250.0"),
    ("WITH t AS (SELECT id, status FROM orders WHERE status = 'completed') SELECT COUNT(*) FROM t", "SELECT countIf(status = 'completed') FROM orders"),
    ("WITH a AS (SELECT status FROM orders), b AS (SELECT status FROM a) SELECT COUNT(*) FROM b", "SELECT count() FROM orders"),
    ("WITH t AS (SELECT status, COUNT(*) c FROM orders GROUP BY 1) SELECT t.status, t.c, u.c FROM t JOIN t u ON t.status = u.status", "SELECT toString(status), count() c, c FROM orders GROUP BY 1"),
    # UNION.
    ("SELECT status FROM orders UNION SELECT city FROM users", "SELECT DISTINCT toString(status) FROM orders UNION ALL SELECT DISTINCT city FROM users"),
    ("SELECT COUNT(*) FROM orders UNION ALL SELECT COUNT(*) FROM users", "SELECT count() FROM orders UNION ALL SELECT count() FROM users"),
    ("SELECT COUNT(*) FROM orders UNION ALL SELECT COUNT(*) FROM orders", "SELECT count() FROM orders UNION ALL SELECT count() FROM orders"),
    ("SELECT COUNT(*) FROM orders UNION SELECT COUNT(*) FROM orders", "SELECT count() FROM orders"),
    ("SELECT COUNT(*) FROM orders WHERE status = 'a' UNION ALL SELECT COUNT(*) FROM orders WHERE status = 'shipped'", "SELECT toUInt64(0) UNION ALL SELECT toUInt64(1000)"),
    ("SELECT MAX(quantity_dim) x FROM orders UNION SELECT MAX(quantity_dim) x FROM orders", "SELECT toInt32(4)"),
    ("SELECT COUNT(*) FROM (SELECT status FROM orders UNION ALL SELECT status FROM orders) t", "SELECT 2 * count() FROM orders"),
    ("SELECT COUNT(*) FROM (SELECT status FROM orders UNION SELECT status FROM orders) t", "SELECT toUInt64(4)"),
    # Subqueries and EXISTS.
    ("SELECT COUNT(*) FROM orders WHERE EXISTS (SELECT 1 FROM users WHERE city = 'no such')", "SELECT toUInt64(0)", "EXISTS"),
    ("SELECT COUNT(*) FROM orders WHERE EXISTS (SELECT 1 FROM users WHERE city = 'Austin')", "SELECT count() FROM orders", "EXISTS"),
    ("SELECT COUNT(*) FROM orders o WHERE EXISTS (SELECT 1 FROM users u WHERE u.id = o.user_id AND u.city = 'Austin')", "SELECT count() FROM orders WHERE user_id IN (SELECT id FROM users WHERE city = 'Austin')", "EXISTS"),
    ("SELECT COUNT(*) FROM orders o WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = o.user_id AND u.city = 'Austin')", "SELECT count() FROM orders WHERE user_id NOT IN (SELECT id FROM users WHERE city = 'Austin')", "EXISTS"),
    ("SELECT COUNT(*) FROM orders WHERE user_id IN (SELECT id FROM users WHERE city = 'Austin')", "SELECT count() FROM orders WHERE user_id IN (SELECT id FROM users WHERE city = 'Austin')"),
    ("SELECT COUNT(*) FROM orders WHERE user_id NOT IN (SELECT id FROM users WHERE city = 'Austin')", "SELECT count() FROM orders WHERE user_id NOT IN (SELECT id FROM users WHERE city = 'Austin')"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim > (SELECT AVG(amount_dim) FROM orders)", "SELECT count() FROM orders WHERE amount > (SELECT avg(amount) FROM orders)"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim = (SELECT MAX(amount_dim) FROM orders)", "SELECT count() FROM orders WHERE amount = (SELECT max(amount) FROM orders)"),
    ("SELECT id, (SELECT COUNT(*) FROM users) FROM orders ORDER BY id LIMIT 1", "SELECT 1, 200", "a subquery in the select"),
    ("SELECT (SELECT COUNT(*) FROM users), COUNT(*) FROM orders", "SELECT 200, count() FROM orders", "a subquery in the select"),
    # Joins through the data model.
    ("SELECT SUM(o.quantity_dim) FROM orders o CROSS JOIN users u WHERE o.__cubeJoinField = u.__cubeJoinField", "SELECT sum(quantity) FROM orders"),
    ("SELECT COUNT(*) FROM users u CROSS JOIN orders o WHERE u.__cubeJoinField = o.__cubeJoinField", "SELECT count() FROM users u LEFT JOIN orders o ON u.id = o.user_id"),
    ("SELECT u.id, COUNT(*) c FROM users u CROSS JOIN orders o WHERE u.__cubeJoinField = o.__cubeJoinField GROUP BY 1 ORDER BY 1 LIMIT 2", "SELECT u.id, count() FROM users u LEFT JOIN orders o ON u.id = o.user_id GROUP BY 1 ORDER BY 1 LIMIT 2"),
    ("SELECT SUM(id * c) FROM (SELECT u.id, COUNT(*) c FROM users u CROSS JOIN orders o WHERE u.__cubeJoinField = o.__cubeJoinField GROUP BY 1 LIMIT 2147483647) t", "SELECT sum(u.id) FROM users u LEFT JOIN orders o ON u.id = o.user_id"),
    ("SELECT SUM(o.amount_dim) FROM users u CROSS JOIN orders o WHERE u.__cubeJoinField = o.__cubeJoinField", "SELECT sum(amount) FROM orders"),
    ("SELECT COUNT(o.id) FROM users u CROSS JOIN orders o WHERE u.__cubeJoinField = o.__cubeJoinField", "SELECT count() FROM orders"),
    # A subquery of raw rows over a join.
    ("SELECT SUM(price) FROM (SELECT o.id, p.price FROM orders o CROSS JOIN products p WHERE o.__cubeJoinField = p.__cubeJoinField) t", "SELECT sum(p.price) FROM orders o LEFT JOIN products p ON o.product_id = p.id"),
    ("SELECT COUNT(price) FROM (SELECT o.id, p.price FROM orders o CROSS JOIN products p WHERE o.__cubeJoinField = p.__cubeJoinField) t", "SELECT count(p.price) FROM orders o LEFT JOIN products p ON o.product_id = p.id"),
    ("SELECT status, SUM(price) FROM (SELECT o.status, p.price FROM orders o CROSS JOIN products p WHERE o.__cubeJoinField = p.__cubeJoinField) t GROUP BY 1", "SELECT toString(o.status), sum(p.price) FROM orders o LEFT JOIN products p ON o.product_id = p.id GROUP BY 1"),
    ("WITH t AS (SELECT o.id, p.price FROM orders o CROSS JOIN products p WHERE o.__cubeJoinField = p.__cubeJoinField) SELECT SUM(price) FROM t", "SELECT sum(p.price) FROM orders o LEFT JOIN products p ON o.product_id = p.id"),
    ("SELECT SUM(id) FROM (SELECT u.id FROM users u CROSS JOIN orders o WHERE u.__cubeJoinField = o.__cubeJoinField) t", "SELECT sum(u.id) FROM users u LEFT JOIN orders o ON u.id = o.user_id"),
    ("SELECT COUNT(id) FROM (SELECT u.id FROM users u CROSS JOIN orders o WHERE u.__cubeJoinField = o.__cubeJoinField) t", "SELECT count(u.id) FROM users u LEFT JOIN orders o ON u.id = o.user_id"),
    ("SELECT COUNT(c) FROM (SELECT users_city c FROM sales) t", "SELECT count() FROM orders"),
    ("SELECT SUM(x) FROM (SELECT users_city, id x FROM sales) t", "SELECT sum(id) FROM orders"),
    ("SELECT u.city, MEASURE(o.revenue) AS r FROM orders o CROSS JOIN users u GROUP BY 1", "SELECT u.city, sum(toFloat64(o.amount)) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1"),
    # Boolean expressions in SELECT.
    ("SELECT is_vip, COUNT(*) FROM users GROUP BY 1", "SELECT toBool(is_vip), count() FROM users GROUP BY 1"),
    ("SELECT status = 'shipped' b, COUNT(*) FROM orders GROUP BY 1", "SELECT toBool(status = 'shipped') b, count() FROM orders GROUP BY 1"),
    ("SELECT quantity_dim > 2 AND status = 'shipped' b, COUNT(*) FROM orders GROUP BY 1", "SELECT toBool(quantity > 2 AND status = 'shipped') b, count() FROM orders GROUP BY 1"),
    ("SELECT NOT (cancel_status = 'shipped') b, COUNT(*) FROM orders GROUP BY 1", "SELECT toBool(NOT (%s = 'shipped')) b, count() FROM orders GROUP BY 1" % CS),
    ("SELECT cancel_status IS NULL b, COUNT(*) FROM orders GROUP BY 1", "SELECT toBool(%s IS NULL) b, count() FROM orders GROUP BY 1" % CS),
    ("SELECT status IN ('shipped') b, COUNT(*) FROM orders GROUP BY 1", "SELECT toBool(status IN ('shipped')) b, count() FROM orders GROUP BY 1"),
    ("SELECT SUM(CASE WHEN quantity_dim > 2 OR cancel_status IS NULL THEN amount_dim END) FROM orders", "SELECT sumIf(amount, quantity > 2 OR %s IS NULL) FROM orders" % CS),
    ("SELECT id, status = 'shipped' b FROM orders ORDER BY id LIMIT 3", "SELECT id, toBool(status = 'shipped') b FROM orders ORDER BY id LIMIT 3"),
    # Date arithmetic. Stock Cube has no template for an interval in an aggregation.
    ("SELECT COUNT(*) FROM orders WHERE created_at >= TIMESTAMP '2025-01-01 00:00:00' + INTERVAL '1 day'", "SELECT count() FROM orders WHERE created_at >= toDateTime('2025-01-02 00:00:00', 'UTC')"),
    ("SELECT COUNT(*) FROM orders WHERE created_at + INTERVAL '1 day' >= TIMESTAMP '2025-01-02 00:00:00'", "SELECT count() FROM orders WHERE created_at + INTERVAL 1 DAY >= toDateTime('2025-01-02 00:00:00', 'UTC')", INTERVAL),
    ("SELECT MAX(created_at + INTERVAL '1 month') FROM orders", "SELECT max(created_at + INTERVAL 1 MONTH) FROM orders", INTERVAL),
    ("SELECT MAX(created_at - INTERVAL '1 hour') FROM orders", "SELECT max(created_at - INTERVAL 1 HOUR) FROM orders", INTERVAL),
    ("SELECT DATE_TRUNC('month', created_at + INTERVAL '1 day') m, COUNT(*) FROM orders GROUP BY 1", "SELECT toStartOfMonth(created_at + INTERVAL 1 DAY) m, count() FROM orders GROUP BY 1", INTERVAL),
    ("SELECT EXTRACT(YEAR FROM created_at) y, COUNT(*) FROM orders GROUP BY 1", "SELECT toYear(created_at) y, count() FROM orders GROUP BY 1", "rollup"),
    ("SELECT DATEDIFF('day', MIN(created_at), MAX(created_at)) FROM orders", "SELECT dateDiff('day', min(created_at), max(created_at)) FROM orders"),
    ("SELECT CAST(created_at AS date) d, COUNT(*) FROM orders GROUP BY 1", "SELECT toDate(created_at) d, count() FROM orders GROUP BY 1"),
    ("SELECT COUNT(*) FROM orders WHERE CAST(created_at AS date) = DATE '2025-01-15'", "SELECT count() FROM orders WHERE toDate(created_at) = '2025-01-15'"),
    # Cube reads this as notInDateRange, and it joins the ranges with OR.
    ("SELECT COUNT(*) FROM orders WHERE DATE_TRUNC('day', created_at) NOT IN (TIMESTAMP '2025-01-05 00:00:00', TIMESTAMP '2025-03-09 00:00:00')", "SELECT count() FROM orders WHERE toDate(created_at) NOT IN ('2025-01-05', '2025-03-09')"),
    ("SELECT COUNT(*) FROM orders WHERE NOT (DATE_TRUNC('day', created_at) IN (TIMESTAMP '2025-01-05 00:00:00', TIMESTAMP '2025-03-09 00:00:00'))", "SELECT count() FROM orders WHERE toDate(created_at) NOT IN ('2025-01-05', '2025-03-09')"),
    ("SELECT COUNT(*) FROM orders WHERE DATE_TRUNC('month', created_at) NOT IN (TIMESTAMP '2025-01-01 00:00:00', TIMESTAMP '2025-03-01 00:00:00')", "SELECT count() FROM orders WHERE toMonth(created_at) NOT IN (1, 3)"),
    ("SELECT COUNT(*) FROM orders WHERE DATE_TRUNC('day', created_at) NOT IN (TIMESTAMP '2025-01-05 00:00:00')", "SELECT count() FROM orders WHERE toDate(created_at) <> '2025-01-05'"),
    ("SELECT COUNT(*) FROM orders WHERE created_at NOT BETWEEN TIMESTAMP '2025-01-05 00:00:00' AND TIMESTAMP '2025-03-09 00:00:00'", "SELECT count() FROM orders WHERE created_at NOT BETWEEN toDateTime('2025-01-05 00:00:00', 'UTC') AND toDateTime('2025-03-09 00:00:00', 'UTC')"),
    ("SELECT COUNT(*) FROM orders WHERE NOT (created_at <= TIMESTAMP '2025-03-09 00:00:00')", "SELECT count() FROM orders WHERE created_at > toDateTime('2025-03-09 00:00:00', 'UTC')"),
    ("SELECT COUNT(*) FROM orders WHERE NOT (created_at >= TIMESTAMP '2025-01-05 00:00:00' AND created_at < TIMESTAMP '2025-03-09 00:00:00')", "SELECT count() FROM orders WHERE NOT (created_at >= toDateTime('2025-01-05 00:00:00', 'UTC') AND created_at < toDateTime('2025-03-09 00:00:00', 'UTC'))"),
    ("SELECT COUNT(*) FROM orders WHERE created_at < TIMESTAMP '2025-01-05 00:00:00' OR created_at >= TIMESTAMP '2025-03-09 00:00:00'", "SELECT count() FROM orders WHERE created_at < toDateTime('2025-01-05 00:00:00', 'UTC') OR created_at >= toDateTime('2025-03-09 00:00:00', 'UTC')"),
    # Casts in a list, and on a column with NULL.
    ("SELECT COUNT(*) FROM orders WHERE user_id IN (CAST(200 AS numeric), CAST('1' AS numeric))", "SELECT count() FROM orders WHERE user_id IN (200, 1)"),
    ("SELECT COUNT(*) FROM orders WHERE user_id IN (200::numeric, 1::numeric)", "SELECT count() FROM orders WHERE user_id IN (200, 1)"),
    ("SELECT COUNT(*) FROM typed_events WHERE CAST(n_int AS numeric) = 1", "SELECT count() FROM typed_events WHERE n_int = 1"),
    ("SELECT COUNT(*) FROM typed_events WHERE CAST(n_int AS numeric) = 1.5", "SELECT count() FROM typed_events WHERE n_int = 1.5"),
    ("SELECT COUNT(*) FROM typed_events WHERE n_int::numeric IN ('2', 1.5)", "SELECT count() FROM typed_events WHERE n_int IN (2, 1.5)"),
    ("SELECT COUNT(*) FROM typed_events WHERE CAST(n_int AS numeric) BETWEEN 1.5 AND 3", "SELECT count() FROM typed_events WHERE n_int BETWEEN 1.5 AND 3"),
    # IS DISTINCT FROM next to AND. Cube writes it with no parentheses.
    ("SELECT COUNT(*) FROM orders WHERE cancel_status IS DISTINCT FROM 'shipped' AND quantity_dim = 1", "SELECT count() FROM orders WHERE %s IS DISTINCT FROM 'shipped' AND quantity = 1" % CS),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status IS NOT DISTINCT FROM 'shipped' AND quantity_dim = 1", "SELECT count() FROM orders WHERE %s IS NOT DISTINCT FROM 'shipped' AND quantity = 1" % CS),
    ("SELECT COUNT(*) FROM orders WHERE NOT (cancel_status IS DISTINCT FROM 'shipped')", "SELECT count() FROM orders WHERE %s IS NOT DISTINCT FROM 'shipped'" % CS),
    ("SELECT cancel_status IS DISTINCT FROM 'shipped' b, COUNT(*) FROM orders GROUP BY 1", "SELECT toBool(%s IS DISTINCT FROM 'shipped') b, count() FROM orders GROUP BY 1" % CS),
    # Long lists, in the forms that have no OR.
    ("SELECT COUNT(*) FROM orders WHERE %s" % " OR ".join("id = %d" % i for i in range(1, 201)), "SELECT toUInt64(200)"),
    ("SELECT COUNT(*) FROM orders WHERE %s" % " AND ".join("id <> %d" % i for i in range(1, 201)), "SELECT toUInt64(4800)"),
    ("SELECT COUNT(*) FROM orders WHERE %s" % " OR ".join("cancel_status = 'v%d'" % i for i in range(60)), "SELECT toUInt64(0)"),
    ("SELECT COUNT(*) FROM orders WHERE %s" % " AND ".join("cancel_status <> 'v%d'" % i for i in range(60)), "SELECT count() FROM orders WHERE status <> 'completed'"),
    ("SELECT COUNT(*) FROM orders WHERE id IN (%s)" % sql_list(range(1, 501)), "SELECT toUInt64(500)"),
    ("SELECT COUNT(*) FROM orders WHERE id NOT IN (%s)" % sql_list(range(1, 501)), "SELECT toUInt64(4500)"),
    ("SELECT COUNT(*) FROM orders WHERE cancel_status NOT IN (%s)" % ",".join("'v%d'" % i for i in range(100)), "SELECT count() FROM orders WHERE status <> 'completed'"),
    ("SELECT COUNT(*) FROM orders WHERE amount_dim IN (%s)" % sql_list(fractions(100, step=0.0001)), "SELECT count() FROM orders WHERE amount IN (%s)" % sql_list(fractions(100, step=0.0001))),
]

# Constructs that Cube reads with another meaning, and that have no form that the driver can write. Each row has the text and a text of its message. The message names the construct.
NATIVE_REFUSALS = [
    ("SELECT status, COUNT(*) FROM orders GROUP BY ROLLUP(status)", "GROUP BY ROLLUP"),
    ("SELECT status, quantity_dim, COUNT(*) FROM orders GROUP BY ROLLUP(status, quantity_dim)", "GROUP BY ROLLUP"),
    ("SELECT status, COUNT(*) FROM orders GROUP BY CUBE(status)", "GROUP BY CUBE"),
    ("SELECT status, COUNT(*) FROM orders GROUP BY GROUPING SETS ((status), ())", "GROUP BY GROUPING SETS"),
    ("SELECT SUM(DISTINCT quantity_dim) FROM orders", "DISTINCT"),
    ("SELECT status, SUM(DISTINCT quantity_dim) FROM orders GROUP BY 1", "DISTINCT"),
    ("SELECT SUM(DISTINCT quantity_dim + 0) FROM orders", "DISTINCT"),
    ("SELECT STDDEV(DISTINCT quantity_dim) FROM orders", "DISTINCT"),
    ("SELECT MEASURE(revenue) FILTER (WHERE status = 'completed') FROM orders", "FILTER"),
    ("SELECT SUM(revenue) FILTER (WHERE status = 'completed') FROM orders", "FILTER"),
    ("SELECT STRING_AGG(status, ',') FILTER (WHERE status = 'completed') FROM orders", "FILTER"),
    ("SELECT cancel_status, COUNT(*) FROM orders GROUP BY 1 ORDER BY 1 NULLS FIRST LIMIT 1", "NULLS FIRST"),
    ("SELECT cancel_status FROM orders ORDER BY cancel_status DESC NULLS FIRST LIMIT 1", "NULLS FIRST"),
    ("SELECT COUNT(*) FROM orders a CROSS JOIN orders b", "cross product"),
    ("SELECT COUNT(*) FROM orders a CROSS JOIN users b", "cross product"),
    ("SELECT COUNT(*) FROM orders, users", "cross product"),
    ("WITH a AS (SELECT COUNT(*) c FROM orders), b AS (SELECT COUNT(*) d FROM users) SELECT c, d FROM a, b", "cross product"),
    ("SELECT COUNT(*) FROM (SELECT COUNT(*) c FROM orders) a, (SELECT COUNT(*) d FROM users) b", "cross product"),
    ("SELECT COUNT(*) FROM orders a, orders b WHERE a.__cubeJoinField = b.__cubeJoinField", "to itself"),
    ("SELECT COUNT(u.id) FROM users u CROSS JOIN orders o WHERE u.__cubeJoinField = o.__cubeJoinField", "one to many"),
    ("SELECT SUM(u.id) FROM users u CROSS JOIN orders o WHERE u.__cubeJoinField = o.__cubeJoinField", "one to many"),
    ("SELECT AVG(u.id) FROM users u CROSS JOIN orders o WHERE u.__cubeJoinField = o.__cubeJoinField", "one to many"),
    ("SELECT SUM(quantity_dim) FROM orders o CROSS JOIN payments p WHERE o.__cubeJoinField = p.__cubeJoinField", "one to many"),
    ("SELECT COUNT(*) FROM customer_orders", "one to many"),
    ("SELECT SUM(id) FROM customer_orders", "one to many"),
    ("SELECT amount_dim / 0 FROM orders LIMIT 1", "divides by zero"),
    ("SELECT SUM(amount_dim / 0.0) FROM orders", "divides by zero"),
    ("SELECT MEASURE(revenue) / 0 FROM orders", "divides by zero"),
    ("SELECT id, ROW_NUMBER() OVER (ORDER BY id) r FROM orders ORDER BY id LIMIT 3", "over the rows of a cube with a rolling window"),
    ("SELECT DISTINCT ON (status) status, id FROM orders ORDER BY status, id", "over the rows of a cube with a rolling window"),
    ("SELECT MAX(created_at) - MIN(created_at) FROM orders", "interval"),
    # A text that restarts Cube, or stops it after many seconds, stops first.
    ("SELECT COUNT(*) FROM orders WHERE id IN (%s)" % sql_list(range(1, 2100)), "a list of 2099 values"),
    ("SELECT COUNT(*) FROM orders WHERE %s" % " OR ".join("(id = %d AND quantity_dim = %d)" % (i, i) for i in range(1, 2100)), "conditions joined"),
]


def fan_out_cases():
    """A dimension next to a one to many join, each aggregation, each form of the reference. A measure stays the measure, see Fan-out in docs/query-compilation.md."""
    users = [{"source-table": "t:orders", "alias": "O", "strategy": "left-join", "condition": ["=", "d:users.id", "d:orders.user_id|O"]}]
    inner = [dict(users[0], strategy="inner-join")]
    payments = [{"source-table": "t:payments", "alias": "P", "strategy": "left-join", "condition": ["=", "d:orders.id", "d:payments.order_id|P"]}]
    to_user = [{"source-table": "t:users", "alias": "U", "strategy": "left-join", "condition": ["=", "d:orders.user_id", "d:users.id|U"]}]
    ujoin = "users u LEFT JOIN orders o ON u.id = o.user_id"
    ijoin = "users u INNER JOIN orders o ON u.id = o.user_id"
    pays = "(SELECT id AS order_id, 'card' AS method FROM orders UNION ALL SELECT id, 'fee' FROM orders)"
    ojoin = "orders o LEFT JOIN %s p ON o.id = p.order_id" % pays
    both = ojoin + " LEFT JOIN users u ON o.user_id = u.id"
    out = []
    for name, clause, sql, refusal in (("count_rows", ["count"], "count()", None),
                                       ("count", ["count", "d:users.id"], "count(u.id)", None),
                                       ("sum", ["sum", "d:users.id"], "sum(u.id)", None),
                                       ("avg", ["avg", "d:users.id"], "avg(u.id)", None),
                                       ("min", ["min", "d:users.id"], "min(u.id)", None),
                                       ("max", ["max", "d:users.id"], "max(u.id)", None),
                                       ("distinct", ["distinct", "d:users.id"], "uniqExact(u.id)", None),
                                       ("stddev", ["stddev", "d:users.id"], "stddevPop(u.id)", NO_FORM),
                                       ("var", ["var", "d:users.id"], "varPop(u.id)", NO_FORM),
                                       ("median", ["median", "d:users.id"], "quantileExact(0.5)(u.id)", NO_FORM),
                                       ("count_where", ["count-where", [">", "d:users.id", 5]], "countIf(u.id > 5)", None),
                                       ("sum_where", ["sum-where", "d:users.id", [">", "d:users.id", 5]], "sumIf(u.id, u.id > 5)", None),
                                       ("share", ["share", [">", "d:users.id", 5]], "countIf(u.id > 5) / count()", None)):
        out += [
            case("fan_out/users/%s/total" % name, "users", "SELECT %s FROM %s" % (sql, ujoin), refusal, aggregation=[clause], joins=users),
            case("fan_out/users/%s/by_own_column" % name, "users", "SELECT u.city, %s FROM %s GROUP BY 1" % (sql, ujoin), refusal, aggregation=[clause], breakout=["d:users.city"], joins=users),
            case("fan_out/users/%s/inner" % name, "users", "SELECT %s FROM %s" % (sql, ijoin), refusal, aggregation=[clause], joins=inner),
        ]
    raw = {"source-table": "t:users", "fields": ["d:users.id"], "joins": [dict(users[0], fields="none")]}
    named = ["field", "id", {"base-type": "type/Integer"}]
    out += [
        case("fan_out/users/sum/by_many_side", "users", "SELECT nullIf(toString(o.status), ''), sum(u.id) FROM %s GROUP BY 1" % ujoin, aggregation=[["sum", "d:users.id"]], breakout=["d:orders.status|O"], joins=users),
        case("fan_out/users/sum/later_stage_by_name", None, "SELECT sum(u.id) FROM %s" % ujoin, source_query=raw, aggregation=[["sum", named]]),
        case("fan_out/users/count/later_stage_by_name", None, "SELECT count(u.id) FROM %s" % ujoin, source_query=raw, aggregation=[["count", named]]),
        case("fan_out/users/many_side_sum", "users", "SELECT toFloat64(sum(o.amount)) FROM %s" % ujoin, aggregation=[["sum", "d:orders.amount_dim|O"]], joins=users),
        case("fan_out/orders/rows", "orders", "SELECT count() FROM %s" % ojoin, aggregation=[["count"]], joins=payments),
        case("fan_out/orders/sum", "orders", "SELECT sum(o.quantity) FROM %s" % ojoin, aggregation=[["sum", "d:orders.quantity_dim"]], joins=payments),
        case("fan_out/orders/count", "orders", "SELECT count(o.quantity) FROM %s" % ojoin, aggregation=[["count", "d:orders.quantity_dim"]], joins=payments),
        case("fan_out/orders/by_many_side", "orders", "SELECT p.method, sum(o.quantity) FROM %s GROUP BY 1" % ojoin, aggregation=[["sum", "d:orders.quantity_dim"]], breakout=["d:payments.method|P"], joins=payments),
        case("fan_out/both_sides/rows", "orders", "SELECT count() FROM %s" % both, aggregation=[["count"]], joins=payments + to_user),
        case("fan_out/both_sides/sum", "orders", "SELECT sum(u.id) FROM %s" % both, aggregation=[["sum", "d:users.id|U"]], joins=payments + to_user),
        case("fan_out/both_sides/count", "orders", "SELECT count(u.id) FROM %s" % both, aggregation=[["count", "d:users.id|U"]], joins=payments + to_user),
        case("fan_out/both_sides/own_sum", "orders", "SELECT sum(o.quantity) FROM %s" % both, aggregation=[["sum", "d:orders.quantity_dim"]], joins=payments + to_user),
        case("fan_out/many_to_one/own_sum", "orders", "SELECT sum(o.quantity) FROM orders o LEFT JOIN users u ON o.user_id = u.id", aggregation=[["sum", "d:orders.quantity_dim"]], joins=to_user),
        case("fan_out/many_to_one/joined_sum", "orders", "SELECT sum(u.id) FROM orders o LEFT JOIN users u ON o.user_id = u.id", aggregation=[["sum", "d:users.id|U"]], joins=to_user),
        case("fan_out/view/count_rows", "customer_orders", None, "one to many", aggregation=[["count"]]),
        case("fan_out/view/sum", "customer_orders", None, "one to many", aggregation=[["sum", "d:customer_orders.id"]]),
        case("fan_out/view/count_of_column", "customer_orders", None, "one to many", aggregation=[["count", "d:customer_orders.city"]]),
        # A condition that the join of the model does not hold stops, in each form.
        case("join/condition/two_conditions", "orders", None, "whole table", aggregation=[["count"]],
             joins=[{"source-table": "t:users", "alias": "U", "strategy": "left-join", "condition": ["and", ["=", "d:orders.user_id", "d:users.id|U"], ["=", "d:users.city|U", "Austin"]]}]),
        case("join/condition/inequality", "orders", None, "whole table", aggregation=[["count"]],
             joins=[{"source-table": "t:users", "alias": "U", "strategy": "left-join", "condition": [">", "d:orders.user_id", "d:users.id|U"]}]),
        case("join/condition/other_columns", "orders", None, "through the data model", aggregation=[["count"]],
             joins=[{"source-table": "t:users", "alias": "U", "strategy": "left-join", "condition": ["=", "d:orders.id", "d:users.id|U"]}]),
        case("join/strategy/right", "orders", None, "right-join", aggregation=[["count"]], joins=[dict(to_user[0], strategy="right-join")]),
        case("join/strategy/full", "orders", None, "full-join", aggregation=[["count"]], joins=[dict(to_user[0], strategy="full-join")]),
        case("join/card/whole_table", "orders", "SELECT count() FROM orders", aggregation=[["count"]],
             joins=[{"source-query": {"source-table": "t:users"}, "alias": "U", "strategy": "left-join", "condition": ["=", "d:orders.user_id", ["field", "id", {"base-type": "type/Integer", "join-alias": "U"}]]}]),
        case("join/card/aggregated", "orders", None, "whole table", aggregation=[["count"]],
             joins=[{"source-query": {"source-table": "t:users", "breakout": ["d:users.id"], "aggregation": [["count"]]}, "alias": "U", "strategy": "left-join",
                     "condition": ["=", "d:orders.user_id", ["field", "id", {"base-type": "type/Integer", "join-alias": "U"}]]}]),
    ]
    return out


def name_reference_cases():
    """A later stage names a column of raw rows by its output or by the name of its field. Metabase core finds both, so both must read the same number."""
    price = "d:orders>products.price"
    raw = {"source-table": "t:orders", "fields": ["d:orders.id", price]}
    joined = "orders o LEFT JOIN products p ON o.product_id = p.id"
    named = lambda name: ["field", name, {"base-type": "type/Float"}]  # noqa: E731
    out = []
    for name in ("price", "products__via__product_id__price"):
        out += [
            case("name/%s/sum" % name, None, "SELECT toFloat64(sum(p.price)) FROM %s" % joined, source_query=raw, aggregation=[["sum", named(name)]]),
            case("name/%s/avg" % name, None, "SELECT avg(toFloat64(p.price)) FROM %s" % joined, source_query=raw, aggregation=[["avg", named(name)]]),
            case("name/%s/count" % name, None, "SELECT count(p.price) FROM %s" % joined, source_query=raw, aggregation=[["count", named(name)]]),
            case("name/%s/max" % name, None, "SELECT max(p.price) FROM %s" % joined, source_query=raw, aggregation=[["max", named(name)]]),
            case("name/%s/sum_where" % name, None, "SELECT sumIf(toFloat64(p.price), p.price > 50) FROM %s" % joined, source_query=raw, aggregation=[["sum-where", named(name), [">", named(name), 50]]]),
            case("name/%s/by_column" % name, None, "SELECT o.status, toFloat64(sum(p.price)) FROM %s GROUP BY 1" % joined,
                 source_query={"source-table": "t:orders", "fields": ["d:orders.status", price]}, aggregation=[["sum", named(name)]], breakout=[["field", "status", {"base-type": "type/Text"}]]),
            case("name/%s/own_column_next_to_it" % name, None, "SELECT sum(o.quantity) FROM orders o", source_query={"source-table": "t:orders", "fields": ["d:orders.quantity_dim", price]},
                 aggregation=[["sum", ["field", "quantity_dim", {"base-type": "type/Integer"}]]]),
        ]
    return out


def list_cases():
    """A long list of each type, with `=`, `!=` and NOT, against the limit of the planner of Cube. See Long lists in docs/query-compilation.md."""
    numbers = list(range(1, 101))
    texts = ["v%d" % i for i in range(100)]
    fractions_100 = fractions(100, step=0.0001)
    days = sorted({"2025-%02d-%02d" % (i % 12 + 1, i % 28 + 1) for i in range(100)})
    times = sorted({"2025-%02d-%02dT%02d:00:00" % (i % 12 + 1, i % 28 + 1, i % 24) for i in range(100)})
    time_list = ",".join("toDateTime('%s', 'UTC')" % t.replace("T", " ") for t in times)
    out = []
    for size in (100, 500, 1000):
        ids = list(range(1, size + 1))
        out += [
            case("long_list/%d/number/equal" % size, "orders", "SELECT count() FROM orders WHERE id IN (%s)" % sql_list(ids), aggregation=[["count"]], filter=["=", "d:orders.id", *ids]),
            case("long_list/%d/number/not_equal" % size, "orders", "SELECT count() FROM orders WHERE id NOT IN (%s)" % sql_list(ids), aggregation=[["count"]], filter=["!=", "d:orders.id", *ids]),
            case("long_list/%d/number/not" % size, "orders", "SELECT count() FROM orders WHERE id NOT IN (%s)" % sql_list(ids), aggregation=[["count"]], filter=["not", ["=", "d:orders.id", *ids]]),
        ]
    too_long = list(range(1, 2100))
    out += [
        case("long_list/too_long/number", "orders", None, "a list of 2099 values", aggregation=[["count"]], filter=["=", "d:orders.id", *too_long]),
        case("long_list/too_long/not_equal", "orders", None, "a list of 2099 values", aggregation=[["count"]], filter=["!=", "d:orders.id", *too_long]),
        case("long_list/2000/number", "orders", "SELECT toUInt64(2000)", aggregation=[["count"]], filter=["=", "d:orders.id", *range(1, 2001)]),
        case("long_list/text/equal", "orders", "SELECT count() FROM orders WHERE status IN (%s)" % ",".join("'%s'" % t for t in texts), aggregation=[["count"]], filter=["=", "d:orders.status", *texts]),
        case("long_list/text/not_equal", "orders", "SELECT count() FROM orders", aggregation=[["count"]], filter=["!=", "d:orders.status", *texts]),
        case("long_list/text/not_equal_null", "orders", "SELECT count() FROM orders", aggregation=[["count"]], filter=["!=", "d:orders.cancel_status", *texts]),
        case("long_list/text/not_null", "orders", "SELECT count() FROM orders WHERE status <> 'completed'", aggregation=[["count"]], filter=["not", ["=", "d:orders.cancel_status", *texts]]),
        case("long_list/fraction/equal", "orders", "SELECT count() FROM orders WHERE amount IN (%s)" % sql_list(fractions_100), aggregation=[["count"]], filter=["=", AMOUNT, *fractions_100]),
        case("long_list/fraction/not_equal", "orders", "SELECT count() FROM orders WHERE amount NOT IN (%s)" % sql_list(fractions_100), aggregation=[["count"]], filter=["!=", AMOUNT, *fractions_100]),
        case("long_list/time/equal", "orders", "SELECT count() FROM orders WHERE created_at IN (%s)" % time_list, aggregation=[["count"]], filter=["=", "d:orders.created_at", *times]),
        case("long_list/time/not_equal", "orders", "SELECT count() FROM orders WHERE created_at NOT IN (%s)" % time_list, aggregation=[["count"]], filter=["!=", "d:orders.created_at", *times]),
        case("long_list/day/equal", "orders", "SELECT count() FROM orders WHERE toDate(created_at) IN (%s)" % ",".join("'%s'" % d for d in days), aggregation=[["count"]], filter=["=", "d:orders.created_at", *days]),
        case("long_list/day/not_equal", "orders", "SELECT count() FROM orders WHERE toDate(created_at) NOT IN (%s)" % ",".join("'%s'" % d for d in days), aggregation=[["count"]], filter=["!=", "d:orders.created_at", *days]),
        case("long_list/date_column/equal", "typed_events", "SELECT count() FROM typed_events WHERE day IN (%s)" % ",".join("'%s'" % d for d in days), aggregation=[["count"]], filter=["=", "d:typed_events.day", *days]),
        case("long_list/view/not_equal", "sales", "SELECT count() FROM orders", aggregation=[["count"]], filter=["!=", "d:sales.status", *texts]),
    ]
    return out


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
        if c["truth"] is None:
            problems.append("%s: no refusal: %s" % (c["id"], str(rows)[:120]))
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


def run_native_semantics(execute, differ):
    """NATIVE_SEMANTICS and NATIVE_REFUSALS. `execute(sql)` gives (rows, error). Returns (a count of each group, the problems)."""
    problems = []
    counts = {"truth": 0, "refused": 0}
    for row in NATIVE_SEMANTICS:
        sql, truth = row[:2]
        may_refuse = row[2] if len(row) > 2 else None
        rows, error = execute(sql)
        if error:
            if may_refuse and may_refuse in error:
                counts["refused"] += 1
            else:
                problems.append("refused %r: %s" % (sql[:200], error[:220].replace("\n", " ")))
            continue
        diff = differ(rows, truth, False, 1e-6)
        if diff:
            problems.append("wrong %r: %s" % (sql[:200], diff[:200]))
        else:
            counts["truth"] += 1
    for sql, needle in NATIVE_REFUSALS:
        rows, error = execute(sql)
        if not error:
            problems.append("no refusal %r: %s" % (sql[:200], str(rows)[:120]))
        elif needle not in error:
            problems.append("another message %r: %s" % (sql[:200], error[:220].replace("\n", " ")))
        else:
            counts["refused"] += 1
    return counts, problems
