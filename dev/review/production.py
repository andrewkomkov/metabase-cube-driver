"""A sample of the review shapes on Cube with dev mode off and Cube Store, against the truth from ClickHouse, with the model and the doors of a private key.

    python3 production.py [--out DIR] [--every N]

It reads the connection `Cube prod`, and it makes it if the stack has none. The command fails on a wrong number, a raw error, a value of a private key, or a
foreign key to a private key lost. It prints each other difference of fields and foreign keys from the demo Cube: a cube that inherits a join from a private parent has no
foreign key here, because /v1/meta does not show that parent with dev mode off.
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys
import time

import lib
import release_cases

SECRET = "cube-dev-only-secret-0123456789abcdef"
CONNECTIONS = {"Cube": "cube", "Cube prod": "cube-prod"}
# The shapes of the catalog, the rollups, the six forms of the New York day, sources and filters, with each tenth shape of the grid.
SAMPLE = r"^(c|r|z|e|v|n|j)/"
PRIVATE_KEY_TARGETS = {("stock_items", "warehouse_id"): ("warehouses", "id")}
PRIVATE = {"payments": "id", "warehouses": "id", "promos": "id", "shipments": "order_id"}


def settled(r):
    """Two groups that Metabase on Postgres settled, see docs/review/0.1.0-gate.md: Postgres has two rows for the repeated hour of a clock change, as Cube has, and the ClickHouse truth
    has one. And `x/num/round`, where the ClickHouse truth rounds half away from zero."""
    if r["id"] == "x/num/round":
        return True
    found = re.search(r"rows: got (\d+), truth (\d+)", r.get("diff") or "")
    return bool(re.search(r"^grid/.*/hour/", r["id"]) and found and int(found.group(1)) == int(found.group(2)) + 1)


def connect(mb):
    have = {d["name"]: d for d in mb.api("GET", "/api/database")["data"]}
    for name, host in CONNECTIONS.items():
        if name not in have:
            have[name] = mb.api("POST", "/api/database", {"engine": "cube", "name": name, "details": {
                "host": host, "port": 15432, "dbname": "cube", "user": "cube", "password": "cube-dev-only",
                "cube-rest-url": "http://%s:4000" % host, "cube-api-secret-value": SECRET}})
    for name in CONNECTIONS:
        for _ in range(200):
            if mb.api("GET", "/api/database/%d" % have[name]["id"]).get("initial_sync_status") == "complete":
                break
            time.sleep(3)
    return have


def model(mb, db):
    meta = mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % db)
    by_id = {f["id"]: (t["name"], f["name"]) for t in meta["tables"] for f in t["fields"]}
    out = {}
    for t in meta["tables"]:
        # A semantic type also comes from the analysis after sync, and that ends later, so the compare has only what the metadata step writes.
        out[t["name"]] = {f["name"]: (f.get("visibility_type"), by_id.get(f.get("fk_target_field_id"))) for f in t["fields"]}
    return out, {t["name"]: t["id"] for t in meta["tables"]}


def model_differences(dev, prod):
    out = []
    for table in sorted(set(dev) | set(prod)):
        if table not in prod or table not in dev:
            out.append("table %s only on %s" % (table, "prod" if table in prod else "dev"))
            continue
        for field in sorted(set(dev[table]) | set(prod[table])):
            a, b = dev[table].get(field), prod[table].get(field)
            if a != b:
                out.append("%s.%s dev %s prod %s" % (table, field, a, b))
    return out


def private_doors(mb, db, tables):
    leaks = []
    refused = 0
    for table, name in PRIVATE.items():
        ref = ["field", name, {"base-type": "type/Integer"}]
        tid = tables[table]
        for query in ({"source-table": tid, "fields": [ref], "limit": 3},
                      {"source-table": tid, "aggregation": [["max", ref]]},
                      {"source-table": tid, "aggregation": [["min", ref]]},
                      {"source-table": tid, "breakout": [ref], "aggregation": [["count"]], "limit": 3},
                      {"source-table": tid, "filter": [">", ref, 100000], "aggregation": [["count"]]},
                      {"source-query": {"source-table": tid}, "filter": [">", ref, 100000], "aggregation": [["count"]]}):
            res = mb.api("POST", "/api/dataset", {"database": db, "type": "query", "query": query}, ok=(200, 202, 400, 500))
            if res.get("error"):
                refused += 1
            else:
                leaks.append("%s: %s" % (json.dumps(query)[:120], res["data"]["rows"][:2]))
        res = mb.api("POST", "/api/dataset", {"database": db, "type": "native", "native": {"query": "SELECT MAX(%s) FROM %s" % (name, table)}}, ok=(200, 202, 400, 500))
        if res.get("error"):
            refused += 1
        else:
            leaks.append("native %s: %s" % (table, res["data"]["rows"][:2]))
    return refused, leaks


def run_release_cases(mb, db):
    """dev/review/release_cases.py on this Cube."""
    meta = mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % db)
    tables = {t["name"]: t for t in meta["tables"]}

    def field_id(table, name):
        return next(f["id"] for f in tables[table]["fields"] if f["name"] == name)

    def fk_id(source, target):
        keys = {f["id"] for f in tables[target]["fields"] if f.get("semantic_type") == "type/PK"}
        return next(f["id"] for f in tables[source]["fields"] if f.get("fk_target_field_id") in keys)

    def run_query(body):
        res = mb.api("POST", "/api/dataset", dict(body, database=db), ok=(200, 202, 400, 403, 500))
        error = res.get("error") or res.get("message")
        return (None, str(error)) if error else (res["data"]["rows"], None)

    def execute(query, parameters):
        return run_query(dict({"type": "query", "query": query}, **({"parameters": parameters} if parameters else {})))

    def execute_native(sql):
        return run_query({"type": "native", "native": {"query": sql, "template-tags": {}}})

    def differ(rows, truth, ordered, tol):
        return lib.compare(rows, lib.ch(truth), ordered=ordered, tol=tol)

    cases = release_cases.fraction_cases() + release_cases.join_cases() + release_cases.fan_out_cases() + release_cases.name_reference_cases() + release_cases.list_cases()
    counts, problems = release_cases.run(cases, execute, differ, field_id, fk_id, lambda table: tables[table]["id"])
    native_counts, native_problems = release_cases.run_native(execute_native, differ, dev_mode=False)
    semantic_counts, semantic_problems = release_cases.run_native_semantics(execute_native, differ)
    print("release cases", counts, "native", native_counts, "native semantics", semantic_counts, "problems", len(problems) + len(native_problems) + len(semantic_problems))
    for line in (problems + native_problems + semantic_problems)[:40]:
        print("  ", line)
    return problems + native_problems + semantic_problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.environ.get("PRODUCTION_OUT", "out-production"))
    ap.add_argument("--every", type=int, default=10)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    mb = lib.Metabase().login()
    have = connect(mb)
    dev, _ = model(mb, have["Cube"]["id"])
    prod, tables = model(mb, have["Cube prod"]["id"])
    differences = model_differences(dev, prod)
    print("model differences", len(differences))
    lost = [key for key in PRIVATE_KEY_TARGETS if prod.get(key[0], {}).get(key[1], (None, None))[1] != PRIVATE_KEY_TARGETS[key]]
    print("foreign keys to a private key that are lost", lost)
    for line in differences[:40]:
        print("  ", line)
    refused, leaks = private_doors(mb, have["Cube prod"]["id"], tables)
    print("private doors: refused %d, values %d" % (refused, len(leaks)))
    for line in leaks[:10]:
        print("  ", line)
    release_problems = run_release_cases(mb, have["Cube prod"]["id"])
    runner = [sys.executable, "-u", os.path.join(os.path.dirname(os.path.abspath(lib.__file__)), "gate", "run_gate.py"), "--db", "Cube prod"]
    verdicts = collections.Counter()
    bad = []
    for label, extra in (("sample", ["--only", SAMPLE]), ("grid", ["--only", "^grid/", "--every", str(a.every)])):
        path = os.path.join(a.out, "prod-%s.jsonl" % label)
        subprocess.run(runner + extra + ["--out", path], check=False, stdout=subprocess.DEVNULL)
        for line in open(path):
            r = json.loads(line)
            verdicts[r["verdict"]] += 1
            if r["verdict"] in ("wrong", "raw_error", "harness") and not settled(r):
                bad.append(r)
    print("shapes", dict(verdicts))
    for r in bad[:40]:
        print("  ", r["verdict"], r["id"], str(r.get("diff") or r.get("error"))[:220])
    return 1 if bad or leaks or lost or release_problems else 0


if __name__ == "__main__":
    sys.exit(main())
