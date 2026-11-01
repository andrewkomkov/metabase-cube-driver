"""The refusal groups of the generator, each in the smallest form that a person builds, with the truth from ClickHouse."""
import json

import lib
import run_grid

mb = lib.Metabase().login()
mb.api("PUT", "/api/setting/report-timezone", {"value": None})
mb.api("PUT", "/api/setting/start-of-week", {"value": None})
c = run_grid.Ctx(mb, "Cube")
PD = "orders o JOIN users u ON o.user_id = u.id WHERE o.status = 'completed'"
ALL = "orders o JOIN users u ON o.user_id = u.id JOIN products p ON o.product_id = p.id"
SH = [
    ("paid_sales: net sales, count by week of year", "paid_sales", dict(aggregation=["m:paid_sales.net_sales", "m:paid_sales.count"], breakout=["d:paid_sales.created_at@week-of-year"]), None),
    ("paid_sales: net sales by month of year", "paid_sales", dict(aggregation=["m:paid_sales.net_sales"], breakout=["d:paid_sales.created_at@month-of-year"]), "SELECT toMonth(o.created_at), sum(o.amount) FROM %s GROUP BY 1" % PD),
    ("paid_sales: net sales by day of week", "paid_sales", dict(aggregation=["m:paid_sales.net_sales"], breakout=["d:paid_sales.created_at@day-of-week"]), "SELECT toDayOfWeek(o.created_at) % 7 + 1, sum(o.amount) FROM " + PD + " GROUP BY 1"),
    ("paid_sales: net sales by hour of day", "paid_sales", dict(aggregation=["m:paid_sales.net_sales"], breakout=["d:paid_sales.created_at@hour-of-day"]), "SELECT toHour(o.created_at), sum(o.amount) FROM %s GROUP BY 1" % PD),
    ("sales: revenue by month of year", "sales", dict(aggregation=["m:sales.revenue"], breakout=["d:sales.created_at@month-of-year"]), "SELECT toMonth(created_at), sum(amount) FROM orders GROUP BY 1"),
    ("orders: revenue by month of year", "orders", dict(aggregation=["m:orders.revenue"], breakout=["d:orders.created_at@month-of-year"]), "SELECT toMonth(created_at), sum(amount) FROM orders GROUP BY 1"),
    ("paid_sales: vip count, count by status, city", "paid_sales", dict(aggregation=["m:paid_sales.users_vip_count", "m:paid_sales.count"], breakout=["d:paid_sales.status", "d:paid_sales.users_city"]), "SELECT toString(o.status), u.city, uniqExactIf(u.id, u.is_vip), count() FROM %s GROUP BY 1, 2" % PD),
    ("paid_sales: net sales by city", "paid_sales", dict(aggregation=["m:paid_sales.net_sales"], breakout=["d:paid_sales.users_city"]), "SELECT u.city, sum(o.amount) FROM %s GROUP BY 1" % PD),
    ("paid_sales: net sales by city, filter on city", "paid_sales", dict(aggregation=["m:paid_sales.net_sales"], breakout=["d:paid_sales.users_city"], filter=["starts-with", "d:paid_sales.users_city", "Chi"]), "SELECT u.city, sum(o.amount) FROM %s AND u.city = 'Chicago' GROUP BY 1" % PD),
    ("paid_sales: distinct cities by vip", "paid_sales", dict(aggregation=[["distinct", "d:paid_sales.users_city"]], breakout=["d:paid_sales.users_is_vip"]), None),
    ("paid_sales: distinct status, net sales by city", "paid_sales", dict(aggregation=[["distinct", "d:paid_sales.status"], "m:paid_sales.net_sales"], breakout=["d:paid_sales.users_city"]), "SELECT u.city, uniqExact(o.status), sum(o.amount) FROM %s GROUP BY 1" % PD),
    ("sales: distinct cities, revenue by status", "sales", dict(aggregation=[["distinct", "d:sales.users_city"], "m:sales.revenue"], breakout=["d:sales.status"]), "SELECT toString(o.status), uniqExact(u.city), sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1"),
    ("sales: distinct status, revenue by city", "sales", dict(aggregation=[["distinct", "d:sales.status"], "m:sales.revenue"], breakout=["d:sales.users_city"]), "SELECT u.city, uniqExact(o.status), sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1"),
    ("sales: distinct cities alone", "sales", dict(aggregation=[["distinct", "d:sales.users_city"]]), "SELECT uniqExact(u.city) FROM orders o JOIN users u ON o.user_id = u.id"),
    ("sales: count where city, by status", "sales", dict(aggregation=[["count-where", ["=", "d:sales.users_city", "Austin"]]], breakout=["d:sales.status"]), "SELECT toString(o.status), countIf(u.city = 'Austin') FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1"),
    ("sales: share completed, by state", "sales", dict(aggregation=[["share", ["=", "d:sales.status", "completed"]]], breakout=["d:sales.users_state"]), "SELECT u.state, 1.0 * countIf(o.status = 'completed') / count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1"),
    ("sales: count where completed, by product", "sales", dict(aggregation=[["count-where", ["=", "d:sales.status", "completed"]]], breakout=["d:sales.products_category"]), "SELECT p.category, countIf(o.status = 'completed') FROM orders o JOIN products p ON o.product_id = p.id GROUP BY 1"),
    ("orders: count where completed, by city through a join", "orders", dict(aggregation=[["count-where", ["=", "d:orders.status", "completed"]]], breakout=["d:orders>users.city"]), "SELECT u.city, countIf(o.status = 'completed') FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1"),
    ("orders: count where vip, by status", "orders", dict(aggregation=[["count-where", ["=", "d:orders>users.is_vip", True]]], breakout=["d:orders.status"]), "SELECT toString(o.status), countIf(u.is_vip) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1"),
    ("orders: bin of id", "orders", dict(aggregation=[["count"]], breakout=[["field", "d:orders.id", {"binning": {"strategy": "bin-width", "bin-width": 1000}}]]), "SELECT floor(id / 1000) * 1000, count() FROM orders GROUP BY 1"),
    ("orders: bin of user_id", "orders", dict(aggregation=[["count"]], breakout=[["field", "d:orders.user_id", {"binning": {"strategy": "bin-width", "bin-width": 50}}]]), "SELECT floor(user_id / 50) * 50, count() FROM orders GROUP BY 1"),
    ("orders: bin of quantity", "orders", dict(aggregation=[["count"]], breakout=[["field", "d:orders.quantity_dim", {"binning": {"strategy": "bin-width", "bin-width": 2}}]]), "SELECT floor(quantity / 2) * 2, count() FROM orders GROUP BY 1"),
    ("orders: default bins of amount", "orders", dict(aggregation=[["count"]], breakout=[["field", "d:orders.amount_dim", {"binning": {"strategy": "default"}}]]), None),
    ("orders: ten bins of amount", "orders", dict(aggregation=[["count"]], breakout=[["field", "d:orders.amount_dim", {"binning": {"strategy": "num-bins", "num-bins": 10}}]]), None),
    ("orders: distinct of a time, Kathmandu", "orders", dict(aggregation=[["distinct", "d:orders.created_at"]]), "SELECT uniqExact(created_at) FROM orders"),
]


def walk(x):
    if isinstance(x, list) and len(x) == 3 and x[0] == "field" and isinstance(x[1], str):
        r = c.ref(x[1])
        return ["field", r[1], dict(r[2] or {}, **x[2])]
    if isinstance(x, list):
        return [walk(i) for i in x]
    return c.walk(x) if isinstance(x, (str, dict)) else x


for name, table, q, truth in SH:
    if "Kathmandu" in name:
        mb.api("PUT", "/api/setting/report-timezone", {"value": "Asia/Kathmandu"})
    body = {"source-table": c.tables[table]["id"]}
    for k, v in q.items():
        body[k] = walk(v)
    res = mb.api("POST", "/api/dataset", {"database": c.db, "type": "query", "query": body}, ok=(200, 202, 400, 403, 500))
    if res.get("error") or "data" not in res:
        print("REFUSED", name, "|", str(res.get("error"))[:210], flush=True)
    else:
        verdict = "no truth" if truth is None else (lib.compare(res["data"]["rows"], lib.ch(truth), tol=1e-6) or "equal")
        print("OK", name, "|", verdict, "|", json.dumps(res["data"]["rows"][:2], default=str)[:90], flush=True)
mb.api("PUT", "/api/setting/report-timezone", {"value": None})
