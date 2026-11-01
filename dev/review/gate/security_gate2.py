"""Permissions and the row rule of Cube, as people: a person with no access to a database, a person with the query builder only, and two people with their own rows."""
import json
import time

import lib

mb = lib.Metabase().login()
dbs = {d["name"]: d for d in mb.api("GET", "/api/database")["data"]}
if "Secure" not in dbs:
    dbs["Secure"] = mb.api("POST", "/api/database", {"engine": "cube", "name": "Secure", "details": {
        "host": "cube-secure", "port": 15432, "dbname": "cube", "user": "metabase", "password": "cube-secure-dev-only", "cube-rest-url": "http://cube-secure:4000",
        "cube-api-secret-value": "cube-secure-dev-only-secret-0123456789", "cube-user-attribute": "email"}})
    for _ in range(50):
        d = mb.api("GET", "/api/database/%d" % dbs["Secure"]["id"])
        if d.get("initial_sync_status") == "complete":
            break
        time.sleep(3)
d = mb.api("GET", "/api/database/%d" % dbs["Secure"]["id"])
if d["details"].get("cube-user-attribute") != "email":
    det = dict(d["details"], **{"cube-user-attribute": "email"})
    det.pop("cube-sql-user-attribute", None)
    mb.api("PUT", "/api/database/%d" % d["id"], {"details": det})
print("details keys", sorted(mb.api("GET", "/api/database/%d" % dbs["Secure"]["id"])["details"]))
SEC = dbs["Secure"]["id"]
ADMIN_META = mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % SEC)
T = {t["name"]: t for t in ADMIN_META["tables"]}
F = lambda t, n: ["field", [f for f in T[t]["fields"] if f["name"] == n][0]["id"], None]  # noqa: E731
print("secure tables", sorted(T))


def user(email):
    have = [u for u in mb.api("GET", "/api/user")["data"] if u["email"] == email]
    if not have:
        have = [mb.api("POST", "/api/user", {"first_name": email[:3], "last_name": "x", "email": email, "password": "gate-person-1x"})]
    return lib.Metabase(email=email, password="gate-person-1x").login(), have[0]


group = [g for g in mb.api("GET", "/api/permissions/group") if g["name"] == "All Users"][0]["id"]
graph = mb.api("GET", "/api/permissions/graph")
graph["groups"][str(group)][str(dbs["Secure"]["id"])] = {"view-data": "unrestricted", "create-queries": "query-builder-and-native"}
mb.api("PUT", "/api/permissions/graph", graph)
alice, _ = user("alice@example.com")
bob, _ = user("bob@example.com")
carol, _ = user("carol@example.com")  # Cube does not know this person.


def rows(api, q):
    res = api("POST", "/api/dataset", q, ok=(200, 202, 400, 403, 500))
    if isinstance(res, str):
        return "TEXT " + res[:150]
    if res.get("error") or "data" not in res:
        return "ERR " + str(res.get("error") or res.get("message") or res)[:200]
    return res["data"]["rows"][:6]


REV = lambda: {"database": SEC, "type": "query", "query": {"source-table": T["orders"]["id"], "aggregation": [["sum", F("orders", "amount_dim")], ["count"]]}}  # noqa: E731
truth = {c: lib.ch("SELECT sum(o.amount), count() FROM orders o JOIN users u ON o.user_id = u.id WHERE u.city = '%s'" % c)[0] for c in ("Austin", "Boston")}
print("truth", truth)
shapes = {
    "plain": REV(),
    "by city": {"database": SEC, "type": "query", "query": {"source-table": T["users"]["id"], "breakout": [F("users", "city")], "aggregation": [["count"]]}},
    "raw users": {"database": SEC, "type": "query", "query": {"source-table": T["users"]["id"], "fields": [F("users", "city"), F("users", "email")], "limit": 2}},
    "stage": {"database": SEC, "type": "query", "query": {"source-query": REV()["query"], "aggregation": [["count"]]}},
    "native": {"database": SEC, "type": "native", "native": {"query": "SELECT MEASURE(revenue), COUNT(*) FROM orders", "template-tags": {}}},
    "native city": {"database": SEC, "type": "native", "native": {"query": "SELECT city, COUNT(*) FROM users GROUP BY 1", "template-tags": {}}},
    "native widen": {"database": SEC, "type": "native", "native": {"query": "SELECT city, COUNT(*) FROM users WHERE city = 'Boston' OR city = 'Austin' GROUP BY 1", "template-tags": {}}},
    "native __user": {"database": SEC, "type": "native", "native": {"query": "SELECT city, COUNT(*) FROM users WHERE __user = 'metabase' GROUP BY 1", "template-tags": {}}},
    "native set": {"database": SEC, "type": "native", "native": {"query": "SET user = 'metabase'", "template-tags": {}}},
    "analyst view": {"database": SEC, "type": "query", "query": {"source-table": (T.get("analyst_sales") or T["orders"])["id"], "aggregation": [["count"]]}},
}
for who, m in (("alice", alice),):
    for name, q in list(shapes.items())[:1]:
        print(who, "|", name, "|", json.dumps(rows(m.api, q), default=str)[:190], flush=True)
