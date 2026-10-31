"""Runs the shapes of shapes.py through /api/dataset or MCP, and compares each number with ClickHouse.

    python3 run_grid.py --db Cube --channel dataset --out results.jsonl [--only REGEX] [--every N]
"""
import argparse
import concurrent.futures
import hashlib
import json
import re
import threading
import time
import uuid

import lib
import shapes as S


class Ctx:
    def __init__(self, mb, db_name):
        self.mb = mb
        self.lock = threading.Lock()
        dbs = mb.api("GET", "/api/database")["data"]
        self.db = [d for d in dbs if d["name"] == db_name][0]["id"]
        meta = mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % self.db)
        self.tables = {t["name"]: t for t in meta["tables"]}
        self.field_by_id = {}
        for t in meta["tables"]:
            for f in t["fields"]:
                self.field_by_id[f["id"]] = (t["name"], f["name"])
        self.measures = {}
        for m in mb.api("GET", "/api/measure"):
            if m.get("archived"):
                continue
            ids = [x for x in _ints_after_field(m.get("definition"))]
            for i in ids:
                if i in self.field_by_id:
                    self.measures[self.field_by_id[i]] = m["id"]
        segs = mb.api("GET", "/api/segment")
        segs = segs.get("data", segs) if isinstance(segs, dict) else segs
        self.segments = {}
        for s in segs:
            if s.get("archived"):
                continue
            ids = [x for x in _ints_after_field(s.get("definition"))]
            for i in ids:
                if i in self.field_by_id:
                    self.segments[self.field_by_id[i]] = s["id"]
        self.cards = {}
        self.cols = {}
        self.collection = mb.api("POST", "/api/collection", {"name": "review-%s-%d" % (db_name.replace(" ", "-"), time.time())})["id"]

    def field(self, table, name):
        for f in self.tables[table]["fields"]:
            if f["name"] == name:
                return f
        raise KeyError("%s.%s" % (table, name))

    def fk(self, source, target):
        pk = {f["id"] for f in self.tables[target]["fields"] if f.get("semantic_type") == "type/PK"}
        for f in self.tables[source]["fields"]:
            if f.get("fk_target_field_id") in pk:
                return f
        raise KeyError("no fk %s > %s" % (source, target))

    def ref(self, s):
        kind, body = s[0], s[2:]
        if kind == "m":
            return ["measure", self.measures[tuple(body.split("."))]]
        if kind == "s":
            return self.segments[tuple(body.split("."))]
        opts = {}
        if "@" in body:
            body, opts["temporal-unit"] = body.split("@")
        if "#" in body:
            body, b = body.split("#")
            opts["binning"] = {"strategy": "bin-width", "bin-width": float(b.split("=")[1])}
        path, name = body.split(".")
        if ">" in path:
            source, path = path.split(">")
            opts["source-field"] = self.fk(source, path)["id"]
        return ["field", self.field(path, name)["id"], opts or None]

    def walk(self, x):
        if isinstance(x, str) and re.match(r"^[dms]:[a-z_]", x):
            return self.ref(x)
        if isinstance(x, list):
            return [self.walk(i) for i in x]
        if isinstance(x, dict):
            return {k: self.walk(v) for k, v in x.items()}
        return x

    def inner(self, sh):
        q = {"source-table": self.tables[sh["table"]]["id"]}
        if sh.get("agg"):
            q["aggregation"] = self.walk(sh["agg"])
        if sh.get("by"):
            q["breakout"] = self.walk(sh["by"])
        where = sh.get("where") or []
        if where:
            q["filter"] = self.walk(["and"] + where) if len(where) > 1 else self.walk(where[0])
        if sh.get("order"):
            q["order-by"] = self.walk(sh["order"])
        if sh.get("limit"):
            q["limit"] = sh["limit"]
        if sh.get("fields"):
            q["fields"] = self.walk(sh["fields"])
        return q

    def dq(self, query):
        return {"database": self.db, "type": "query", "query": query}

    def card(self, inner, source):
        k = (json.dumps(inner, sort_keys=True), source)
        with self.lock:
            if k not in self.cards:
                self.cards[k] = self.mb.api("POST", "/api/card", {
                    "name": "review " + hashlib.md5(k[0].encode()).hexdigest()[:8], "dataset_query": self.dq(inner), "display": "table", "visualization_settings": {},
                    "collection_id": self.collection, "type": "model" if source == "model" else "question"})["id"]
            return self.cards[k]

    def columns(self, inner):
        k = json.dumps(inner, sort_keys=True)
        with self.lock:
            if k in self.cols:
                return self.cols[k]
        one = dict(inner, limit=1)
        _, cols, err = self.mb.run(self.dq(one))
        if err:
            raise RuntimeError("inner: " + err)
        with self.lock:
            self.cols[k] = cols
        return cols

    def refs(self, inner):
        """c["v"], c["b"], and each column by name. A stage reads a column of the stage before it by name."""
        cols = self.columns(inner)
        c = {}
        for col in cols:
            ref = ["field", col["name"], {"base-type": col["base_type"]}]
            c[col["name"]] = ref
            if col["base_type"] in ("type/DateTime", "type/DateTimeWithLocalTZ", "type/Date"):
                for unit in ("month", "quarter", "day"):
                    c["%s@%s" % (col["name"], unit)] = ["field", col["name"], {"base-type": col["base_type"], "temporal-unit": unit}]
        if "aggregation" in inner:
            first, last = cols[0], cols[-1]
            c["b"], c["v"] = c[first["name"]], c[last["name"]]
            c["b@quarter"] = c.get(first["name"] + "@quarter")
        return c

    def staged(self, sh):
        """Returns the stages as a list: the first is the source (a table query or a card), and the others are maps of clauses."""
        inner = self.inner(sh["inner"])
        c = self.refs(inner)
        stages = []
        if sh["source"] == "inline":
            stages.append({"inner": inner})
        else:
            stages.append({"card": self.card(inner, sh["source"])})
        stages.append(sh["outer"](c))
        if sh.get("outer2"):
            one = sh["outer"](c)
            cols = self.columns(dict(source_wrap(stages[0]), **one))
            c2 = {}
            for col in cols:
                c2[col["name"]] = ["field", col["name"], {"base-type": col["base_type"]}]
            c2["v"] = c2[cols[-1]["name"]]
            stages.append(sh["outer2"](c2))
        return stages

    def legacy(self, sh):
        if "native" in sh:
            q = {"database": self.db, "type": "native", "native": {"query": sh["native"], "template-tags": sh.get("tags") or {}}}
            if sh.get("parameters"):
                q["parameters"] = sh["parameters"]
            return q
        if "inner" in sh:
            stages = self.staged(sh)
            q = source_wrap(stages[0])
            for st in stages[1:]:
                q = dict(st, **{"source-query": q}) if "source-query" in q or "source-table" not in q or not str(q["source-table"]).startswith("card__") or st is not stages[1] else dict(q, **st)
            return self.dq(q)
        return self.dq(self.inner(sh))

    def mbql5(self, sh):
        def clause(x):
            if isinstance(x, dict):
                return {k: clause(v) for k, v in x.items()}
            if not isinstance(x, list) or not x or not isinstance(x[0], str):
                return [clause(i) for i in x] if isinstance(x, list) else x
            if x[0] == "field":
                return ["field", x[2] or {}, x[1]]
            args, opts = x[1:], {}
            if args and isinstance(args[-1], dict) and "case-sensitive" in args[-1]:
                opts, args = args[-1], args[:-1]
            if x[0] in ("asc", "desc"):
                return [x[0], {}, clause(args[0])]
            return [x[0], opts] + [clause(a) for a in args]

        def stage(cl):
            st = {"lib/type": "mbql.stage/mbql"}
            for k, v in cl.items():
                if k == "aggregation":
                    st["aggregation"] = [clause(a) for a in v]
                elif k == "breakout":
                    st["breakout"] = [clause(a) for a in v]
                elif k == "filter":
                    st["filters"] = [clause(a) for a in (v[1:] if v[0] == "and" else [v])]
                elif k == "order-by":
                    st["order-by"] = [clause(a) for a in v]
                elif k == "fields":
                    st["fields"] = [clause(a) for a in v]
                elif k == "limit":
                    st["limit"] = v
                elif k == "source-table":
                    st["source-table"] = v
            return st

        if "inner" in sh:
            stages = self.staged(sh)
            first = stages[0]
            if "inner" in first:
                out = [stage(first["inner"])]
                out += [stage(s) for s in stages[1:]]
            else:
                head = stage(stages[1])
                head["source-card"] = first["card"]
                out = [head] + [stage(s) for s in stages[2:]]
        else:
            out = [stage(self.inner(sh))]
        return {"lib/type": "mbql/query", "database": self.db, "stages": out}


