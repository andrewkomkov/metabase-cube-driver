"""What Cube gives for a time measure on a DateTime64, on REST and on the SQL it writes."""
import base64
import json
import urllib.parse
import urllib.request


def jwt():
    enc = lambda x: base64.urlsafe_b64encode(json.dumps(x).encode()).rstrip(b"=").decode()  # noqa: E731
    return enc({"alg": "none", "typ": "JWT"}) + "." + enc({"sub": "gate"}) + "."


def get(port, path, q):
    url = "http://127.0.0.1:%d/cubejs-api/v1/%s?query=%s" % (port, path, urllib.parse.quote(json.dumps(q)))
    r = urllib.request.Request(url, headers={"Authorization": "gate"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read())


for port, name in ((14400, "dialect"), (14403, "stock")):
    for zone in ("UTC", "America/New_York"):
        q = {"measures": ["ny_a64.m_last_at"], "timezone": zone}
        for _ in range(8):
            out = get(port, "load", q)
            if "data" in out:
                break
            import time
            time.sleep(2)
        print(name, zone, "load", out.get("data"))
        print(name, zone, "sql", get(port, "sql", q)["sql"]["sql"][0][:400])
