"""`make fuzz SEED=n COUNT=n`: random Metabase queries against an independent ClickHouse truth. See README.md.

Exit code 0 means no wrong number, no missing refusal, no raw error and no failure of the runner. A clear refusal is a pass, and the report counts it.
"""
import argparse
import concurrent.futures
import copy
import hashlib
import json
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "review"))

import gen  # noqa: E402
import known_limits  # noqa: E402
import lib  # noqa: E402
import mbql  # noqa: E402
import oracle  # noqa: E402
import run_grid  # noqa: E402

CASES = os.path.join(HERE, "cases")
FAILURES = ("wrong", "no_refusal", "raw_error", "harness", "truth_error")
ADMIN = os.environ.get("REVIEW_ADMIN", "admin@example.com")
PASSWORD = os.environ.get("REVIEW_PASSWORD", "fuzz-dev-only-1")
# A message of Metabase core about a query that it cannot read. Here the generator is at fault, not the driver.
INVALID = re.compile(r"Invalid (MBQL|output)|does not match schema|Cannot determine the source table")


STOCK_NO_ROWS = re.compile(r"\b(sum|min|max)OrNull\(")
# The connection broke. This says nothing about the query, so it is no refusal.
BROKEN = re.compile(r"I/O error|Connection (refused|reset)|This connection has been closed|Connection is closed")


def clear(err):
    """The driver adds the text of Cube after its own message, and only its own message counts."""
    return known_limits.clear_error(err.split(" Cube said:")[0]) and not BROKEN.search(err)


def run_query(mb, query):
    """(rows, error, error type)."""
    res = mb.api("POST", "/api/dataset", query, ok=(200, 202, 400, 403, 500))
    if isinstance(res, str):
        return None, res, "text"
    if res.get("error") or res.get("status") == "failed":
        return None, str(res.get("error") or res.get("message") or res.get("error_type")), res.get("error_type")
    if "data" not in res:
        return None, json.dumps(res)[:400], "invalid-query"
    return res["data"]["rows"], None, None


