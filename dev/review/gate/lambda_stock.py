"""Revenue by month of a rollup_lambda on stock Cube, in each zone, with the difference from ClickHouse. And a bin of a key column on Postgres."""
import lib
import run_grid

mb = lib.Metabase().login()
c = run_grid.Ctx(mb, "Cube stock")
for zone in (None, "America/New_York", "America/Los_Angeles", "Europe/Berlin", "Asia/Kolkata"):
    mb.api("PUT", "/api/setting/report-timezone", {"value": zone})
    q = {"source-table": c.tables["lambda_sales"]["id"], "aggregation": [["measure", c.measures[("lambda_sales", "revenue")]]], "breakout": [c.ref("d:lambda_sales.created_at@month")]}
    res = mb.api("POST", "/api/dataset", {"database": c.db, "type": "query", "query": q}, ok=(200, 202, 400, 500))
    x = "toTimeZone(created_at, '%s')" % zone if zone else "created_at"
    truth = {str(a): float(b) for a, b in lib.ch("SELECT toStartOfMonth(%s), sum(amount) FROM orders GROUP BY 1" % x)}
    if res.get("error") or "data" not in res:
        print(zone, "ERR", str(res.get("error"))[:200])
        continue
    got = {r[0][:10]: r[1] for r in res["data"]["rows"]}
    print(zone, "diff", {k: round(got.get(k, 0) - truth.get(k, 0), 2) for k in sorted(set(got) | set(truth)) if abs(got.get(k, 0) - truth.get(k, 0)) > 0.001}, "total got", sum(got.values()), "truth", sum(truth.values()), flush=True)
print("rows in the last hours of the batch", lib.ch("SELECT toString(status), count(), sum(amount) FROM orders WHERE created_at >= '2025-06-30 20:00:00' AND created_at < '2025-07-01 00:00:00' GROUP BY 1"))
mb.api("PUT", "/api/setting/report-timezone", {"value": None})
pg = [d for d in mb.api("GET", "/api/database")["data"] if d["name"] == "PG"][0]["id"]
t = [x for x in mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % pg)["tables"] if x["name"] == "orders"][0]
for name in ("id", "user_id"):
    f = [x for x in t["fields"] if x["name"] == name][0]
    res = mb.api("POST", "/api/dataset", {"database": pg, "type": "query", "query": {"source-table": t["id"], "aggregation": [["count"]], "breakout": [["field", f["id"], {"binning": {"strategy": "bin-width", "bin-width": 1000}}]]}}, ok=(200, 202, 400, 500))
    print("PG bin of", name, f.get("semantic_type"), bool(f.get("fingerprint")), res.get("error") or res["data"]["rows"][:2])
k = run_grid.Ctx(mb, "Cube")
for name in ("id", "user_id", "quantity_dim"):
    f = k.field("orders", name)
    print("Cube", name, f.get("semantic_type"), "fingerprint", bool(f.get("fingerprint")), f.get("has_field_values"))
