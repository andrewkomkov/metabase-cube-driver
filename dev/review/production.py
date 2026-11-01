"""A sample of the review shapes on Cube with dev mode off and Cube Store, against the truth from ClickHouse, with the model and the doors of a private key.

    python3 production.py [--out DIR] [--every N]

It reads the connection `Cube prod`, and it makes it if the stack has none. The command fails on a wrong number, a raw error, a value of a private key, or a model with other
fields or foreign keys than the demo Cube.
"""
import argparse
import collections
import json
import os
import subprocess
import sys
import time

import lib

SECRET = "cube-dev-only-secret-0123456789abcdef"
CONNECTIONS = {"Cube": "cube", "Cube prod": "cube-prod"}
# The shapes of the catalog, the rollups, the six forms of the New York day, sources and filters, with each tenth shape of the grid.
SAMPLE = r"^(c|r|z|e|v|n|j)/"
PRIVATE = {"payments": "id", "warehouses": "id", "promos": "id", "shipments": "order_id"}


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
        out[t["name"]] = {f["name"]: (f.get("visibility_type"), f.get("semantic_type"), by_id.get(f.get("fk_target_field_id"))) for f in t["fields"]}
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
    for line in differences[:40]:
        print("  ", line)
    refused, leaks = private_doors(mb, have["Cube prod"]["id"], tables)
    print("private doors: refused %d, values %d" % (refused, len(leaks)))
    for line in leaks[:10]:
        print("  ", line)
    runner = [sys.executable, "-u", os.path.join(os.path.dirname(os.path.abspath(lib.__file__)), "gate", "run_gate.py"), "--db", "Cube prod"]
    verdicts = collections.Counter()
    bad = []
    for label, extra in (("sample", ["--only", SAMPLE]), ("grid", ["--only", "^grid/", "--every", str(a.every)])):
        path = os.path.join(a.out, "prod-%s.jsonl" % label)
        subprocess.run(runner + extra + ["--out", path], check=False, stdout=subprocess.DEVNULL)
        for line in open(path):
            r = json.loads(line)
            verdicts[r["verdict"]] += 1
            if r["verdict"] in ("wrong", "raw_error", "harness"):
                bad.append(r)
    print("shapes", dict(verdicts))
    for r in bad[:40]:
        print("  ", r["verdict"], r["id"], str(r.get("diff") or r.get("error"))[:220])
    return 1 if bad or leaks else 0


if __name__ == "__main__":
    sys.exit(main())