class Runner:
    def __init__(self, mb, ctx, stock=False):
        self.mb, self.ctx, self.stock = mb, ctx, stock
        self.settings = (None, None)
        self.truth_cache = {}
        self.lock = threading.Lock()

    def apply(self, zone, week):
        if self.settings[0] != zone:
            self.mb.api("PUT", "/api/setting/report-timezone", {"value": zone})
        if self.settings[1] != week:
            self.mb.api("PUT", "/api/setting/start-of-week", {"value": week})
        self.settings = (zone, week)
        # A saved question keeps its columns, and those can read the settings.

    def reset(self):
        self.apply(None, None)

    def truth(self, sql):
        with self.lock:
            if sql in self.truth_cache:
                return self.truth_cache[sql]
        rows = lib.ch(sql)
        with self.lock:
            if len(self.truth_cache) < 2000:
                self.truth_cache[sql] = rows
        return rows

    def judge(self, spec):
        """One verdict. The settings of the spec must be on."""
        t = oracle.truth(spec)
        out = {"truth_sql": t["sql"]}
        err = rows = etype = None
        try:
            query = mbql.build(self.ctx, spec)
            out["query"] = query
            rows, err, etype = run_query(self.mb, query)
        except mbql.InnerError as e:
            err, etype = str(e), "inner"
        except KeyError as e:
            return dict(out, verdict="harness", error="no such object in Metabase: %s" % e)
        except RuntimeError as e:
            text = str(e)
            if "/api/card" in text:
                # Metabase runs the query of a new card. A refusal there is a refusal of the query.
                err, etype = text, "inner"
            else:
                return dict(out, verdict="harness", error=text[:500])
        if err is not None:
            out["error"] = err[:700]
            if INVALID.search(err):
                return dict(out, verdict="harness")
            if t.get("expect") == "refusal":
                return dict(out, verdict="refused", expected=True)
            return dict(out, verdict="refused" if clear(err) else "raw_error")
        if t["expect"] == "refusal":
            return dict(out, verdict="no_refusal", diff=t["why"], got=rows[:4])
        if t["expect"] == "unknown":
            return dict(out, verdict="skip", why=t["why"])
        try:
            want = self.truth(t["sql"])
        except RuntimeError as e:
            return dict(out, verdict="truth_error", error=str(e)[:700])
        diff = lib.compare(rows, want, ordered=t["ordered"], tol=t["tol"])
        if diff is None:
            return dict(out, verdict="pass", rows=len(rows))
        if self.stock and lib.compare(rows, self.truth(STOCK_NO_ROWS.sub(r"\1(", t["sql"])), ordered=t["ordered"], tol=t["tol"]) is None:
            # The truth stays NULL, and stock Cube has no way to it. See "Known wrong numbers" in docs/query-compilation.md.
            return dict(out, verdict="stock_no_rows", rows=len(rows))
        # Known limits of stock Cube, in dev/review/known_limits.py: the dialect is the fix, and Cube does not show the type of the column.
        text = json.dumps(spec)
        if self.stock and ("ts64_berlin" in text or ("day32" in text and spec.get("zone"))):
            return dict(out, verdict="stock_known", diff=diff[:800], rows=len(rows))
        return dict(out, verdict="wrong", diff=diff[:800], got=rows[:4], want=want[:4], rows=len(rows))

    def check(self, spec):
        r = self.judge(spec)
        if r["verdict"] in FAILURES:
            # A failure must come back, because a query can meet a restart or a refresh.
            time.sleep(0.2)
            again = self.judge(spec)
            if again["verdict"] != r["verdict"]:
                return dict(again, flaky=r["verdict"], flaky_error=str(r.get("error") or r.get("diff"))[:300])
        return r

    # ------------------------------------------------------------------ the smallest query

    def reduce(self, spec, verdict, budget=70):
        """Removes each part of the query while the same kind of failure stays. Returns the smallest spec."""
        current = copy.deepcopy(spec)
        tried = 0
        progress = True
        while progress and tried < budget:
            progress = False
            for cand in candidates(current):
                tried += 1
                if tried > budget:
                    break
                self.apply(cand.get("zone"), cand.get("week"))
                try:
                    if self.judge(cand)["verdict"] == verdict:
                        current = cand
                        progress = True
                        break
                except Exception:  # noqa: BLE001 - a smaller form that breaks the runner is not a smaller form of this failure
                    continue
        self.apply(spec.get("zone"), spec.get("week"))
        return current


def candidates(spec):
    """Smaller forms of a spec, the largest step first."""
    out = []

    def add(fn):
        c = copy.deepcopy(spec)
        fn(c)
        if c != spec:
            out.append(c)

    if spec.get("stage"):
        add(lambda c: c.pop("stage"))
        for key in ("where", "by", "agg"):
            for i in range(len(spec["stage"].get(key, []))):
                add(lambda c, k=key, n=i: c["stage"][k].pop(n))
        add(lambda c: (c["stage"].pop("order", None), c["stage"].pop("limit", None)))
        add(lambda c: c["stage"].update(via="inline"))
    add(lambda c: c.pop("join", None))
    add(lambda c: c.update(zone=None))
    add(lambda c: c.update(week=None))
    add(lambda c: (c.pop("order", None), c.pop("limit", None)))
    for key in ("where", "by", "agg"):
        for i in range(len(spec.get(key, []))):
            add(lambda c, k=key, n=i: c[k].pop(n))
    for i, f in enumerate(spec.get("where", [])):
        for smaller in smaller_filters(f):
            add(lambda c, n=i, s=smaller: c["where"].__setitem__(n, s))
    for i, b in enumerate(spec.get("by", [])):
        if b.get("bin"):
            add(lambda c, n=i: c["by"][n].pop("bin"))
    return [c for c in out if usable(c)]


def smaller_filters(f):
    if f.get("op") in ("and", "or"):
        for a in f["a"]:
            yield a
        for i in range(len(f["a"])):
            if len(f["a"]) > 2:
                yield dict(f, a=f["a"][:i] + f["a"][i + 1:])
    if f.get("op") == "not":
        yield f["a"][0]
    if isinstance(f.get("v"), list) and len(f["v"]) > 1 and f.get("op") in ("=", "!="):
        yield dict(f, v=f["v"][:1])