def source_wrap(first):
    if "inner" in first:
        return {"source-query": first["inner"]}
    return {"source-table": "card__%d" % first["card"]}


def _ints_after_field(obj):
    if isinstance(obj, list):
        if len(obj) == 3 and obj[0] == "field":
            if isinstance(obj[2], int):
                yield obj[2]
            elif isinstance(obj[1], int):
                yield obj[1]
        for x in obj:
            yield from _ints_after_field(x)
    elif isinstance(obj, dict):
        for x in obj.values():
            yield from _ints_after_field(x)


TRUTH = {}
TRUTH_LOCK = threading.Lock()


def truth(sql):
    with TRUTH_LOCK:
        if sql in TRUTH:
            return TRUTH[sql]
    rows = lib.ch(sql)
    with TRUTH_LOCK:
        TRUTH[sql] = rows
    return rows


def run_mcp(ctx, mcp, sh):
    if "native" in sh:
        if sh.get("tags"):
            return None, "skip: template tags have no MCP form"
        res, err = mcp.call("execute_sql", database_id=ctx.db, sql=sh["native"], row_limit=2000)
    else:
        if any("aggregation" in json.dumps(o) and o[0] in ("asc", "desc") and o[1][0] == "aggregation" for o in sh.get("order") or []):
            return None, "skip: an order by a position has no short MCP form"
        res, err = mcp.call("execute_query", query=ctx.mbql5(sh), row_limit=2000)
    if err:
        return None, err
    rows = list(res.get("rows") or [])
    while res.get("next_cursor"):
        res, err = mcp.call("execute_query" if "native" not in sh else "execute_sql", cursor=res["next_cursor"], row_limit=2000)
        if err:
            return None, err
        rows += res.get("rows") or []
    return rows, None


