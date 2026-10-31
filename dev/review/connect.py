"""Signs in to the review Metabase and connects Cube with the dialect and stock Cube."""
import time

from lib import Metabase

DBS = {
    "Cube": ("cube", 15432, "http://cube:4000"),
    "Cube stock": ("cube-stock", 15432, "http://cube-stock:4000"),
}
SECRET = "cube-dev-only-secret-0123456789abcdef"

mb = Metabase().login()
have = {d["name"]: d for d in mb.api("GET", "/api/database")["data"]}
print("databases before:", sorted(have))
for name, (host, port, rest) in DBS.items():
    if name not in have:
        d = mb.api("POST", "/api/database", {"engine": "cube", "name": name, "details": {
            "host": host, "port": port, "dbname": "cube", "user": "cube", "password": "cube-dev-only",
            "cube-rest-url": rest, "cube-api-secret": SECRET}})
        have[name] = d
for name in DBS:
    for _ in range(100):
        d = mb.api("GET", "/api/database/%d" % have[name]["id"])
        if d.get("initial_sync_status") == "complete":
            break
        time.sleep(3)
    print(name, d["id"], d.get("initial_sync_status"))