def usable(spec):
    """A stage that refers to a column of the first stage needs that column."""
    n_by, n_agg = len(spec.get("by", [])), len(spec.get("agg", []))
    if not spec.get("fields") and not n_agg:
        return False
    if spec.get("fields") and not spec.get("stage") and not spec.get("limit"):
        return False
    if spec.get("stage"):
        width = (len(spec.get("fields", [])) + len(spec.get("measure_fields", []))) if spec.get("fields") else n_by + n_agg
        st = spec["stage"]
        if not st.get("agg") and not st.get("by") and not st.get("limit"):
            return False
        refs = re.findall(r'"i": (\d+)', json.dumps(st))
        if any(int(i) >= width for i in refs):
            return False
    for o in spec.get("order", []):
        if ("agg" in o and o["agg"] >= n_agg) or ("by" in o and o["by"] >= n_by):
            return False
    return True


def case_name(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:12]


def save_case(spec, r, seed, index):
    os.makedirs(CASES, exist_ok=True)
    path = os.path.join(CASES, case_name(spec) + ".json")
    new = not os.path.exists(path)
    if new:
        with open(path, "w") as f:
            json.dump({"spec": spec, "found": {"seed": seed, "index": index, "verdict": r["verdict"], "diff": r.get("diff"), "error": r.get("error"), "truth_sql": r.get("truth_sql")}}, f, indent=1, sort_keys=True, ensure_ascii=False)
            f.write("\n")
    return path, new


def load_cases():
    if not os.path.isdir(CASES):
        return []
    out = []
    for name in sorted(os.listdir(CASES)):
        if name.endswith(".json"):
            with open(os.path.join(CASES, name)) as f:
                out.append((name, json.load(f)))
    return out


def connect():
    mb = lib.Metabase(lib.MB, PASSWORD, ADMIN).login()
    have = {d["name"]: d for d in mb.api("GET", "/api/database")["data"]}
    if "Cube" not in have:
        have["Cube"] = mb.api("POST", "/api/database", {"engine": "cube", "name": "Cube", "details": {
            "host": "cube", "port": 15432, "dbname": "cube", "user": "cube", "password": "cube-dev-only", "cube-rest-url": "http://cube:4000",
            "cube-api-secret": "cube-dev-only-secret-0123456789abcdef"}})
    for _ in range(200):
        d = mb.api("GET", "/api/database/%d" % have["Cube"]["id"])
        if d.get("initial_sync_status") == "complete":
            break
        time.sleep(3)
    return mb, run_grid.Ctx(mb, "Cube")


