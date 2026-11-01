"""A rollup_lambda under a report zone on stock Cube and with the dialect, and a plain partitioned rollup with a build range next to it."""
import json

import lib
import run_grid

NY = "America/New_York"
mb = lib.Metabase().login()
mb.api("PUT", "/api/setting/report-timezone", {"value": NY})
for name in ("Cube", "Cube stock"):
    c = run_grid.Ctx(mb, name)
    for table, m, st, at in (("lambda_sales", "revenue", "status", "created_at"), ("pa_range_mid", "revenue", "status", "created_at")):
        for label, unit, val, cond in (("Q2", "quarter", "2025-04-01", "toStartOfQuarter(x) = '2025-04-01'"), ("June", "month", "2025-06-01", "toStartOfMonth(x) = '2025-06-01'"), ("July", "month", "2025-07-01", "toStartOfMonth(x) = '2025-07-01'"),
                                       ("March", "month", "2025-03-01", "toStartOfMonth(x) = '2025-03-01'"), ("Jan", "month", "2025-01-01", "toStartOfMonth(x) = '2025-01-01'"), ("all by month", None, None, "1")):
            f = c.ref("d:%s.%s@%s" % (table, at, unit)) if unit else None
            q = {"source-table": c.tables[table]["id"], "aggregation": [["measure", c.measures[(table, m)]]], "breakout": [c.ref("d:%s.%s" % (table, st))]}
            if f:
                q["filter"] = ["=", f, val]
            else:
                q["breakout"] = [c.ref("d:%s.%s@month" % (table, at))]
            res = mb.api("POST", "/api/dataset", {"database": c.db, "type": "query", "query": q}, ok=(200, 202, 400, 500))
            x = "toTimeZone(created_at, '%s')" % NY
            k = "toString(status)" if f else "toStartOfMonth(%s)" % x
            truth = lib.ch("SELECT %s AS k, sum(amount) FROM orders WHERE %s GROUP BY k" % (k, cond.replace("x", x, 1) if f else "1"))
            if res.get("error") or "data" not in res:
                print(name, table, label, "ERR", str(res.get("error"))[:170])
                continue
            print(name, table, label, lib.compare(res["data"]["rows"], truth) or "equal", "| SQL:", res["data"]["native_form"]["query"][:230] if label == "Q2" else "", flush=True)
mb.api("PUT", "/api/setting/report-timezone", {"value": None})
