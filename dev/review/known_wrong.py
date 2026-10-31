"""Each entry of the README section "Known wrong numbers" on both variants of Cube. Is it true, and what does the driver do?"""
import base64
import hashlib
import hmac
import json
import time
import urllib.parse

import lib
import run_grid as R

mb = lib.Metabase().login()
for d in mb.api("GET", "/api/database")["data"]:
    if d["name"] in ("Cube", "Cube stock"):
        mb.api("POST", "/api/database/%d/sync_schema" % d["id"], {})
time.sleep(25)
CTX = {d: R.Ctx(mb, d) for d in ("Cube", "Cube stock")}


def native(ctx, sql):
    r, _, e = mb.run({"database": ctx.db, "type": "native", "native": {"query": sql}})
    return r if not e else "ERROR: " + e[:170]


def query(ctx, sh):
    r, _, e = mb.run(ctx.dq(ctx.inner(sh)))
    return r if not e else "ERROR: " + e[:170]


TY = "typed_events"
B = "ts64_berlin"
A1, A2 = "2025-03-01 02:00:00", "2025-03-01 03:00:00"
for name, ctx in CTX.items():
    print("=====", name)
    # 1. A long decimal.
    print("1 raw long decimal", query(ctx, {"table": TY, "fields": ["d:typed_events.id", "d:typed_events.dec_big"], "where": [["=", "d:typed_events.id", 1]]}), "truth", lib.ch("SELECT toString(dec_big) FROM typed_events WHERE id = 1"))
    print("1 native long decimal", native(ctx, "SELECT dec_big FROM typed_events WHERE id = 1"))
    print("1 text form", query(ctx, {"table": TY, "fields": ["d:typed_events.dec_big_text"], "where": [["=", "d:typed_events.id", 1]]}))
    print("1 sum of a long decimal", query(ctx, {"table": TY, "agg": ["m:typed_events.sum_big"], "where": [["<=", "d:typed_events.id", 3]]}), "truth", lib.ch("SELECT toString(sum(dec_big)) FROM typed_events WHERE id <= 3"))
    # 2. COUNT(*) on no rows.
    print("2 orders", native(ctx, "SELECT COUNT(*) FROM orders WHERE status = 'nope'"))
    print("2 no rollup", native(ctx, "SELECT COUNT(*) FROM typed_events WHERE lc = 'nope'"))
    print("2 measure", native(ctx, "SELECT MEASURE(count) FROM typed_events WHERE lc = 'nope'"))
    print("2 query builder", query(ctx, {"table": TY, "agg": ["m:typed_events.count"], "where": [["=", "d:typed_events.lc", "nope"]]}))
    # 3. A switch dimension.
    print("3 native raw", native(ctx, "SELECT id, cur FROM review_ts64 WHERE id = 1"))
    s = native(ctx, "SELECT * FROM review_ts64 WHERE id = 1")
    print("3 native star", s if isinstance(s, str) else (len(s), s[:2]))
    print("3 count over a subquery", native(ctx, "SELECT COUNT(*) FROM (SELECT id, cur FROM review_ts64 WHERE id <= 10) t"), "truth 10")
    print("3 sum of a dimension over the rows", native(ctx, "SELECT SUM(id) FROM (SELECT id, cur FROM review_ts64 WHERE id <= 10) t"), "truth 55")
    print("3 query builder", query(ctx, {"table": "review_ts64", "fields": ["d:review_ts64.id", "d:review_ts64.cur"], "where": [["=", "d:review_ts64.id", 1]]}))
    print("3 orders star", str(native(ctx, "SELECT * FROM orders WHERE id = 1 LIMIT 5"))[:150])
    # Stock entries. Run on both variants, so that the dialect shows the truth.
    T = "SELECT countIf(ts64_berlin >= toDateTime64('%s', 3, 'UTC') AND ts64_berlin < toDateTime64('%s', 3, 'UTC')) FROM typed_events" % (A1, A2)
    print("5 truth half open", lib.ch(T), "rows of the second before b", lib.ch("SELECT count() FROM typed_events WHERE ts64_berlin >= toDateTime64('%s', 3, 'UTC') - 1 AND ts64_berlin < toDateTime64('%s', 3, 'UTC')" % (A2, A2)))
    # A wide range, so that the last second holds a row with milliseconds.
    edge = lib.ch("SELECT toString(toTimeZone(ts64_berlin, 'UTC')), id FROM typed_events WHERE toUnixTimestamp64Milli(ts64_berlin) % 1000 != 0 ORDER BY id LIMIT 1 OFFSET 30")[0]
    t = edge[0]
    whole = t[:19]
    nxt = lib.ch("SELECT toString(addSeconds(toDateTime('%s', 'UTC'), 1))" % whole)[0][0]
    tr = lib.ch("SELECT count() FROM typed_events WHERE ts64_berlin >= toDateTime64('2025-03-01 00:00:00', 3, 'UTC') AND ts64_berlin < toDateTime64('%s', 3, 'UTC')" % nxt)
    print("5 row at", t, "upper bound", nxt)
    print("5 native x>=a AND x<b", native(ctx, "SELECT MEASURE(count) FROM review_ts64 WHERE ts64 >= TIMESTAMP '2025-03-01 00:00:00' AND ts64 < TIMESTAMP '%s'" % nxt), "truth", tr)
    print("5 builder, no rollup", query(ctx, {"table": "review_ts64", "agg": ["m:review_ts64.count"], "where": [[">=", "d:review_ts64.ts64", "2025-03-01T00:00:00"], ["<", "d:review_ts64.ts64", nxt.replace(" ", "T")]]}), "truth", tr)
    print("5 builder, rollup", query(ctx, {"table": "review_ts64_rollup", "agg": ["m:review_ts64_rollup.count"], "where": [[">=", "d:review_ts64_rollup.ts64", "2025-03-01T00:00:00"], ["<", "d:review_ts64_rollup.ts64", nxt.replace(" ", "T")]]}), "truth", tr)
    print("5 builder, rollup, by day", query(ctx, {"table": "review_ts64_rollup", "agg": ["m:review_ts64_rollup.count"], "by": ["d:review_ts64_rollup.ts64@month"], "where": [[">=", "d:review_ts64_rollup.ts64", "2025-03-01T00:00:00"], ["<", "d:review_ts64_rollup.ts64", nxt.replace(" ", "T")]]}), "truth", tr)
    # 6. NOT (x > t), and a shift.
    tn = lib.ch("SELECT countIf(NOT ts64_berlin > toDateTime64('%s', 3, 'UTC')) FROM typed_events" % whole)
    print("6 builder NOT x>t", query(ctx, {"table": "review_ts64", "agg": ["m:review_ts64.count"], "where": [["not", [">", "d:review_ts64.ts64", whole.replace(" ", "T")]]]}), "truth", tn)
    print("6 native NOT x>t", native(ctx, "SELECT MEASURE(count) FROM review_ts64 WHERE NOT (ts64 > TIMESTAMP '%s')" % whole), "truth", tn)
    # A shift, with x > t. The rows of the first second after t, one day before the shifted range.
    shift_truth = lib.ch("SELECT toStartOfDay(addDays(toTimeZone(ts64_berlin, 'UTC'), 1)) d, count() FROM typed_events WHERE addDays(toTimeZone(ts64_berlin, 'UTC'), 1) > toDateTime64('2025-03-05 04:00:00', 3, 'UTC') AND addDays(toTimeZone(ts64_berlin, 'UTC'), 1) <= toDateTime64('2025-03-05 23:59:59', 3, 'UTC') GROUP BY d")
    print("6 shift x>t", query(ctx, {"table": "review_ts64", "agg": ["m:review_ts64.count_prior_day"], "by": ["d:review_ts64.ts64@day"], "where": [[">", "d:review_ts64.ts64", "2025-03-05T04:00:00"], ["<=", "d:review_ts64.ts64", "2025-03-05T23:59:59"]]}), "truth", shift_truth)
    # 7. A raw DateTime64.
    print("7 raw", query(ctx, {"table": TY, "fields": ["d:typed_events.id", "d:typed_events.ts64_berlin"], "where": [["=", "d:typed_events.id", 7]]}), "truth", lib.ch("SELECT toString(toTimeZone(ts64_berlin, 'UTC')) FROM typed_events WHERE id = 7"))

