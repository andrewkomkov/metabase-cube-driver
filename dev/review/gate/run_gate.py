"""Runs shapes_gate.py through /api/dataset, or through MCP, and compares each number with ClickHouse. It writes one JSON line per shape.

    python3 run_gate.py --db Cube --out out/dialect.jsonl [--only REGEX] [--channel dataset|mcp] [--every N]
"""
import argparse
import concurrent.futures
import copy
import json
import re
import sys

import lib
import run_grid
import shapes_gate as G

RAW = re.compile(r"^(ERROR: |Error executing query|Query failed: (Error|\"?ERROR)|.*Can't find rewrite|.*Internal Error)")


def to5(c):
    if isinstance(c, dict):
        return {k: to5(v) for k, v in c.items()}
    if not isinstance(c, list) or not c or not isinstance(c[0], str):
        return [to5(x) for x in c] if isinstance(c, list) else c
    if c[0] == "field":
        return ["field", c[2] or {}, c[1]]
    return [c[0], {}] + [to5(x) for x in c[1:]]


class Run:
    def __init__(self, mb, db, mcp=None):
        self.mb, self.ctx, self.mcp = mb, run_grid.Ctx(mb, db), mcp
        self.zone = self.week = None
        self.mb.api("PUT", "/api/setting/report-timezone", {"value": None})
        self.mb.api("PUT", "/api/setting/start-of-week", {"value": None})

    def settings(self, zone, week):
        if zone != self.zone:
            self.mb.api("PUT", "/api/setting/report-timezone", {"value": zone})
            self.zone = zone
        if week != self.week:
            self.mb.api("PUT", "/api/setting/start-of-week", {"value": week})
            self.week = week

    def walk(self, x):
        """Ctx.walk, with field names inside an option map and tables as t:name."""
        if isinstance(x, list) and len(x) == 3 and x[0] == "field" and isinstance(x[1], str):
            ref = self.ctx.ref(x[1])
            return ["field", ref[1], dict(ref[2] or {}, **(x[2] or {}))]
        if isinstance(x, str) and x.startswith("t:"):
            return self.ctx.tables[x[2:]]["id"]
        if isinstance(x, str) and re.match(r"^[dms]:[a-z_]", x):
            return self.ctx.ref(x)
        if isinstance(x, list):
            return [self.walk(i) for i in x]
        if isinstance(x, dict):
            return {k: self.walk(v) for k, v in x.items()}
        return x

    def inner(self, sh):
        q = {"source-table": self.ctx.tables[sh["table"]]["id"]}
        for key, out in (("agg", "aggregation"), ("by", "breakout"), ("order", "order-by"), ("fields", "fields"), ("joins", "joins"), ("exprs", "expressions")):
            if sh.get(key):
                q[out] = self.walk(sh[key])
        for key in ("limit", "page"):
            if sh.get(key):
                q[key] = sh[key]
        if sh.get("extra_fields"):
            q["fields"] = q["fields"] + [["expression", n] for n in sh["extra_fields"]]
        where = sh.get("where") or []
        if where:
            walked = [self.walk(w) for w in where]
            q["filter"] = walked[0] if len(walked) == 1 else ["and"] + walked
        return q

    def outer(self, sh, inner):
        kind, via = sh["outer"]["kind"], sh["via"]
        cols = self.ctx.columns(inner)
        ref = lambda c, **o: ["field", c["name"], dict({"base-type": c["base_type"]}, **o)]  # noqa: E731
        by_name = {c["name"]: c for c in cols}
        first, last = cols[0], cols[-1]
        o = {}
        if kind == "value_filter":
            thr = lib.ch(sh["threshold"])[0][0]
            o["filter"] = [">", ref(last), float(thr) if thr is not None else 0]
        elif kind == "top3":
            o["order-by"] = [["desc", ref(last)], ["asc", ref(first)]]
            o["limit"] = 3
        elif kind == "sum":
            o["aggregation"] = [["sum", ref(last)]]
        elif kind == "count":
            o["aggregation"] = [["count"]]
        elif kind == "three":
            nested = {"source-query": inner, "filter": [">", ref(last), 20000]}
            inner2 = nested if via == "inline" else {"source-table": "card__%d" % self.ctx.card(inner, via), "filter": [">", ref(last), 20000]}
            return inner2, {"aggregation": [["sum", ref(last)]]}
        elif kind == "raw_group":
            o["aggregation"] = [["sum", ref(by_name[sh["outer"]["sum"]])]] + ([] if sh["outer"].get("no_count") else [["count"]])
            o["breakout"] = [ref(by_name[sh["outer"]["col"]])]
        elif kind == "raw_time":
            o["aggregation"] = [["sum", ref(by_name[sh["outer"]["sum"]])], ["count"]]
            o["breakout"] = [ref(by_name[sh["outer"]["col"]], **{"temporal-unit": "quarter"})]
            o["filter"] = [">", ref(by_name[sh["outer"]["sum"]]), sh["outer"]["gt"]]
        elif kind == "measure_cols":
            o["aggregation"] = [["sum", ref(by_name["revenue"])], ["avg", ref(by_name["amount_dim"])]] if "amount_dim" in by_name else [["sum", ref(by_name["revenue"])]]
            o["breakout"] = [ref(by_name["status"])]
        elif kind == "cum":
            o["fields"] = [ref(first), ref(last)]
            o["expressions"] = {"z": ["+", ref(last), 0]}
        elif kind == "div1000":
            o["expressions"] = {"z": ["/", ref(last), 1000]}
            o["fields"] = [ref(first), ["expression", "z"]]
        elif kind == "eq_first":
            o["filter"] = ["=", ref(first), sh["outer"]["value"]]
        return None, o

    def build(self, sh):
        if sh.get("native"):
            tags = copy.deepcopy(sh.get("tags") or {})
            for t in tags.values():
                if t.get("dimension"):
                    t["dimension"] = self.ctx.ref(t["dimension"])
            q = {"database": self.ctx.db, "type": "native", "native": {"query": sh["native"], "template-tags": tags}}
            if sh.get("params"):
                q["parameters"] = sh["params"]
            return q
        inner = self.inner(sh)
        if not sh.get("outer"):
            q = self.ctx.dq(inner)
        else:
            mid, o = self.outer(sh, inner)
            if mid is not None:
                q = self.ctx.dq(dict(o, **{"source-query": mid}))
            elif sh["via"] == "inline":
                q = self.ctx.dq(dict(o, **{"source-query": inner}))
            else:
                q = self.ctx.dq(dict(o, **{"source-table": "card__%d" % self.ctx.card(inner, sh["via"])}))
        if sh.get("parameters"):
            q["parameters"] = self.walk(sh["parameters"])
            for p in q["parameters"]:
                if isinstance(p.get("target"), list) and p["target"][0] == "dimension":
                    pass
        return q

    def run_dataset(self, sh, q):
        res = self.mb.api("POST", "/api/dataset", q, ok=(200, 202, 400, 403, 500))
        if isinstance(res, str):
            return None, res
        if res.get("error") or res.get("status") == "failed":
            return None, str(res.get("error") or res.get("message") or res.get("error_type"))
        if "data" not in res:
            return None, json.dumps(res)[:400]
        return res["data"]["rows"], None

    def run_mcp(self, sh, q):
        if q["type"] == "native":
            if sh.get("tags"):
                return "skip", "tags"
            res, err = self.mcp.call("execute_sql", database_id=q["database"], sql=q["native"]["query"], row_limit=2000)
            return (res.get("rows"), None) if err is None else (None, err)
        inner = q["query"]
        if "source-query" in inner or "joins" in inner or q.get("parameters") or "page" in inner:
            return "skip", "form"
        stage = {"lib/type": "mbql.stage/mbql"}
        src = inner["source-table"]
        if isinstance(src, str):
            stage["source-card"] = int(src.split("__")[1])
        else:
            stage["source-table"] = src
        for a, b in (("aggregation", "aggregation"), ("breakout", "breakout")):
            if inner.get(a):
                stage[b] = [to5(x) for x in inner[a]]
        if inner.get("filter"):
            stage["filters"] = [to5(inner["filter"])]
        if inner.get("order-by"):
            stage["order-by"] = [to5(x) for x in inner["order-by"]]
        if inner.get("limit"):
            stage["limit"] = inner["limit"]
        if inner.get("fields"):
            stage["fields"] = [to5(x) for x in inner["fields"]]
        if inner.get("expressions"):
            return "skip", "expressions"
        query = {"lib/type": "mbql/query", "database": q["database"], "stages": [stage]}
        res, err = self.mcp.call("execute_query", query=query, row_limit=2000)
        return (res.get("rows"), None) if err is None else (None, err)

    def judge(self, sh, channel):
        out = {"id": sh["id"], "zone": sh.get("zone"), "week": sh.get("week")}
        try:
            q = self.build(sh)
        except KeyError as e:
            return dict(out, verdict="no_object", error=str(e))
        except RuntimeError as e:
            text = str(e)
            if "/api/card" in text or text.startswith("inner"):
                return dict(out, verdict="refused" if not RAW.search(text.split(": ", 1)[-1]) else "raw_error", error=text[:700])
            return dict(out, verdict="harness", error=text[:700])
        out["query"] = q
        rows, err = (self.run_mcp if channel == "mcp" else self.run_dataset)(sh, q)
        if rows == "skip":
            return dict(out, verdict="skip", error=err)
        if err is not None:
            kind = "raw_error" if RAW.search(err.strip()) else "refused"
            return dict(out, verdict=kind, error=err[:900], expected=bool(sh.get("refuse")))
        out["sql"] = sh["truth"]
        try:
            want = lib.ch(sh["truth"])
        except RuntimeError as e:
            return dict(out, verdict="truth_error", error=str(e)[:500], got=rows[:4])
        if sh.get("refuse"):
            return dict(out, verdict="no_refusal", got=rows[:4])
        tol = sh.get("tol", 1e-9)
        if sh.get("exact"):
            # A long number must not lose digits with no error.
            diff = None
            exp = sorted(want, key=lib.key)
            got = sorted(rows, key=lib.key)
            for a, b in zip(got, exp):
                for x, y in zip(a, b):
                    if str(x) != str(y) and str(lib.norm(x)) != str(lib.norm(y)):
                        diff = "exact: got %s, truth %s" % (a, b)
            if diff is None and len(got) != len(exp):
                diff = "rows"
        else:
            diff = lib.compare(rows, want, ordered=sh.get("ordered", False), tol=tol)
        if diff is None:
            return dict(out, verdict="pass", rows=len(rows))
        return dict(out, verdict="review" if sh.get("loose") else "wrong", diff=diff[:900], got=rows[:6], want=want[:6])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="Cube")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only")
    ap.add_argument("--channel", default="dataset")
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    mb = lib.Metabase().login()
    run = Run(mb, a.db, lib.Mcp(mb) if a.channel == "mcp" else None)
    shapes = [s for s in G.SHAPES if not a.only or re.search(a.only, s["id"])][:: a.every]
    groups = {}
    for s in shapes:
        groups.setdefault((s.get("zone"), s.get("week")), []).append(s)
    counts = {}
    done = 0
    with open(a.out, "w") as f, concurrent.futures.ThreadPoolExecutor(a.workers) as pool:
        for (zone, week), group in groups.items():
            run.settings(zone, week)

            def one(sh):
                try:
                    return run.judge(sh, a.channel)
                except Exception as e:  # noqa: BLE001 - one bad reply must not stop the run
                    return {"id": sh["id"], "zone": sh.get("zone"), "verdict": "harness", "error": "%s: %s" % (type(e).__name__, str(e)[:500])}
            for r in pool.map(one, group):
                counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
                f.write(json.dumps(r, default=str) + "\n")
                f.flush()
                done += 1
            print(done, len(shapes), counts, flush=True)
    run.settings(None, None)
    print("total", len(shapes), counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
