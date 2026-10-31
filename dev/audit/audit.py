#!/usr/bin/env python3
"""Runs the audit cases of docs/cube-audit.md on Cube, with no Metabase and no driver. See README.md."""
import argparse
import base64
import hashlib
import hmac
import importlib.util
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

HERE = os.path.dirname(os.path.abspath(__file__))
REST = "http://127.0.0.1:4600"
WIRE = ("127.0.0.1", 4601)
SECRET = "audit-dev-only-0123456789abcdef0123456789"
DBS = {"ch": ("clickhouse", "clickhouse", "8123"), "pg": ("postgres", "postgres", "5432")}
BASE_CONFIGS = ["ch-on", "ch-off", "pg-on", "pg-off"]
DEFAULT_VERSION = "v1.8.15"
DEFAULT_CH = "26.8.36-alpine"
KEEP_IMAGES = ("v1.6.76", "v1.0.0")  # Many defects use these, so one run keeps them until --down.


def compose(*args, env=None, stdin=None, check=False, timeout=600):
    full = dict(os.environ, **(env or {}))
    res = subprocess.run(["docker", "compose", "-p", "mcd-audit", "-f", os.path.join(HERE, "docker-compose.yml")] + list(args),
                         input=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=full, cwd=HERE, timeout=timeout)
    if check and res.returncode:
        raise SystemExit(res.stdout)
    return res.stdout


def wait_memory(min_mb=1500):
    """Other services run on this host. Do not start a set while available memory is low."""
    while True:
        out = subprocess.run(["free", "-m"], stdout=subprocess.PIPE, text=True).stdout.splitlines()
        available = int(out[1].split()[-1])
        if available >= min_mb:
            return
        print("waiting: %d MB available, need %d" % (available, min_mb), flush=True)
        time.sleep(30)


def check_disk(min_gb=15):
    out = subprocess.run(["df", "-BG", "--output=avail", "/"], stdout=subprocess.PIPE, text=True).stdout.splitlines()
    free = int(out[1].strip().rstrip("G"))
    if free < min_gb:
        raise SystemExit("stop: %d GB free on /, need %d" % (free, min_gb))


