"""Details of the findings under a report time zone. It also adds a timestamptz copy to Postgres, because a time literal on a column with a zone there is the second opinion."""
import subprocess
import time
import urllib.parse
import json

import lib
import run_grid as R

mb = lib.Metabase().login()
CRT = "d:orders.created_at"
subprocess.run(["docker", "exec", "mcd-review-postgres-1", "psql", "-U", "shop", "-d", "shop", "-c",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS created_tz timestamptz; UPDATE orders SET created_tz = created_at AT TIME ZONE 'UTC'"], capture_output=True)
pg_id = [d["id"] for d in mb.api("GET", "/api/database")["data"] if d["name"] == "PG"][0]
mb.api("POST", "/api/database/%d/sync_schema" % pg_id, {})
time.sleep(10)
ctx = R.Ctx(mb, "Cube")
stock = R.Ctx(mb, "Cube stock")
pg = R.Ctx(mb, "PG")


def show(name, c, sh, truth=None):
    q = c.dq(c.inner(sh))
    r, _, e = mb.run(q)
    sql = mb.api("POST", "/api/dataset/native", q, ok=(200, 202, 400, 500))
    print(name, "| GOT", (r or [])[:4], "| ERR", (e or "")[:140])
    print("   SQL", " ".join(str(sql.get("query")).split())[:360])
    if truth:
        print("   TRUTH", lib.ch(truth)[:4])


T = "2025-06-15T11:00:00"
tn = "2025-06-15 11:00:00"
try:
    for tz in ("America/New_York", "Asia/Kathmandu"):
        mb.api("PUT", "/api/setting/report-timezone", {"value": tz})
        print("=====", tz)
        show("cube >= time", ctx, {"table": "orders", "agg": ["m:orders.count"], "where": [[">=", CRT, T]]}, "SELECT count() FROM orders WHERE created_at >= toDateTime('%s', '%s')" % (tn, tz))
        show("stock >= time", stock, {"table": "orders", "agg": ["m:orders.count"], "where": [[">=", CRT, T]]})
        col = pg.field("orders", "created_tz")["id"]
        for name, op in ((">=", ">="), ("<", "<")):
            q = pg.dq({"source-table": pg.tables["orders"]["id"], "aggregation": [["count"]], "filter": [op, ["field", col, None], T]})
            print("postgres timestamptz", name, mb.run(q)[0], " ".join(str(mb.api("POST", "/api/dataset/native", q).get("query")).split())[:200])
        print("truth utc form", lib.ch("SELECT countIf(created_at >= '%s'), countIf(created_at < '%s') FROM orders" % (tn, tn)), "truth in zone", lib.ch("SELECT countIf(created_at >= toDateTime('%s', '%s')), countIf(created_at < toDateTime('%s', '%s')) FROM orders" % (tn, tz, tn, tz)))
        show("cube time at Z", ctx, {"table": "orders", "agg": ["m:orders.count"], "where": [[">=", CRT, T + "Z"]]}, "SELECT count() FROM orders WHERE created_at >= '%s'" % tn)
        # Rolling windows in the zone.
        L = "toTimeZone(created_at, '%s')" % tz
        W = [[">=", CRT, "2025-03-01"], ["<", CRT, "2025-05-01"]]
        M = "toStartOfMonth(%s)" % L
        s = "toDateTime('2025-03-01 00:00:00', '%s')" % tz
        e = "toDateTime('2025-05-01 00:00:00', '%s')" % tz
        show("cube mtd by month", ctx, {"table": "orders", "agg": ["m:orders.revenue_mtd"], "by": [CRT + "@month"], "where": W}, "SELECT %s, sum(amount) FROM orders WHERE created_at >= %s AND created_at < %s GROUP BY 1 ORDER BY 1" % (M, s, e))
        show("stock mtd by month", stock, {"table": "orders", "agg": ["m:orders.revenue_mtd"], "by": [CRT + "@month"], "where": W})
        show("cube ytd by month", ctx, {"table": "orders", "agg": ["m:orders.revenue_ytd"], "by": [CRT + "@month"], "where": W},
             "SELECT 'march', sumIf(amount, %s >= '2025-01-01' AND created_at < toDateTime('2025-04-01 00:00:00', '%s')), 'april', sumIf(amount, %s >= '2025-01-01' AND created_at < %s) FROM orders" % (L, tz, L, e))
        show("cube trailing 30d by month", ctx, {"table": "orders", "agg": ["m:orders.revenue_trailing_30d"], "by": [CRT + "@month"], "where": W},
             "SELECT sumIf(amount, created_at >= toDateTime('2025-03-31 23:59:59', '%s') - INTERVAL 30 DAY AND created_at <= toDateTime('2025-03-31 23:59:59', '%s')) FROM orders" % (tz, tz))
        show("cube mtd by day", ctx, {"table": "orders", "agg": ["m:orders.revenue_mtd"], "by": [CRT + "@day"], "where": [[">=", CRT, "2025-03-01"], ["<", CRT, "2025-03-03"]]},
             "SELECT sumIf(amount, %s >= '2025-03-01' AND %s < '2025-03-02'), sumIf(amount, %s >= '2025-03-01' AND %s < '2025-03-03') FROM orders" % (L, L, L, L))
        # The same query on the REST API, with no driver.
        q = {"measures": ["orders.revenue_mtd"], "timezone": tz, "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month", "dateRange": ["2025-03-01", "2025-04-30"]}]}
        st, text, _ = lib.http("GET", "http://127.0.0.1:14400/cubejs-api/v1/load?query=" + urllib.parse.quote(json.dumps(q)))
        print("REST dialect mtd", [r.get("orders.revenue_mtd") for r in json.loads(text).get("data", [])], json.loads(text).get("error"))
        st, text, _ = lib.http("GET", "http://127.0.0.1:14403/cubejs-api/v1/load?query=" + urllib.parse.quote(json.dumps(q)))
        print("REST stock mtd", [r.get("orders.revenue_mtd") for r in json.loads(text).get("data", [])], json.loads(text).get("error"))
        st, text, _ = lib.http("GET", "http://127.0.0.1:14400/cubejs-api/v1/sql?query=" + urllib.parse.quote(json.dumps(q)))
        print("REST sql", " ".join(json.loads(text)["sql"]["sql"][0].split())[:1100])
        # Hour of day over the change of the clock.
        show("cube hour of day", ctx, {"table": "orders", "agg": ["m:orders.count"], "by": [CRT + "@hour-of-day"], "where": [["between", CRT, "2025-03-09", "2025-03-09"]]},
             "SELECT toHour(%s), count() FROM orders WHERE toDate(%s) = '2025-03-09' GROUP BY 1 ORDER BY 1" % (L, L))
        show("cube date by month", ctx, {"table": "typed_events", "agg": ["m:typed_events.count"], "by": ["d:typed_events.day@month"]}, "SELECT toStartOfMonth(day), count() FROM typed_events GROUP BY 1")
finally:
    mb.api("PUT", "/api/setting/report-timezone", {"value": None})
