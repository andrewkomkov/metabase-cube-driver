"""A connection with the person attribute from its first sync. No list or sample may hold rows of another person."""
import json
import time

import lib

mb = lib.Metabase().login()
dbs = {d["name"]: d for d in mb.api("GET", "/api/database")["data"]}
if "Secure2" not in dbs:
    dbs["Secure2"] = mb.api("POST", "/api/database", {"engine": "cube", "name": "Secure2", "details": {
        "host": "cube-secure", "port": 15432, "dbname": "cube", "user": "metabase", "password": "cube-secure-dev-only", "cube-rest-url": "http://cube-secure:4000",
        "cube-api-secret-value": "cube-secure-dev-only-secret-0123456789", "cube-user-attribute": "email"}})
    for _ in range(50):
        if mb.api("GET", "/api/database/%d" % dbs["Secure2"]["id"]).get("initial_sync_status") == "complete":
            break
        time.sleep(3)
    time.sleep(25)
db = dbs["Secure2"]["id"]
group = [g for g in mb.api("GET", "/api/permissions/group") if g["name"] == "All Users"][0]["id"]
graph = mb.api("GET", "/api/permissions/graph")
graph["groups"][str(group)][str(db)] = {"view-data": "unrestricted", "create-queries": "query-builder-and-native"}
mb.api("PUT", "/api/permissions/graph", graph)
meta = mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % db)
t = {x["name"]: x for x in meta["tables"]}
city = [f for f in t["users"]["fields"] if f["name"] == "city"][0]
print("city", {k: city.get(k) for k in ("has_field_values", "fingerprint")})
mb.api("POST", "/api/field/%d/rescan_values" % city["id"], {}, ok=(200, 202, 500))
time.sleep(5)
for email in ("alice@example.com", "bob@example.com"):
    m = lib.Metabase(email=email, password="gate-person-1x").login()
    print(email, "values", json.dumps(m.api("GET", "/api/field/%d/values" % city["id"], ok=(200, 403, 500)))[:150])
    print(email, "search Bo", json.dumps(m.api("GET", "/api/field/%d/search/%d?value=Bo" % (city["id"], city["id"]), ok=(200, 403, 500)))[:100])
    print(email, "param", json.dumps(m.api("POST", "/api/dataset/parameter/values", {"parameter": {"id": "p", "type": "string/="}, "field_ids": [city["id"]]}, ok=(200, 403, 500)))[:150])
    st, text, _ = lib.http("GET", mb.url + "/api/automagic-dashboards/table/%d" % t["users"]["id"], headers={"X-Metabase-Session": m.sid}, timeout=120)
    print(email, "xray", st, len(text))
    if st == 200:
        seen = set()
        for dc in json.loads(text).get("dashcards", []):
            q = (dc.get("card") or {}).get("dataset_query")
            if q:
                r = m.api("POST", "/api/dataset", q, ok=(200, 202, 400, 403, 500))
                for row in (r.get("data") or {}).get("rows", []):
                    for v in row:
                        if isinstance(v, str) and v in ("Austin", "Boston", "Chicago", "Seattle"):
                            seen.add(v)
        print(email, "cities in x-ray cards", sorted(seen))