def run_seed(runner, seed, count, start=0, reduce=True, save=True, workers=3, log=print, max_reduce=200):
    """Runs one seed. Returns the report."""
    counts = {}
    features = {}
    refusals = {}
    failures = []
    flaky = []
    t0 = time.time()
    index = start
    specs_done = 0
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        while index < start + count:
            end = min((index // gen.BLOCK + 1) * gen.BLOCK, start + count)
            block = [(i, gen.generate(seed, i)) for i in range(index, end)]
            zone, week = block[0][1]["zone"], block[0][1]["week"]
            runner.apply(zone, week)
            results = list(pool.map(lambda item: _safe(runner, item[1]), block))
            for (i, spec), r in zip(block, results):
                v = r["verdict"]
                counts[v] = counts.get(v, 0) + 1
                for tag in gen.tags(spec):
                    bucket = features.setdefault(tag, {"pass": 0, "refused": 0, "failed": 0})
                    bucket["pass" if v == "pass" else "refused" if v == "refused" else "failed" if v in FAILURES else "pass"] += 1 if v != "skip" else 0
                if v == "refused":
                    key = re.sub(r"'[^']*'|\d+", "#", r.get("error", ""))[:110]
                    refusals[key] = refusals.get(key, 0) + 1
                if r.get("flaky"):
                    flaky.append({"index": i, "first": r["flaky"], "text": r.get("flaky_error")})
                if v in FAILURES:
                    failures.append({"index": i, "spec": spec, "result": r})
            specs_done += len(block)
            index = end
            log("%d/%d %s %ds" % (specs_done, count, counts, time.time() - t0))
    report_failures = []
    cases_new = 0
    for n, fail in enumerate(failures):
        spec, r = fail["spec"], fail["result"]
        small = spec
        if reduce and r["verdict"] in ("wrong", "no_refusal", "raw_error") and n < max_reduce:
            runner.apply(spec.get("zone"), spec.get("week"))
            small = runner.reduce(spec, r["verdict"])
            if small != spec:
                runner.apply(small.get("zone"), small.get("week"))
                r = runner.judge(small)
        name = None
        if save and r["verdict"] in ("wrong", "no_refusal", "raw_error"):
            path, new = save_case(small, r, seed, fail["index"])
            cases_new += 1 if new else 0
            name = os.path.basename(path)
        report_failures.append({"index": fail["index"], "verdict": r["verdict"], "case": name, "spec": small, "diff": r.get("diff"), "error": r.get("error"), "truth_sql": r.get("truth_sql"), "query": r.get("query")})
        log("FAIL %s %s %s" % (r["verdict"], name, str(r.get("diff") or r.get("error"))[:240]))
    runner.reset()
    total = sum(counts.values())
    refused = counts.get("refused", 0)
    return {"seed": seed, "count": total, "counts": counts, "refusal_rate": round(refused / total, 4) if total else 0, "seconds": int(time.time() - t0), "cases_new": cases_new,
            "distinct_failures": len({f["case"] for f in report_failures if f["case"]}), "failures": report_failures, "flaky": flaky, "features": features,
            "refusals": dict(sorted(refusals.items(), key=lambda x: -x[1])[:40])}


def _safe(runner, spec):
    try:
        return runner.check(spec)
    except Exception as e:  # noqa: BLE001 - one bad reply must not stop a run of thousands
        return {"verdict": "harness", "error": "%s: %s" % (type(e).__name__, str(e)[:400])}


def replay(runner, log=print):
    bad = []
    done = 0
    for name, case in load_cases():
        spec = case["spec"]
        runner.apply(spec.get("zone"), spec.get("week"))
        r = _safe(runner, spec)
        done += 1
        if r["verdict"] in FAILURES:
            bad.append({"case": name, "verdict": r["verdict"], "diff": r.get("diff"), "error": r.get("error")})
            log("FAIL %s %s %s" % (name, r["verdict"], str(r.get("diff") or r.get("error"))[:240]))
    runner.reset()
    return {"cases": done, "failed": bad}


def print_summary(report):
    print("fuzz seed %s: %d queries, %s, refusal rate %.1f%%, %d s" % (report["seed"], report["count"], json.dumps(report["counts"], sort_keys=True), 100 * report["refusal_rate"], report["seconds"]))
    if report["flaky"]:
        print("flaky: %d" % len(report["flaky"]))
    empty = sorted(k for k, v in report["features"].items() if v["pass"] == 0)
    if empty:
        print("no query of these features gave a number: %s" % ", ".join(empty))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default=None)
    ap.add_argument("--count", type=int, default=1000)
    ap.add_argument("--start", type=int, default=0, help="first index of the seed")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--replay", action="store_true", help="only run the saved cases")
    ap.add_argument("--no-reduce", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--show", type=int, help="print the spec, the query and the truth of this index and stop")
    a = ap.parse_args()
    seed = a.seed if a.seed is not None else str(int(time.time()))
    if a.show is not None:
        spec = gen.generate(seed, a.show)
        print(json.dumps(spec, indent=1))
        print(oracle.truth(spec))
        return 0
    os.makedirs(a.out, exist_ok=True)
    mb, ctx = connect()
    runner = Runner(mb, ctx, stock=os.environ.get("CUBE_CLICKHOUSE_FIXES") == "off")
    runner.reset()
    try:
        replayed = replay(runner)
        print("replay: %d cases, %d failed" % (replayed["cases"], len(replayed["failed"])))
        if a.replay:
            return 1 if replayed["failed"] else 0
        report = run_seed(runner, seed, a.count, start=a.start, reduce=not a.no_reduce, save=not a.no_save, workers=a.workers)
        report["replay"] = replayed
    finally:
        runner.reset()
    path = os.path.join(a.out, "report-%s.json" % seed)
    with open(path, "w") as f:
        json.dump(report, f, indent=1, sort_keys=True, default=str)
    print_summary(report)
    print("report: %s" % path)
    return 1 if report["failures"] or replayed["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
