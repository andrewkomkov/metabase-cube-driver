"""The same queries on two Metabases of one version, with and without the plugin: Sample Database and a Postgres database. Rows and SQL must be equal."""
import json
import os
import time

import lib

A = lib.Metabase(url="http://127.0.0.1:13300").login()
B = lib.Metabase(url="http://127.0.0.1:13301").login()
for m in (A, B):
    dbs = {d["name"]: d for d in m.api("GET", "/api/database")["data"]}
    if "PG" not in dbs:
        m.api("POST", "/api/database", {"engine": "postgres", "name": "PG", "details": {"host": "postgres", "port": 5432, "dbname": "shop", "user": "shop", "password": "shop-dev-only", "ssl": False}})
    for _ in range(60):
        d = [x for x in m.api("GET", "/api/database")["data"] if x["name"] == "PG"][0]
        if d.get("initial_sync_status") == "complete":
            break
        time.sleep(3)
time.sleep(10)


def meta(m, name):
    d = [x for x in m.api("GET", "/api/database")["data"] if x["name"] == name][0]
    md = m.api("GET", "/api/database/%d/metadata?include_hidden=true" % d["id"])
    return d["id"], {t["name"].lower(): {"id": t["id"], "f": {f["name"].lower(): f for f in t["fields"]}, "n": t["name"]} for t in md["tables"]}


def queries(tb, db, name):
    if name == "Sample Database":
        o, p, pe = tb["orders"], tb["products"], tb["people"]
        tid, tot, at, disc, pid, sub = o["id"], o["f"]["total"]["id"], o["f"]["created_at"]["id"], o["f"]["discount"]["id"], o["f"]["product_id"]["id"], o["f"]["subtotal"]["id"]
        cat = p["f"]["category"]["id"]
        city = pe["f"]["city"]["id"]
    else:
        o, p = tb["orders"], tb["products"]
        tid, tot, at, disc, pid, sub = o["id"], o["f"]["amount"]["id"], o["f"]["created_at"]["id"], o["f"]["quantity"]["id"], o["f"]["product_id"]["id"], o["f"]["amount"]["id"]
        cat = p["f"]["category"]["id"]
        city = None
    F = lambda i, **k: ["field", i, k or None]  # noqa: E731
    out = {}
    base = {"source-table": tid}
    out["count"] = dict(base, aggregation=[["count"]])
    out["sum avg min max distinct"] = dict(base, aggregation=[["sum", F(tot)], ["avg", F(tot)], ["min", F(tot)], ["max", F(tot)], ["distinct", F(pid)]])
    out["stddev var"] = dict(base, aggregation=[["stddev", F(tot)], ["var", F(tot)]])
    out["median"] = dict(base, aggregation=[["median", F(tot)]]) if name != "Sample Database" else dict(base, aggregation=[["count"]])
    for unit in ("day", "week", "month", "quarter", "year", "hour-of-day", "day-of-week", "month-of-year", "week-of-year"):
        out["by " + unit] = dict(base, aggregation=[["count"], ["sum", F(tot)]], breakout=[F(at, **{"temporal-unit": unit})], limit=50, **{"order-by": [["asc", F(at, **{"temporal-unit": unit})]]})
    out["implicit join"] = dict(base, aggregation=[["sum", F(tot)]], breakout=[F(cat, **{"source-field": pid})])
    out["explicit join"] = dict(base, joins=[{"source-table": p["id"], "alias": "P", "strategy": "left-join", "fields": "none", "condition": ["=", F(pid), F(p["f"]["id"]["id"], **{"join-alias": "P"})]}], aggregation=[["count"]], breakout=[F(cat, **{"join-alias": "P"})])
    out["stage"] = {"source-query": dict(base, aggregation=[["sum", F(tot)]], breakout=[F(at, **{"temporal-unit": "month"})]), "aggregation": [["count"]]}
    out["expression"] = dict(base, expressions={"x": ["*", F(tot), 2]}, aggregation=[["sum", ["expression", "x"]]])
    out["case"] = dict(base, aggregation=[["sum", ["case", [[[">", F(tot), 100], F(tot)]], {"default": 0}]]])
    out["cum sum"] = dict(base, aggregation=[["cum-sum", F(tot)]], breakout=[F(at, **{"temporal-unit": "year"})])
    out["offset"] = dict(base, aggregation=[["offset", {}, ["sum", F(tot)], -1]], breakout=[F(at, **{"temporal-unit": "year"})])
    out["relative"] = dict(base, aggregation=[["count"]], filter=["time-interval", F(at), -1, "year"])
    out["between day"] = dict(base, aggregation=[["count"]], filter=["between", F(at, **{"temporal-unit": "day"}), "2025-03-09", "2025-03-10"])
    out["gt time"] = dict(base, aggregation=[["count"]], filter=[">", F(at), "2025-03-09T12:00:00"])
    out["raw"] = dict(base, limit=3, **{"order-by": [["asc", F(tot)]]})
    out["bins"] = dict(base, aggregation=[["count"]], breakout=[F(tot, binning={"strategy": "bin-width", "bin-width": 50})])
    out["default bins"] = dict(base, aggregation=[["count"]], breakout=[F(tot, binning={"strategy": "default"})])
    if city:
        out["contains"] = {"source-table": tb["people"]["id"], "aggregation": [["count"]], "filter": ["contains", F(city), "ville", {"case-sensitive": False}]}
    return db, out


