"""Secrets, permissions and per-person data, along each path that reads a row. The truth for each person is ClickHouse with the filter of the row-level rule."""
import json
import re
import subprocess
import time

import lib

mb = lib.Metabase().login()
PW = "review-dev-only-person"
SECRET = "cube-secure-dev-only-secret-0123456789"
SQLPW = "cube-secure-dev-only"
FINDINGS = []


def report(ok, name, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (" | " + str(detail)[:400] if detail else ""))
    if not ok:
        FINDINGS.append(name)


def person(email):
    users = mb.api("GET", "/api/user")["data"]
    found = [u for u in users if u["email"] == email]
    if not found:
        mb.api("POST", "/api/user", {"first_name": email.split("@")[0], "last_name": "Review", "email": email, "password": PW})
    return lib.Metabase(email=email, password=PW).login()


have = {d["name"]: d["id"] for d in mb.api("GET", "/api/database")["data"]}
if "Cube secure" not in have:
    d = mb.api("POST", "/api/database", {"engine": "cube", "name": "Cube secure", "details": {
        "host": "cube-secure", "port": 15432, "dbname": "cube", "user": "metabase", "password": SQLPW,
        "cube-rest-url": "http://cube-secure:4000", "cube-api-secret-value": SECRET, "cube-user-attribute": "email"}})
    have["Cube secure"] = d["id"]
    while mb.api("GET", "/api/database/%d" % d["id"]).get("initial_sync_status") != "complete":
        time.sleep(2)
db = have["Cube secure"]
meta = mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % db)
T = {t["name"]: t for t in meta["tables"]}
F = {(t["name"], f["name"]): f["id"] for t in meta["tables"] for f in t["fields"]}
M = {}
for m in mb.api("GET", "/api/measure"):
    if m["table_id"] == T["orders"]["id"]:
        M[m["name"].lower()] = m["id"]
print("measures", sorted(M), "tables", sorted(T))

alice, bob, carol = person("alice@example.com"), person("bob@example.com"), person("carol@example.com")
PEOPLE = {"Austin": alice, "Boston": bob}
J = "orders o JOIN users u ON o.user_id = u.id"
COUNT = {"database": db, "type": "query", "query": {"source-table": T["orders"]["id"], "aggregation": [["count"]]}}
COUNT_M = {"database": db, "type": "query", "query": {"source-table": T["orders"]["id"], "aggregation": [["measure", M["revenue"]]]}}
BY_STATUS = {"database": db, "type": "query", "query": {"source-table": T["orders"]["id"], "aggregation": [["measure", M["revenue"]]], "breakout": [["field", F[("orders", "status")], None]]}}
NATIVE = {"database": db, "type": "native", "native": {"query": "SELECT status, MEASURE(revenue) FROM orders GROUP BY 1"}}
STAGE = {"database": db, "type": "query", "query": {"source-query": BY_STATUS["query"], "aggregation": [["count"]]}}

