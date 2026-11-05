"""Thirty native queries that an analyst writes on the demo model, each with its truth in direct ClickHouse SQL. They were written before the grammar of native SQL, so they measure it and do not follow it.

`python3 analyst.py` runs them on a database of Metabase. It fails if a query gives a wrong number or a raw error, if a query refuses with no entry in LIMITS, or if more than MAX_REFUSALS refuse. See "Native SQL" in docs/query-compilation.md.
"""
import argparse
import json
import sys

import lib

MAX_REFUSALS = 3

# id, native SQL, truth, and options. `ordered` compares the order of the rows. `tags` holds template tags, as a function of the field ids.
QUERIES = [
    ("total_revenue", "SELECT MEASURE(revenue) FROM orders", "SELECT sum(amount) FROM orders"),
    ("revenue_by_status", "SELECT status, MEASURE(revenue) AS revenue FROM orders GROUP BY status ORDER BY revenue DESC",
     "SELECT toString(status), sum(amount) AS r FROM orders GROUP BY 1 ORDER BY r DESC", {"ordered": True}),
    ("orders_by_month", "SELECT DATE_TRUNC('month', created_at) AS month, COUNT(*) AS orders FROM orders GROUP BY 1 ORDER BY 1",
     "SELECT toStartOfMonth(created_at), count() FROM orders GROUP BY 1 ORDER BY 1", {"ordered": True}),
    ("revenue_per_day_in_a_month",
     "SELECT DATE_TRUNC('day', created_at) AS day, MEASURE(revenue) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-04-01' GROUP BY 1 ORDER BY 1",
     "SELECT toDate(created_at), sum(amount) FROM orders WHERE created_at >= toDateTime('2025-03-01 00:00:00','UTC') AND created_at < toDateTime('2025-04-01 00:00:00','UTC') GROUP BY 1 ORDER BY 1",
     {"ordered": True}),
    ("sum_with_having", "SELECT status, SUM(amount_dim) AS total FROM orders GROUP BY status HAVING SUM(amount_dim) > 100000 ORDER BY status",
     "SELECT toString(status) AS s, sum(amount) AS t FROM orders GROUP BY s HAVING t > 100000 ORDER BY s", {"ordered": True}),
    ("avg_min_max_by_status", "SELECT status, AVG(amount_dim), MIN(amount_dim), MAX(amount_dim) FROM orders GROUP BY 1",
     "SELECT toString(status), avg(amount), min(amount), max(amount) FROM orders GROUP BY 1"),
    ("distinct_customers_by_status", "SELECT status, COUNT(DISTINCT user_id) AS customers FROM orders GROUP BY 1",
     "SELECT toString(status), uniqExact(user_id) FROM orders GROUP BY 1"),
    ("star_of_a_small_cube", "SELECT * FROM products ORDER BY id LIMIT 5", "SELECT id, name, category, price FROM products ORDER BY id LIMIT 5", {"ordered": True, "columns": ["id", "name", "category", "price"]}),
    ("top_customers", "SELECT user_id, SUM(amount_dim) AS total FROM orders GROUP BY user_id ORDER BY total DESC, user_id LIMIT 10",
     "SELECT user_id, sum(amount) AS t FROM orders GROUP BY user_id ORDER BY t DESC, user_id LIMIT 10", {"ordered": True}),
    ("not_equal", "SELECT COUNT(*) FROM orders WHERE status <> 'cancelled'", "SELECT count() FROM orders WHERE status <> 'cancelled'"),
    ("not_in_with_null", "SELECT COUNT(*) FROM orders WHERE cancel_status NOT IN ('shipped', 'processing')",
     "SELECT count() FROM orders WHERE nullIf(toString(status), 'completed') NOT IN ('shipped', 'processing') AND status <> 'completed'"),
    ("like_with_case", "SELECT COUNT(*) FROM users WHERE email LIKE 'user1%'", "SELECT count() FROM users WHERE email LIKE 'user1%'"),
    ("ilike", "SELECT COUNT(*) FROM products WHERE name ILIKE '%PRODUCT 1%'", "SELECT count() FROM products WHERE name ILIKE '%PRODUCT 1%'"),
    ("between_numbers", "SELECT COUNT(*), MEASURE(revenue) FROM orders WHERE amount_dim BETWEEN 40 AND 50.5", "SELECT count(), sum(amount) FROM orders WHERE amount BETWEEN 40 AND 50.5"),
    ("between_dates", "SELECT COUNT(*) FROM orders WHERE created_at BETWEEN '2025-03-01' AND '2025-03-31'",
     "SELECT count() FROM orders WHERE created_at BETWEEN toDateTime('2025-03-01 00:00:00','UTC') AND toDateTime('2025-03-31 00:00:00','UTC')"),
    ("case_buckets", "SELECT CASE WHEN amount_dim >= 100 THEN 'big' WHEN amount_dim >= 50 THEN 'medium' ELSE 'small' END AS bucket, COUNT(*) AS n FROM orders GROUP BY 1",
     "SELECT multiIf(amount >= 100, 'big', amount >= 50, 'medium', 'small') AS b, count() FROM orders GROUP BY b"),
    ("conditional_sum", "SELECT SUM(CASE WHEN status = 'completed' THEN amount_dim ELSE 0 END) AS completed, SUM(amount_dim) AS total FROM orders",
     "SELECT sumIf(amount, status = 'completed'), sum(amount) FROM orders"),
    ("unit_price", "SELECT id, amount_dim / quantity_dim AS unit_price FROM orders ORDER BY id LIMIT 5",
     "SELECT id, toFloat64(amount) / nullIf(toFloat64(quantity), 0) FROM orders ORDER BY id LIMIT 5", {"ordered": True, "tol": 1e-9}),
    ("average_by_hand", "SELECT status, SUM(amount_dim) / COUNT(*) AS aov FROM orders GROUP BY status",
     "SELECT toString(status), toFloat64(sum(amount)) / count() FROM orders GROUP BY 1", {"tol": 1e-9}),
    ("filter_over_a_subquery", "SELECT * FROM (SELECT status, COUNT(*) AS c FROM orders GROUP BY 1) t WHERE c > 900 ORDER BY status",
     "SELECT toString(status) AS s, count() AS c FROM orders GROUP BY s HAVING c > 900 ORDER BY s", {"ordered": True}),
    ("join_of_the_model", "SELECT u.city, MEASURE(o.revenue) AS revenue FROM orders o JOIN users u ON o.__cubeJoinField = u.__cubeJoinField GROUP BY 1 ORDER BY 1",
     "SELECT u.city, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1 ORDER BY 1", {"ordered": True}),
    ("join_on_keys", "SELECT p.category, COUNT(*) AS n FROM orders o JOIN products p ON o.product_id = p.id GROUP BY 1",
     "SELECT p.category, count() FROM orders o JOIN products p ON o.product_id = p.id GROUP BY 1"),
    ("view_by_city", "SELECT users_city, MEASURE(revenue) AS revenue FROM sales GROUP BY 1 ORDER BY 1",
     "SELECT u.city, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1 ORDER BY 1", {"ordered": True}),
    ("boolean_filter", "SELECT COUNT(*) FROM users WHERE is_vip", "SELECT count() FROM users WHERE is_vip"),
    ("and_or_not", "SELECT COUNT(*) FROM orders WHERE status = 'completed' OR (quantity_dim > 2 AND NOT amount_dim < 50)",
     "SELECT count() FROM orders WHERE status = 'completed' OR (quantity > 2 AND NOT amount < 50)"),
    ("decimal_exact", "SELECT COUNT(*) FROM orders WHERE amount_dim = 44.01", "SELECT count() FROM orders WHERE amount = 44.01"),
    ("alias_with_the_name_of_a_column", "SELECT status, MAX(amount_dim) AS amount_dim FROM orders GROUP BY 1 ORDER BY amount_dim DESC, status",
     "SELECT toString(status) AS s, max(amount) AS m FROM orders GROUP BY s ORDER BY m DESC, s", {"ordered": True}),
    ("round_and_coalesce", "SELECT COALESCE(cancel_status, 'none') AS s, ROUND(AVG(amount_dim), 2) AS mean FROM orders GROUP BY 1",
     "SELECT coalesce(nullIf(toString(status), 'completed'), 'none') AS s, round(avg(amount), 2) FROM orders GROUP BY s", {"tol": 1e-9}),
    ("template_variable", "SELECT COUNT(*) FROM orders WHERE status = {{status}}", "SELECT count() FROM orders WHERE status = 'shipped'",
     {"tags": {"status": {"id": "s", "name": "status", "display-name": "Status", "type": "text"}},
      "parameters": [{"type": "category", "target": ["variable", ["template-tag", "status"]], "value": "shipped"}]}),
    ("field_filter", "SELECT status, COUNT(*) FROM orders WHERE {{status}} GROUP BY 1", "SELECT toString(status), count() FROM orders WHERE status IN ('shipped', 'completed') GROUP BY 1",
     {"field_filter": ("orders", "status", ["shipped", "completed"])}),
]