def one(ctx, mcp, sh, channel):
    r = {"id": sh["id"], "channel": channel}
    try:
        if channel == "mcp":
            rows, err = run_mcp(ctx, mcp, sh)
            if err and err.startswith("skip:"):
                r.update(verdict="skip", error=err)
                return r
        else:
            q = ctx.legacy(sh)
            r["query"] = q
            rows, _, err = ctx.mb.run(q)
    except Exception as e:
        rows, err = None, "harness: %s" % e
    want = None
    try:
        if sh.get("truth"):
            want = truth(sh["truth"])
    except Exception as e:
        r.update(verdict="truth_error", error=str(e)[:400], truth_sql=sh["truth"])
        return r
    r["truth_sql"] = sh.get("truth")
    if err:
        r["error"] = err[:600]
        if err.startswith("harness"):
            r["verdict"] = "harness"
        elif sh.get("expect") == "refusal":
            r["verdict"] = "refused_as_expected"
        else:
            r["verdict"] = "error"
        return r
    r["got"] = rows[:6]
    r["rows"] = len(rows)
    # The MCP tools stop at 4000 rows over all pages here, with no error. The driver does not see the pages.
    if channel == "mcp" and want is not None and len(want) > 4000 and len(rows) == 4000:
        r["verdict"] = "skip"
        r["error"] = "skip: MCP gives at most 4000 rows"
        return r
    if sh.get("expect") == "refusal":
        r["verdict"] = "no_refusal"
        return r
    if sh.get("kind") == "row_count":
        r["verdict"] = "pass" if len(rows) == sh["want_rows"] else "wrong"
        r["diff"] = "rows: got %d, truth %d" % (len(rows), sh["want_rows"])
        return r
    if want is None:
        r["verdict"] = "info"
        return r
    r["want"] = want[:6]
    diff = lib.compare(rows, want, ordered=sh.get("ordered", False), tol=sh.get("tol", 1e-9))
    r["verdict"] = "pass" if diff is None else "wrong"
    if diff:
        r["diff"] = diff[:800]
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="Cube")
    ap.add_argument("--channel", default="dataset")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only")
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    mb = lib.Metabase().login()
    ctx = Ctx(mb, a.db)
    mcps = threading.local()
    all_shapes = S.shapes()
    if a.only:
        all_shapes = [s for s in all_shapes if re.search(a.only, s["id"])]
    if a.every > 1:
        # A stable choice, so that the two channels read the same part of the grid.
        all_shapes = [s for s in all_shapes if int(hashlib.md5(s["id"].encode()).hexdigest(), 16) % a.every == 0]

    def job(sh):
        if a.channel == "mcp" and not getattr(mcps, "c", None):
            mcps.c = lib.Mcp(lib.Metabase().login())
        return one(ctx, getattr(mcps, "c", None), sh, a.channel)

    t0 = time.time()
    counts = {}
    with open(a.out, "w") as f, concurrent.futures.ThreadPoolExecutor(a.workers) as ex:
        for i, r in enumerate(ex.map(job, all_shapes), 1):
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
            f.write(json.dumps(r, default=str) + "\n")
            f.flush()
            if i % 100 == 0:
                print(i, len(all_shapes), counts, int(time.time() - t0), "s", flush=True)
    print("done", a.db, a.channel, len(all_shapes), counts, int(time.time() - t0), "s", flush=True)


if __name__ == "__main__":
    main()
