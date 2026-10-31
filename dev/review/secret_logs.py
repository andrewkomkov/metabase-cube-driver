"""Broken connections through the real validate endpoint, then a read of each log for a secret or a JWT. It also compares a truncated MCP page on Cube and on Sample Database."""
import json
import re
import subprocess

import lib

mb = lib.Metabase().login()
SECRET = "cube-secure-dev-only-secret-0123456789"
SQLPW = "cube-secure-dev-only"
BASE = {"host": "cube-secure", "port": 15432, "dbname": "cube", "user": "metabase", "password": SQLPW, "cube-rest-url": "http://cube-secure:4000", "cube-api-secret-value": SECRET}
CASES = {"wrong SQL password": {"password": "wrong-dev-only-password"}, "no such REST host": {"cube-rest-url": "http://no-such-host:4000"}, "wrong API secret": {"cube-api-secret-value": "wrong-dev-only-secret"}}
for name, change in CASES.items():
    details = dict(BASE, **change)
    st, text, _ = lib.http("POST", mb.url + "/api/database/validate", {"details": {"engine": "cube", "details": details}}, {"X-Metabase-Session": mb.sid})
    data = json.loads(text)
    print("validate", name, "HTTP", st, "valid", data.get("valid"), "message", str(data.get("message") or data.get("errors"))[:180])
    # The body repeats the request of the admin. This is documented, so only the other fields count.
    rest = {k: v for k, v in data.items() if k != "details"}
    print("   secret in the rest of the response:", [w for w in (SECRET, SQLPW, "wrong-dev-only-secret", "wrong-dev-only-password") if w in json.dumps(rest)])
for name in ("metabase", "cube", "cube-stock", "cube-secure"):
    log = subprocess.run(["docker", "logs", "mcd-review-%s-1" % name], capture_output=True, text=True)
    text = log.stdout + log.stderr
    hits = [w for w in (SECRET, "cube-dev-only-secret-0123456789abcdef", "wrong-dev-only-secret", "wrong-dev-only-password", SQLPW) if w in text]
    jwt = re.findall(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", text)
    print("log", name, "secrets", hits, "jwt", len(jwt))

# A second page with no row_limit, on an aggregation with more than 2100 rows.
plain = lib.Metabase("http://127.0.0.1:13301").login()
meta = plain.api("GET", "/api/database/1/metadata")
orders = [t for t in meta["tables"] if t["name"].lower() == "orders"][0]
col = [f["id"] for f in orders["fields"] if f["name"].lower() == "created_at"][0]
q = {"lib/type": "mbql/query", "database": 1, "stages": [{"lib/type": "mbql.stage/mbql", "source-table": orders["id"], "aggregation": [["count", {}]], "breakout": [["field", {"temporal-unit": "hour"}, col]]}]}
c = lib.Mcp(plain)
res, err = c.call("execute_query", query=q, row_limit=2000)
print("sample page 1", len(res["rows"]), res.get("truncated"), bool(res.get("next_cursor")), err)
res, err = c.call("execute_query", cursor=res["next_cursor"])
print("sample page 2, no row_limit", len(res["rows"]), res.get("truncated"), bool(res.get("next_cursor")), err)