# Order of two people: a cached or pooled answer of the first must not reach the second.
for _ in range(2):
    print("alice", rows(alice.api, REV()), "bob", rows(bob.api, REV()))
# A saved question and a dashboard with the person who reads it.
card = alice.api("POST", "/api/card", {"name": "gate secure", "display": "table", "visualization_settings": {}, "dataset_query": REV(), "cache_ttl": 600})["id"]
print("saved, alice", alice.api("POST", "/api/card/%d/query" % card, {})["data"]["rows"])
print("saved, bob", bob.api("POST", "/api/card/%d/query" % card, {})["data"]["rows"])
city = F("users", "city")[1]
for who, m in (("alice", alice), ("bob", bob)):
    print(who, "values", json.dumps(m.api("GET", "/api/field/%d/values" % city, ok=(200, 403, 500)), default=str)[:150], "| search", json.dumps(m.api("GET", "/api/field/%d/search/%d?value=Bo" % (city, city), ok=(200, 403, 500)), default=str)[:120])
    print(who, "param", json.dumps(m.api("POST", "/api/dataset/parameter/values", {"parameter": {"id": "p", "type": "string/="}, "field_ids": [city]}, ok=(200, 403, 500)), default=str)[:160])
amcp = lib.Mcp(alice)
out, err = amcp.call("execute_query", query={"lib/type": "mbql/query", "database": SEC, "stages": [{"lib/type": "mbql.stage/mbql", "source-table": T["users"]["id"], "aggregation": [["count", {}]], "breakout": [["field", {}, city]]}]})
print("mcp alice by city", err or out.get("rows"))
out, err = amcp.call("execute_sql", database_id=SEC, sql="SELECT city, COUNT(*) FROM users GROUP BY 1")
print("mcp alice sql", err or out.get("rows"))
# A person with no access to the demo database, through each door. On OSS this means no new queries: blocked view-data is a paid feature.
graph = mb.api("GET", "/api/permissions/graph")
CUBE = dbs["Cube"]["id"]
graph["groups"][str(group)][str(CUBE)] = {"view-data": "unrestricted", "create-queries": "no"}
mb.api("PUT", "/api/permissions/graph", graph)
c_meta = mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % CUBE)
ct = {t["name"]: t for t in c_meta["tables"]}
status = [f for f in ct["orders"]["fields"] if f["name"] == "status"][0]["id"]
q = {"database": CUBE, "type": "query", "query": {"source-table": ct["orders"]["id"], "aggregation": [["count"]]}}
print("blocked dataset", rows(carol.api, q))
print("blocked native", rows(carol.api, {"database": CUBE, "type": "native", "native": {"query": "SELECT COUNT(*) FROM orders", "template-tags": {}}}))
print("blocked metadata", str(carol.api("GET", "/api/table/%d/query_metadata" % ct["orders"]["id"], ok=(200, 403)))[:90])
print("blocked values", str(carol.api("GET", "/api/field/%d/values" % status, ok=(200, 403)))[:90])
print("blocked measures", len([m for m in carol.api("GET", "/api/measure", ok=(200, 403)) if m.get("table_id") in {t["id"] for t in ct.values()}]) if isinstance(carol.api("GET", "/api/measure", ok=(200, 403)), list) else "403")
cm = lib.Mcp(carol)
out, err = cm.call("execute_query", query={"lib/type": "mbql/query", "database": CUBE, "stages": [{"lib/type": "mbql.stage/mbql", "source-table": ct["orders"]["id"], "aggregation": [["count", {}]]}]})
print("blocked mcp query", err or out)
out, err = cm.call("execute_sql", database_id=CUBE, sql="SELECT COUNT(*) FROM orders")
print("blocked mcp sql", err or out)
out, err = cm.call("browse_data", action="list_tables", database_id=CUBE)
print("blocked mcp tables", str(err or out)[:160])
out, err = cm.call("search", term_queries=["revenue"])
print("blocked mcp search", str(err or out)[:260])
graph = mb.api("GET", "/api/permissions/graph")
graph["groups"][str(group)][str(CUBE)] = {"view-data": "unrestricted", "create-queries": "query-builder-and-native"}
mb.api("PUT", "/api/permissions/graph", graph)
