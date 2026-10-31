"""Entry 8 of the README section "Known wrong numbers": the legacy planner on stock Cube. It also prints what /v1/meta says about a switch dimension, for the question whether the scan can see one."""
import base64
import hashlib
import hmac
import json
import time
import urllib.parse

import lib


def b64(x):
    return base64.urlsafe_b64encode(x).rstrip(b"=").decode()


now = int(time.time())
head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
body = b64(json.dumps({"iat": now, "exp": now + 900}).encode())
sig = b64(hmac.new(b"cube-legacy-dev-only-secret-0123456789", (head + "." + body).encode(), hashlib.sha256).digest())
auth = {"Authorization": head + "." + body + "." + sig}
q = urllib.parse.quote(json.dumps({"measures": ["orders.revenue_trailing_30d"], "order": {"orders.created_at": "asc"},
                                   "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month", "dateRange": ["2025-02-01", "2025-03-31"]}]}))
for _ in range(60):
    st, text, _h = lib.http("GET", "http://127.0.0.1:14402/cubejs-api/v1/load?query=" + q, headers=auth)
    data = json.loads(text) if text.startswith("{") else {}
    if st == 200 and data.get("error") != "Continue wait":
        break
    time.sleep(3)
print("legacy, stock", st, [(r.get("orders.created_at.month"), r.get("orders.revenue_trailing_30d")) for r in data.get("data", [])], data.get("error"))
print("truth", lib.ch("SELECT m, sumIf(o.amount, o.created_at >= addDays(e, -30) AND o.created_at <= e) FROM orders o CROSS JOIN (SELECT toDateTime('2025-02-01') m, toDateTime('2025-02-28 23:59:59') e UNION ALL SELECT toDateTime('2025-03-01'), toDateTime('2025-03-31 23:59:59')) AS w GROUP BY m ORDER BY m"))
st, text, _h = lib.http("GET", "http://127.0.0.1:14402/cubejs-api/v1/sql?query=" + q, headers=auth)
print("legacy sql head", " ".join(json.loads(text)["sql"]["sql"][0].split())[:200])
st, text, _h = lib.http("GET", "http://127.0.0.1:14400/cubejs-api/v1/sql?query=" + q)
print("tesseract sql head", " ".join(json.loads(text)["sql"]["sql"][0].split())[:120])
st, text, _h = lib.http("GET", "http://127.0.0.1:14400/cubejs-api/v1/meta?extended=true")
for c in json.loads(text)["cubes"]:
    if c["name"] == "orders":
        print("meta", [d for d in c["dimensions"] if d["name"] == "orders.display_currency"])