def field_id(mb, db_id, table, column):
    meta = mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % db_id)
    for t in meta["tables"]:
        if t["name"].lower() == table:
            for f in t["fields"]:
                if f["name"].lower() == column:
                    return f["id"]
    raise KeyError("%s.%s" % (table, column))


def dataset(mb, db_id, sql, options):
    native = {"query": sql}
    body = {"type": "native", "database": db_id, "native": native}
    if options.get("tags"):
        native["template-tags"] = options["tags"]
        body["parameters"] = options["parameters"]
    if options.get("field_filter"):
        table, column, values = options["field_filter"]
        native["template-tags"] = {column: {"id": "f", "name": column, "display-name": column, "type": "dimension", "widget-type": "string/=",
                                            "dimension": ["field", field_id(mb, db_id, table, column), None]}}
        body["parameters"] = [{"type": "string/=", "target": ["dimension", ["template-tag", column]], "value": values}]
    return body


def run(mb, db_id):
    """A list of (id, verdict, text). The verdict is pass, refused, wrong or error."""
    out = []
    for query in QUERIES:
        name, sql, truth = query[:3]
        options = query[3] if len(query) > 3 else {}
        rows, cols, err = mb.run(dataset(mb, db_id, sql, options))
        if err:
            refused = "Cube said:" not in err and "ERROR:" not in err and "Exception" not in err
            out.append((name, "refused" if refused else "error", err[:300]))
            continue
        if options.get("columns"):
            names = [c["name"].lower() for c in cols]
            rows = [[row[names.index(c)] for c in options["columns"]] for row in rows]
        want = lib.ch(truth)
        diff = lib.compare(rows, want, ordered=options.get("ordered", False), tol=options.get("tol", 1e-9))
        out.append((name, "wrong" if diff else "pass", diff or ""))
    return out


# The queries that may refuse, each with the real limit. Keep this list as short as the limit of Cube allows.
LIMITS = {
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="Cube")
    args = parser.parse_args()
    mb = lib.Metabase(lib.MB, lib.PASSWORD, lib.ADMIN).login()
    db_id = {d["name"]: d["id"] for d in mb.api("GET", "/api/database")["data"]}[args.db]
    results = run(mb, db_id)
    bad = 0
    refused = 0
    for name, verdict, text in results:
        print("%-34s %s %s" % (name, verdict, text))
        if verdict == "refused":
            refused += 1
            if name not in LIMITS:
                bad += 1
        elif verdict != "pass":
            bad += 1
    print(json.dumps({"queries": len(results), "refused": refused, "bad": bad, "db": args.db}))
    if bad or refused > MAX_REFUSALS or len(results) != 30:
        sys.exit(1)


if __name__ == "__main__":
    main()
