"""The four truths that recent commits changed, read from Metabase on Postgres, next to the same query on Cube. Needs the tables that pg_truths.sql makes."""
import json
import time

import lib
import run_grid

NY = "America/New_York"
mb = lib.Metabase().login()
pg_id = [d for d in mb.api("GET", "/api/database")["data"] if d["name"] == "PG"][0]["id"]
mb.api("POST", "/api/database/%d/sync_schema" % pg_id)
for _ in range(40):
    meta = mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % pg_id)
    names = {t["name"] for t in meta["tables"]}
    if {"people", "tz_events", "regions_t"} <= names:
        break
    time.sleep(3)
time.sleep(15)
meta = mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % pg_id)
T = {t["name"]: t for t in meta["tables"]}
F = lambda t, n: [f for f in T[t]["fields"] if f["name"] == n][0]  # noqa: E731
ref = lambda t, n, **o: ["field", F(t, n)["id"], o or None]  # noqa: E731


def run(db, q):
    res = mb.api("POST", "/api/dataset", {"database": db, "type": "query", "query": q}, ok=(200, 202, 400, 403, 500))
    if res.get("error") or "data" not in res:
        return "ERR " + str(res.get("error"))[:230]
    return res["data"]["rows"]


print("== meta.semantic_types: a text column with a foreign key on Postgres")
f = F("people", "state")
print("PG people.state semantic", f["semantic_type"], "target", f["fk_target_field_id"], "| plain text column of the same name, users.state:", F("users", "state")["semantic_type"])
for name in ("email", "name", "city"):
    print("PG people.%s %s" % (name, F("people", name)["semantic_type"]))
cube = run_grid.Ctx(mb, "Cube")
for tbl, name in (("users", "state"), ("users", "email"), ("users", "name"), ("users", "city"), ("join_customers", "state"), ("metric_customers", "state")):
    x = cube.field(tbl, name)
    print("Cube %s.%s %s target %s has_field_values %s" % (tbl, name, x["semantic_type"], x.get("fk_target_field_id"), x.get("has_field_values")))
# What a person sees: the group by state and the filter on a foreign key column stay the same.
print("Cube by users.state", run(cube.db, {"source-table": cube.tables["users"]["id"], "breakout": [["field", cube.field("users", "state")["id"], None]], "aggregation": [["count"]]}))
print("truth", lib.ch("SELECT state, count() FROM users GROUP BY 1 ORDER BY 1"))

print("== cube.sql_sync: a hidden key on Postgres")
mb.api("PUT", "/api/field/%d" % F("regions_t", "state")["id"], {"visibility_type": "hidden"})
qm = mb.api("GET", "/api/table/%d/query_metadata" % T["regions_t"]["id"])
print("PG hidden field in query_metadata:", [x["name"] for x in qm["fields"]])
res = mb.api("POST", "/api/dataset", {"database": pg_id, "type": "query", "query": {"source-table": T["regions_t"]["id"], "limit": 1}})
print("PG raw columns with a hidden field:", [c["name"] for c in res["data"]["cols"]], res["data"]["rows"])
res = run(pg_id, {"source-table": T["regions_t"]["id"], "breakout": [ref("regions_t", "state")], "aggregation": [["count"]], "limit": 2})
print("PG breakout on a hidden field:", res)
res = run(pg_id, {"source-table": T["regions_t"]["id"], "aggregation": [["count"]], "filter": ["=", ref("regions_t", "state"), "TX"]})
print("PG filter on a hidden field:", res)
qm = mb.api("GET", "/api/table/%d/query_metadata" % cube.tables["payments"]["id"])
print("Cube payments in query_metadata:", [x["name"] for x in qm["fields"]])
res = mb.api("POST", "/api/dataset", {"database": cube.db, "type": "query", "query": {"source-table": cube.tables["payments"]["id"], "limit": 1}})
print("Cube payments raw columns:", [c["name"] for c in res["data"]["cols"]], res["data"]["rows"])
print("Cube payments by method:", run(cube.db, {"source-table": cube.tables["payments"]["id"], "breakout": [["field", cube.field("payments", "method")["id"], None]], "aggregation": [["measure", cube.measures[("payments", "count")]]]}))
print("truth:", lib.ch("SELECT method, count() FROM (SELECT 'card' AS method FROM orders UNION ALL SELECT 'fee' FROM orders) GROUP BY method"))

