#!/usr/bin/env python3
"""Runs the repro of a claim on Cube, with no Metabase and no driver. See README.md."""
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = os.environ.get("CUBE_VERSION", "v1.8.15")
REST = "http://127.0.0.1:4500"
SECRET = "repro-dev-only-0123456789abcdef0123456789"
DBS = {"ch": ("clickhouse", "clickhouse", "8123"), "pg": ("postgres", "postgres", "5432")}
ALL_CONFIGS = ["ch-on", "ch-off", "pg-on", "pg-off"]


def compose(*args, env=None, stdin=None, check=False, timeout=300):
    full = dict(os.environ, **(env or {}))
    res = subprocess.run(["docker", "compose", "-p", "mcd-repro", "-f", os.path.join(HERE, "docker-compose.yml")] + list(args),
                         input=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=full, cwd=HERE, timeout=timeout)
    if check and res.returncode:
        raise SystemExit(res.stdout)
    return res.stdout


def jwt(secret=SECRET, claims=None):
    enc = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    head = enc(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = enc(json.dumps(dict({"iat": int(time.time()), "exp": int(time.time()) + 3600}, **(claims or {}))).encode())
    sig = enc(hmac.new(secret.encode(), (head + "." + body).encode(), hashlib.sha256).digest())
    return head + "." + body + "." + sig


def http(method, path, body=None, token="good"):
    headers = {"Content-Type": "application/json"}
    if token == "good":
        headers["Authorization"] = jwt()
    elif token == "wrong":
        headers["Authorization"] = jwt("a-wrong-secret-a-wrong-secret-a-wrong-secret")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(REST + path, data=data, headers=headers, method=method), timeout=120) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, repr(e)


def cube_api(path, body):
    for _ in range(60):
        status, text = http("POST", path, body)
        if status == 200 and '"Continue wait"' in text:
            time.sleep(1)
            continue
        return status, text
    return status, text


def psql(script, csv=False):
    args = ["exec", "-T", "-e", "PGPASSWORD=repro", "postgres", "psql", "-h", "cube", "-p", "15432", "-U", "repro", "-d", "repro",
            "-X", "-q", "-A", "-F", " | ", "-P", "footer=off", "-f", "-"]
    return compose(*args, stdin=script).rstrip("\n")


def truth(db, sql):
    if db == "ch":
        return compose("exec", "-T", "clickhouse", "clickhouse-client", "-u", "repro", "--password", "repro", "-d", "repro",
                       "--format", "TSVWithNames", "--multiquery", stdin=sql).rstrip("\n")
    return compose("exec", "-T", "-e", "PGPASSWORD=repro", "postgres", "psql", "-U", "repro", "-d", "repro", "-X", "-q", "-A", "-F", " | ",
                   "-P", "footer=off", "-f", "-", stdin=sql).rstrip("\n")


def seed(claim_dir, db):
    path = os.path.join(claim_dir, "seed.clickhouse.sql" if db == "ch" else "seed.postgres.sql")
    if db == "ch":
        reset = "DROP DATABASE IF EXISTS repro; CREATE DATABASE repro;\n"
    else:
        reset = "DROP SCHEMA IF EXISTS repro CASCADE; CREATE SCHEMA repro;\n"
    text = open(path).read() if os.path.exists(path) else ""
    out = truth(db, reset + text)
    if "rror" in out:
        print("SEED OUTPUT: " + out)


def rows_of_load(text):
    try:
        doc = json.loads(text)
    except ValueError:
        return text
    if "error" in doc:
        return "ERROR " + str(doc["error"])[:600]
    res = doc.get("results") or [doc]
    out = []
    for r in res:
        out.append(json.dumps(r.get("data"), ensure_ascii=False))
        if "total" in doc or "total" in r:
            out.append("total=%s" % r.get("total"))
    return "\n".join(out)


def meta_part(name, extended=False):
    status, text = http("GET", "/cubejs-api/v1/meta" + ("?extended=true" if extended else ""))
    try:
        doc = json.loads(text)
    except ValueError:
        return "HTTP %s %s" % (status, text[:600])
    if "cubes" not in doc:
        return "HTTP %s %s" % (status, text[:800])
    for c in doc["cubes"]:
        if c["name"] == name:
            keep = {k: c.get(k) for k in ("name", "type", "joins", "public", "connectedComponent") if k in c}
            keep["measures"] = [{k: m.get(k) for k in ("name", "type", "public", "isVisible", "aggType") if k in m} for m in c.get("measures", [])]
            keep["dimensions"] = [{k: d.get(k) for k in ("name", "type", "public", "isVisible", "primaryKey") if k in d} for d in c.get("dimensions", [])]
            return json.dumps(keep, ensure_ascii=False)
    return "not in /v1/meta: " + ", ".join(c["name"] for c in doc["cubes"])


def wait_memory(min_mb=1500):
    """Other services run on this host. Do not start a Cube while available memory is low."""
    while True:
        with open("/proc/meminfo") as f:
            info = dict(line.split(":", 1) for line in f)
        available = int(info["MemAvailable"].split()[0]) // 1024
        if available >= min_mb:
            return
        print("waiting: %d MB available, need %d" % (available, min_mb), flush=True)
        time.sleep(30)