# Each path gives the rows of the person, on two people, and no path shows the other.
saved = mb.api("POST", "/api/card", {"name": "secure review", "dataset_query": BY_STATUS, "display": "table", "visualization_settings": {}, "type": "question", "collection_id": None})
for city, who in PEOPLE.items():
    w_rev = lib.ch("SELECT o.status, sum(o.amount) FROM %s WHERE u.city = '%s' GROUP BY 1" % (J, city))
    r, _, e = who.run(BY_STATUS)
    report(not e and lib.compare(r, w_rev) is None, "dataset by status, " + city, e or r[:1])
    r, _, e = who.run(NATIVE)
    report(not e and lib.compare(r, w_rev) is None, "native by status, " + city, e or r[:1])
    r, _, e = who.run(STAGE)
    report(not e and r == [[len(w_rev)]], "stage over the query, " + city, e or r)
    res = who.api("POST", "/api/card/%d/query" % saved["id"], {"parameters": []}, ok=(200, 202))
    report(not res.get("error") and lib.compare(res["data"]["rows"], w_rev) is None, "saved question, " + city, res.get("error") or res["data"]["rows"][:1])
    text = who.api("POST", "/api/card/%d/query/csv" % saved["id"], {"parameters": []}, ok=(200, 202))
    rows = [ln.split(",") for ln in str(text).strip().splitlines()[1:]]
    report(len(rows) == len(w_rev) and lib.compare([[a, float(b.replace('"', ""))] for a, b in rows], w_rev) is None, "csv export, " + city, str(text)[:120])
    pv = who.api("POST", "/api/dataset/pivot", dict(BY_STATUS, pivot_rows=[0], pivot_cols=[]), ok=(200, 202, 400, 500))
    total = str(lib.ch("SELECT sum(o.amount) FROM %s WHERE u.city = '%s'" % (J, city))[0][0])
    prows = (pv.get("data") or {}).get("rows") or []
    report(not pv.get("error") and any(float(total) == float(x[2]) for x in prows if x[1] == 1) if prows and len(prows[0]) == 3 else False, "pivot with a total, " + city, pv.get("error") or prows[-2:])
    c = lib.Mcp(who)
    q5 = {"lib/type": "mbql/query", "database": db, "stages": [{"lib/type": "mbql.stage/mbql", "source-table": T["orders"]["id"], "aggregation": [["measure", {}, M["revenue"]]], "breakout": [["field", {}, F[("orders", "status")]]]}]}
    res, err = c.call("execute_query", query=q5)
    report(not err and lib.compare(res["rows"], w_rev) is None, "mcp execute_query, " + city, err or res["rows"][:1])
    res, err = c.call("execute_sql", database_id=db, sql=NATIVE["native"]["query"])
    report(not err and lib.compare(res["rows"], w_rev) is None, "mcp execute_sql, " + city, err or res["rows"][:1])
    res, err = c.call("run_saved_question", id=saved["id"])
    report(not err and lib.compare(res["rows"], w_rev) is None, "mcp run_saved_question, " + city, err or res["rows"][:1])
    # Values of a filter.
    fv = who.api("GET", "/api/field/%d/search/%d?value=a" % (F[("users", "city")], F[("users", "city")]), ok=(200, 400, 403, 404, 500))
    vals = {x[0] for x in fv} if isinstance(fv, list) else fv
    report(vals == {city} or (isinstance(vals, set) and vals <= {city}) or not isinstance(vals, set), "search of a city field, " + city, vals)
    fvv = who.api("GET", "/api/field/%d/values" % F[("users", "city")], ok=(200, 400, 403, 404, 500))
    vs = fvv.get("values") if isinstance(fvv, dict) else None
    report(not vs or {x[0] for x in vs} <= {city}, "stored values of a city field, " + city, vs)
    # Ways around the rule in native SQL.
    for attempt in ("SELECT status, MEASURE(revenue) FROM orders WHERE __user = 'admin@example.com' GROUP BY 1",
                    "SET user = 'admin@example.com'",
                    "SELECT MEASURE(revenue) FROM orders WHERE city = 'Seattle' OR 1=1",
                    "SELECT o.status, MEASURE(o.revenue), u.city FROM orders o CROSS JOIN users u GROUP BY 1, 3",
                    "SELECT city FROM users GROUP BY 1",
                    "SELECT * FROM (SELECT city, MEASURE(count) c FROM users GROUP BY 1 LIMIT 100) t",
                    "SELECT * FROM information_schema.tables",
                    "SELECT usename FROM pg_catalog.pg_user"):
        r, _, e = who.run({"database": db, "type": "native", "native": {"query": attempt}})
        text = json.dumps(r)
        others = [c2 for c2 in ("Austin", "Boston", "Seattle", "Chicago") if c2 != city and c2 in text]
        leak = bool(others)
        if attempt.startswith("SELECT status"):
            leak = not e and lib.compare(r, w_rev) is not None
        report(not leak, "native, not around the rule: %s, %s" % (attempt[:60], city), (e or text)[:200])