def jwt():
    enc = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    head = enc(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = enc(json.dumps({"iat": int(time.time()), "exp": int(time.time()) + 3600}).encode())
    sig = enc(hmac.new(SECRET.encode(), (head + "." + body).encode(), hashlib.sha256).digest())
    return head + "." + body + "." + sig


def http(method, path, body=None, auth=True, timeout=120):
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = jwt()
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(REST + path, data=data, headers=headers, method=method), timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, repr(e)


class Wire:
    """The Postgres wire protocol, so a case controls the parameter type OID that a real client sends."""

    def __init__(self):
        self.s = socket.create_connection(WIRE, timeout=120)
        body = struct.pack("!I", 196608)
        for k, v in (("user", "audit"), ("database", "audit")):
            body += k.encode() + b"\0" + v.encode() + b"\0"
        body += b"\0"
        self.s.sendall(struct.pack("!I", len(body) + 4) + body)
        while True:
            t, p = self.read()
            if t == b"R":
                code = struct.unpack("!I", p[:4])[0]
                if code == 3:
                    self.send(b"p", b"audit\0")
                elif code == 5:
                    inner = hashlib.md5(b"auditaudit").hexdigest().encode()
                    self.send(b"p", b"md5" + hashlib.md5(inner + p[4:8]).hexdigest().encode() + b"\0")
            elif t == b"Z":
                return
            elif t == b"E":
                raise RuntimeError(self.err(p))

    def send(self, t, p):
        self.s.sendall(t + struct.pack("!I", len(p) + 4) + p)

    def read(self):
        head = self.recv(5)
        n = struct.unpack("!I", head[1:])[0]
        return head[:1], self.recv(n - 4)

    def recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("connection closed")
            buf += chunk
        return buf

    @staticmethod
    def err(p):
        fields = {}
        for part in p.split(b"\0"):
            if part:
                fields[part[:1].decode()] = part[1:].decode(errors="replace")
        return fields.get("M", "unknown error")

    def collect(self):
        res = {"cols": [], "oids": [], "rows": [], "error": None}
        while True:
            t, p = self.read()
            if t == b"T":
                n = struct.unpack("!H", p[:2])[0]
                i = 2
                res["cols"], res["oids"] = [], []
                for _ in range(n):
                    end = p.index(b"\0", i)
                    res["cols"].append(p[i:end].decode())
                    i = end + 1
                    res["oids"].append(struct.unpack("!I", p[i + 6:i + 10])[0])
                    i += 18
            elif t == b"D":
                n = struct.unpack("!H", p[:2])[0]
                i = 2
                row = []
                for _ in range(n):
                    ln = struct.unpack("!i", p[i:i + 4])[0]
                    i += 4
                    if ln < 0:
                        row.append(None)
                    else:
                        row.append(p[i:i + ln].decode(errors="replace"))
                        i += ln
                res["rows"].append(row)
            elif t == b"E":
                res["error"] = self.err(p)
            elif t == b"Z":
                return res

    def simple(self, sql):
        self.send(b"Q", sql.encode() + b"\0")
        return self.collect()

    def extended(self, sql, params, oids):
        """Parse, Bind, Describe, Execute, Sync, with text parameters."""
        self.send(b"P", b"\0" + sql.encode() + b"\0" + struct.pack("!H", len(oids)) + b"".join(struct.pack("!I", o) for o in oids))
        bind = b"\0\0" + struct.pack("!H", 0) + struct.pack("!H", len(params))
        for v in params:
            if v is None:
                bind += struct.pack("!i", -1)
            else:
                raw = str(v).encode()
                bind += struct.pack("!i", len(raw)) + raw
        bind += struct.pack("!H", 0)
        self.send(b"B", bind)
        self.send(b"D", b"P\0")
        self.send(b"E", b"\0" + struct.pack("!I", 0))
        self.send(b"S", b"")
        return self.collect()

    def close(self):
        try:
            self.send(b"X", b"")
            self.s.close()
        except OSError:
            pass


def shape_of(plan_rows):
    """Reads EXPLAIN. A scan with the wrapper is a pushdown. Any plan node over a plain scan is post-processing."""
    text = "\n".join(str(c) for r in plan_rows for c in r)
    if "CubeScanWrapper" in text or "CubeScanWrappedSql" in text or "WrappedSelect" in text:
        return "pushdown"
    logical = "\n".join(str(r[1]) for r in plan_rows if r and r[0] == "logical_plan") or text
    first = logical.strip().splitlines()[0] if logical.strip() else ""
    if first.startswith("CubeScan"):
        return "regular"
    if "CubeScan" in logical:
        return "post-processing"
    return "no-scan"


def run_wire_case(case):
    """Returns dict with rows, error, shape, oids."""
    out = {"rows": None, "error": None, "shape": None, "oids": None}
    try:
        w = Wire()
    except Exception as e:
        out["error"] = "connect: %s" % e
        return out
    try:
        for stmt in case.get("setup", []):
            w.simple(stmt)
        if "wire" in case:
            res = w.extended(case["wire"]["sql"], case["wire"].get("params", []), case["wire"].get("oids", []))
        else:
            res = w.simple(case["sql"])
        out["rows"], out["error"], out["oids"] = res["rows"], res["error"], res["oids"]
        probe = case.get("explain", case.get("sql"))
        if probe:
            plan = w.simple("EXPLAIN " + probe)
            if plan["error"]:
                out["shape"] = "not-planned"
            else:
                out["shape"] = shape_of(plan["rows"])
                out["plan"] = "\n".join(str(c) for r in plan["rows"] for c in r)[:6000]
    except Exception as e:
        out["error"] = "wire: %s" % e
    finally:
        w.close()
    return out


def run_rest_case(case):
    out = {"rows": None, "error": None, "shape": "rest", "used": None}
    query = case["rest"]
    body = {"query": query}
    cols = case.get("cols") or (query.get("dimensions", []) + query.get("measures", []))
    status, text = 0, ""
    for _ in range(90):
        status, text = http("POST", "/cubejs-api/v1/load", body)
        if status == 200 and '"Continue wait"' in text:
            time.sleep(1)
            continue
        break
    try:
        doc = json.loads(text)
    except ValueError:
        out["error"] = "HTTP %s %s" % (status, text[:600])
        return out
    if "error" in doc:
        out["error"] = "HTTP %s %s" % (status, str(doc["error"])[:600])
    else:
        r = (doc.get("results") or [doc])[0]
        data = r.get("data", [])
        names = cols
        for td in query.get("timeDimensions", []):
            if td.get("granularity") and not case.get("cols"):
                names = [td["dimension"] + "." + td["granularity"]] + names
        out["rows"] = [[row.get(c) for c in names] for row in data]
        out["used"] = sorted((r.get("usedPreAggregations") or {}).keys())
    st, text = http("POST", "/cubejs-api/v1/sql", body)
    try:
        s = json.loads(text)["sql"]
        out["generated"] = (s["sql"][0] + " PARAMS " + json.dumps(s["sql"][1]))[:6000]
    except (ValueError, KeyError, TypeError):
        pass
    return out


def run_direct_case(case, db):
    """SQL on the database itself. It takes no part in Cube, so it tests the claim about the database."""
    out = db_sql(db, case["direct"] + ";\n") if isinstance(case["direct"], str) else db_sql(db, case["direct"][db] + ";\n")
    if re.search(r"(?i)exception|^error", out):
        return {"rows": None, "error": out[:600], "shape": "database"}
    rows = [[None if c == "\\N" else c for c in line.split("\t")] for line in out.splitlines()]
    return {"rows": rows, "error": None, "shape": "database"}


def run_meta_case(case):
    """Lists the members of a cube or view in /v1/meta."""
    status, text = http("GET", "/cubejs-api/v1/meta")
    try:
        for c in json.loads(text)["cubes"]:
            if c["name"] == case["meta"]:
                return {"rows": [[m["name"], m["type"]] for m in c["dimensions"] if m["type"] == case.get("type", "geo")], "error": None, "shape": "meta"}
    except (ValueError, KeyError) as e:
        return {"rows": None, "error": "%s %s" % (e, text[:300]), "shape": "meta"}
    return {"rows": [], "error": None, "shape": "meta"}


def db_sql(db, sql):
    if db == "ch":
        return compose("exec", "-T", "clickhouse", "clickhouse-client", "-u", "audit", "--password", "audit", "-d", "audit",
                       "--format", "TSV", "--multiquery", stdin=sql).rstrip("\n")
    return compose("exec", "-T", "-e", "PGPASSWORD=audit", "postgres", "psql", "-U", "audit", "-d", "audit", "-X", "-q", "-A", "-t",
                   "-F", "\t", "-P", "null=\\N", "-v", "ON_ERROR_STOP=1", "-f", "-", stdin=sql).rstrip("\n")


def seed(spec, db):
    if db == "ch":
        reset = "DROP DATABASE IF EXISTS audit; CREATE DATABASE audit; DROP DATABASE IF EXISTS dev_pre_aggregations;\n"
        text = spec.SEED_CH
    else:
        reset = "DROP SCHEMA IF EXISTS audit CASCADE; CREATE SCHEMA audit; DROP SCHEMA IF EXISTS dev_pre_aggregations CASCADE;\n"
        text = spec.SEED_PG
    probe = {"ch": "SELECT count() FROM system.tables WHERE database = 'audit';\n",
             "pg": "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'audit';\n"}[db]
    # The ClickHouse image first runs a temporary server, which can pass the health check and then stop.
    for _ in range(20):
        out = db_sql(db, reset + text)
        time.sleep(2)
        check = db_sql(db, probe).strip()
        if check.isdigit() and int(check) > 0:
            if re.search(r"(?i)exception|error", out):
                print("SEED OUTPUT: " + out, flush=True)
            return
        time.sleep(3)
    raise SystemExit("seed failed: " + out[:500])


def wait_ready():
    for _ in range(180):
        status, _ = http("GET", "/readyz", auth=False, timeout=5)
        if status == 200:
            return True
        time.sleep(1)
    return False


def norm(v):
    if v is None:
        return None
    s = str(v)
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(\.\d+)?(Z|\+00(:00)?)?", s)
    if m:
        frac = (m.group(3) or "").rstrip("0")
        return m.group(1) + " " + m.group(2) + ("" if frac == "." else frac)
    try:
        d = Decimal(s)
        if d.is_finite():
            d = d.normalize()
            return format(d, "f")
    except InvalidOperation:
        pass
    return s


def norm_rows(rows):
    return None if rows is None else [[norm(c) for c in r] for r in rows]


def expected_for(case, db):
    exp = case.get("expected")
    if isinstance(exp, dict):
        exp = exp.get(db)
    return exp


def judge(case, db, res):
    """match: the run gives the derived expectation. diff: it does not."""
    err_exp = case.get("expected_error")
    if isinstance(err_exp, dict):
        err_exp = err_exp.get(db)
    if err_exp is not None:
        return "match" if res["error"] and err_exp in res["error"] else "diff"
    if res["error"]:
        return "diff"
    got, want = norm_rows(res["rows"]), norm_rows(expected_for(case, db))
    if case.get("sorted_rows") and got is not None:
        key = lambda r: [str(c) for c in r]
        got, want = sorted(got, key=key), sorted(want, key=key)
    return "match" if got == want else "diff"


def truth_check(case, db):
    t = case.get("truth")
    if isinstance(t, dict):
        t = t.get(db)
    if not t:
        return None
    return db_sql(db, t + ";\n")


def load_spec(defect):
    path = os.path.join(HERE, "cases", defect + ".py")
    sp = importlib.util.spec_from_file_location("cases_" + defect, path)
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod


def write_model(spec):
    work = os.path.join(HERE, ".work", "model")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    os.makedirs(os.path.dirname(work), exist_ok=True)
    with open(os.path.join(work, "model.yml"), "w") as f:
        f.write(spec.MODEL)


def config_env(cfg, version):
    """A config is db-planner with optional suffixes: +ch=TAG for a ClickHouse version, +nopush for CUBESQL_SQL_PUSH_DOWN=false."""
    parts = cfg.split("+")
    db, tess = parts[0].split("-")
    dbtype, host, port = DBS[db]
    env = {"DB_TYPE": dbtype, "DB_HOST": host, "DB_PORT": port, "TESSERACT": {"on": "true", "off": "false", "def": ""}[tess],
           "CUBE_VERSION": version, "CLICKHOUSE_VERSION": DEFAULT_CH, "SQL_PUSH_DOWN": "true"}
    for p in parts[1:]:
        if p.startswith("ch="):
            env["CLICKHOUSE_VERSION"] = p[3:]
        elif p == "nopush":
            env["SQL_PUSH_DOWN"] = "false"
    return db, env


def run_config(defect, spec, cfg, version, only=None):
    db, env = config_env(cfg, version)
    wait_memory()
    check_disk()
    write_model(spec)
    with open(os.path.join(HERE, ".work", "planner.env"), "w") as f:
        if env["TESSERACT"]:
            f.write("CUBEJS_TESSERACT_SQL_PLANNER=%s\n" % env["TESSERACT"])
    # A ClickHouse data directory does not open on an older server, so a change of version starts from an empty volume.
    image = subprocess.run(["docker", "ps", "-a", "--filter", "name=^mcd-audit-clickhouse-1$", "--format", "{{.Image}}"], stdout=subprocess.PIPE, text=True).stdout.strip()
    if image and not image.endswith(":" + env["CLICKHOUSE_VERSION"]):
        compose("rm", "-s", "-f", "-v", "clickhouse")
    compose("up", "-d", "--wait", "clickhouse", "postgres", env=env, check=True)
    seed(spec, db)
    compose("up", "-d", "--force-recreate", "cube", env=env, check=True)
    ready = wait_ready()
    results = []
    for case in spec.CASES:
        if only and case["id"] not in only:
            continue
        allowed = case.get("configs")
        if allowed and cfg not in allowed:
            continue
        skip = case.get("skip")
        if skip and db in skip:
            continue
        if not ready:
            res = {"rows": None, "error": "cube not ready", "shape": None}
        elif "rest" in case:
            res = run_rest_case(case)
        elif "direct" in case:
            res = run_direct_case(case, db)
        elif "meta" in case:
            res = run_meta_case(case)
        else:
            res = run_wire_case(case)
        verdict = judge(case, db, res)
        res["verdict"] = verdict
        res["truth"] = truth_check(case, db)
        res["id"] = case["id"]
        results.append(res)
        shown = res["error"] if res["error"] else json.dumps(res["rows"])
        print("  %-34s %-5s %-16s %s" % (case["id"], verdict, res.get("shape") or "", (shown or "")[:170]), flush=True)
    return results


def results_path(defect, version, cfg):
    safe = cfg.replace("+", "_").replace("=", "-")
    return os.path.join(HERE, "results", defect, "%s--%s.json" % (version, safe))


def run_defect(defect, configs, version, only=None):
    spec = load_spec(defect)
    use = configs or (getattr(spec, "CONFIGS", BASE_CONFIGS) + getattr(spec, "EXTRA_CONFIGS", []))
    for cfg in use:
        print("== %s %s %s" % (defect, version, cfg), flush=True)
        results = run_config(defect, spec, cfg, version, only)
        path = results_path(defect, version, cfg)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        previous = {}
        if only and os.path.exists(path):
            previous = {r["id"]: r for r in json.load(open(path))}
        for r in results:
            previous[r["id"]] = r
        ordered = [previous[c["id"]] for c in spec.CASES if c["id"] in previous]
        with open(path, "w") as f:
            json.dump(ordered, f, indent=1, ensure_ascii=False)


def pulled_list():
    return os.path.join(HERE, ".work", "pulled-images.txt")


def note_pull(tag):
    """Remembers an image that this tool pulled, so that --down removes only those."""
    image = "cubejs/cube:" + tag
    have = subprocess.run(["docker", "image", "inspect", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not have:
        with open(pulled_list(), "a") as f:
            f.write(image + "\n")


def drop_pulled(keep=()):
    path = pulled_list()
    if not os.path.exists(path):
        return
    names = sorted(set(open(path).read().split()))
    left = []
    for image in names:
        if image.split(":")[1] in keep:
            left.append(image)
            continue
        subprocess.run(["docker", "image", "rm", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(path, "w") as f:
        f.write("\n".join(left) + ("\n" if left else ""))


def run_tags(defect, tags=None, configs=None):
    """Runs the TAG_CASES of a defect on older images, to find where a behaviour began."""
    spec = load_spec(defect)
    wanted = tags or getattr(spec, "TAGS", [])
    cfgs = configs or getattr(spec, "TAG_CONFIGS", ["pg-on", "ch-on"])
    for tag in wanted:
        note_pull(tag)
        for cfg in cfgs:
            print("== %s %s %s" % (defect, tag, cfg), flush=True)
            try:
                results = run_config(defect, spec, cfg, tag, getattr(spec, "TAG_CASES", None))
            except SystemExit as e:
                print("  skipped: %s" % str(e)[-300:], flush=True)
                results = [{"id": "image", "rows": None, "error": "stack did not start: " + str(e)[-200:], "shape": None, "verdict": "diff"}]
            path = results_path(defect, tag, cfg)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(results, f, indent=1, ensure_ascii=False)
        compose("rm", "-s", "-f", "-v", "cube", env={"CUBE_VERSION": tag})
        drop_pulled(keep=KEEP_IMAGES)


def bisect(defect, case_id, cfg, low, high):
    """Finds the first tag where a case stops to match. low must match and high must differ."""
    spec = load_spec(defect)
    with open(os.path.join(HERE, "tags.txt")) as f:
        tags = f.read().split()
    lo, hi = tags.index(low), tags.index(high)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        tag = tags[mid]
        note_pull(tag)
        print("== bisect %s %s %s" % (defect, tag, cfg), flush=True)
        try:
            results = run_config(defect, spec, cfg, tag, [case_id])
        except SystemExit as e:
            # Some tags have no image on Docker Hub.
            print("  skipped: %s" % str(e)[-200:], flush=True)
            tags.pop(mid)
            hi -= 1
            continue
        path = results_path(defect, tag, cfg)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(results, f, indent=1, ensure_ascii=False)
        compose("rm", "-s", "-f", "-v", "cube", env={"CUBE_VERSION": tag})
        drop_pulled(keep=KEEP_IMAGES)
        if results[0]["verdict"] == "match":
            lo = mid
        else:
            hi = mid
    print("first tag that differs: %s, last that matches: %s" % (tags[hi], tags[lo]), flush=True)


def all_defects():
    return sorted(n[:-3] for n in os.listdir(os.path.join(HERE, "cases")) if n.endswith(".py"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("defects", nargs="*", help="defect ids from cases/, or 'all'")
    ap.add_argument("--config", action="append", help="for example ch-on, pg-off, ch-on+ch=25.3-alpine, ch-on+nopush")
    ap.add_argument("--version", default=DEFAULT_VERSION, help="Cube image tag")
    ap.add_argument("--case", action="append", help="one case id")
    ap.add_argument("--tags", action="store_true", help="also run TAG_CASES on the TAGS of the defect")
    ap.add_argument("--tag", action="append", help="older Cube tags to run with --tags")
    ap.add_argument("--only-tags", action="store_true", help="run only the older tags")
    ap.add_argument("--bisect", nargs=2, metavar=("LOW", "HIGH"), help="with one defect, --case and --config: finds the first tag that differs")
    ap.add_argument("--down", action="store_true", help="remove the stack and its volumes")
    args = ap.parse_args()
    if args.down:
        print(compose("down", "-v", "--remove-orphans"))
        drop_pulled()
        return
    if args.bisect:
        bisect(args.defects[0], args.case[0], args.config[0], args.bisect[0], args.bisect[1])
        return
    defects = all_defects() if args.defects == ["all"] else args.defects
    for d in defects:
        if not args.only_tags:
            run_defect(d, args.config, args.version, args.case)
        if args.tags or args.only_tags:
            run_tags(d, args.tag, args.config)


if __name__ == "__main__":
    main()