def wait_ready():
    for _ in range(120):
        status, _text = http("GET", "/readyz", token=None)
        if status == 200:
            return True
        time.sleep(1)
    return False


def run_step(step, db, emit):
    sid = step["id"]
    emit("## " + sid + (" -- " + step["note"] if step.get("note") else ""))
    if "sql" in step:
        emit("$ psql> " + step["sql"])
        emit(psql(step["sql"] + ";\n"))
        if step.get("explain"):
            emit("$ psql> EXPLAIN " + step["sql"])
            emit(psql("EXPLAIN " + step["sql"] + ";\n")[:1800])
    elif "script" in step:
        emit("$ psql script:\n" + step["script"].rstrip())
        emit(psql(step["script"]))
    elif "truth" in step or "truth_" + db in step:
        sql = step.get("truth_" + db, step.get("truth"))
        emit("$ %s> %s" % ({"ch": "clickhouse", "pg": "postgres"}[db], sql))
        emit(truth(db, sql))
    elif "rest" in step:
        emit("$ POST /cubejs-api/v1/load " + json.dumps(step["rest"]))
        status, text = cube_api("/cubejs-api/v1/load", {"query": step["rest"]})
        emit("HTTP %s %s" % (status, rows_of_load(text)))
        if step.get("rest_sql"):
            _s, text = http("POST", "/cubejs-api/v1/sql", {"query": step["rest"]})
            try:
                sql = json.loads(text)["sql"]["sql"]
                emit("SQL: " + sql[0] + "\nPARAMS: " + json.dumps(sql[1]))
            except (ValueError, KeyError):
                emit("SQL ERROR: " + text[:600])
    elif "meta" in step:
        emit(meta_part(step["meta"], step.get("extended", False)))
    elif "meta_mentions" in step:
        _status, text = http("GET", "/cubejs-api/v1/meta?extended=true")
        emit("$ GET /cubejs-api/v1/meta?extended=true")
        for word in step["meta_mentions"]:
            emit("%s: %d" % (word, text.count(word)))
    elif "http" in step:
        h = step["http"]
        status, text = http(h.get("method", "GET"), h["path"], h.get("body"), h.get("token", "good"))
        emit("$ %s %s token=%s" % (h.get("method", "GET"), h["path"], h.get("token", "good")))
        emit("HTTP %s %s" % (status, text[:500]))
    elif "logs" in step:
        lines = compose("logs", "--no-color", "--no-log-prefix", "cube").splitlines()
        hits = [l for l in lines if re.search(step["logs"], l)]
        emit("$ docker compose logs cube | grep -E %r" % step["logs"])
        for line in hits[-int(step.get("tail", 6)):]:
            emit(re.sub(r"ey[\w-]+\.ey[\w-]+\.[\w-]+", "<JWT>", line)[:600] if step.get("mask") else line[:600])
        emit("matching lines: %d" % len(hits))
    if step.get("sleep"):
        time.sleep(step["sleep"])


def run_claim(claim, only=None):
    claim_dir = os.path.join(HERE, claim)
    spec = json.load(open(os.path.join(claim_dir, "claim.json")))
    os.makedirs(os.path.join(claim_dir, "results"), exist_ok=True)
    for cfg in spec.get("configs", ALL_CONFIGS):
        if only and cfg != only:
            continue
        db, tess = cfg.split("-")
        dbtype, host, port = DBS[db]
        env = {"CLAIM": claim, "DB_TYPE": dbtype, "DB_HOST": host, "DB_PORT": port, "TESSERACT": "true" if tess == "on" else "false", "CUBE_VERSION": VERSION}
        if os.path.exists(os.path.join(claim_dir, "cube.env")):
            env["CUBE_ENV_FILE"] = "./%s/cube.env" % claim
        if os.path.exists(os.path.join(claim_dir, "cube.js")):
            env["CUBE_JS"] = "./%s/cube.js" % claim
        wait_memory()
        compose("up", "-d", "--wait", "clickhouse", "postgres", env=env, check=True)
        seed(claim_dir, db)
        compose("up", "-d", "--force-recreate", "cube", env=env, check=True)
        ready = wait_ready()
        path = os.path.join(claim_dir, "results", "%s-%s.txt" % (VERSION, cfg))
        with open(path, "w") as f:
            def emit(text):
                print(text, flush=True)
                f.write(text + "\n")
            emit("# %s %s %s tesseract=%s ready=%s" % (claim, VERSION, dbtype, tess, ready))
            for step in spec["steps"]:
                if step.get("only") and cfg not in step["only"]:
                    continue
                run_step(step, db, emit)


def main():
    args = sys.argv[1:]
    only = None
    if args[:1] == ["--down"]:
        print(compose("down", "-v", "--remove-orphans"))
        return
    if args[:1] == ["--config"]:
        only = args[1]
        args = args[2:]
    for claim in args:
        run_claim(claim, only)


main()
