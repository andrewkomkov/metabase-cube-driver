"""`make fuzz-native SEED=n COUNT=n`: the generator of the grammar of native SQL, on one path of Cube. See README.md.

For each seed: COUNT queries from `native_grammar.generate`, each compared with a truth in direct ClickHouse SQL, and COUNT/10 mutations from `native_grammar.mutate`, each of which must refuse. A real parser, libpg_query through pglast,
prints each text again, and the printed text must have the same answer from the grammar. The report goes to dev/fuzz/out/native-<path>-<seed>.json. Exit code 0 means no wrong number, no raw error, no refusal of a query on the list with no report time zone, and no mutation that ran.
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "review"))

import known_limits  # noqa: E402
import lib  # noqa: E402
import native_grammar as grammar  # noqa: E402

FAILURES = ("wrong", "raw_error", "truth_error", "harness", "refused_on_the_list", "mutation_ran", "leak")
GRAMMAR_REFUSAL = re.compile(r"^Native SQL on Cube:|private member|is private in the Cube data model")
ADMIN = os.environ.get("REVIEW_ADMIN", "admin@example.com")
PASSWORD = os.environ.get("REVIEW_PASSWORD", "fuzz-dev-only-1")
# A refusal of a query on the list that holds for a real reason of Cube, not of the grammar: the driver sees this from the data model or from Cube, and it says what to do.
SEMANTIC_REFUSAL = re.compile(r"join paths|rolling window|time shift|FILTER_PARAMS|counts the primary key|too long for the planner")


def round_half(sql, rows, want):
    """True if each cell that differs is a ROUND that is one unit of its last digit away, in a query with ROUND of an aggregation."""
    if not re.search(r"ROUND\((AVG|SUM|MIN|MAX|STDDEV\w*)\(", sql, re.I) or len(rows) != len(want):
        return False
    differ = 0
    for a, b in zip(sorted(rows, key=lib.key), sorted(want, key=lib.key)):
        for x, y in zip(a, b):
            if lib.same(x, y, 1e-6):
                continue
            try:
                gap = abs(float(x) - float(y))
            except (TypeError, ValueError):
                return False
            if not any(abs(gap - 10 ** -k) < 10 ** -k * 1e-6 for k in range(0, 7)):
                return False
            differ += 1
    return differ > 0


class Runner:
    def __init__(self, mb, db, stock):
        self.mb, self.db, self.stock = mb, db, stock
        self.zone = "unset"
        self.fields = {}
        self.lock = threading.Lock()

    def apply_zone(self, zone):
        if self.zone != zone:
            self.mb.api("PUT", "/api/setting/report-timezone", {"value": zone})
            self.zone = zone

    def field_id(self, table, column):
        with self.lock:
            if not self.fields:
                meta = self.mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % self.db)
                for t in meta["tables"]:
                    for f in t["fields"]:
                        self.fields[(t["name"].lower(), f["name"].lower())] = f["id"]
            return self.fields[(table, column)]

    def body(self, spec):
        native = {"query": spec["sql"], "template-tags": {}}
        out = {"database": self.db, "type": "native", "native": native}
        for name, tag in (spec.get("tags") or {}).items():
            tag = dict(tag)
            field = tag.pop("field", None)
            if field:
                tag["dimension"] = ["field", self.field_id(*field), None]
            native["template-tags"][name] = tag
        if spec.get("parameters") is not None:
            out["parameters"] = spec["parameters"]
        return out

    def run(self, spec):
        res = self.mb.api("POST", "/api/dataset", self.body(spec), ok=(200, 202, 400, 403, 500))
        if isinstance(res, str):
            return None, None, res
        if res.get("error") or res.get("status") == "failed":
            return None, None, str(res.get("error") or res.get("message") or res.get("error_type"))
        if "data" not in res:
            return None, None, json.dumps(res)[:400]
        return res["data"]["rows"], res["data"]["cols"], None

    def judge(self, spec):
        out = {"query": spec["sql"], "truth_sql": spec.get("truth"), "zone": spec.get("zone")}
        rows, cols, err = self.run(spec)
        if err is not None:
            out["error"] = err[:700]
            text = err.split(" Cube said:")[0]
            if not known_limits.clear_error(text) or "Connection" in err or "connection" in err:
                return dict(out, verdict="raw_error")
            if "Cube said:" in err:
                # The planner of Cube has no plan for this form. The driver names it. The report counts these, because each one is a form to read or to take off the list.
                return dict(out, verdict="planner")
            if GRAMMAR_REFUSAL.search(text) and not spec.get("zone") and not SEMANTIC_REFUSAL.search(text) and not (self.stock and "stock Cube" in text):
                return dict(out, verdict="refused_on_the_list")
            return dict(out, verdict="refused")
        names = [c["name"].lower() for c in cols]
        if spec.get("forbid") and set(spec["forbid"]) & set(names):
            return dict(out, verdict="leak", diff="a private column in the result: %s" % names)
        if spec.get("columns"):
            try:
                rows = [[row[names.index(c)] for c in spec["columns"]] for row in rows]
            except ValueError:
                return dict(out, verdict="wrong", diff="the columns are %s" % names)
        try:
            want = lib.ch(spec["truth"])
        except RuntimeError as e:
            return dict(out, verdict="truth_error", error=str(e)[:700])
        tol = spec.get("tol", 1e-6)
        diff = lib.compare(rows, want, ordered=spec.get("ordered", False), tol=tol)
        if diff is None:
            return dict(out, verdict="pass", rows=len(rows))
        if self.stock and grammar.NO_ROWS in spec["truth"]:
            plain = lib.ch(spec["truth"].replace(grammar.NO_ROWS, ""))
            same_cells = len(rows) == len(want) == len(plain) and all(all(lib.same(g, w, tol) or lib.same(g, p, tol) for g, w, p in zip(a, b, c)) for a, b, c in zip(*[sorted(x, key=lib.key) for x in (rows, want, plain)]))
            # An expression of an aggregation of no rows, such as MAX(x - 1) over 0 rows, is that value of ClickHouse with the expression on it, where the truth is NULL.
            # A UNION ALL of aggregations has one such row for each part, in the order of the text.
            no_rows = len(rows) == len(want) and len(rows) <= 4 and all(len(g) == len(w) and all(a is None or lib.same(x, a, tol) for x, a in zip(g, w)) for g, w in zip(rows, want))
            if same_cells or no_rows or lib.compare(rows, plain, ordered=spec.get("ordered", False), tol=tol) is None:
                return dict(out, verdict="stock_no_rows")
        if self.stock and "ts64_berlin" in spec["truth"]:
            # A filter on a DateTime64 that is not a date range: stock Cube compares the column at whole seconds. See "Known wrong numbers" in docs/query-compilation.md.
            seconds = lib.ch(spec["truth"].replace("ts64_berlin", "toStartOfSecond(ts64_berlin)"))
            if lib.compare(rows, seconds, ordered=spec.get("ordered", False), tol=tol) is None:
                return dict(out, verdict="stock_datetime64_second")
        if round_half(spec["sql"], rows, want):
            # ROUND of an aggregation that lands on a half: Cube rounds the Double above the database, and the Double of 125.025 is below the half. See "Known wrong numbers" in
            # docs/query-compilation.md.
            return dict(out, verdict="round_half_double", diff=diff[:300])
        return dict(out, verdict="wrong", diff=diff[:800], got=rows[:4], want=want[:4])

    def check(self, spec):
        r = self.judge(spec)
        if r["verdict"] in ("wrong", "raw_error"):
            # A failure must come back, because a query can meet a restart or a refresh.
            time.sleep(0.3)
            again = self.judge(spec)
            if again["verdict"] != r["verdict"]:
                return dict(again, flaky=r["verdict"], flaky_error=str(r.get("error") or r.get("diff"))[:300])
        return r

    def check_mutation(self, spec):
        rows, _cols, err = self.run(spec)
        out = {"query": spec["sql"], "construct": spec["construct"]}
        if err is None:
            return dict(out, verdict="mutation_ran", got=rows[:2])
        text = err.split(" Cube said:")[0]
        if "Cube said:" in err or not known_limits.clear_error(text):
            return dict(out, verdict="mutation_ran", error=err[:400])
        return dict(out, verdict="refused", error=text[:300])


def run_zone_cases(runner, zone, log):
    """Each shape of a time under a report time zone. Each one gives the truth or a clear refusal."""
    counts = {}
    failures = []
    runner.apply_zone(zone)
    for spec in grammar.zone_cases(zone):
        r = runner.check(spec)
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        if r["verdict"] in FAILURES:
            failures.append(r)
            log("ZONE FAIL %s %s\n  %s\n  %s" % (zone, r["verdict"], r["query"], str(r.get("diff") or r.get("error"))[:300]))
    runner.apply_zone(None)
    return {"counts": counts, "failures": failures}


def real_parser_print(sql):
    """The text of a real PostgreSQL parser, or None if it cannot read this text."""
    import pglast  # A dependency of this check only: dev/fuzz/requirements.txt.
    try:
        return pglast.prettify(sql)
    except Exception:  # noqa: BLE001 - PostgreSQL has no such syntax, or the printer has no form of it
        return None


def admit_all(queries, variant, zone, admit_cmd):
    """The answer of the grammar for each text, with no Cube."""
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", dir="/tmp", delete=False) as handle:
        for sql in queries:
            handle.write(json.dumps(sql) + "\n")
        path = handle.name
    command = [part.replace("{queries}", path).replace("{variant}", variant).replace("{zone}", zone or "") for part in admit_cmd]
    command = [part for part in command if part]
    out = subprocess.run(command, capture_output=True, text=True, timeout=1200)
    os.unlink(path)
    lines = [line for line in out.stdout.splitlines() if line.startswith("{")]
    if len(lines) != len(queries):
        raise RuntimeError("admit: %d answers for %d texts: %s" % (len(lines), len(queries), (out.stderr or out.stdout)[-800:]))
    return [json.loads(line) for line in lines]


def run_permanent(runner, log):
    """The permanent cases of native_grammar.permanent_cases."""
    counts = {}
    failures = []
    runner.apply_zone(None)
    for spec in grammar.permanent_cases():
        if spec["truth"] is None:
            r = runner.check_mutation(dict(spec, construct="a permanent case that must refuse"))
            r["verdict"] = "refused" if r["verdict"] == "refused" else "mutation_ran"
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
            if r["verdict"] != "refused":
                failures.append(r)
                log("PERMANENT FAIL %s\n  %s" % (r["verdict"], r["query"]))
            continue
        r = runner.check(spec)
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        if r["verdict"] in FAILURES or r["verdict"] in ("refused", "planner"):
            failures.append(r)
            log("PERMANENT FAIL %s\n  %s\n  %s" % (r["verdict"], r["query"], str(r.get("diff") or r.get("error"))[:300]))
    log(json.dumps({"permanent": counts}))
    return {"counts": counts, "failures": failures}


def run_seed(runner, seed, count, workers, log):
    counts = {}
    refusals = {}
    failures = []
    flaky = 0
    t0 = time.time()
    specs = [grammar.generate(seed, i) for i in range(count)]
    index = 0
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        while index < count:
            end = min((index // grammar.BLOCK + 1) * grammar.BLOCK, count)
            block = specs[index:end]
            runner.apply_zone(block[0]["zone"])
            for spec, r in zip(block, pool.map(runner.check, block)):
                v = r["verdict"]
                counts[v] = counts.get(v, 0) + 1
                flaky += 1 if r.get("flaky") else 0
                if v in ("refused", "planner"):
                    key = re.sub(r"'[^']*'|\d+", "#", r.get("error", ""))[:110]
                    refusals[key] = refusals.get(key, 0) + 1
                if v == "planner":
                    log("PLANNER zone=%s\n  %s\n  %s" % (r.get("zone"), r["query"], str(r.get("error"))[:300]))
                if v in FAILURES:
                    failures.append(r)
                    log("FAIL %s zone=%s %s\n  %s\n  truth %s\n  %s" % (v, r.get("zone"), "", r["query"], r.get("truth_sql"), str(r.get("diff") or r.get("error"))[:400]))
            index = end
            if index % (grammar.BLOCK * 10) == 0 or index == count:
                log("%d/%d %s %ds" % (index, count, counts, time.time() - t0))
    runner.apply_zone(None)
    return {"count": count, "counts": counts, "flaky": flaky, "failures": failures, "refusals": dict(sorted(refusals.items(), key=lambda x: -x[1])[:30]), "seconds": int(time.time() - t0)}


def run_mutations(runner, seed, count, workers, log):
    counts = {}
    failures = []
    specs = [grammar.mutate(seed, i) for i in range(count)]
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        for r in pool.map(runner.check_mutation, specs):
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
            if r["verdict"] in FAILURES:
                failures.append(r)
                log("MUTATION RAN %s\n  %s" % (r.get("construct"), r["query"]))
    return {"count": count, "counts": counts, "failures": failures}


def allow_list():
    """The names of the constructs of the allow-list, from the source of the grammar."""
    with open(os.path.join(HERE, "..", "..", "src", "metabase", "driver", "cube", "grammar.clj")) as handle:
        text = handle.read()
    block = text[text.index("(def allow-list"):text.index("(def ^:private ^:dynamic *catalog*")]
    return set(re.findall(r"^\s*\{?:([\w-]+/[\w-]+)\s+\"", block, flags=re.M))


def offline(seed, count, mutations, variant, admit_cmd, log):
    """The grammar with no Cube: a text and its print from a real parser have one answer, each mutation refuses, and each construct has a query."""
    specs = [grammar.generate(seed, i) for i in range(count)]
    # Parameters reach the grammar as literals, so these texts stay out. Each other text is on its own.
    plain = [s["sql"] for s in specs if not s.get("tags")]
    muts = [grammar.mutate(seed, i) for i in range(mutations)]
    answers = admit_all(plain, variant, None, admit_cmd)
    used = {"param/value": sum(1 for s in specs if s.get("tags"))}
    for answer in answers:
        for construct in answer.get("used", []):
            used[construct] = used.get(construct, 0) + 1
    printed = [(sql, real_parser_print(sql)) for sql in plain + [m["sql"] for m in muts]]
    pairs = [(a, b) for a, b in printed if b]
    first = admit_all([a for a, _ in pairs], variant, None, admit_cmd)
    second = admit_all([b for _, b in pairs], variant, None, admit_cmd)
    differ = [{"text": a, "print": b, "first": x.get("admitted"), "second": y.get("admitted"), "why": y.get("message") or x.get("message")} for (a, b), x, y in zip(pairs, first, second) if bool(x.get("admitted")) != bool(y.get("admitted"))]
    ran = admit_all([m["sql"] for m in muts], variant, None, admit_cmd)
    admitted_mutations = [{"text": m["sql"], "construct": m["construct"]} for m, a in zip(muts, ran) if a.get("admitted")]
    crashes = [a for a in answers + ran + first + second if a.get("crash")]
    for d in differ[:20]:
        log("ROUND TRIP %s -> %s: %s\n  %s\n  %s\n  %s" % (d["first"], d["second"], d["why"], d["text"], d["print"], ""))
    for d in admitted_mutations[:20]:
        log("ADMITTED MUTATION %s\n  %s" % (d["construct"], d["text"]))
    for d in crashes[:5]:
        log("CRASH %s" % d.get("message"))
    return {"texts": len(plain), "round_trip": {"checked": len(pairs), "differ": len(differ), "unprinted": len(printed) - len(pairs)}, "differ": differ[:50],
            "mutations": {"count": len(muts), "admitted": len(admitted_mutations)}, "admitted_mutations": admitted_mutations[:50], "crashes": len(crashes), "used": dict(sorted(used.items())),
            "not_proven": sorted(allow_list() - set(used))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", default=str(int(time.time())))
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--mutations", type=int, default=None)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--db", default="Cube")
    parser.add_argument("--stock", action="store_true")
    parser.add_argument("--path", default=None, help="The name of the path in the report.")
    parser.add_argument("--offline", action="store_true", help="Only the checks with no Cube.")
    parser.add_argument("--no-offline", action="store_true")
    parser.add_argument("--admit", default=os.environ.get("NATIVE_ADMIT"), help="The command of admit.clj, with {queries}, {variant} and {zone}.")
    args = parser.parse_args()
    log = lambda text: print(text, flush=True)  # noqa: E731
    mutations = args.mutations if args.mutations is not None else max(100, args.count // 10)
    report = {"seed": args.seed, "path": args.path or args.db}
    if args.admit and not args.no_offline:
        report["offline"] = offline(args.seed, args.count, mutations * 5, "stock" if args.stock else "dialect", args.admit.split(), log)
        log(json.dumps({k: v for k, v in report["offline"].items() if k not in ("differ", "admitted_mutations")}))
    if not args.offline:
        mb = lib.Metabase(lib.MB, PASSWORD, ADMIN).login()
        db = {d["name"]: d["id"] for d in mb.api("GET", "/api/database")["data"]}[args.db]
        runner = Runner(mb, db, args.stock)
        report["permanent"] = run_permanent(runner, log)
        report["live"] = run_seed(runner, args.seed, args.count, args.workers, log)
        report["mutations"] = run_mutations(runner, args.seed, mutations, args.workers, log)
        report["zones"] = {zone: run_zone_cases(runner, zone, log) for zone in ("America/New_York", "Asia/Kathmandu")}
        log(json.dumps({zone: result["counts"] for zone, result in report["zones"].items()}))
        log(json.dumps({"counts": report["live"]["counts"], "flaky": report["live"]["flaky"], "mutations": report["mutations"]["counts"], "refusals": report["live"]["refusals"]}, indent=0))
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    with open(os.path.join(HERE, "out", "native-%s-%s.json" % (report["path"], args.seed)), "w") as handle:
        json.dump(report, handle, indent=1, default=str)
    bad = len(report.get("live", {}).get("failures", [])) + len(report.get("mutations", {}).get("failures", []))
    bad += sum(len(result["failures"]) for result in report.get("zones", {}).values())
    bad += len(report.get("permanent", {}).get("failures", []))
    off = report.get("offline")
    if off:
        bad += off["round_trip"]["differ"] + off["mutations"]["admitted"] + off["crashes"] + len(off["not_proven"])
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
