#!/usr/bin/env python3
"""Reproduces the findings in 0.1.0.md. Run it on nettop, on the service metabase-conformance:

    cd ~/metabase-cube-driver/dev
    docker compose -f docker-compose.yml -f docker-compose.conformance.yml up -d metabase-conformance
    cd ..
    python3 docs/review/repro_0_1_0.py            # all findings
    python3 docs/review/repro_0_1_0.py B1 M3      # some findings

The script reads conformance/run.py and does not change it. It adds cards to that Metabase, and it changes the setting
start-of-week and sets it back. Each line shows the output of Metabase. A line with `truth` shows direct SQL on ClickHouse.
"""
import json
import os
import sys

sys.dont_write_bytecode = True
os.environ.setdefault("METABASE_URL", "http://127.0.0.1:3301")
os.environ.setdefault("METABASE_ADMIN_PASSWORD", "conformance-dev-only-1")
sys.path.insert(0, os.environ.get("MCD_CONFORMANCE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "conformance")))
import run as R  # noqa: E402

mb = R.Metabase(os.environ["METABASE_ADMIN_PASSWORD"])
mb.login()
mb.discover()
DB = mb.need_db()
O = "orders"
COUNT = [["count"]]
FINDINGS = {}


def finding(name):
    def wrap(f):
        FINDINGS[name] = f
        return f
    return wrap


def Q(table, **kw):
    return {"database": DB, "type": "query", "query": mb.inner(table, **kw)}


def SQL(sql, tags=None, params=None):
    q = {"database": DB, "type": "native", "native": {"query": sql, "template-tags": tags or {}}}
    if params:
        q["parameters"] = params
    return q


def over(card_id, **kw):
    return {"database": DB, "type": "query", "query": dict({"source-table": "card__%d" % card_id}, **kw)}


def named(name, base_type="type/Decimal", **opts):
    return ["field", name, dict({"base-type": base_type}, **opts)]


def card(name, query, kind="question"):
    return mb.api("POST", "/api/card", {"name": "repro " + name, "dataset_query": query, "display": "table",
                                        "visualization_settings": {}, "type": kind})["id"]


def show(label, query, limit=8):
    res = mb.api("POST", "/api/dataset", query, ok=(200, 202, 400, 403, 500))
    if res.get("error"):
        print("  %-26s ERROR %s" % (label, str(res["error"])[:170].replace("\n", " ")))
    else:
        rows = res["data"]["rows"]
        print("  %-26s rows=%d %s" % (label, len(rows), json.dumps(rows[:limit])))


def truth(sql):
    print("  %-26s %s" % ("truth", json.dumps(R.ch(sql))[:300]))


def mcp_call(m, label, tool, **args):
    res = m.rpc("tools/call", {"name": tool, "arguments": args})
    text = (res.get("content") or [{}])[0].get("text", "")
    if res.get("isError"):
        print("  %-26s ERROR %s" % (label, text[:170].replace("\n", " ")))
    else:
        print("  %-26s rows=%s" % (label, json.dumps(json.JSONDecoder().raw_decode(text)[0].get("rows"))[:200]))


def stage(**kw):
    return {"lib/type": "mbql/query", "database": DB,
            "stages": [dict({"lib/type": "mbql.stage/mbql", "source-table": mb.table(O)["id"]}, **kw)]}


def measure_id(name):
    return mb.entity(mb.measures, O, name)["id"]


def created(**opts):
    return ["field", opts, mb.field(O, "created_at")["id"]]


def raw_model():
    return card("raw model", Q(O, fields=["d:orders.id", "d:orders.status", "d:orders.amount_dim", "d:orders.created_at",
                                          "d:orders.revenue", "d:orders.revenue_trailing_30d"]), "model")


GT = [[">", "d:orders.created_at", "2025-03-15T05:00:00"], ["<=", "d:orders.created_at", "2025-03-16T05:00:00"]]
GT_TRUTH = " FROM orders WHERE created_at>'2025-03-15 05:00:00' AND created_at<='2025-03-16 05:00:00'"


@finding("B1")
def strict_lower_bound():
    show("MBQL count", Q(O, agg=["m:orders.count"], where=GT))
    show("MBQL revenue", Q(O, agg=["m:orders.revenue"], where=GT))
    show("native count", SQL("SELECT MEASURE(count) FROM orders WHERE created_at > '2025-03-15 05:00:00' AND created_at <= '2025-03-16 05:00:00'"))
    truth("SELECT count(),sum(amount)" + GT_TRUTH)
    m = R.Mcp(mb)
    mcp_call(m, "MCP revenue", "execute_query", query=stage(
        aggregation=[["measure", {}, measure_id("revenue")]],
        filters=[[">", {}, created(), "2025-03-15T05:00:00Z"], ["<=", {}, created(), "2025-03-16T05:00:00Z"]]))


@finding("B2")
def outer_date_filter():
    by_month = card("revenue by month", Q(O, agg=["m:orders.revenue"], by=["d:orders.created_at@month"]))
    show("rows, month >= 05-15", over(by_month, filter=[">=", named("created_at", "type/DateTime"), "2025-05-15"]), 2)
    show("sum, month >= 05-15", over(by_month, aggregation=[["sum", named("sum")]],
                                     filter=[">=", named("created_at", "type/DateTime"), "2025-05-15"]))
    truth("SELECT count(),sum(r) FROM (SELECT toStartOfMonth(created_at) m,sum(amount) r FROM orders GROUP BY 1) WHERE m>='2025-05-15'")
    rolling = card("rolling by month", Q(O, agg=["m:orders.revenue_trailing_30d"], by=["d:orders.created_at@month"],
                                         where=[[">=", "d:orders.created_at", "2025-04-01"], ["<", "d:orders.created_at", "2025-07-01"]]))
    show("rolling, month >= 05-15", over(rolling, filter=[">=", named("created_at", "type/DateTime"), "2025-05-15"]))
    truth("SELECT toDateTime('2025-06-01','UTC'),sumIf(amount,created_at>=addDays(toDateTime('2025-06-30 23:59:59','UTC'),-30) AND created_at<=toDateTime('2025-06-30 23:59:59','UTC')) FROM orders")


@finding("B3")
def rolling_through_model():
    model = raw_model()
    roll = named("revenue_trailing_30d")
    show("sum, no bucket", over(model, aggregation=[["sum", roll]]))
    show("sum by status", over(model, aggregation=[["sum", roll]], breakout=[named("status", "type/Text")]))
    show("by month, mid-month filter", over(model, aggregation=[["sum", roll]],
                                            breakout=[named("created_at", "type/DateTime", **{"temporal-unit": "month"})],
                                            filter=["and", [">=", named("created_at", "type/DateTime"), "2025-04-15"],
                                                    ["<", named("created_at", "type/DateTime"), "2025-06-01"]]))
    show("same query, direct", Q(O, agg=["m:orders.revenue_trailing_30d"]))
    truth("SELECT sum(amount) FROM orders")
    m = R.Mcp(mb)
    mcp_call(m, "MCP source-card", "execute_query", query={
        "lib/type": "mbql/query", "database": DB,
        "stages": [{"lib/type": "mbql.stage/mbql", "source-card": model,
                    "aggregation": [["sum", {}, ["field", {"base-type": "type/Decimal"}, "revenue_trailing_30d"]]]}]})


@finding("B4")
def measure_column_of_model():
    model = raw_model()
    above = [">", named("revenue"), 150]
    show("sum, revenue > 150", over(model, aggregation=[["sum", named("amount_dim")]], filter=above))
    show("count, revenue > 150", over(model, aggregation=COUNT, filter=above))
    truth("SELECT sum(amount),count() FROM orders WHERE amount>150")
    show("breakout on revenue", over(model, aggregation=COUNT, breakout=[named("revenue")]))
    truth("SELECT count(DISTINCT amount) FROM orders")
    show("stage one, hidden column", Q(O, agg=["m:orders.count"], by=["d:orders.status"], where=[[">", "d:orders.revenue", 100000]]))
    truth("SELECT status,count() FROM orders WHERE amount>100000 GROUP BY 1")


@finding("B5")
def view_with_two_paths():
    show("both regions", Q("region_sales", agg=["m:region_sales.count"],
                           by=["d:region_sales.customer_region", "d:region_sales.destination_region"]), 4)
    truth("SELECT count() FROM (SELECT u.state, ['TX','IL','WA','MA'][1+o.id%4] d FROM orders o JOIN users u ON o.user_id=u.id GROUP BY 1,2)")


@finding("B6")
def rolling_in_native_sql():
    show("no bucket", SQL("SELECT MEASURE(revenue_trailing_30d) FROM orders"))
    truth("SELECT sum(amount) FROM orders")
    show("filter inside a bucket", SQL("SELECT DATE_TRUNC('month',created_at),MEASURE(revenue_trailing_30d) FROM orders WHERE created_at>='2025-04-15' AND created_at<'2025-06-01' GROUP BY 1"))
    show("whole months", SQL("SELECT DATE_TRUNC('month',created_at),MEASURE(revenue_trailing_30d) FROM orders WHERE created_at>='2025-04-01' AND created_at<'2025-06-01' GROUP BY 1"))
    mcp_call(R.Mcp(mb), "MCP execute_sql", "execute_sql", database_id=DB, sql="SELECT MEASURE(revenue_trailing_30d) FROM orders")


@finding("B7")
def native_between_date_text():
    show("BETWEEN date text", SQL("SELECT MEASURE(count) FROM orders WHERE created_at BETWEEN '2025-03-11' AND '2025-04-13'"))
    show("BETWEEN timestamp", SQL("SELECT MEASURE(count) FROM orders WHERE created_at BETWEEN timestamp '2025-03-11 00:00:00' AND timestamp '2025-04-13 00:00:00'"))
    truth("SELECT count() FROM orders WHERE created_at BETWEEN '2025-03-11' AND '2025-04-13'")


@finding("M1")
def model_with_date_filter():
    model = raw_model()
    show("sum, date >=", over(model, aggregation=[["sum", named("amount_dim")]], filter=[">=", named("created_at", "type/DateTime"), "2025-05-10"]))
    show("count, between", over(model, aggregation=COUNT, filter=["between", named("created_at", "type/DateTime"), "2025-05-10", "2025-05-20"]))
    show("rolling + 1, whole month", Q(O, agg=[["+", "m:orders.revenue_trailing_30d", 1]], by=["d:orders.created_at@month"],
                                       where=[[">=", "d:orders.created_at", "2025-04-01"], ["<", "d:orders.created_at", "2025-05-01"]]))
    truth("SELECT sum(amount) FROM orders WHERE created_at>='2025-05-10'")


@finding("M2")
def week_on_cube_with_no_granularity():
    mb.api("PUT", "/api/setting/start-of-week", {"value": None})
    show("default, users, date", Q("users", agg=["m:users.count"], by=["d:users.created_at@week"], where=[[">=", "d:users.created_at", "2024-03-01"]]), 2)
    show("default, level_one, date", Q("level_one", agg=["m:level_one.revenue"], by=["d:level_one.created_at@week"],
                                       where=[["between", "d:level_one.created_at", "2025-03-05", "2025-04-17"]]), 2)
    show("default, users, no date", Q("users", agg=["m:users.count"], by=["d:users.created_at@week"]), 2)
    mb.api("PUT", "/api/setting/start-of-week", {"value": "wednesday"})
    show("wednesday, orders, date", Q(O, agg=["m:orders.count"], by=["d:orders.created_at@week"], where=[[">=", "d:orders.created_at", "2025-12-01"]]), 2)
    mb.api("PUT", "/api/setting/start-of-week", {"value": None})


@finding("M3")
def mcp_count_with_date_filter():
    m = R.Mcp(mb)
    count = [["measure", {}, measure_id("count")]]
    bounds = [[">=", {}, created(), "2025-05-10"], ["<", {}, created(), "2025-05-21"]]
    mcp_call(m, "MCP count measure", "execute_query", query=stage(aggregation=count, filters=bounds))
    mcp_call(m, "MCP count", "execute_query", query=stage(aggregation=[["count", {}]], filters=bounds))
    mcp_call(m, "MCP revenue and count", "execute_query", query=stage(aggregation=[["measure", {}, measure_id("revenue")]] + count, filters=bounds))
    mcp_call(m, "MCP revenue only", "execute_query", query=stage(aggregation=[["measure", {}, measure_id("revenue")]], filters=bounds))
    show("REST count measure", Q(O, agg=["m:orders.count"], where=[[">=", "d:orders.created_at", "2025-05-10"], ["<", "d:orders.created_at", "2025-05-21"]]))
    truth("SELECT count() FROM orders WHERE created_at>='2025-05-10' AND created_at<'2025-05-21'")


@finding("M4")
def previous_weeks_filter():
    mb.api("PUT", "/api/setting/start-of-week", {"value": None})
    show("default, previous 90 weeks", Q(O, agg=["m:orders.count"], where=[["time-interval", "d:orders.created_at", -90, "week"]]))
    mb.api("PUT", "/api/setting/start-of-week", {"value": "monday"})
    show("monday, previous 90 weeks", Q(O, agg=["m:orders.count"], where=[["time-interval", "d:orders.created_at", -90, "week"]]))
    mb.api("PUT", "/api/setting/start-of-week", {"value": None})


@finding("m1")
def segment_errors():
    show("OR of two segments", Q(O, agg=COUNT, where=[["or", "s:orders.paid", "s:orders.large"]]))
    show("segment of a joined cube", Q(O, agg=["m:orders.revenue"], where=["s:users.vip"]))


@finding("m2")
def fan_out():
    join = [{"source-table": mb.table("payments")["id"], "alias": "P", "strategy": "left-join",
             "condition": ["=", "d:orders.id", "d:payments.order_id|P"]}]
    show("COUNT(*)", Q(O, agg=COUNT, joins=join))
    show("SUM(quantity)", Q(O, agg=[["sum", "d:orders.quantity_dim"]], joins=join))
    truth("SELECT count(),sum(o.quantity) FROM orders o JOIN (SELECT id AS order_id FROM orders UNION ALL SELECT id FROM orders) p ON o.id=p.order_id")


@finding("m3")
def native_count_no_rows():
    show("native COUNT(*)", SQL("SELECT COUNT(*) FROM orders WHERE status='nope'"))
    show("native MEASURE(count)", SQL("SELECT MEASURE(count) FROM orders WHERE status='nope'"))
    show("MBQL count", Q(O, agg=COUNT, where=[["=", "d:orders.status", "nope"]]))
    truth("SELECT count() FROM orders WHERE status='nope'")


@finding("m4")
def raw_errors_of_rolling():
    show("unbounded, two months", Q(O, agg=["m:orders.cumulative_count"], by=["d:orders.created_at@month"],
                                    where=[[">=", "d:orders.created_at", "2025-04-01"], ["<", "d:orders.created_at", "2025-06-01"]]))
    show("unbounded, one month", Q(O, agg=["m:orders.cumulative_count"], by=["d:orders.created_at@month"],
                                   where=[[">=", "d:orders.created_at", "2025-04-01"], ["<", "d:orders.created_at", "2025-05-01"]]))
    truth("SELECT countIf(created_at<='2025-04-30 23:59:59') FROM orders")
    show("max of the column", Q(O, agg=[["max", "d:orders.revenue_trailing_30d"]]))


@finding("m5")
def lambda_cube():
    show("lambda_orders revenue", Q("lambda_orders", agg=["m:lambda_orders.revenue"]))


@finding("m6")
def hidden_members_in_dev_mode():
    show("hidden cube", SQL("SELECT COUNT(*) FROM secret_audit"))
    show("hidden measure", SQL("SELECT MEASURE(public_false_measure) FROM orders"))


@finding("m7")
def validate_echo():
    status, text, _ = R.http("POST", R.MB + "/api/database/validate", {"details": {"engine": "cube", "details": R.CUBE_DB_DETAILS}},
                             {"X-Metabase-Session": mb.sid})
    secret = R.CUBE_DB_DETAILS["cube-api-secret-value"]
    print("  HTTP %s, secret in the response: %s" % (status, secret in text))
    print("  " + text.replace(secret, "<API_SECRET>").replace(R.CUBE_DB_DETAILS["password"], "<SQL_PASSWORD>"))


@finding("m8")
def model_raw_rows():
    show("measure column, one row", over(raw_model(), fields=[named("id"), named("revenue")], filter=["=", named("id"), 7]))
    show("direct raw rows", Q(O, fields=["d:orders.id", "d:orders.revenue"], where=[["=", "d:orders.id", 7]]))


for name in sys.argv[1:] or FINDINGS:
    print(name)
    FINDINGS[name]()