# 8. The legacy planner. REST on the service cube-legacy, with no driver.
now = int(time.time())


def b64(x):
    return base64.urlsafe_b64encode(x).rstrip(b"=").decode()


head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
body = b64(json.dumps({"iat": now, "exp": now + 900}).encode())
sig = b64(hmac.new(b"cube-legacy-dev-only-secret-0123456789", (head + "." + body).encode(), hashlib.sha256).digest())
q = urllib.parse.quote(json.dumps({"measures": ["orders.revenue_trailing_30d"], "order": {"orders.created_at": "asc"},
                                   "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month", "dateRange": ["2025-02-01", "2025-03-31"]}]}))
for _ in range(40):
    st, text, _h = lib.http("GET", "http://127.0.0.1:14402/cubejs-api/v1/load?query=" + q, headers={"Authorization": head + "." + body + "." + sig})
    data = json.loads(text) if text.startswith("{") else {}
    if st == 200 and data.get("error") != "Continue wait":
        break
    time.sleep(3)
print("8 legacy", st, [(r.get("orders.created_at.month"), r.get("orders.revenue_trailing_30d")) for r in data.get("data", [])], data.get("error"))
print("8 truth", lib.ch("SELECT m, sumIf(o.amount, o.created_at >= addDays(e, -30) AND o.created_at <= e) FROM orders o CROSS JOIN (SELECT toDateTime('2025-02-01') m, toDateTime('2025-02-28 23:59:59') e UNION ALL SELECT toDateTime('2025-03-01'), toDateTime('2025-03-31 23:59:59')) AS w GROUP BY m ORDER BY m"))
st, text, _h = lib.http("GET", "http://127.0.0.1:14402/cubejs-api/v1/sql?query=" + q, headers={"Authorization": head + "." + body + "." + sig})
print("8 sql", json.loads(text)["sql"]["sql"][0][:900] if text.startswith("{") and "sql" in json.loads(text) else text[:300])
