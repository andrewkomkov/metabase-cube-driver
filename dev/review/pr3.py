"""PR 3: the driver on ClickHouse 26.10.3, compared with the same shapes on the pin 26.8.36. It needs the compose profile pr3."""
import time

import lib
import run_grid as R

PIN, NEW = "http://127.0.0.1:18123", "http://127.0.0.1:18124"
mb = lib.Metabase().login()
have = {d["name"]: d["id"] for d in mb.api("GET", "/api/database")["data"]}
if "Cube PR3" not in have:
    d = mb.api("POST", "/api/database", {"engine": "cube", "name": "Cube PR3", "details": {
        "host": "cube-pr3", "port": 15432, "dbname": "cube", "user": "cube", "password": "cube-dev-only",
        "cube-rest-url": "http://cube-pr3:4000", "cube-api-secret": "cube-dev-only-secret-0123456789abcdef"}})
    have["Cube PR3"] = d["id"]
    while mb.api("GET", "/api/database/%d" % d["id"]).get("initial_sync_status") != "complete":
        time.sleep(3)
CRT = "d:orders.created_at"
CUM = "m:orders.cumulative_count"
SHAPES = [
    ("one bucket", [[">=", CRT, "2025-04-01"], ["<", CRT, "2025-05-01"]], "month", "1", "created_at >= '2025-04-01' AND created_at < '2025-05-01'"),
    ("two buckets", [[">=", CRT, "2025-04-01"], ["<", CRT, "2025-06-01"]], "month", "1", "created_at >= '2025-04-01' AND created_at < '2025-06-01'"),
    ("six buckets", [[">=", CRT, "2025-01-01"], ["<", CRT, "2025-07-01"]], "month", "1", "created_at >= '2025-01-01' AND created_at < '2025-07-01'"),
    ("days", [[">=", CRT, "2025-03-01"], ["<", CRT, "2025-03-05"]], "day", "1", "created_at >= '2025-03-01' AND created_at < '2025-03-05'"),
    ("two buckets and a status", [[">=", CRT, "2025-04-01"], ["<", CRT, "2025-06-01"], ["=", "d:orders.status", "shipped"]], "month", "status = 'shipped'", "created_at >= '2025-04-01' AND created_at < '2025-06-01'"),
    ("buckets with no rows", [[">=", CRT, "2025-12-01"], ["<", CRT, "2026-03-01"]], "month", "1", "created_at >= '2025-12-01' AND created_at < '2026-03-01'"),
]
TRUNC = {"month": "toStartOfMonth", "day": "toDate"}
for dbname, url in (("Cube", PIN), ("Cube PR3", NEW)):
    ctx = R.Ctx(mb, dbname)
    print("==", dbname, lib.ch("SELECT version()", url))
    for name, where, unit, cond, rng in SHAPES:
        rows, _, err = mb.run(ctx.dq(ctx.inner({"table": "orders", "agg": [CUM], "by": [CRT + "@" + unit], "where": where})))
        # The end of a bucket is the start of the next one, and each bucket has its own query.
        buckets = lib.ch("SELECT DISTINCT %s(created_at) b FROM orders WHERE %s ORDER BY b" % (TRUNC[unit], rng), url)
        step = {"month": "1 MONTH", "day": "1 DAY"}[unit]
        want = [[b[0], lib.ch("SELECT count() FROM orders WHERE created_at < toDateTime('%s') + INTERVAL %s AND %s" % (b[0], step, cond), url)[0][0]] for b in buckets]
        if err:
            print(name, "| ERROR:", err[:110])
        else:
            print(name, "|", "equal" if lib.compare(rows, want) is None else "DIFFERENT got %s truth %s" % (rows, want))