# Metabase result cache. The same query for two people, with a TTL on the root.
mb.api("PUT", "/api/cache", {"model": "root", "model_id": 0, "strategy": {"type": "ttl", "multiplier": 100, "min_duration_ms": 0}}, ok=(200, 204, 400, 402, 403, 404))
first = alice.run(BY_STATUS)[0]
second = bob.run(BY_STATUS)[0]
report(first != second, "cache with a TTL: two people get their own rows", (first[:1], second[:1]))
fc = alice.api("POST", "/api/card/%d/query" % saved["id"], {"parameters": []}, ok=(200, 202))["data"]["rows"]
sc = bob.api("POST", "/api/card/%d/query" % saved["id"], {"parameters": []}, ok=(200, 202))["data"]["rows"]
report(fc != sc, "cache with a TTL on a saved question", (fc[:1], sc[:1]))
mb.api("DELETE", "/api/cache", {"model": "root", "model_id": [0]}, ok=(200, 204, 400, 402, 403, 404))

# Pool: many queries of two people at the same time do not mix.
import concurrent.futures
want_a = lib.ch("SELECT sum(o.amount) FROM %s WHERE u.city = 'Austin'" % J)[0][0]
want_b = lib.ch("SELECT sum(o.amount) FROM %s WHERE u.city = 'Boston'" % J)[0][0]
with concurrent.futures.ThreadPoolExecutor(12) as ex:
    res = list(ex.map(lambda i: ((alice if i % 2 else bob).run(COUNT_M)[0], i % 2), range(60)))
bad = [r for r in res if float(r[0][0][0]) != float(want_a if r[1] else want_b)]
report(not bad, "60 parallel queries of two people do not mix", bad[:2])

# No attribute, and a public link.
r, _, e = carol.run(BY_STATUS)
report(bool(e), "a person that Cube does not know gets an error", (e or r)[:120] if e else r[:2])
mb.api("PUT", "/api/setting/enable-public-sharing", {"value": True})
pub = mb.api("POST", "/api/card/%d/public_link" % saved["id"], {})
st, text, _ = lib.http("GET", mb.url + "/api/public/card/%s/query" % pub["uuid"])
report(st != 200 or '"error"' in text, "a public link shows no rows", text[:160])
mb.api("DELETE", "/api/card/%d/public_link" % saved["id"], ok=(200, 204))

# Hidden members on a Cube with dev mode off, and on a table the model hides.
for sql in ("SELECT COUNT(*) FROM secret_audit",):
    r, _, e = alice.run({"database": db, "type": "native", "native": {"query": sql}})
    report(bool(e), "hidden cube on a Cube with dev mode off", (e or str(r))[:120])

# Metabase permissions on the Cube database of the demo. carol has no data access, and dave has the query builder only.
cube = have["Cube"]
groups = {g["name"]: g["id"] for g in mb.api("GET", "/api/permissions/group")}
group_id = mb.api("POST", "/api/permissions/group", {"name": "review-builder-%d" % time.time()})["id"]
graph = mb.api("GET", "/api/permissions/graph")
allu = str(groups["All Users"])
for d_id in list(graph["groups"].get(allu, {})):
    graph["groups"][allu][d_id] = {"view-data": "unrestricted", "create-queries": "no"}
for d_id in (cube, have["Cube stock"], db, have.get("PG"), 1):
    if d_id:
        graph["groups"].setdefault(allu, {})[str(d_id)] = {"view-data": "unrestricted", "create-queries": "no"}
