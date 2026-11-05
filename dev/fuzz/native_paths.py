"""Helpers of fuzz_native.sh: `connect` makes the three databases of Metabase and prints /v1/meta of the demo Cube, and `summary` prints the numbers of a seed."""
import base64
import glob
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "review"))

import lib  # noqa: E402

SECRET = b"cube-dev-only-secret-0123456789abcdef"
DATABASES = {"Cube": "cube", "Stock": "cube-stock", "Prod": "cube-prod"}


def token():
    encode = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=")  # noqa: E731
    body = encode(b'{"alg":"HS256","typ":"JWT"}') + b"." + encode(json.dumps({"exp": int(time.time()) + 600}).encode())
    return (body + b"." + encode(hmac.new(SECRET, body, hashlib.sha256).digest())).decode()


def connect(cube_url):
    mb = lib.Metabase(lib.MB, lib.PASSWORD, lib.ADMIN).login()
    have = {d["name"]: d for d in mb.api("GET", "/api/database")["data"]}
    for name, host in DATABASES.items():
        if name not in have:
            have[name] = mb.api("POST", "/api/database", {"engine": "cube", "name": name, "details": {
                "host": host, "port": 15432, "dbname": "cube", "user": "cube", "password": "cube-dev-only", "cube-rest-url": "http://%s:4000" % host, "cube-api-secret-value": SECRET.decode()}})
    for name in DATABASES:
        for _ in range(200):
            if mb.api("GET", "/api/database/%d" % have[name]["id"]).get("initial_sync_status") == "complete":
                break
            time.sleep(3)
    request = urllib.request.Request(cube_url + "/cubejs-api/v1/meta?extended=true", headers={"Authorization": token()})
    with urllib.request.urlopen(request, timeout=120) as response:
        print(response.read().decode())


def summary(out, seed):
    for path in sorted(glob.glob(os.path.join(out, "native-*-%s.json" % seed))):
        report = json.load(open(path))
        live = report.get("live", {})
        print(json.dumps({"path": report["path"], "seed": seed, "count": live.get("count"), "counts": live.get("counts"), "flaky": live.get("flaky"), "mutations": report.get("mutations", {}).get("counts"),
                          "offline": {k: report["offline"][k] for k in ("round_trip", "mutations", "crashes")} if report.get("offline") else None}))


def table(*reports):
    """The allow-list as a table of the docs, with the queries of each construct in these reports."""
    import fuzz_native  # noqa: PLC0415 - it reads the source of the grammar

    with open(os.path.join(HERE, "..", "..", "src", "metabase", "driver", "cube", "grammar.clj")) as handle:
        text = handle.read()
    block = text[text.index("(def allow-list"):text.index("(def ^:private ^:dynamic *catalog*")]
    described = dict(re.findall(r"^\s*\{?:([\w-]+/[\w-]+)\s+\"(.*)\"", block, flags=re.M))
    used = {}
    for path in reports:
        for name, count in json.load(open(path)).get("offline", {}).get("used", {}).items():
            used[name] = used.get(name, 0) + count
    assert set(described) == fuzz_native.allow_list()
    print("| Construct | Form | Proof |\n| --- | --- | --- |")
    for name in sorted(described):
        print("| `%s` | %s | %d |" % (name, described[name].replace("|", "\\|"), used.get(name, 0)))


if __name__ == "__main__":
    {"connect": connect, "summary": summary, "table": table}[sys.argv[1]](*sys.argv[2:])