print("== zone.*.time_measure: max of a time under a report zone")
stock = run_grid.Ctx(mb, "Cube stock")
for zone in (None, NY, "Asia/Kolkata"):
    mb.api("PUT", "/api/setting/report-timezone", {"value": zone})
    print("-- zone", zone)
    print("PG max(timestamptz)", run(pg_id, {"source-table": T["tz_events"]["id"], "aggregation": [["max", ref("tz_events", "ts")]]}))
    print("PG max(timestamp)", run(pg_id, {"source-table": T["tz_events"]["id"], "aggregation": [["max", ref("tz_events", "ts_plain")]]}))
    print("PG max(timestamptz(3))", run(pg_id, {"source-table": T["tz_events"]["id"], "aggregation": [["max", ref("tz_events", "ts3")]]}))
    print("PG day, max", run(pg_id, {"source-table": T["tz_events"]["id"], "breakout": [ref("tz_events", "ts", **{"temporal-unit": "day"})], "aggregation": [["max", ref("tz_events", "ts")]], "order-by": [["asc", ref("tz_events", "ts", **{"temporal-unit": "day"})]], "limit": 2}))
    for c, label in ((cube, "dialect"), (stock, "stock")):
        for tbl, m_ in (("ny_a", "m_last_at"), ("ny_a", "m_last_local"), ("ny_b", "m_last_at"), ("ny_d", "m_last_at"), ("ny_a64", "m_last_at"), ("orders", "last_order_at"), ("metric_types", "m_last_order_at")):
            print(label, tbl, m_, run(c.db, {"source-table": c.tables[tbl]["id"], "aggregation": [["measure", c.measures[(tbl, m_)]]]}))
        print(label, "ny_a max(at)", run(c.db, {"source-table": c.tables["ny_a"]["id"], "aggregation": [["max", ["field", c.field("ny_a", "at")["id"], None]]]}))
        print(label, "ny_a day, m_last_at", run(c.db, {"source-table": c.tables["ny_a"]["id"], "breakout": [["field", c.field("ny_a", "at")["id"], {"temporal-unit": "day"}]], "aggregation": [["measure", c.measures[("ny_a", "m_last_at")]]], "order-by": [["asc", ["field", c.field("ny_a", "at")["id"], {"temporal-unit": "day"}]]], "limit": 2}))
print("truth", lib.ch("SELECT max(ts), toString(max(toTimeZone(ts, '%s'))), max(ts64) FROM zone_events" % NY))

print("== a64 on stock: a day under a report zone keeps the last second")
mb.api("PUT", "/api/setting/report-timezone", {"value": NY})
pairs = [("2025-03-08", "day"), ("2025-03-09", "day"), ("2025-11-02", "day"), ("2025-07-04", "day")]
for day, _ in pairs:
    truth = lib.ch("SELECT count(), sum(amount) FROM zone_events WHERE toDate(toTimeZone(ts64, '%s')) = '%s'" % (NY, day))
    # Postgres has this table with the same rows.
    p = run(pg_id, {"source-table": T["tz_events"]["id"], "aggregation": [["count"], ["sum", ref("tz_events", "amount")]], "filter": ["=", ref("tz_events", "ts3", **{"temporal-unit": "day"}), day]})
    print(day, "truth", truth, "PG", p)
    for c, label in ((cube, "dialect"), (stock, "stock")):
        at = ["field", c.field("ny_a64", "at")["id"], {"temporal-unit": "day"}]
        print("   %s filter" % label, run(c.db, {"source-table": c.tables["ny_a64"]["id"], "aggregation": [["count"], ["measure", c.measures[("ny_a64", "m_revenue")]]], "filter": ["=", at, day]}))
        print("   %s bucket" % label, run(c.db, {"source-table": c.tables["ny_a64"]["id"], "aggregation": [["count"], ["measure", c.measures[("ny_a64", "m_revenue")]]], "breakout": [at], "filter": ["between", at, day, day]}))
for lo, hi in (("2025-03-08T00:00:00", "2025-03-09T00:00:00"), ("2025-03-09T00:00:00.500", "2025-03-09T00:00:00.900")):
    truth = lib.ch("SELECT count() FROM zone_events WHERE ts64 >= toDateTime64('%s', 3, '%s') AND ts64 < toDateTime64('%s', 3, '%s')" % (lo.replace("T", " "), NY, hi.replace("T", " "), NY))
    p = run(pg_id, {"source-table": T["tz_events"]["id"], "aggregation": [["count"]], "filter": ["and", [">=", ref("tz_events", "ts3"), lo], ["<", ref("tz_events", "ts3"), hi]]})
    print(lo, hi, "truth", truth, "PG", p)
    for c, label in ((cube, "dialect"), (stock, "stock")):
        at = ["field", c.field("ny_a64", "at")["id"], None]
        print("   %s half-open" % label, run(c.db, {"source-table": c.tables["ny_a64"]["id"], "aggregation": [["count"]], "filter": ["and", [">=", at, lo], ["<", at, hi]]}))
mb.api("PUT", "/api/setting/report-timezone", {"value": None})
print("no zone: a64 on stock")
for day in ("2025-03-08",):
    truth = lib.ch("SELECT count() FROM zone_events WHERE toDate(ts64) = '%s'" % day)
    p = run(pg_id, {"source-table": T["tz_events"]["id"], "aggregation": [["count"]], "filter": ["=", ref("tz_events", "ts3", **{"temporal-unit": "day"}), day]})
    print(day, "truth", truth, "PG", p, "stock", run(stock.db, {"source-table": stock.tables["ny_a64"]["id"], "aggregation": [["count"]], "filter": ["=", ["field", stock.field("ny_a64", "at")["id"], {"temporal-unit": "day"}], day]}))
    print("  half-open at last second: truth", lib.ch("SELECT count() FROM zone_events WHERE ts64 >= '2025-03-08 00:00:00' AND ts64 < '2025-03-09 00:00:00'"),
          "stock", run(stock.db, {"source-table": stock.tables["ny_a64"]["id"], "aggregation": [["count"]], "filter": ["and", [">=", ["field", stock.field("ny_a64", "at")["id"], None], "2025-03-08T00:00:00"], ["<", ["field", stock.field("ny_a64", "at")["id"], None], "2025-03-09T00:00:00"]]}))
print(json.dumps({"done": True}))