graph["groups"][str(group_id)] = {str(cube): {"view-data": "unrestricted", "create-queries": "query-builder"}}
mb.api("PUT", "/api/permissions/graph", graph)
dave = person("dave@example.com")
ddata = mb.api("GET", "/api/user")["data"]
dave_id = [u for u in ddata if u["email"] == "dave@example.com"][0]["id"]
mb.api("POST", "/api/permissions/membership", {"group_id": group_id, "user_id": dave_id})
dmeta = mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % cube)
OT = {t["name"]: t for t in dmeta["tables"]}
QB = {"database": cube, "type": "query", "query": {"source-table": OT["orders"]["id"], "aggregation": [["count"]]}}
NA = {"database": cube, "type": "native", "native": {"query": "SELECT MEASURE(count) FROM orders"}}
carol2 = lib.Metabase(email="carol@example.com", password=PW).login()
r, _, e = carol2.run(QB)
report(bool(e), "no query access: query builder refused", (e or str(r))[:140])
r, _, e = carol2.run(NA)
report(bool(e), "no query access: native refused", (e or str(r))[:140])
c = lib.Mcp(carol2)
res, err = c.call("execute_query", query={"lib/type": "mbql/query", "database": cube, "stages": [{"lib/type": "mbql.stage/mbql", "source-table": OT["orders"]["id"], "aggregation": [["count", {}]]}]})
report(bool(err), "no query access: mcp execute_query refused", (err or str(res))[:140])
res, err = c.call("execute_sql", database_id=cube, sql="SELECT MEASURE(count) FROM orders")
report(bool(err), "no query access: mcp execute_sql refused", (err or str(res))[:140])
r, _, e = dave.run(QB)
report(not e and r == [[5000]], "query builder access: query builder runs", (e or str(r))[:140])
r, _, e = dave.run(NA)
report(bool(e), "query builder access: native refused", (e or str(r))[:140])
c = lib.Mcp(dave)
res, err = c.call("execute_sql", database_id=cube, sql="SELECT MEASURE(count) FROM orders")
report(bool(err), "query builder access: mcp execute_sql refused", (err or str(res))[:140])

# Secrets in responses.
DEMO_SECRET = "cube-dev-only-secret-0123456789abcdef"
WORDS = [SECRET, SQLPW, DEMO_SECRET, "cube-dev-only\"", "shop-dev-only"]
for who, label in ((mb, "admin"), (alice, "alice")):
    for path in ("/api/database/%d" % db, "/api/database/%d" % cube, "/api/database", "/api/database/%d/metadata" % db, "/api/session/properties", "/api/setting"):
        st, text, _ = lib.http("GET", mb.url + path, headers={"X-Metabase-Session": who.sid})
        hits = [w for w in WORDS if w in text]
        report(not hits, "no secret in %s, %s" % (path, label), hits)
c = lib.Mcp(mb)
for tool, args in (("browse_data", {"action": "list_databases"}), ("browse_data", {"action": "get_fields", "table_ids": [T["orders"]["id"]]})):
    res, err = c.call(tool, **args)
    text = json.dumps(res) + (err or "")
    report(not [w for w in WORDS if w in text], "no secret in mcp %s %s" % (tool, args.get("action")))

# A broken connection, so that the driver writes errors. Then read each log.
for details in ({"host": "cube-secure", "port": 15432, "dbname": "cube", "user": "metabase", "password": "wrong-dev-only-password", "cube-rest-url": "http://cube-secure:4000", "cube-api-secret-value": SECRET},
                {"host": "cube-secure", "port": 15432, "dbname": "cube", "user": "metabase", "password": SQLPW, "cube-rest-url": "http://no-such-host:4000", "cube-api-secret-value": SECRET},
                {"host": "cube-secure", "port": 15432, "dbname": "cube", "user": "metabase", "password": SQLPW, "cube-rest-url": "http://cube-secure:4000", "cube-api-secret-value": "wrong-dev-only-secret"}):
    st, text, _ = lib.http("POST", mb.url + "/api/database/validate", {"engine": "cube", "details": details}, {"X-Metabase-Session": mb.sid})
    print("validate", st, re.sub(r"\s+", " ", text)[:220])
    no_echo = text.replace(details["password"], "").replace(details["cube-api-secret-value"], "")
for name in ("metabase", "cube", "cube-stock", "cube-secure"):
    log = subprocess.run(["docker", "logs", "mcd-review-%s-1" % name], capture_output=True, text=True)
    text = log.stdout + log.stderr
    hits = [w for w in (SECRET, DEMO_SECRET, "wrong-dev-only-secret", "wrong-dev-only-password", SQLPW) if w in text]
    jwt = re.findall(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", text)
    report(not hits and not (name == "metabase" and jwt), "no secret in the log of " + name, (hits, len(jwt)))

print("findings:", FINDINGS)
