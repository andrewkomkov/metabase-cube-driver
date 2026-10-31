"""Signs in to the review Metabase and connects Cube with the dialect, stock Cube and, if it runs, the Postgres copy of the demo data."""
import os
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
# The Postgres copy is the second opinion on what a unit means in Metabase.
names = list(DBS)
if os.environ.get("REVIEW_POSTGRES") and "PG" not in have:
    have["PG"] = mb.api("POST", "/api/database", {"engine": "postgres", "name": "PG", "details": {
        "host": os.environ["REVIEW_POSTGRES"], "port": 5432, "dbname": "shop", "user": "shop", "password": "shop-dev-only", "ssl": False}})
if "PG" in have:
    names.append("PG")
for name in names:
    for _ in range(100):
        d = mb.api("GET", "/api/database/%d" % have[name]["id"])
        if d.get("initial_sync_status") == "complete":
            break
        time.sleep(3)
    print(name, d["id"], d.get("initial_sync_status"))