def run(m, q):
    res = m.api("POST", "/api/dataset", q, ok=(200, 202, 400, 403, 500))
    return (res.get("data") or {}).get("rows"), (res.get("data") or {}).get("native_form", {}).get("query"), res.get("error")


total = same = 0
for name in ("Sample Database", "PG") if not os.environ.get("META_ONLY") else ():
    for zone in (None, "America/New_York"):
        for m in (A, B):
            m.api("PUT", "/api/setting/report-timezone", {"value": zone})
        (da, ta), (db_, tb_) = meta(A, name), meta(B, name)
        _, qa = queries(ta, da, name)
        _, qb = queries(tb_, db_, name)
        for key in qa:
            ra = run(A, {"database": da, "type": "query", "query": qa[key]})
            rb = run(B, {"database": db_, "type": "query", "query": qb[key]})
            total += 1
            ok = ra[0] == rb[0] and ra[2] == rb[2]
            sql_same = str(ra[1]) == str(rb[1])
            if ok and sql_same:
                same += 1
            else:
                print("DIFF", name, zone, key, "rows equal", ok, "sql equal", sql_same, "|", str(ra[2])[:120], "|", str(rb[2])[:120])
                if not sql_same:
                    print("   A:", str(ra[1])[:300])
                    print("   B:", str(rb[1])[:300])
        sql = "SELECT COUNT(*) FROM ORDERS" if name == "Sample Database" else "SELECT COUNT(*) FROM orders"
        ra = run(A, {"database": da, "type": "native", "native": {"query": sql, "template-tags": {}}})
        rb = run(B, {"database": db_, "type": "native", "native": {"query": sql, "template-tags": {}}})
        total += 1
        same += 1 if ra[0] == rb[0] else 0
for m in (A, B):
    m.api("PUT", "/api/setting/report-timezone", {"value": None})
print("queries", total, "equal", same)
da, ta = meta(A, "PG")
db_, tb_ = meta(B, "PG")
sa = {(t, f): (x["semantic_type"], x["base_type"], x["visibility_type"], x["has_field_values"]) for t in ta for f, x in ta[t]["f"].items() if t in ("orders", "users", "products")}
sb = {(t, f): (x["semantic_type"], x["base_type"], x["visibility_type"], x["has_field_values"]) for t in tb_ for f, x in tb_[t]["f"].items() if t in ("orders", "users", "products")}
print("PG field metadata differences", {k: (sa.get(k), sb.get(k)) for k in set(sa) | set(sb) if sa.get(k) != sb.get(k)})
measures_a = [m_ for m_ in A.api("GET", "/api/measure") if m_.get("table_id") in {t["id"] for t in ta.values()}]
print("measures on PG with the plugin", len(measures_a), "description of PG", repr([d for d in A.api("GET", "/api/database")["data"] if d["name"] == "PG"][0].get("description"))[:80])
