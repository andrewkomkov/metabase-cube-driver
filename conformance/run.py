#!/usr/bin/env python3
"""Conformance runner. See conformance/README.md.

Run: METABASE_ADMIN_PASSWORD=... python3 conformance/run.py [--only REGEX] [--no-write] [--json PATH]
"""
import argparse
import base64
import datetime as dt
import hashlib
import html.parser
import json
import os
import re
import secrets
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FEATURES = os.path.join(HERE, "features.yaml")
MB = os.environ.get("METABASE_URL", "http://127.0.0.1:3300").rstrip("/")
CUBE = os.environ.get("CUBE_REST_URL", "http://127.0.0.1:4400").rstrip("/")
ADMIN_EMAIL = os.environ.get("METABASE_ADMIN_EMAIL", "admin@example.com")
MCP_PATH = "/api/metabase-mcp"
REDIRECT = "http://127.0.0.1:9/callback"
SCOPES = "agent:content:read agent:content:write agent:query:run agent:sql:run agent:delivery:write"
HTTP_TIMEOUT = 180
CUBE_DB_DETAILS = {
    "host": os.environ.get("CUBE_SQL_HOST", "cube"),
    "port": int(os.environ.get("CUBE_SQL_PORT", "15432")),
    "dbname": "cube",
    "user": "cube",
    "password": "cube-dev-only",
    "cube-rest-url": os.environ.get("CUBE_REST_INTERNAL_URL", "http://cube:4000"),
    "cube-api-secret": "cube-dev-only-secret-0123456789abcdef",
    "ssl": False,
}

DATE_RANGE = ["2025-01-01", "2025-03-31"]


class Fail(Exception):
    """A check found a wrong or missing value."""


# ----------------------------------------------------------------- features.yaml


def load_features(path=FEATURES):
    """Read the flat list of mappings. The standard library has no YAML parser."""
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^(- |  )([a-z_]+): (.*)$", line.rstrip("\n"))
            if not m:
                continue
            if m.group(1) == "- ":
                entries.append({})
            value = m.group(3)
            entries[-1][m.group(2)] = json.loads(value) if value.startswith('"') else value
    return entries


def write_statuses(results, path=FEATURES):
    """Replace only the status lines."""
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    current = None
    for i, line in enumerate(lines):
        m = re.match(r"^- id: (.*)$", line)
        if m:
            current = m.group(1).strip()
        elif line.startswith("  status: ") and current in results:
            lines[i] = "  status: %s\n" % results[current]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ----------------------------------------------------------------- HTTP


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def http(method, url, body=None, headers=None, form=False):
    """Return (status, text, headers). HTTP errors are normal results."""
    headers = dict(headers or {})
    data = None
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body, doseq=True).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        resp = _opener.open(req, timeout=HTTP_TIMEOUT)
    except urllib.error.HTTPError as e:
        resp = e
    except (urllib.error.URLError, OSError) as e:
        raise Fail("%s %s: %s" % (method, url, e))
    return resp.getcode(), resp.read().decode("utf-8", "replace"), resp.headers


# ----------------------------------------------------------------- truth


def ch(sql):
    """Run SQL on the demo ClickHouse. This is the only source of truth values."""
    cmd = [
        "docker", "compose", "-f", os.path.join(REPO, "dev", "docker-compose.yml"), "exec", "-T", "clickhouse",
        "clickhouse-client", "--user", "shop", "--password", "shop-dev-only", "--database", "shop",
        "--output_format_json_quote_64bit_integers=0", "--output_format_json_quote_decimals=0",
        "--format", "JSONCompact", "--query", sql,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise Fail("truth SQL failed: %s: %s" % (sql, p.stderr.strip()[:300]))
    return json.loads(p.stdout)["data"]


# ----------------------------------------------------------------- comparison


def norm(v):
    """Make a value comparable. A number inside a string stays a string: the driver must return numbers."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}(:?\d{2})?)?$", v):
        s = v.replace(" ", "T").replace("Z", "+00:00")
        try:
            d = dt.datetime.fromisoformat(s)
        except ValueError:
            return v
        if d.tzinfo:
            d = d.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return d.replace(microsecond=0).isoformat()
    return v


def sort_key(row):
    return [(0 if x is None else 1, round(x, 3) if isinstance(x, float) else str(x)) if not isinstance(x, float) else (1, "%020.3f" % x) for x in row]


def _same(a, b, tol):
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= max(abs(b) * max(tol, 1e-6), 1e-6)
    return a == b


def assert_rows(got, want, ordered=False, tol=0.0, label="rows"):
    got = [[norm(x) for x in r] for r in got]
    want = [[norm(x) for x in r] for r in want]
    if not ordered:
        got = sorted(got, key=sort_key)
        want = sorted(want, key=sort_key)
    if len(got) != len(want):
        raise Fail("%s: got %d rows, truth has %d. got=%s truth=%s" % (label, len(got), len(want), got[:4], want[:4]))
    for g, w in zip(got, want):
        if len(g) != len(w) or not all(_same(x, y, tol) for x, y in zip(g, w)):
            raise Fail("%s: got %s, truth %s" % (label, g, w))


def values_of(vals):
    return [[v[0] if isinstance(v, list) else (v.get("value") if isinstance(v, dict) else v)] for v in vals]


# ----------------------------------------------------------------- Metabase client


class Metabase:
    def __init__(self, password, db_name=None):
        self.password = password
        self.db_name = db_name
        self.sid = None
        self.db_error = None
        self.db = None
        self.tables = {}
        self.measures = []
        self.segments = []
        self.cube_meta = None
        self.collection_id = None
        self.created = []

    def api(self, method, path, body=None, ok=(200, 201, 202, 204)):
        headers = {"X-Metabase-Session": self.sid} if self.sid else {}
        status, text, _ = http(method, MB + path, body, headers)
        if status not in ok:
            raise Fail("%s %s: HTTP %s: %s" % (method, path, status, text[:300]))
        return json.loads(text) if text.strip()[:1] in ("{", "[") else text

    def wait_for_metabase(self, seconds=180):
        deadline = time.time() + seconds
        while True:
            try:
                if http("GET", MB + "/api/health")[0] == 200:
                    return
            except Fail:
                pass
            if time.time() > deadline:
                raise Fail("Metabase at %s is not healthy after %d s" % (MB, seconds))
            time.sleep(3)

    def login(self):
        self.wait_for_metabase()
        props = self.api("GET", "/api/session/properties")
        fresh = not props.get("has-user-setup")
        if fresh:
            self.password = self.password or secrets.token_urlsafe(24)
            self.api("POST", "/api/setup", {
                "token": props["setup-token"],
                "user": {"email": ADMIN_EMAIL, "first_name": "Conformance", "last_name": "Admin", "password": self.password},
                "prefs": {"site_name": "Cube demo", "allow_tracking": False},
            })
        if not self.password:
            raise Fail("Metabase has an admin. Set METABASE_ADMIN_PASSWORD")
        self.sid = self.api("POST", "/api/session", {"username": ADMIN_EMAIL, "password": self.password})["id"]
        if fresh:
            self.connect_cube()

    def connect_cube(self, seconds=300):
        db = self.api("POST", "/api/database", {"engine": "cube", "name": "Cube", "details": CUBE_DB_DETAILS})
        deadline = time.time() + seconds
        while self.api("GET", "/api/database/%d" % db["id"]).get("initial_sync_status") != "complete":
            if time.time() > deadline:
                raise Fail("first sync of the Cube database did not end in %d s" % seconds)
            time.sleep(3)

    def discover(self):
        dbs = self.api("GET", "/api/database")["data"]
        found = [d for d in dbs if (d["name"] == self.db_name if self.db_name else d["engine"] == "cube")]
        if not found:
            self.db_error = "no database with engine cube in Metabase (found: %s)" % [d["engine"] for d in dbs]
            return
        self.db = found[0]["id"]
        meta = self.api("GET", "/api/database/%d/metadata?include_hidden=true" % self.db)
        self.tables = {t["name"].lower(): t for t in meta["tables"]}
        self.measures = self.api("GET", "/api/measure")
        self.segments = self.api("GET", "/api/segment")
        if isinstance(self.segments, dict):
            self.segments = self.segments.get("data", [])

    def need_db(self):
        if self.db is None:
            raise Fail(self.db_error or "Cube database not found")
        return self.db

    def table(self, name):
        self.need_db()
        t = self.tables.get(name.lower())
        if not t:
            raise Fail("no table %s in the Cube database. It has: %s" % (name, sorted(self.tables)[:30]))
        return t

    def field(self, table, name):
        for f in self.table(table)["fields"]:
            if f["name"].lower() == name.lower():
                return f
        raise Fail("no field %s.%s" % (table, name))

    def has_field(self, table, name):
        return any(f["name"].lower() == name.lower() for f in self.table(table)["fields"])

    def member_meta(self, table, name):
        """Cube /meta entry. Used for the aggregation type and titles, never for truth."""
        if self.cube_meta is None:
            status, text, _ = http("GET", CUBE + "/cubejs-api/v1/meta")
            self.cube_meta = json.loads(text).get("cubes", []) if status == 200 else []
        for c in self.cube_meta:
            if c["name"] == table:
                for m in c.get("measures", []) + c.get("dimensions", []) + c.get("segments", []):
                    if m["name"] == "%s.%s" % (table, name):
                        return m
        return {}

    def entity(self, entities, table, name):
        tid = self.table(table)["id"]
        meta = self.member_meta(table, name)
        names = {_slug(name), _slug(meta.get("title", "")), _slug(meta.get("shortTitle", ""))} - {""}
        for e in entities:
            if e.get("table_id") == tid and not e.get("archived") and _slug(e.get("name", "")) in names:
                return e
        return None

    def measure_clause(self, table, name):
        e = self.entity(self.measures, table, name)
        if e:
            return ["measure", e["id"]]
        f = self.field(table, name)
        agg = {"sum": "sum", "count": "sum", "countDistinct": "sum", "countDistinctApprox": "sum", "avg": "avg",
               "min": "min", "max": "max"}.get(self.member_meta(table, name).get("aggType"), "max")
        return [agg, ["field", f["id"], None]]

    def segment_clause(self, table, name):
        e = self.entity(self.segments, table, name)
        if e:
            return ["segment", e["id"]]
        return ["=", ["field", self.field(table, name)["id"], None], True]

    def fk_field(self, source, target):
        pk = [f["id"] for f in self.table(target)["fields"] if f.get("semantic_type") == "type/PK"]
        for f in self.table(source)["fields"]:
            if pk and f.get("fk_target_field_id") in pk:
                return f
        raise Fail("no foreign key from %s to %s" % (source, target))

    def ref(self, s):
        """Resolve d:cube.member[@unit|#bins=N|#width=N], d:a>b.member, d:b.member|Alias, m:cube.member, s:cube.member."""
        kind, body = s[0], s[2:]
        if kind == "m":
            return self.measure_clause(*body.split("."))
        if kind == "s":
            return self.segment_clause(*body.split("."))
        opts = {}
        if "|" in body:
            body, opts["join-alias"] = body.split("|")
        if "@" in body:
            body, opts["temporal-unit"] = body.split("@")
        if "#" in body:
            body, b = body.split("#")
            k, v = b.split("=")
            opts["binning"] = {"strategy": "num-bins", "num-bins": int(v)} if k == "bins" else {"strategy": "bin-width", "bin-width": float(v)}
        path, name = body.split(".")
        if ">" in path:
            source, path = path.split(">")
            opts["source-field"] = self.fk_field(source, path)["id"]
        return ["field", self.field(path, name)["id"], opts or None]

    def walk(self, x):
        if isinstance(x, str) and re.match(r"^[dms]:[a-z_]", x):
            return self.ref(x)
        if isinstance(x, list):
            return [self.walk(i) for i in x]
        if isinstance(x, dict):
            return {k: self.walk(v) for k, v in x.items()}
        return x

    def inner(self, table, agg=None, by=None, where=None, order=None, limit=None, fields=None, joins=None, exprs=None):
        q = {"source-table": self.table(table)["id"]}
        if agg:
            q["aggregation"] = self.walk(agg)
        if by:
            q["breakout"] = self.walk(by)
        if where:
            q["filter"] = self.walk(["and"] + where) if len(where) > 1 else self.walk(where[0])
        if order:
            q["order-by"] = self.walk(order)
        if limit:
            q["limit"] = limit
        if fields:
            q["fields"] = self.walk(fields)
        if joins:
            q["joins"] = self.walk(joins)
        if exprs:
            q["expressions"] = self.walk(exprs)
        return q

    def dataset(self, query):
        res = self.api("POST", "/api/dataset", query)
        if res.get("error") or res.get("status") == "failed":
            raise Fail("query failed: %s" % str(res.get("error") or res.get("error_type"))[:400])
        return res

    def raw(self, query):
        return self.dataset({"database": self.need_db(), "type": "query", "query": query})["data"]

    def mbql(self, table, **kw):
        return self.raw(self.inner(table, **kw))

    def native(self, sql, tags=None, parameters=None):
        query = {"database": self.need_db(), "type": "native", "native": {"query": sql, "template-tags": tags or {}}}
        if parameters:
            query["parameters"] = parameters
        return self.dataset(query)["data"]

    def collection(self):
        if self.collection_id is None:
            self.collection_id = self.api("POST", "/api/collection", {"name": "conformance-%d" % int(time.time())})["id"]
        return self.collection_id

    def card(self, name, query, kind="question", parameters=None):
        body = {"name": "conformance " + name, "dataset_query": query, "display": "table", "visualization_settings": {},
                "collection_id": self.collection(), "type": kind}
        if parameters:
            body["parameters"] = parameters
        return self.api("POST", "/api/card", body)

    def mbql_card(self, name, table, kind="question", **kw):
        return self.card(name, {"database": self.need_db(), "type": "query", "query": self.inner(table, **kw)}, kind)

    def run_card(self, card_id, parameters=None):
        res = self.api("POST", "/api/card/%d/query" % card_id, {"parameters": parameters or []})
        if res.get("error"):
            raise Fail("card query failed: %s" % str(res["error"])[:400])
        return res["data"]

    def dashboard(self, name, parameters, cards):
        """cards: list of (card_id, [(parameter_id, target)])."""
        d = self.api("POST", "/api/dashboard", {"name": "conformance " + name, "collection_id": self.collection(), "parameters": parameters})
        dashcards = []
        for i, (card_id, wires) in enumerate(cards):
            dashcards.append({"id": -(i + 1), "card_id": card_id, "row": i * 4, "col": 0, "size_x": 8, "size_y": 4,
                              "parameter_mappings": [{"parameter_id": p, "card_id": card_id, "target": t} for p, t in wires]})
        return self.api("PUT", "/api/dashboard/%d" % d["id"], {"dashcards": dashcards, "parameters": parameters})

    def run_dashcard(self, dash, index, parameters):
        dc = dash["dashcards"][index]
        res = self.api("POST", "/api/dashboard/%d/dashcard/%d/card/%d/query" % (dash["id"], dc["id"], dc["card_id"]), {"parameters": parameters})
        if res.get("error"):
            raise Fail("dashcard query failed: %s" % str(res["error"])[:400])
        return res["data"]

    def cleanup(self):
        targets = list(self.created)
        if self.collection_id is not None:
            targets.append(("/api/collection/%d" % self.collection_id, {"archived": True}))
        for path, body in targets:
            try:
                self.api("PUT", path, body)
            except Fail as e:
                print("warning: cleanup: %s" % e)


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


# ----------------------------------------------------------------- MCP client


class _ConsentForm(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.hidden = {}
        self.scopes = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("id") == "consent-form":
            self.action = a.get("action")
        if tag == "input" and a.get("type") == "hidden" and a.get("name"):
            self.hidden[a["name"]] = a.get("value") or ""
        if tag == "input" and a.get("name") == "granted_scope":
            self.scopes.append(a.get("value"))


def _sse(text):
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return json.loads(text)


class Mcp:
    def __init__(self, mb):
        self.mb = mb
        self.headers = None
        self.error = None
        self.next_id = 1

    def connect(self):
        if self.headers:
            return
        if self.error:
            raise Fail(self.error)
        try:
            self._connect()
        except Fail as e:
            self.error = "MCP connect: %s" % e
            raise Fail(self.error)

    def _connect(self):
        if not self.mb.sid:
            raise Fail("no Metabase session")
        status, text, _ = http("POST", MB + "/oauth/register", {
            "client_name": "conformance", "redirect_uris": [REDIRECT], "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none", "scope": SCOPES})
        if status != 201:
            raise Fail("register: HTTP %s %s" % (status, text[:200]))
        client = json.loads(text)["client_id"]
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        resource = MB + MCP_PATH
        session = "metabase.SESSION=" + self.mb.sid
        query = urllib.parse.urlencode({"client_id": client, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES,
                                        "state": secrets.token_hex(8), "code_challenge": challenge,
                                        "code_challenge_method": "S256", "resource": resource})
        status, text, headers = http("GET", MB + "/oauth/authorize?" + query, headers={"Cookie": session})
        if status != 200:
            raise Fail("authorize: HTTP %s %s" % (status, text[:200]))
        form = _ConsentForm()
        form.feed(text)
        if not form.action:
            raise Fail("authorize: no consent form")
        cookies = [c.split(";")[0] for c in headers.get_all("Set-Cookie") or []]
        body = dict(form.hidden, approved="true", granted_scope=form.scopes)
        status, text, headers = http("POST", form.action, body, {"Cookie": "; ".join([session] + cookies)}, form=True)
        location = headers.get("Location") or ""
        code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("code")
        if status != 302 or not code:
            raise Fail("consent: HTTP %s %s %s" % (status, text[:200], location[:200]))
        status, text, _ = http("POST", MB + "/oauth/token", {
            "grant_type": "authorization_code", "code": code[0], "redirect_uri": REDIRECT, "client_id": client,
            "code_verifier": verifier, "resource": resource}, form=True)
        if status != 200:
            raise Fail("token: HTTP %s %s" % (status, text[:200]))
        headers = {"Authorization": "Bearer " + json.loads(text)["access_token"], "Accept": "application/json, text/event-stream"}
        status, text, resp = http("POST", MB + MCP_PATH, {
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "conformance", "version": "1"}}}, headers)
        if status != 200 or not resp.get("Mcp-Session-Id"):
            raise Fail("initialize: HTTP %s %s" % (status, text[:200]))
        headers["Mcp-Session-Id"] = resp["Mcp-Session-Id"]
        http("POST", MB + MCP_PATH, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers)
        self.headers = headers

    def rpc(self, method, params=None):
        self.connect()
        self.next_id += 1
        status, text, _ = http("POST", MB + MCP_PATH, {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}, self.headers)
        if status != 200:
            raise Fail("%s: HTTP %s %s" % (method, status, text[:300]))
        msg = _sse(text)
        if "error" in msg:
            raise Fail("%s: %s" % (method, json.dumps(msg["error"])[:400]))
        return msg["result"]

    def call(self, tool, **args):
        res = self.rpc("tools/call", {"name": tool, "arguments": args})
        text = "".join(c.get("text", "") for c in res.get("content", []))
        if res.get("isError"):
            raise Fail("%s: %s" % (tool, text[:500]))
        try:
            return json.loads(text)
        except ValueError:
            return text


def to_mbql5(clause):
    """Turn a legacy clause into the shape that execute_query accepts: ["op", {}, ...args]."""
    if not isinstance(clause, list) or not clause or not isinstance(clause[0], str):
        return [to_mbql5(c) for c in clause] if isinstance(clause, list) else clause
    if clause[0] == "field":
        return ["field", clause[2] or {}, clause[1]]
    return [clause[0], {}] + [to_mbql5(c) for c in clause[1:]]


def mcp_query(mb, table, agg=None, by=None, where=None):
    stage = {"lib/type": "mbql.stage/mbql", "source-table": mb.table(table)["id"]}
    if agg:
        stage["aggregation"] = [to_mbql5(mb.walk(a)) for a in agg]
    if by:
        stage["breakout"] = [to_mbql5(mb.walk(b)) for b in by]
    if where:
        stage["filters"] = [to_mbql5(mb.walk(w)) for w in where]
    return {"lib/type": "mbql/query", "database": mb.need_db(), "stages": [stage]}


def mcp_stage(tid, **stage):
    return {"lib/type": "mbql/query", "stages": [dict({"lib/type": "mbql.stage/mbql", "source-table": tid}, **stage)]}


def mcp_rows(ctx, table, sql, **kw):
    assert_rows(ctx.mcp.call("execute_query", query=mcp_query(ctx.mb, table, **kw))["rows"], ch(sql))


def get_fields_table(ctx, tid):
    res = ctx.mcp.call("browse_data", action="get_fields", table_ids=[tid])
    tables = res.get("tables") or res.get("data") or []
    if not tables:
        raise Fail("get_fields returned no table: %s" % str(res)[:300])
    return tables[0]


def deep_find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = deep_find(v, key)
            if r is not None:
                return r
    if isinstance(obj, list):
        for v in obj:
            r = deep_find(v, key)
            if r is not None:
                return r
    return None


# ----------------------------------------------------------------- check registry

CHECKS = {}


def check(check_id):
    def wrap(fn):
        if check_id in CHECKS:
            raise SystemExit("duplicate check id: " + check_id)
        CHECKS[check_id] = fn
        return fn
    return wrap


def Q(check_id, table, sql, ordered=False, tol=0.0, post=None, **kw):
    """Query builder check: run MBQL, compare with the truth SQL, then run post(ctx, data)."""
    def run(ctx):
        data = ctx.mb.mbql(table, **kw)
        assert_rows(data["rows"], ch(sql), ordered=ordered, tol=tol)
        if post:
            post(ctx, data)
    check(check_id)(run)


def N(check_id, cube_sql, sql, ordered=False, tol=0.0, tags=None, parameters=None):
    """Native check: run SQL API text through Metabase, compare with the truth SQL."""
    def run(ctx):
        data = ctx.mb.native(cube_sql, tags=tags, parameters=parameters)
        assert_rows(data["rows"], ch(sql), ordered=ordered, tol=tol)
    check(check_id)(run)


class Ctx:
    def __init__(self, mb, mcp):
        self.mb = mb
        self.mcp = mcp


def expect_format(style=None, currency=None, decimals=None, semantic=None):
    """The last result column carries the display format of the Cube member."""
    def post(ctx, data):
        col = data["cols"][-1]
        settings, sem = dict(col.get("settings") or {}), col.get("semantic_type")
        if semantic and sem == semantic:
            return
        if style and settings.get("number_style") != style:
            raise Fail("column settings %s, semantic %s: want number_style %s" % (settings, sem, style))
        if currency and settings.get("currency") != currency:
            raise Fail("column settings %s: want currency %s" % (settings, currency))
        if decimals is not None and settings.get("decimals") != decimals:
            raise Fail("column settings %s: want decimals %s" % (settings, decimals))
        if not settings:
            raise Fail("column has no format settings")
    return post


def expect_text(table, text):
    """The metadata of the table exposes this text, so people and agents can read it."""
    def post(ctx, data):
        tid = ctx.mb.table(table)["id"]
        blob = json.dumps(ctx.mb.table(table)) + json.dumps([m for m in ctx.mb.measures + ctx.mb.segments if m.get("table_id") == tid])
        if text not in blob:
            raise Fail("metadata of %s has no text %r" % (table, text))
    return post


def expect_hidden(table, *names):
    def post(ctx, data):
        for name in names:
            if ctx.mb.has_field(table, name) and ctx.mb.field(table, name).get("visibility_type") not in ("hidden", "retired", "sensitive"):
                raise Fail("%s.%s is visible" % (table, name))
            if ctx.mb.entity(ctx.mb.measures + ctx.mb.segments, table, name):
                raise Fail("%s.%s has an entity" % (table, name))
    return post


def expect_field(table, name, **want):
    def post(ctx, data):
        f = ctx.mb.field(table, name)
        for k, v in want.items():
            if f.get(k) != v:
                raise Fail("%s.%s %s=%r, want %r" % (table, name, k, f.get(k), v))
    return post


def expect_measure_attr(table, name, key, value):
    def post(ctx, data):
        obj = ctx.mb.entity(ctx.mb.measures, table, name) or ctx.mb.field(table, name)
        if obj.get(key) != value:
            raise Fail("%s.%s %s=%r, want %r" % (table, name, key, obj.get(key), value))
    return post


def expect_title(table, name, want):
    def post(ctx, data):
        e = ctx.mb.entity(ctx.mb.measures, table, name)
        got = e["name"] if e else ctx.mb.field(table, name)["display_name"]
        if got != want:
            raise Fail("%s.%s title %r, want %r" % (table, name, got, want))
    return post


def expect_temporal(table, name):
    def post(ctx, data):
        f = ctx.mb.field(table, name)
        if not (f.get("effective_type") or f["base_type"]).startswith(("type/Date", "type/Time")):
            raise Fail("%s.%s has type %s" % (table, name, f["base_type"]))
    return post


def expect_geo(ctx, data):
    sems = {f.get("semantic_type") for f in ctx.mb.table("users")["fields"]}
    if not {"type/Latitude", "type/Longitude"} <= sems:
        raise Fail("users has no fields with Latitude and Longitude semantic types")


def expect_field_format(table, name, style=None, currency=None):
    semantic = {"currency": "type/Currency", "percent": "type/Percentage"}.get(style)

    def post(ctx, data):
        f = ctx.mb.field(table, name)
        settings = f.get("settings") or {}
        if not settings and not (semantic and f.get("semantic_type") == semantic):
            raise Fail("%s.%s has no format settings" % (table, name))
        if style and settings.get("number_style") != style and f.get("semantic_type") != semantic:
            raise Fail("%s.%s settings %s, want number_style %s" % (table, name, settings, style))
        if currency and settings.get("currency") != currency:
            raise Fail("%s.%s settings %s, want currency %s" % (table, name, settings, currency))
    return post


def expect_table_attr(table, key, want):
    def post(ctx, data):
        got = ctx.mb.table(table).get(key)
        if got != want:
            raise Fail("%s %s=%r, want %r" % (table, key, got, want))
    return post


def expect_no_table(name):
    def post(ctx, data):
        t = ctx.mb.tables.get(name)
        if t and t.get("visibility_type") is None:
            raise Fail("table %s is visible" % name)
    return post


def expect_absent(table, name):
    def post(ctx, data):
        if ctx.mb.has_field(table, name) and ctx.mb.field(table, name).get("visibility_type") not in ("hidden", "retired"):
            raise Fail("%s.%s is visible, the view must not expose it" % (table, name))
    return post


def expect_no_text(table, text):
    def post(ctx, data):
        if text in json.dumps(ctx.mb.table(table)):
            raise Fail("metadata of %s shows %r" % (table, text))
    return post


def expect_numeric(ctx, data):
    if not isinstance(data["rows"][0][0], (int, float)):
        raise Fail("value %r is not a number" % (data["rows"][0][0],))


PAID = "status IN ('completed','shipped')"
ROLL = "['2025-01-01','2025-02-01','2025-03-01']"
RANGE_WHERE = [["between", "d:orders.created_at", DATE_RANGE[0], DATE_RANGE[1]]]
FEB_MAR = [["between", "d:orders.created_at", "2025-02-01", "2025-03-31"]]
MONTH = ["d:orders.created_at@month"]
BY_MONTH = {"by": MONTH, "where": RANGE_WHERE, "order": [["asc", MONTH[0]]]}
ORD = "orders"
SIZE = "if(quantity >= 3, 'large', 'small')"
BY_STATUS_SIZE = ("SELECT o.status, o.size, t.r FROM (SELECT status, %s AS size FROM orders GROUP BY status, size) o "
                  "JOIN (SELECT status, sum(amount) AS r FROM orders GROUP BY status) t ON o.status = t.status" % SIZE)
ID7 = [["=", "d:orders.id", 7]]


def monthly(expr):
    """Truth for a window measure: for three months, expr over orders o with bounds m and e."""
    return ("SELECT m, %s FROM orders o CROSS JOIN (SELECT toDateTime(x, 'UTC') AS m, "
            "addSeconds(toDateTime(addMonths(toDateTime(x, 'UTC'), 1), 'UTC'), -1) AS e FROM (SELECT arrayJoin(%s) AS x)) "
            "GROUP BY m ORDER BY m" % (expr, ROLL))


# ----- measures: types

Q("measure.type.count", ORD, "SELECT count() FROM orders", agg=["m:orders.count"])
Q("measure.type.count_distinct", ORD, "SELECT uniqExact(user_id) FROM orders", agg=["m:orders.unique_customers"])
Q("measure.type.count_distinct_approx", ORD, "SELECT uniqExact(user_id) FROM orders", tol=0.05, agg=["m:orders.approx_customers"])
Q("measure.type.sum", ORD, "SELECT sum(amount) FROM orders", agg=["m:orders.revenue"])
Q("measure.type.avg", ORD, "SELECT avg(amount) FROM orders", agg=["m:orders.avg_order_value"])
Q("measure.type.min", ORD, "SELECT min(amount) FROM orders", agg=["m:orders.min_amount"])
Q("measure.type.max", ORD, "SELECT max(amount) FROM orders", agg=["m:orders.max_amount"])
Q("measure.type.number", ORD, "SELECT sumIf(amount, %s) / sum(amount) FROM orders" % PAID, agg=["m:orders.paid_share"])
Q("measure.type.number_agg", ORD, "SELECT quantile(0.5)(amount) FROM orders", agg=["m:orders.p50_amount"])
Q("measure.type.rank", ORD, "SELECT status, rank() OVER (ORDER BY sum(amount) DESC) FROM orders GROUP BY status",
  agg=["m:orders.revenue_rank"], by=["d:orders.status"])
Q("measure.type.string", ORD, "SELECT arrayStringConcat(arraySort(groupUniqArray(status)), ',') FROM orders", agg=["m:orders.statuses"])
Q("measure.type.time", ORD, "SELECT min(created_at) FROM orders", agg=["m:orders.first_order_at"])
Q("measure.type.boolean", ORD, "SELECT count() > 0 FROM orders", agg=["m:orders.has_orders"])

# ----- measures: options

REV = "SELECT sum(amount) FROM orders"
Q("measure.opt.filters", ORD, "SELECT sumIf(amount, %s) FROM orders" % PAID, agg=["m:orders.paid_revenue"])
Q("measure.opt.filters_on_count", "users", "SELECT countIf(is_vip) FROM users", agg=["m:users.vip_count"])
Q("measure.opt.title", ORD, "SELECT avg(amount) FROM orders", agg=["m:orders.avg_order_value"], post=expect_title("orders", "avg_order_value", "AOV"))
Q("measure.opt.description", ORD, REV, agg=["m:orders.revenue"], post=expect_measure_attr("orders", "revenue", "description", "Total order amount in USD."))
Q("measure.opt.public_false", ORD, REV, agg=["m:orders.revenue"], post=expect_hidden("orders", "public_false_measure"))
Q("measure.opt.meta", ORD, REV, agg=["m:orders.revenue_meta"], post=expect_text("orders", "Sum of order amount."))
Q("measure.opt.drill_members", ORD, "SELECT id, status, created_at FROM orders ORDER BY id LIMIT 3", ordered=True,
  fields=["d:orders.id", "d:orders.status", "d:orders.created_at"], order=[["asc", "d:orders.id"]], limit=3, post=expect_text("orders", "drill"))
Q("measure.opt.mask", "masked_sales", "SELECT count(), -1 FROM orders", agg=["m:masked_sales.count", "m:masked_sales.revenue_masked"])
Q("measure.opt.cross_cube_reference", ORD, "SELECT (SELECT 2 * count() FROM orders) / count() FROM orders", agg=["m:orders.payments_per_order"])
Q("measure.format.currency", ORD, REV, agg=["m:orders.revenue"], post=expect_format("currency", semantic="type/Currency"))
Q("measure.format.currency_code", ORD, REV, agg=["m:orders.revenue_eur"], post=expect_format("currency", "EUR"))
Q("measure.format.percent", ORD, "SELECT sumIf(amount, %s) / sum(amount) FROM orders" % PAID, agg=["m:orders.paid_share"], post=expect_format("percent", semantic="type/Percentage"))
Q("measure.format.percent_n", ORD, "SELECT sumIf(amount, %s) / sum(amount) FROM orders" % PAID, agg=["m:orders.paid_share_1"], post=expect_format("percent", decimals=1))
Q("measure.format.number_n", ORD, REV, agg=["m:orders.revenue_number"], post=expect_format(decimals=0))
Q("measure.format.abbr", ORD, REV, agg=["m:orders.revenue_abbr"], post=expect_format())
Q("measure.format.accounting", ORD, REV, agg=["m:orders.revenue_accounting"], post=expect_format(decimals=2))
Q("measure.format.d3", ORD, REV, agg=["m:orders.revenue_d3"], post=expect_format())

# ----- measures: rolling_window

Q("measure.rolling.trailing", ORD, monthly("sumIf(o.amount, o.created_at >= addDays(e, -30) AND o.created_at <= e)"),
  ordered=True, agg=["m:orders.revenue_trailing_30d"], **BY_MONTH)
Q("measure.rolling.leading_offset", ORD, monthly("sumIf(o.amount, o.created_at >= m AND o.created_at < addDays(m, 7))"),
  ordered=True, agg=["m:orders.revenue_leading_7d"], **BY_MONTH)
Q("measure.rolling.unbounded", ORD, monthly("countIf(o.created_at <= e)"), ordered=True, agg=["m:orders.cumulative_count"], **BY_MONTH)
Q("measure.rolling.to_date_month", ORD, monthly("sumIf(o.amount, o.created_at >= m AND o.created_at <= e)"),
  ordered=True, agg=["m:orders.revenue_mtd"], **BY_MONTH)
Q("measure.rolling.to_date_year", ORD, monthly("sumIf(o.amount, o.created_at >= toStartOfYear(m) AND o.created_at <= e)"),
  ordered=True, agg=["m:orders.revenue_ytd"], **BY_MONTH)

# ----- measures: multi_stage

_SHIFT = ("SELECT toStartOfMonth(addMonths(created_at, %d)) AS m, sum(amount) FROM orders "
          "GROUP BY m HAVING m >= '2025-02-01' AND m <= '2025-03-01' ORDER BY m")
Q("measure.multi_stage.time_shift_prior", ORD, _SHIFT % 1, ordered=True, agg=["m:orders.revenue_prior_month"], by=MONTH, where=FEB_MAR, order=[["asc", MONTH[0]]])
Q("measure.multi_stage.time_shift_next", ORD, _SHIFT % -1, ordered=True, agg=["m:orders.revenue_next_month"], by=MONTH, where=FEB_MAR, order=[["asc", MONTH[0]]])
Q("measure.multi_stage.time_shift_named_calendar", "calendar_sales",
  "SELECT toStartOfMonth(toDate(created_at) + 28) AS m, sum(amount) FROM orders GROUP BY m HAVING m = '2025-03-01'",
  agg=["m:calendar_sales.revenue_prior_fiscal"], by=["d:calendar_sales.fiscal_calendar_date@month"],
  where=[["between", "d:calendar_sales.fiscal_calendar_date", "2025-03-01", "2025-03-31"]])
Q("measure.multi_stage.filter_exclude", ORD, "SELECT 'completed', sum(amount) FROM orders",
  agg=["m:orders.revenue_all_statuses"], by=["d:orders.status"], where=[["=", "d:orders.status", "completed"]])
Q("measure.multi_stage.grain_keep_only", ORD, BY_STATUS_SIZE, agg=["m:orders.revenue_keep_status"], by=["d:orders.status", "d:orders.size"])
Q("measure.multi_stage.grain_exclude", ORD, BY_STATUS_SIZE, agg=["m:orders.revenue_excl_size"], by=["d:orders.status", "d:orders.size"])
Q("measure.multi_stage.grain_include", ORD,
  "SELECT status, max(r) FROM (SELECT status, %s AS size, sum(amount) AS r FROM orders GROUP BY status, size) GROUP BY status" % SIZE,
  agg=["m:orders.revenue_incl_size_max"], by=["d:orders.status"])
Q("measure.multi_stage.group_by_legacy", ORD, BY_STATUS_SIZE, agg=["m:orders.revenue_group_by_legacy"], by=["d:orders.status", "d:orders.size"])
Q("measure.multi_stage.share_of_total", ORD, "SELECT status, sum(amount) / (SELECT sum(amount) FROM orders) FROM orders GROUP BY status",
  agg=["m:orders.revenue_share_of_total"], by=["d:orders.status"])
Q("measure.multi_stage.case_switch", ORD,
  "SELECT 'USD', toFloat64(sum(amount)) FROM orders UNION ALL SELECT 'EUR', toFloat64(sum(amount)) * 0.5 FROM orders",
  agg=["m:orders.revenue_in_currency"], by=["d:orders.display_currency"])

# ----- dimensions

Q("dimension.type.string", ORD, "SELECT status, count() FROM orders GROUP BY status", agg=["m:orders.count"], by=["d:orders.status"])
Q("dimension.type.number", ORD, "SELECT quantity, count() FROM orders GROUP BY quantity", agg=["m:orders.count"], by=["d:orders.quantity_dim"])
Q("dimension.type.boolean", ORD, "SELECT %s, count() FROM orders GROUP BY 1" % PAID, agg=["m:orders.count"], by=["d:orders.is_paid"])
Q("dimension.type.time", ORD, "SELECT toStartOfYear(created_at), count() FROM orders GROUP BY 1", agg=["m:orders.count"], by=["d:orders.created_at@year"],
  post=expect_temporal("orders", "created_at"))
Q("dimension.type.geo", "users", "SELECT concat(toString(latitude), ',', toString(longitude)), count() FROM users GROUP BY 1",
  agg=["m:users.count"], by=["d:users.location"], post=expect_geo)
Q("dimension.type.switch", ORD, "SELECT arrayJoin(['USD','EUR'])", by=["d:orders.display_currency"])
Q("dimension.opt.case_sql", ORD, "SELECT %s, count() FROM orders GROUP BY 1" % SIZE, agg=["m:orders.count"], by=["d:orders.size"])
Q("dimension.opt.case_label_sql", "users",
  "SELECT multiIf(created_at < toDateTime('2024-04-01 00:00:00', 'UTC'), 'Q1', created_at < toDateTime('2024-07-01 00:00:00', 'UTC'), 'Q2', 'later'), count() FROM users GROUP BY 1",
  agg=["m:users.count"], by=["d:users.signup_cohort"])
Q("dimension.opt.sub_query", "users", "SELECT u.id, count() FROM users u JOIN orders o ON o.user_id = u.id WHERE u.id <= 3 GROUP BY u.id",
  fields=["d:users.id", "d:users.lifetime_orders"], where=[["<=", "d:users.id", 3]])
Q("dimension.opt.propagate_filters", "users", "SELECT u.id, count() FROM users u JOIN orders o ON o.user_id = u.id WHERE u.id <= 3 GROUP BY u.id",
  fields=["d:users.id", "d:users.lifetime_paid_orders"], where=[["<=", "d:users.id", 3]])
Q("dimension.opt.primary_key", ORD, "SELECT status FROM orders WHERE id = 7", fields=["d:orders.status"], where=ID7,
  post=expect_field("orders", "id", semantic_type="type/PK"))
Q("dimension.opt.title", ORD, "SELECT quantity, count() FROM orders GROUP BY quantity", agg=["m:orders.count"], by=["d:orders.quantity_dim"],
  post=expect_field("orders", "quantity_dim", display_name="Quantity"))
Q("dimension.opt.description", ORD, "SELECT quantity, count() FROM orders GROUP BY quantity", agg=["m:orders.count"], by=["d:orders.quantity_dim"],
  post=expect_field("orders", "quantity_dim", description="Units in the order."))
Q("dimension.opt.public_false", ORD, "SELECT count() FROM orders", agg=["m:orders.count"], post=expect_hidden("orders", "internal_note"))
Q("dimension.opt.meta", ORD, "SELECT status, count() FROM orders GROUP BY status", agg=["m:orders.count"], by=["d:orders.status_meta"],
  post=expect_text("orders", "Use for order lifecycle questions."))
Q("dimension.opt.mask_static", "masked_sales", "SELECT 'hidden', count() FROM orders", agg=["m:masked_sales.count"], by=["d:masked_sales.masked_status"])
Q("dimension.opt.mask_sql", "users", "SELECT email FROM users WHERE id = 1", fields=["d:users.email_masked"], where=[["=", "d:users.id", 1]])
Q("dimension.opt.links", ORD, "SELECT status, count() FROM orders GROUP BY status", agg=["m:orders.count"], by=["d:orders.status_linked"],
  post=expect_text("orders", "Search status"))
Q("dimension.opt.synthetic_hidden", ORD, "SELECT count() FROM orders", agg=["m:orders.count"], post=expect_hidden("orders", "status_linked___link_search_url"))
Q("dimension.format.currency", ORD, "SELECT amount FROM orders WHERE id = 7", fields=["d:orders.amount_dim"], where=ID7, post=expect_field_format("orders", "amount_dim", "currency"))
Q("dimension.format.currency_code", ORD, "SELECT amount FROM orders WHERE id = 7", fields=["d:orders.amount_eur"], where=ID7, post=expect_field_format("orders", "amount_eur", "currency", "EUR"))
Q("dimension.format.percent", ORD, "SELECT amount / 1000 FROM orders WHERE id = 7", fields=["d:orders.amount_pct"], where=ID7, post=expect_field_format("orders", "amount_pct", "percent"))
Q("dimension.format.abbr", ORD, "SELECT amount FROM orders WHERE id = 7", fields=["d:orders.amount_abbr"], where=ID7, post=expect_field_format("orders", "amount_abbr"))
Q("dimension.format.id", ORD, "SELECT id FROM orders WHERE id = 7", fields=["d:orders.order_ref"], where=ID7, post=expect_field_format("orders", "order_ref"))
_INV = "SELECT concat('https://example.com/invoice/', toString(id)) FROM orders WHERE id = 7"
Q("dimension.format.link", ORD, _INV, fields=["d:orders.invoice_url"], where=ID7, post=expect_field("orders", "invoice_url", semantic_type="type/URL"))
Q("dimension.format.link_label", ORD, _INV, fields=["d:orders.invoice_labeled"], where=ID7, post=expect_text("orders", "Open invoice"))
Q("dimension.format.image_url", ORD, "SELECT concat('https://example.com/thumb/', toString(id), '.png') FROM orders WHERE id = 7",
  fields=["d:orders.thumb_url"], where=ID7, post=expect_field("orders", "thumb_url", semantic_type="type/ImageURL"))
Q("dimension.format.time", ORD, "SELECT created_at FROM orders WHERE id = 7", fields=["d:orders.created_label"], where=ID7, post=expect_text("orders", "%Y-%m-%d %H:%M:%S"))

# ----- custom granularities, through proxy dimensions because the SQL API has no other way

Q("granularity.custom.offset", ORD, "SELECT addMonths(toStartOfQuarter(addMonths(created_at, -1)), 1) AS q, count() FROM orders GROUP BY q",
  agg=["m:orders.count"], by=["d:orders.created_fiscal_quarter"])
Q("granularity.custom.negative_offset", ORD, "SELECT toStartOfWeek(created_at, 0) AS w, count() FROM orders GROUP BY w ORDER BY w LIMIT 3", ordered=True,
  agg=["m:orders.count"], by=["d:orders.created_week_sunday"], order=[["asc", "d:orders.created_week_sunday"]], limit=3)
Q("granularity.custom.origin", ORD, "SELECT addMonths(toStartOfYear(addMonths(created_at, -3)), 3) AS y, count() FROM orders GROUP BY y",
  agg=["m:orders.count"], by=["d:orders.created_fiscal_year"])
Q("granularity.custom.interval_hours", ORD, "SELECT toStartOfInterval(created_at, INTERVAL 12 HOUR) AS h, count() FROM orders GROUP BY h ORDER BY h LIMIT 3", ordered=True,
  agg=["m:orders.count"], by=["d:orders.created_half_day"], order=[["asc", "d:orders.created_half_day"]], limit=3)
Q("granularity.proxy_default", ORD, "SELECT toStartOfMonth(created_at) AS m, count() FROM orders GROUP BY m", agg=["m:orders.count"], by=["d:orders.created_month_proxy"])
Q("granularity.custom.title", ORD, "SELECT count() FROM orders", agg=["m:orders.count"], post=expect_text("orders", "Fiscal quarter"))

# ----- segments

Q("segment.filter", ORD, "SELECT count() FROM orders WHERE %s" % PAID, agg=["m:orders.count"], where=["s:orders.paid"])
Q("segment.second", ORD, "SELECT count() FROM orders WHERE quantity >= 3", agg=["m:orders.count"], where=["s:orders.large"])
Q("segment.combined", ORD, "SELECT count() FROM orders WHERE quantity >= 3 AND %s" % PAID, agg=["m:orders.count"], where=["s:orders.large", "s:orders.paid"])
Q("segment.with_breakout", "users", "SELECT city, count() FROM users WHERE is_vip GROUP BY city", agg=["m:users.count"], by=["d:users.city"], where=["s:users.vip"])
Q("segment.description", ORD, "SELECT count() FROM orders WHERE %s" % PAID, agg=["m:orders.count"], where=["s:orders.paid"], post=expect_text("orders", "Completed or shipped orders."))
Q("segment.title", ORD, "SELECT count() FROM orders WHERE quantity >= 3", agg=["m:orders.count"], where=["s:orders.large"], post=expect_text("orders", "Large orders"))

# ----- joins

Q("join.many_to_one", "sales", "SELECT u.city, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY u.city", agg=["m:sales.count"], by=["d:sales.users_city"])
Q("join.one_to_many_no_fanout", "extended_sales", "SELECT m, (SELECT sum(amount) FROM orders) FROM (SELECT arrayJoin(['card','fee']) AS m)",
  agg=["m:extended_sales.revenue"], by=["d:extended_sales.payments_method"])
Q("join.one_to_many_child_measure", "extended_sales", "SELECT 'card', sum(amount) FROM orders UNION ALL SELECT 'fee', toDecimal64(count(), 2) FROM orders",
  agg=["m:extended_sales.payments_total"], by=["d:extended_sales.payments_method"])
Q("join.alias", "promo_sales", "SELECT concat('P', toString(id % 10)), concat('P', toString((id + 1) % 10)), count() FROM orders GROUP BY 1, 2",
  agg=["m:promo_sales.count"], by=["d:promo_sales.first_promo_code", "d:promo_sales.second_promo_code"])
Q("join.fan_trap_avg", "extended_sales", "SELECT avg(amount) FROM orders", agg=["m:extended_sales.avg_order_value"])
N("join.sql_cube_join_field", "SELECT o.status AS s, u.city AS c, MEASURE(o.count) AS n FROM orders o CROSS JOIN users u WHERE o.__cubeJoinField = u.__cubeJoinField GROUP BY 1, 2",
  "SELECT o.status, u.city, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1, 2")
N("join.one_to_one", "SELECT s.carrier_mode AS m, MEASURE(o.count) AS n FROM orders o CROSS JOIN shipments s WHERE o.__cubeJoinField = s.__cubeJoinField GROUP BY 1",
  "SELECT ['air','ground','sea'][1 + id % 3], count() FROM orders GROUP BY 1")
N("join.transitive", "SELECT p.category AS c, MEASURE(u.count) AS n FROM users u CROSS JOIN orders o CROSS JOIN products p "
  "WHERE u.__cubeJoinField = o.__cubeJoinField AND o.__cubeJoinField = p.__cubeJoinField GROUP BY 1",
  "SELECT p.category, uniqExact(o.user_id) FROM orders o JOIN products p ON o.product_id = p.id GROUP BY 1")

# ----- cubes

CNT_U = "SELECT count() FROM users"
Q("cube.sql_table", "products", "SELECT count() FROM products", agg=["m:products.count"])
Q("cube.sql", "payments", "SELECT 2 * count() FROM orders", agg=["m:payments.count"])
Q("cube.title", "users", CNT_U, agg=["m:users.count"], post=expect_table_attr("users", "display_name", "Customers"))
Q("cube.description", "users", CNT_U, agg=["m:users.count"], post=expect_table_attr("users", "description", "People who have an account in the shop."))
Q("cube.public_false", ORD, "SELECT count() FROM orders", agg=["m:orders.count"], post=expect_no_table("secret_audit"))
Q("cube.meta", "users", CNT_U, agg=["m:users.count"], post=expect_text("users", "One row per customer account."))
Q("cube.extends", "cheap_products", "SELECT count() FROM products WHERE price < 20", agg=["m:cheap_products.count"])
Q("cube.sql_alias", ORD, "SELECT status, count() FROM orders GROUP BY status", agg=["m:orders.count"], by=["d:orders.status"])
Q("cube.refresh_key", ORD, REV, agg=["m:orders.revenue"])
Q("cube.calendar", "calendar_sales", "SELECT toDate('2025-03-01'), sum(amount) FROM orders WHERE toStartOfMonth(created_at) = '2025-03-01'",
  agg=["m:calendar_sales.revenue"], by=["d:calendar_sales.fiscal_calendar_date@month"], where=[["between", "d:calendar_sales.fiscal_calendar_date", "2025-03-01", "2025-03-31"]])
Q("context.filter_params", "filtered_orders", "SELECT count() FROM orders WHERE status = 'shipped'", agg=["m:filtered_orders.count"], where=[["=", "d:filtered_orders.status", "shipped"]])
Q("context.sql_utils_convert_tz", "filtered_orders", "SELECT toStartOfYear(created_at), count() FROM orders GROUP BY 1", agg=["m:filtered_orders.count"], by=["d:filtered_orders.created_berlin@year"])

# ----- views

CNT_O = "SELECT count() FROM orders"
COMPLETED = "SELECT sum(amount) FROM orders WHERE status = 'completed'"
Q("view.basic", "sales", "SELECT status, sum(amount) FROM orders GROUP BY status", agg=["m:sales.revenue"], by=["d:sales.status"])
Q("view.title", "sales", CNT_O, agg=["m:sales.count"], post=expect_table_attr("sales", "display_name", "Sales"))
Q("view.description", "paid_sales", "SELECT count() FROM orders WHERE status = 'completed'", agg=["m:paid_sales.count"],
  post=expect_table_attr("paid_sales", "description", "Completed orders only. Default filters enforce this."))
Q("view.meta", "sales", CNT_O, agg=["m:sales.count"], post=expect_text("sales", "Preferred entry point for agents."))
Q("view.public_false", "sales", CNT_O, agg=["m:sales.count"], post=expect_no_table("hidden_sales"))
Q("view.includes", "sales", CNT_O, agg=["m:sales.count"], post=expect_absent("sales", "min_amount"))
Q("view.excludes", "paid_sales", "SELECT u.city, uniqExact(o.user_id) FROM orders o JOIN users u ON o.user_id = u.id WHERE o.status = 'completed' GROUP BY u.city",
  agg=["m:paid_sales.users_count"], by=["d:paid_sales.users_city"], post=expect_absent("paid_sales", "users_email"))
Q("view.prefix", "sales", "SELECT category, count() FROM orders o JOIN products p ON o.product_id = p.id GROUP BY category", agg=["m:sales.count"], by=["d:sales.products_category"])
Q("view.member_alias", "paid_sales", COMPLETED, agg=["m:paid_sales.net_sales"])
Q("view.member_title", "paid_sales", COMPLETED, agg=["m:paid_sales.net_sales"], post=expect_title("paid_sales", "net_sales", "Net sales"))
Q("view.member_description", "paid_sales", COMPLETED, agg=["m:paid_sales.net_sales"], post=expect_measure_attr("paid_sales", "net_sales", "description", "Revenue under another name."))
Q("view.member_format", "paid_sales", COMPLETED, agg=["m:paid_sales.net_sales"], post=expect_format("currency", semantic="type/Currency"))
Q("view.member_meta", "paid_sales", COMPLETED, agg=["m:paid_sales.net_sales"], post=expect_text("paid_sales", "curated"))
Q("view.derived_measure", "paid_sales", "SELECT avg(amount) FROM orders WHERE status = 'completed'", agg=["m:paid_sales.revenue_per_order"])
Q("view.extends", "extended_sales", "SELECT status, sum(amount) FROM orders GROUP BY status", agg=["m:extended_sales.revenue"], by=["d:extended_sales.status"])
Q("view.default_filters", "paid_sales", "SELECT status, count() FROM orders WHERE status = 'completed' GROUP BY status", agg=["m:paid_sales.count"], by=["d:paid_sales.status"])
Q("view.folders", "sales", REV, agg=["m:sales.revenue"], post=expect_text("sales", "Money"))
Q("view.folders_nested", "paid_sales", "SELECT count() FROM orders WHERE status = 'completed'", agg=["m:paid_sales.count"], post=expect_text("paid_sales", "Ratios"))
Q("view.access_row_level", "restricted_sales", "SELECT status, count() FROM orders WHERE status = 'shipped' GROUP BY status", agg=["m:restricted_sales.count"], by=["d:restricted_sales.status"])
Q("view.access_member_level", "restricted_sales", "SELECT sum(amount) FROM orders WHERE status = 'shipped'", agg=["m:restricted_sales.revenue"], post=expect_absent("restricted_sales", "created_at"))
Q("view.group", "paid_sales", "SELECT count() FROM orders WHERE status = 'completed'", agg=["m:paid_sales.count"], post=expect_text("paid_sales", "Revenue views"))

# ----- inheritance, view shapes and cube variants: each check reads the synced metadata and a value


def expect_sync(table, dims=(), measures=(), segments=(), absent=(), display=None, no_tables=()):
    def post(ctx, data):
        mb = ctx.mb
        for name in dims:
            kind = str(mb.field(table, name).get("database_type"))
            if kind.startswith("cube.measure/") or kind == "cube.segment":
                raise Fail("%s.%s synced as %s, want a dimension" % (table, name, kind))
        for name in measures:
            if not mb.entity(mb.measures, table, name) and not str(mb.field(table, name).get("database_type")).startswith("cube.measure/"):
                raise Fail("%s.%s did not sync as a measure" % (table, name))
        for name in segments:
            if not mb.entity(mb.segments, table, name) and str(mb.field(table, name).get("database_type")) != "cube.segment":
                raise Fail("%s.%s did not sync as a segment" % (table, name))
        for name in absent:
            if mb.has_field(table, name):
                raise Fail("%s.%s must not sync" % (table, name))
        for name in no_tables:
            if name in mb.tables:
                raise Fail("table %s must not sync" % name)
        if display is not None and mb.table(table)["display_name"] != display:
            raise Fail("%s has display name %r, want %r" % (table, mb.table(table)["display_name"], display))
    return post


def sql_uses_alias(needle):
    def post(ctx, data):
        query = urllib.parse.quote(json.dumps({"measures": ["orders.count"]}))
        _s, text, _h = http("GET", CUBE + "/cubejs-api/v1/sql?query=" + query)
        if needle not in text:
            raise Fail("generated SQL has no %s: %s" % (needle, text[:200]))
    return post


def inherited_rollup(ctx, data):
    q = {"measures": ["level_one.revenue"], "dimensions": ["level_one.status"],
         "timeDimensions": [{"dimension": "level_one.created_at", "granularity": "month"}]}
    used_rollup(q, "monthly")(ctx, data)


SIZE_3 = "quantity >= 3"
REGION_SQL = "transform(%s, ['TX','IL','WA','MA'], ['South','Midwest','West','East'], '')"
Q("inherit.cube.members", "level_one", "SELECT count() FROM orders", agg=["m:level_one.count"],
  post=expect_sync("level_one", ["id", "product_id", "quantity", "created_at"], ["count"], display="Level one"))
Q("inherit.cube.override_measure", "level_one", "SELECT sum(amount * 2) FROM orders", agg=["m:level_one.revenue"],
  post=expect_sync("level_one", measures=["revenue"]))
Q("inherit.cube.override_dimension", "level_one", "SELECT upper(status), count() FROM orders GROUP BY status",
  agg=["m:level_one.count"], by=["d:level_one.status"], post=expect_sync("level_one", ["status"]))
Q("inherit.cube.add_measure", "level_one", "SELECT avg(quantity) FROM orders", agg=["m:level_one.avg_quantity"],
  post=expect_sync("level_one", measures=["avg_quantity"]))
Q("inherit.cube.add_dimension", "level_one", "SELECT if(%s, 'high', 'low') AS b, count() FROM orders GROUP BY b" % SIZE_3,
  agg=["m:level_one.count"], by=["d:level_one.quantity_band"], post=expect_sync("level_one", ["quantity_band"]))
Q("inherit.multi_level.rows", "level_two", "SELECT count() FROM orders WHERE %s" % SIZE_3, agg=["m:level_two.count"],
  post=expect_sync("level_two", ["id", "quantity_band"], ["count"], display="Level two"))
Q("inherit.multi_level.add_measure", "level_two", "SELECT max(quantity) FROM orders", agg=["m:level_two.max_quantity"],
  post=expect_sync("level_two", measures=["max_quantity", "avg_quantity"]))
Q("inherit.multi_level.override", "level_two", "SELECT sum(amount * 2) FROM orders WHERE %s" % SIZE_3,
  agg=["m:level_two.revenue"], post=expect_sync("level_two", measures=["revenue"]))
Q("inherit.abstract_base", "level_one", "SELECT count() FROM orders", agg=["m:level_one.count"],
  post=expect_sync("level_one", no_tables=["base_sales"]))
Q("inherit.segment", "level_one", "SELECT count() FROM orders WHERE %s" % SIZE_3, agg=["m:level_one.count"],
  where=["s:level_one.large"], post=expect_sync("level_one", segments=["large"]))
Q("inherit.pre_aggregation", "level_one",
  "SELECT upper(status), toStartOfMonth(created_at) AS m, sum(amount * 2) FROM orders GROUP BY status, m",
  agg=["m:level_one.revenue"], by=["d:level_one.status", "d:level_one.created_at@month"], post=inherited_rollup)


@check("inherit.join")
def _(ctx):
    expect_sync("level_one", ["product_id"])(ctx, None)
    data = ctx.mb.native("SELECT products.category, MEASURE(level_one.count) FROM level_one CROSS JOIN products GROUP BY 1")
    assert_rows(data["rows"], ch("SELECT p.category, count() FROM orders o JOIN products p ON o.product_id = p.id GROUP BY p.category"))


Q("inherit.view.members", "extended_sales", "SELECT count() FROM orders", agg=["m:extended_sales.count"],
  post=expect_sync("extended_sales", ["status", "users_city"], ["count", "revenue"], display="Extended sales"))
Q("inherit.view.adds", "extended_sales", "SELECT sum(amount) + count() FROM orders", agg=["m:extended_sales.payments_total"],
  post=expect_sync("extended_sales", ["payments_method"], ["payments_total"]))
Q("inherit.view.multi_level", "deep_extended_sales", "SELECT ['air','ground','sea'][1 + id % 3] AS c, count() FROM orders GROUP BY c",
  agg=["m:deep_extended_sales.count"], by=["d:deep_extended_sales.shipments_carrier_mode"],
  post=expect_sync("deep_extended_sales", ["shipments_carrier_mode", "payments_method", "status"], ["count"]))

Q("view.includes_star", "products_lite", "SELECT category, count() FROM products GROUP BY category",
  agg=["m:products_lite.count"], by=["d:products_lite.category"],
  post=expect_sync("products_lite", ["id", "name", "category"], ["count", "avg_price"], display="Products lite"))
Q("view.excludes_star", "products_lite", "SELECT avg(price) FROM products", agg=["m:products_lite.avg_price"],
  post=expect_sync("products_lite", absent=["price"]))
Q("view.public_true", "products_lite", "SELECT count() FROM products", agg=["m:products_lite.count"],
  post=expect_sync("products_lite", measures=["count"]))
Q("view.join_path_transitive", "region_sales",
  "SELECT %s AS r, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY r" % (REGION_SQL % "u.state"),
  agg=["m:region_sales.count"], by=["d:region_sales.customer_region"], post=expect_sync("region_sales", ["customer_region"]))
Q("view.several_paths", "region_sales",
  "SELECT %s AS r, count() FROM orders GROUP BY r" % (REGION_SQL % "['TX','IL','WA','MA'][1 + id % 4]"),
  agg=["m:region_sales.count"], by=["d:region_sales.destination_region"],
  post=expect_sync("region_sales", ["customer_region", "destination_region"]))
Q("view.same_name_two_cubes", "region_sales", "SELECT count(), uniqExact(user_id) FROM orders",
  agg=["m:region_sales.count", "m:region_sales.customer_count"], post=expect_sync("region_sales", measures=["count", "customer_count"]))
Q("view.on_extended_cube", "level_two_view", "SELECT sum(amount * 2) FROM orders WHERE %s" % SIZE_3, agg=["m:level_two_view.revenue"],
  post=expect_sync("level_two_view", ["status", "quantity_band"], ["revenue", "max_quantity"], ["large"]))
Q("view.prefix_sync", "sales", "SELECT u.city, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY u.city",
  agg=["m:sales.count"], by=["d:sales.users_city"], post=expect_sync("sales", ["users_city", "products_category"], ["users_count"], absent=["city"]))
Q("view.member_alias_sync", "paid_sales", "SELECT sum(amount) FROM orders WHERE status = 'completed'", agg=["m:paid_sales.net_sales"],
  post=expect_sync("paid_sales", measures=["net_sales"], absent=["revenue"]))

Q("cube.sql_sync", "payments", "SELECT method, count() FROM (SELECT 'card' AS method FROM orders UNION ALL SELECT 'fee' FROM orders) GROUP BY method",
  agg=["m:payments.count"], by=["d:payments.method"], post=expect_sync("payments", ["method", "order_id"], ["count", "total"], absent=["id"]))
Q("cube.sql_alias_sync", "orders", "SELECT count() FROM orders", agg=["m:orders.count"],
  post=lambda ctx, data: (sql_uses_alias("ord__count")(ctx, data), expect_sync("orders", ["status"], ["count"])(ctx, data)))
Q("cube.data_source_default", "explicit_source", "SELECT category, count() FROM products GROUP BY category",
  agg=["m:explicit_source.count"], by=["d:explicit_source.category"], post=expect_sync("explicit_source", ["category"], ["count"]))
Q("cube.polymorphic_first", "vip_people", "SELECT city, count() FROM users WHERE is_vip GROUP BY city",
  agg=["m:vip_people.count"], by=["d:vip_people.city"], post=expect_sync("vip_people", ["id", "city"], ["count"], display="VIP people"))
Q("cube.polymorphic_second", "regular_people", "SELECT city, count() FROM users WHERE NOT is_vip GROUP BY city",
  agg=["m:regular_people.count"], by=["d:regular_people.city"], post=expect_sync("regular_people", ["id", "city"], ["count"]))
Q("cube.polymorphic_base", "vip_people", "SELECT count() FROM users WHERE is_vip", agg=["m:vip_people.count"],
  post=expect_sync("vip_people", no_tables=["people"]))
Q("cube.generated_jinja", "gen_completed_orders", "SELECT sum(amount) FROM orders WHERE status = 'completed'", agg=["m:gen_completed_orders.revenue"],
  post=expect_sync("gen_completed_orders", ["id"], ["count", "revenue"], display="Generated completed orders"))
Q("cube.generated_jinja_second", "gen_shipped_orders", "SELECT count() FROM orders WHERE status = 'shipped'", agg=["m:gen_shipped_orders.count"],
  post=expect_sync("gen_shipped_orders", ["id"], ["count", "revenue"]))
Q("cube.generated_async", "async_processing_orders", "SELECT sum(amount) FROM orders WHERE status = 'processing'", agg=["m:async_processing_orders.revenue"],
  post=expect_sync("async_processing_orders", ["id"], ["count", "revenue"], display="Async processing orders"))
Q("cube.generated_async_second", "async_cancelled_orders", "SELECT count() FROM orders WHERE status = 'cancelled'", agg=["m:async_cancelled_orders.count"],
  post=expect_sync("async_cancelled_orders", ["id"], ["count", "revenue"]))

# ----- hierarchies

Q("hierarchy.levels", ORD, "SELECT status, %s, count() FROM orders GROUP BY 1, 2" % SIZE, agg=["m:orders.count"], by=["d:orders.status", "d:orders.size"], post=expect_text("orders", "Fulfilment"))
Q("hierarchy.public_false", ORD, CNT_O, agg=["m:orders.count"], post=expect_no_text("orders", "internal_levels"))


# ----- pre-aggregations: the value comes through Metabase, Cube reports which rollup serves the same query

def used_rollup(query, needle):
    def post(ctx, data):
        for _ in range(40):
            status, text, _h = http("POST", CUBE + "/cubejs-api/v1/load", {"query": query})
            res = json.loads(text)
            if res.get("error") == "Continue wait":
                time.sleep(1)
                continue
            if status != 200:
                raise Fail("Cube load: HTTP %s %s" % (status, text[:300]))
            used = json.dumps(res.get("usedPreAggregations") or {})
            if needle not in used:
                raise Fail("Cube did not serve the query from %s: %s" % (needle, used[:300]))
            return
        raise Fail("Cube load: timeout")
    return post


Q("preagg.rollup", ORD, "SELECT status, sum(amount) FROM orders GROUP BY status", agg=["m:orders.revenue"], by=["d:orders.status"],
  post=used_rollup({"measures": ["orders.revenue"], "dimensions": ["orders.status"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month"}]}, "revenue_by_status_month"))
Q("preagg.partitioned", ORD, "SELECT toStartOfMonth(created_at), count() FROM orders GROUP BY 1", agg=["m:orders.count"], by=MONTH,
  post=used_rollup({"measures": ["orders.count"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "day"}]}, "count_by_day_partitioned"))
Q("preagg.indexes", ORD, "SELECT status, sumIf(amount, %s) FROM orders GROUP BY status" % PAID, agg=["m:orders.paid_revenue"], by=["d:orders.status"],
  post=used_rollup({"measures": ["orders.paid_revenue"], "dimensions": ["orders.status"]}, "revenue_by_status_indexed"))
Q("preagg.original_sql", "lambda_orders", "SELECT status, sum(amount) FROM orders GROUP BY status", agg=["m:lambda_orders.revenue"], by=["d:lambda_orders.status"],
  post=used_rollup({"measures": ["lambda_orders.revenue"], "dimensions": ["lambda_orders.status"], "timeDimensions": [{"dimension": "lambda_orders.created_at", "granularity": "day"}]}, "daily"))

# ----- SQL API through a native query

STATUS_REV = "SELECT status, sum(amount) FROM orders GROUP BY status"
N("sqlapi.measure_function", "SELECT status, MEASURE(revenue) AS r FROM orders GROUP BY 1", STATUS_REV)
N("sqlapi.aggregate_function", "SELECT status, SUM(revenue) AS r FROM orders GROUP BY 1", STATUS_REV)
N("sqlapi.ungrouped", "SELECT id, status FROM orders WHERE id = 7", "SELECT id, status FROM orders WHERE id = 7")
N("sqlapi.date_trunc", "SELECT DATE_TRUNC('month', created_at) AS m, MEASURE(count) AS n FROM orders GROUP BY 1", "SELECT toStartOfMonth(created_at), count() FROM orders GROUP BY 1")
N("sqlapi.where_like", "SELECT MEASURE(count) AS n FROM orders WHERE status LIKE 'comp%'", "SELECT count() FROM orders WHERE status LIKE 'comp%'")
N("sqlapi.where_in", "SELECT MEASURE(count) AS n FROM orders WHERE status IN ('completed', 'shipped')", "SELECT count() FROM orders WHERE %s" % PAID)
N("sqlapi.where_between", "SELECT MEASURE(count) AS n FROM orders WHERE created_at >= '2025-02-01' AND created_at < '2025-03-01'",
  "SELECT count() FROM orders WHERE created_at >= '2025-02-01' AND created_at < '2025-03-01'")
N("sqlapi.where_is_null", "SELECT MEASURE(count) AS n FROM orders WHERE cancel_status IS NULL", "SELECT count() FROM orders WHERE status = 'completed'")
N("sqlapi.segment_in_where", "SELECT MEASURE(count) AS n FROM orders WHERE paid IS TRUE", "SELECT count() FROM orders WHERE %s" % PAID)
N("sqlapi.having", "SELECT status, MEASURE(count) AS n FROM orders GROUP BY 1 HAVING MEASURE(count) > 1500", "SELECT status, count() FROM orders GROUP BY status HAVING count() > 1500")
N("sqlapi.order_limit", "SELECT status, MEASURE(count) AS n FROM orders GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 2",
  "SELECT status, count() AS n FROM orders GROUP BY status ORDER BY n DESC, status LIMIT 2", ordered=True)
N("sqlapi.post_processing", "SELECT UPPER(status) AS s, n * 2 AS d FROM (SELECT status, MEASURE(count) AS n FROM orders GROUP BY 1) t", "SELECT upper(status), 2 * count() FROM orders GROUP BY status")
N("sqlapi.pushdown_window", "SELECT status, ROW_NUMBER() OVER (ORDER BY status) AS r FROM (SELECT status FROM orders GROUP BY 1) t",
  "SELECT status, row_number() OVER (ORDER BY status) FROM (SELECT status FROM orders GROUP BY 1)")
N("sqlapi.union", "SELECT MEASURE(count) AS n FROM orders WHERE status = 'shipped' UNION ALL SELECT MEASURE(count) AS n FROM orders WHERE status = 'completed'",
  "SELECT countIf(status = 'shipped') FROM orders UNION ALL SELECT countIf(status = 'completed') FROM orders")

# ----- ClickHouse behaviour that reaches Metabase through Cube

Q("clickhouse.decimal", ORD, "SELECT amount FROM orders WHERE id = 7", fields=["d:orders.amount_dim"], where=ID7, post=expect_numeric)
Q("clickhouse.low_cardinality", ORD, "SELECT status FROM orders GROUP BY status", by=["d:orders.status"])
Q("clickhouse.datetime_utc", ORD, "SELECT created_at FROM orders WHERE id = 7", fields=["d:orders.created_at"], where=ID7)
Q("clickhouse.bool", "users", "SELECT is_vip, count() FROM users GROUP BY is_vip", agg=["m:users.count"], by=["d:users.is_vip"])
Q("clickhouse.function_in_measure", ORD, "SELECT status, arrayStringConcat(arraySort(groupUniqArray(status)), ',') FROM orders GROUP BY status", agg=["m:orders.statuses"], by=["d:orders.status"])
Q("clickhouse.nullable_expression", ORD, "SELECT nullIf(status, 'completed'), count() FROM orders GROUP BY 1", agg=["m:orders.count"], by=["d:orders.cancel_status"])
Q("clickhouse.large_result", ORD, "SELECT id FROM orders ORDER BY id LIMIT 2000", ordered=True, fields=["d:orders.id"], order=[["asc", "d:orders.id"]], limit=2000)

# ----- Metabase: aggregations on a dimension

Q("mb.agg.count_rows", ORD, CNT_O, agg=[["count"]])
Q("mb.agg.sum_field", ORD, "SELECT sum(quantity) FROM orders", agg=[["sum", "d:orders.quantity_dim"]])
Q("mb.agg.avg_field", ORD, "SELECT avg(quantity) FROM orders", agg=[["avg", "d:orders.quantity_dim"]])
Q("mb.agg.min_field", ORD, "SELECT min(quantity) FROM orders", agg=[["min", "d:orders.quantity_dim"]])
Q("mb.agg.max_field", ORD, "SELECT max(quantity) FROM orders", agg=[["max", "d:orders.quantity_dim"]])
Q("mb.agg.distinct", ORD, "SELECT uniqExact(status) FROM orders", agg=[["distinct", "d:orders.status"]])
Q("mb.agg.cum_sum", ORD, "SELECT m, sum(s) OVER (ORDER BY m) FROM (SELECT toStartOfQuarter(created_at) AS m, sum(quantity) AS s FROM orders GROUP BY m) ORDER BY m",
  ordered=True, agg=[["cum-sum", "d:orders.quantity_dim"]], by=["d:orders.created_at@quarter"])
Q("mb.agg.cum_count", ORD, "SELECT m, sum(s) OVER (ORDER BY m) FROM (SELECT toStartOfQuarter(created_at) AS m, count() AS s FROM orders GROUP BY m) ORDER BY m",
  ordered=True, agg=[["cum-count"]], by=["d:orders.created_at@quarter"])
Q("mb.agg.stddev", ORD, "SELECT stddevPop(quantity) FROM orders", agg=[["stddev", "d:orders.quantity_dim"]])
Q("mb.agg.variance", ORD, "SELECT varPop(quantity) FROM orders", agg=[["var", "d:orders.quantity_dim"]])
Q("mb.agg.median", ORD, "SELECT quantileExact(0.5)(amount) FROM orders", tol=0.05, agg=[["median", "d:orders.amount_dim"]])
Q("mb.agg.percentile", ORD, "SELECT quantileExact(0.9)(amount) FROM orders", tol=0.05, agg=[["percentile", "d:orders.amount_dim", 0.9]])
Q("mb.agg.sum_where", ORD, "SELECT sumIf(quantity, %s) FROM orders" % PAID, agg=[["sum-where", "d:orders.quantity_dim", ["=", "d:orders.is_paid", True]]])
Q("mb.agg.count_where", ORD, "SELECT countIf(%s) FROM orders" % PAID, agg=[["count-where", ["=", "d:orders.is_paid", True]]])
Q("mb.agg.share", ORD, "SELECT countIf(%s) / count() FROM orders" % PAID, agg=[["share", ["=", "d:orders.is_paid", True]]])
Q("mb.agg.custom_expression", ORD, "SELECT sum(amount) / count() FROM orders", agg=[["/", ["sum", "d:orders.amount_dim"], ["count"]]])
Q("mb.agg.two_measures", ORD, "SELECT status, sum(amount), count() FROM orders GROUP BY status", agg=["m:orders.revenue", "m:orders.count"], by=["d:orders.status"])
Q("mb.agg.offset_window", ORD,
  "SELECT m, lagInFrame(toNullable(s)) OVER (ORDER BY m ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM (SELECT toStartOfMonth(created_at) AS m, sum(quantity) AS s FROM orders GROUP BY m) ORDER BY m",
  ordered=True, agg=[["offset", {}, ["sum", "d:orders.quantity_dim"], -1]], by=MONTH, order=[["asc", MONTH[0]]])

# ----- Metabase: breakouts and date buckets

for _unit, _truth in [
    ("minute", "toStartOfMinute(created_at)"), ("hour", "toStartOfHour(created_at)"), ("day", "toDate(created_at)"),
    ("week", "toStartOfWeek(created_at, 0)"), ("month", "toStartOfMonth(created_at)"), ("quarter", "toStartOfQuarter(created_at)"),
    ("year", "toStartOfYear(created_at)"), ("minute-of-hour", "toMinute(created_at)"), ("hour-of-day", "toHour(created_at)"),
    ("day-of-week", "toDayOfWeek(created_at, 3)"), ("day-of-month", "toDayOfMonth(created_at)"), ("day-of-year", "toDayOfYear(created_at)"),
    ("week-of-year", "toWeek(created_at, 0) + 1"), ("month-of-year", "toMonth(created_at)"), ("quarter-of-year", "toQuarter(created_at)"),
]:
    _by = "d:orders.created_at@%s" % _unit
    Q("mb.bucket.%s" % _unit, ORD, "SELECT x, count() FROM (SELECT %s AS x FROM orders) GROUP BY x ORDER BY x LIMIT 5" % _truth, ordered=True,
      agg=[["count"]], by=[_by], order=[["asc", _by]], limit=5)
Q("mb.breakout.two", ORD, "SELECT status, quantity, count() FROM orders GROUP BY 1, 2", agg=[["count"]], by=["d:orders.status", "d:orders.quantity_dim"])
Q("mb.breakout.boolean", ORD, "SELECT %s, sum(amount) FROM orders GROUP BY 1" % PAID, agg=["m:orders.revenue"], by=["d:orders.is_paid"])
Q("mb.breakout.bin_width", ORD, "SELECT intDiv(toInt64(amount), 100) * 100 AS b, count() FROM orders GROUP BY b", agg=[["count"]], by=["d:orders.amount_dim#width=100"])
Q("mb.breakout.sorted_by_measure", ORD, "SELECT status, sum(amount) AS r FROM orders GROUP BY status ORDER BY r DESC", ordered=True,
  agg=["m:orders.revenue"], by=["d:orders.status"], order=[["desc", ["aggregation", 0]]])
Q("mb.breakout.limit", ORD, "SELECT status, sum(amount) AS r FROM orders GROUP BY status ORDER BY r DESC LIMIT 2", ordered=True,
  agg=["m:orders.revenue"], by=["d:orders.status"], order=[["desc", ["aggregation", 0]]], limit=2)


@check("mb.breakout.bin_count")
def _(ctx):
    rows = ctx.mb.mbql(ORD, agg=[["count"]], by=["d:orders.amount_dim#bins=8"])["rows"]
    if len(rows) < 2:
        raise Fail("binning returned %d rows" % len(rows))
    assert_rows([[sum(r[1] for r in rows)]], ch(CNT_O))


# ----- Metabase: filters

QTY = "d:orders.quantity_dim"
STA = "d:orders.status"
CRT = "d:orders.created_at"
for _fid, _clause, _sql in [
    ("eq_string", ["=", STA, "completed"], "status = 'completed'"),
    ("eq_multi", ["=", STA, "completed", "shipped"], PAID),
    ("neq_string", ["!=", STA, "completed"], "status != 'completed'"),
    ("eq_number", ["=", QTY, 2], "quantity = 2"),
    ("neq_number", ["!=", QTY, 2], "quantity != 2"),
    ("gt", [">", QTY, 2], "quantity > 2"),
    ("gte", [">=", QTY, 2], "quantity >= 2"),
    ("lt", ["<", QTY, 2], "quantity < 2"),
    ("lte", ["<=", QTY, 2], "quantity <= 2"),
    ("between_number", ["between", QTY, 2, 3], "quantity BETWEEN 2 AND 3"),
    ("is_null", ["is-null", "d:orders.cancel_status"], "status = 'completed'"),
    ("not_null", ["not-null", "d:orders.cancel_status"], "status != 'completed'"),
    ("is_empty", ["is-empty", "d:orders.cancel_status"], "status = 'completed'"),
    ("not_empty", ["not-empty", "d:orders.cancel_status"], "status != 'completed'"),
    ("contains", ["contains", STA, "plet"], "status LIKE '%plet%'"),
    ("contains_ci", ["contains", STA, "PLET", {"case-sensitive": False}], "status ILIKE '%PLET%'"),
    ("not_contains", ["does-not-contain", STA, "plet"], "status NOT LIKE '%plet%'"),
    ("starts_with", ["starts-with", STA, "comp"], "status LIKE 'comp%'"),
    ("starts_with_ci", ["starts-with", STA, "COMP", {"case-sensitive": False}], "status ILIKE 'COMP%'"),
    ("ends_with", ["ends-with", STA, "ped"], "status LIKE '%ped'"),
    ("boolean_true", ["=", "d:orders.is_paid", True], PAID),
    ("boolean_false", ["=", "d:orders.is_paid", False], "NOT (%s)" % PAID),
    ("date_on", ["=", CRT + "@day", "2025-02-03"], "toDate(created_at) = '2025-02-03'"),
    ("date_before", ["<", CRT, "2025-02-01"], "created_at < '2025-02-01'"),
    ("date_after", [">", CRT, "2025-12-01"], "created_at > '2025-12-01'"),
    ("date_between", ["between", CRT, "2025-02-01", "2025-02-28"], "toDate(created_at) BETWEEN '2025-02-01' AND '2025-02-28'"),
    ("date_month", ["=", CRT + "@month", "2025-02-01"], "toStartOfMonth(created_at) = '2025-02-01'"),
    ("relative_past", ["time-interval", CRT, -1000, "day"], "toDate(created_at) >= today() - 1000 AND toDate(created_at) < today()"),
    ("relative_current", ["time-interval", CRT, "current", "year"], "toYear(created_at) = toYear(today())"),
    ("exclude_day_of_week", ["!=", CRT + "@day-of-week", 2], "toDayOfWeek(created_at, 3) != 2"),
    ("exclude_month_of_year", ["!=", CRT + "@month-of-year", 2], "toMonth(created_at) != 2"),
    ("and", ["and", ["=", STA, "completed"], [">", QTY, 2]], "status = 'completed' AND quantity > 2"),
    ("or", ["or", ["=", STA, "shipped"], [">", QTY, 3]], "status = 'shipped' OR quantity > 3"),
    ("not", ["not", ["=", STA, "shipped"]], "NOT status = 'shipped'"),
]:
    Q("mb.filter.%s" % _fid, ORD, "SELECT count() FROM orders WHERE %s" % _sql, agg=["m:orders.count"], where=[_clause])

# ----- Metabase: raw rows and expressions

Q("mb.raw.fields_sorted", ORD, "SELECT id, status FROM orders ORDER BY id LIMIT 5", ordered=True, fields=["d:orders.id", "d:orders.status"], order=[["asc", "d:orders.id"]], limit=5)
Q("mb.raw.sort_desc", ORD, "SELECT id FROM orders ORDER BY id DESC LIMIT 3", ordered=True, fields=["d:orders.id"], order=[["desc", "d:orders.id"]], limit=3)

_AMT = "d:orders.amount_dim"
for _name, _clause, _sql in [
    ("add", ["+", QTY, 1], "quantity + 1"),
    ("mul", ["*", QTY, 2], "quantity * 2"),
    ("div", ["/", _AMT, QTY], "amount / quantity"),
    ("case", ["case", [[[">", QTY, 2], "big"]], {"default": "small"}], "if(quantity > 2, 'big', 'small')"),
    ("coalesce", ["coalesce", "d:orders.cancel_status", "done"], "coalesce(nullIf(status, 'completed'), 'done')"),
    ("concat", ["concat", STA, "-x"], "concat(status, '-x')"),
    ("upper", ["upper", STA], "upper(status)"),
    ("lower", ["lower", STA], "lower(status)"),
    ("length", ["length", STA], "length(status)"),
    ("substring", ["substring", STA, 1, 3], "substring(status, 1, 3)"),
    ("replace", ["replace", STA, "e", "E"], "replaceAll(status, 'e', 'E')"),
    ("abs", ["abs", QTY], "abs(quantity)"),
    ("round", ["round", ["/", _AMT, 7]], "round(amount / 7)"),
    ("ceil", ["ceil", ["/", _AMT, 7]], "ceil(amount / 7)"),
    ("floor", ["floor", ["/", _AMT, 7]], "floor(amount / 7)"),
    ("power", ["power", QTY, 2], "pow(quantity, 2)"),
    ("sqrt", ["sqrt", QTY], "sqrt(quantity)"),
    ("log", ["log", _AMT], "log10(toFloat64(amount))"),
    ("get_year", ["get-year", CRT], "toYear(created_at)"),
    ("get_month", ["get-month", CRT], "toMonth(created_at)"),
    ("datetime_add", ["datetime-add", CRT, 1, "day"], "created_at + INTERVAL 1 DAY"),
    ("datetime_diff", ["datetime-diff", CRT, ["datetime-add", CRT, 3, "day"], "day"], "3"),
]:
    Q("mb.expr.%s" % _name, ORD, "SELECT %s FROM orders WHERE id = 7" % _sql, fields=[["expression", _name]], exprs={_name: _clause}, where=ID7)


# ----- metadata


@check("meta.tables_match_public_members")
def _(ctx):
    ctx.mb.need_db()
    want = {"orders", "users", "products", "payments", "promos", "shipments", "sales", "paid_sales", "promo_sales",
            "calendar_sales", "extended_sales", "restricted_sales", "masked_sales", "filtered_orders", "lambda_orders",
            "cheap_products", "fiscal_calendar"}
    got = {n for n, t in ctx.mb.tables.items() if t.get("visibility_type") is None}
    if want - got:
        raise Fail("missing tables: %s" % sorted(want - got))
    if got - want:
        raise Fail("extra tables: %s" % sorted(got - want))
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.count"])["rows"], ch(CNT_O))


@check("meta.fk_implicit_target")
def _(ctx):
    fk = ctx.mb.fk_field("orders", "users")
    if fk["name"] != "user_id":
        raise Fail("foreign key is %s" % fk["name"])
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.count"], by=["d:orders>users.city"])["rows"],
                ch("SELECT u.city, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY u.city"))


@check("meta.base_types")
def _(ctx):
    for name, base in {"status": "type/Text", "created_at": "type/DateTime", "is_paid": "type/Boolean"}.items():
        got = ctx.mb.field(ORD, name)["base_type"]
        if not got.startswith(base):
            raise Fail("orders.%s base_type %s, want %s" % (name, got, base))
    numeric = ctx.mb.field(ORD, "amount_dim")["base_type"]
    if numeric not in ("type/Decimal", "type/Float", "type/Integer", "type/BigInteger", "type/Number"):
        raise Fail("orders.amount_dim base_type %s" % numeric)
    assert_rows(ctx.mb.mbql(ORD, fields=["d:orders.amount_dim"], where=ID7)["rows"], ch("SELECT amount FROM orders WHERE id = 7"))


@check("meta.fingerprint_distinct_count")
def _(ctx):
    fp = ((ctx.mb.field(ORD, "status").get("fingerprint") or {}).get("global") or {}).get("distinct-count")
    assert_rows([[fp]], ch("SELECT uniqExact(status) FROM orders"))


@check("meta.semantic_types")
def _(ctx):
    for field, sem in [("name", "type/Name"), ("email", "type/Email"), ("city", "type/City"), ("state", "type/State")]:
        got = ctx.mb.field("users", field).get("semantic_type")
        if got != sem:
            raise Fail("users.%s semantic_type %s, want %s" % (field, got, sem))
    assert_rows(ctx.mb.mbql("users", fields=["d:users.email"], where=[["=", "d:users.id", 1]])["rows"], ch("SELECT email FROM users WHERE id = 1"))


@check("meta.no_internal_columns")
def _(ctx):
    for table in ("orders", "users", "sales"):
        for f in ctx.mb.table(table)["fields"]:
            if f["name"].startswith("__") and f.get("visibility_type") not in ("hidden", "retired", "sensitive"):
                raise Fail("internal column %s.%s is visible" % (table, f["name"]))
    assert_rows(ctx.mb.mbql("users", agg=["m:users.count"])["rows"], ch(CNT_U))


@check("meta.measure_is_marked")
def _(ctx):
    if not ctx.mb.entity(ctx.mb.measures, ORD, "revenue"):
        f = ctx.mb.field(ORD, "revenue")
        if f.get("semantic_type") not in ("type/Quantity", "type/Currency", "type/Share", "type/Percentage"):
            raise Fail("orders.revenue looks like a plain column: %s" % f.get("semantic_type"))
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.revenue"])["rows"], ch(REV))


@check("meta.resync_keeps_metadata")
def _(ctx):
    db = ctx.mb.need_db()
    ctx.mb.api("POST", "/api/database/%d/sync_schema" % db)
    time.sleep(5)
    meta = ctx.mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % db)
    if "orders" not in {t["name"].lower() for t in meta["tables"]}:
        raise Fail("orders is missing after a sync")
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.count"])["rows"], ch(CNT_O))


# ----- Metabase: field values and search


@check("mb.field_values.list")
def _(ctx):
    vals = ctx.mb.api("GET", "/api/field/%d/values" % ctx.mb.field(ORD, "status")["id"])["values"]
    assert_rows(values_of(vals), ch("SELECT DISTINCT status FROM orders"))


@check("mb.field_values.search")
def _(ctx):
    fid = ctx.mb.field("users", "city")["id"]
    vals = ctx.mb.api("GET", "/api/field/%d/search/%d?value=ost" % (fid, fid))
    assert_rows(values_of(vals), ch("SELECT DISTINCT city FROM users WHERE city LIKE '%ost%'"))


@check("mb.field_values.boolean")
def _(ctx):
    vals = ctx.mb.api("GET", "/api/field/%d/values" % ctx.mb.field("users", "is_vip")["id"])["values"]
    assert_rows(values_of(vals), ch("SELECT DISTINCT is_vip FROM users"))


def search(ctx, q, *models):
    params = [("q", q), ("table_db_id", ctx.mb.need_db())] + [("models", m) for m in models]
    return ctx.mb.api("GET", "/api/search?" + urllib.parse.urlencode(params))["data"]


@check("mb.search.table")
def _(ctx):
    hit = [r for r in search(ctx, "sales", "table") if r["name"].lower() == "sales"]
    if not hit:
        raise Fail("search found no table sales")
    assert_rows(ctx.mb.raw({"source-table": hit[0]["id"], "aggregation": [ctx.mb.measure_clause("sales", "count")]})["rows"], ch(CNT_O))


@check("mb.search.by_description")
def _(ctx):
    res = search(ctx, "account shop", "table")
    if ctx.mb.table("users")["id"] not in [r["id"] for r in res]:
        raise Fail("search by description did not find users: %s" % [r["name"] for r in res][:10])
    assert_rows(ctx.mb.mbql("users", agg=["m:users.count"])["rows"], ch(CNT_U))


@check("mb.search.measure")
def _(ctx):
    res = search(ctx, "Revenue", "measure")
    hit = [r for r in res if r.get("model") == "measure" and _slug(r["name"]) == "revenue"]
    if not hit:
        raise Fail("search has no measure Revenue. Found: %s" % [(r.get("model"), r["name"]) for r in res][:10])
    tid = hit[0].get("table_id") or ctx.mb.table(ORD)["id"]
    assert_rows(ctx.mb.raw({"source-table": tid, "aggregation": [["measure", hit[0]["id"]]]})["rows"], ch(REV))


@check("mb.search.segment")
def _(ctx):
    res = search(ctx, "Paid", "segment")
    hit = [r for r in res if r.get("model") == "segment" and _slug(r["name"]) == "paid"]
    if not hit:
        raise Fail("search has no segment Paid. Found: %s" % [(r.get("model"), r["name"]) for r in res][:10])
    query = {"source-table": ctx.mb.table(ORD)["id"], "aggregation": [["count"]], "filter": ["segment", hit[0]["id"]]}
    assert_rows(ctx.mb.raw(query)["rows"], ch("SELECT count() FROM orders WHERE %s" % PAID))


# ----- Metabase: joins and nesting

ORD_USERS = "SELECT u.%s, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY u.%s"


@check("mb.join.implicit")
def _(ctx):
    assert_rows(ctx.mb.mbql(ORD, agg=[["count"]], by=["d:orders>users.state"])["rows"], ch(ORD_USERS % ("state", "state")))


@check("mb.join.implicit_filter")
def _(ctx):
    assert_rows(ctx.mb.mbql(ORD, agg=[["count"]], where=[["=", "d:orders>users.city", "Boston"]])["rows"],
                ch("SELECT count() FROM orders o JOIN users u ON o.user_id = u.id WHERE u.city = 'Boston'"))


def explicit_join(ctx, strategy="left-join"):
    return [{"source-table": ctx.mb.table("users")["id"], "alias": "U", "strategy": strategy, "condition": ["=", "d:orders.user_id", "d:users.id|U"]}]


@check("mb.join.explicit_left")
def _(ctx):
    assert_rows(ctx.mb.mbql(ORD, agg=[["count"]], by=["d:users.city|U"], joins=explicit_join(ctx))["rows"], ch(ORD_USERS % ("city", "city")))


@check("mb.join.explicit_inner")
def _(ctx):
    assert_rows(ctx.mb.mbql(ORD, agg=[["count"]], by=["d:users.city|U"], joins=explicit_join(ctx, "inner-join"))["rows"], ch(ORD_USERS % ("city", "city")))


@check("mb.join.measure_across_cubes")
def _(ctx):
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.revenue"], by=["d:users.is_vip|U"], joins=explicit_join(ctx))["rows"],
                ch("SELECT u.is_vip, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY u.is_vip"))


@check("mb.nested.filter_on_aggregation")
def _(ctx):
    inner = ctx.mb.inner(ORD, agg=["m:orders.revenue"], by=["d:orders.status"])
    query = {"source-query": inner, "filter": [">", ["field", "sum", {"base-type": "type/Decimal"}], 70000]}
    assert_rows(ctx.mb.raw(query)["rows"], ch("SELECT status, sum(amount) AS r FROM orders GROUP BY status HAVING r > 70000"))


@check("mb.nested.question_as_source")
def _(ctx):
    card = ctx.mb.mbql_card("source", ORD, agg=["m:orders.count"], by=["d:orders.status"])
    assert_rows(ctx.mb.raw({"source-table": "card__%d" % card["id"], "aggregation": [["count"]]})["rows"], ch("SELECT uniqExact(status) FROM orders"))


@check("mb.card.saved_question")
def _(ctx):
    card = ctx.mb.mbql_card("saved", ORD, agg=["m:orders.revenue"], by=["d:orders.status"])
    assert_rows(ctx.mb.run_card(card["id"])["rows"], ch(STATUS_REV))


@check("mb.card.model")
def _(ctx):
    card = ctx.mb.mbql_card("model", ORD, kind="model", agg=["m:orders.revenue"], by=["d:orders.status"])
    assert_rows(ctx.mb.raw({"source-table": "card__%d" % card["id"], "aggregation": [["count"]]})["rows"], ch("SELECT uniqExact(status) FROM orders"))


@check("mb.card.metric")
def _(ctx):
    card = ctx.mb.mbql_card("metric", ORD, kind="metric", agg=["m:orders.revenue"])
    assert_rows(ctx.mb.run_card(card["id"])["rows"], ch(REV))


@check("mb.card.metric_with_breakout")
def _(ctx):
    card = ctx.mb.mbql_card("metric breakout", ORD, kind="metric", agg=["m:orders.revenue"])
    query = {"source-table": "card__%d" % card["id"], "aggregation": [["metric", card["id"]]], "breakout": [ctx.mb.ref("d:orders.status")]}
    assert_rows(ctx.mb.raw(query)["rows"], ch(STATUS_REV))


@check("mb.entity.segment_custom")
def _(ctx):
    t = ctx.mb.table(ORD)["id"]
    seg = ctx.mb.api("POST", "/api/segment", {"name": "conformance shipped", "table_id": t, "description": "conformance",
                                              "definition": {"source-table": t, "filter": ["=", ctx.mb.ref(STA), "shipped"]}})
    ctx.mb.created.append(("/api/segment/%d" % seg["id"], {"archived": True, "revision_message": "conformance run"}))
    assert_rows(ctx.mb.raw({"source-table": t, "aggregation": [["count"]], "filter": ["segment", seg["id"]]})["rows"], ch("SELECT count() FROM orders WHERE status = 'shipped'"))


@check("mb.entity.measure_custom")
def _(ctx):
    t = ctx.mb.table(ORD)["id"]
    definition = {"database": ctx.mb.db, "type": "query", "query": {"source-table": t, "aggregation": [["sum", ctx.mb.ref(QTY)]]}}
    m = ctx.mb.api("POST", "/api/measure", {"name": "conformance units", "table_id": t, "description": "conformance", "definition": definition})
    ctx.mb.created.append(("/api/measure/%d" % m["id"], {"archived": True, "revision_message": "conformance run"}))
    assert_rows(ctx.mb.raw({"source-table": t, "aggregation": [["measure", m["id"]]]})["rows"], ch("SELECT sum(quantity) FROM orders"))


# ----- Metabase: exports


def export(ctx, fmt):
    query = {"database": ctx.mb.need_db(), "type": "query", "query": ctx.mb.inner(ORD, agg=["m:orders.count"], by=[STA], order=[["asc", STA]])}
    status, text, _h = http("POST", MB + "/api/dataset/%s" % fmt, {"query": json.dumps(query)}, {"X-Metabase-Session": ctx.mb.sid}, form=True)
    if status not in (200, 202):
        raise Fail("export %s: HTTP %s %s" % (fmt, status, text[:200]))
    return text


STATUS_CNT = "SELECT status, count() FROM orders GROUP BY status"


@check("mb.export.csv")
def _(ctx):
    rows = [line.split(",") for line in export(ctx, "csv").strip().splitlines()[1:]]
    assert_rows([[r[0], float(r[1])] for r in rows], ch(STATUS_CNT))


@check("mb.export.json")
def _(ctx):
    assert_rows([list(r.values()) for r in json.loads(export(ctx, "json"))], ch(STATUS_CNT))


# ----- Metabase: native query parameters


def tag(name, kind, **extra):
    return {name: dict({"id": name, "name": name, "display-name": name, "type": kind}, **extra)}


def field_tag(ctx, name, ref, widget):
    return tag(name, "dimension", dimension=["field", ctx.mb.ref(ref)[1], None], **{"widget-type": widget})


SHIPPED = "SELECT count() FROM orders WHERE status = 'shipped'"
FEB = "SELECT count() FROM orders WHERE toDate(created_at) BETWEEN '2025-02-01' AND '2025-02-28'"
N("mb.native.plain", "SELECT status, MEASURE(count) AS n FROM orders GROUP BY 1", STATUS_CNT)
N("mb.native.param_text", "SELECT MEASURE(count) AS n FROM orders WHERE status = {{s}}", SHIPPED, tags=tag("s", "text"),
  parameters=[{"type": "category", "target": ["variable", ["template-tag", "s"]], "value": "shipped"}])
N("mb.native.param_number", "SELECT MEASURE(count) AS n FROM orders WHERE quantity_dim = {{q}}", "SELECT count() FROM orders WHERE quantity = 2", tags=tag("q", "number"),
  parameters=[{"type": "number/=", "target": ["variable", ["template-tag", "q"]], "value": "2"}])
N("mb.native.param_date", "SELECT MEASURE(count) AS n FROM orders WHERE created_at >= {{d}}", "SELECT count() FROM orders WHERE created_at >= '2025-12-01'", tags=tag("d", "date"),
  parameters=[{"type": "date/single", "target": ["variable", ["template-tag", "d"]], "value": "2025-12-01"}])
N("mb.native.optional_clause", "SELECT MEASURE(count) AS n FROM orders WHERE 1 = 1 [[AND status = {{s}}]]", CNT_O, tags=tag("s", "text"))


@check("mb.native.field_filter")
def _(ctx):
    data = ctx.mb.native("SELECT MEASURE(count) AS n FROM orders WHERE {{f}}", tags=field_tag(ctx, "f", STA, "string/="),
                         parameters=[{"type": "string/=", "target": ["dimension", ["template-tag", "f"]], "value": ["shipped"]}])
    assert_rows(data["rows"], ch(SHIPPED))


@check("mb.native.field_filter_date")
def _(ctx):
    data = ctx.mb.native("SELECT MEASURE(count) AS n FROM orders WHERE {{f}}", tags=field_tag(ctx, "f", CRT, "date/all-options"),
                         parameters=[{"type": "date/all-options", "target": ["dimension", ["template-tag", "f"]], "value": "2025-02-01~2025-02-28"}])
    assert_rows(data["rows"], ch(FEB))


@check("mb.native.saved_card_param")
def _(ctx):
    query = {"database": ctx.mb.need_db(), "type": "native", "native": {"query": "SELECT MEASURE(count) AS n FROM orders WHERE status = {{s}}", "template-tags": tag("s", "text")}}
    card = ctx.mb.card("native param", query)
    rows = ctx.mb.run_card(card["id"], [{"type": "category", "target": ["variable", ["template-tag", "s"]], "value": "shipped"}])["rows"]
    assert_rows(rows, ch(SHIPPED))


# ----- Metabase: dashboards


def dash_with_param(ctx, name, ptype, section, ref):
    card = ctx.mb.mbql_card("dash " + name, ORD, agg=["m:orders.count"])
    param = {"id": "p1", "name": "P", "slug": "p", "type": ptype, "sectionId": section}
    target = ["dimension", ctx.mb.ref(ref)]
    return ctx.mb.dashboard(name, [param], [(card["id"], [("p1", target)])]), target


def run_param(ctx, dash, target, ptype, value):
    return ctx.mb.run_dashcard(dash, 0, [{"id": "p1", "type": ptype, "value": value, "target": target}])["rows"]


@check("mb.dashboard.filter_string")
def _(ctx):
    dash, target = dash_with_param(ctx, "string", "string/=", "string", STA)
    assert_rows(run_param(ctx, dash, target, "string/=", ["shipped"]), ch(SHIPPED))


@check("mb.dashboard.filter_number")
def _(ctx):
    dash, target = dash_with_param(ctx, "number", "number/>=", "number", QTY)
    assert_rows(run_param(ctx, dash, target, "number/>=", [3]), ch("SELECT count() FROM orders WHERE quantity >= 3"))


@check("mb.dashboard.filter_date")
def _(ctx):
    dash, target = dash_with_param(ctx, "date", "date/all-options", "date", CRT)
    assert_rows(run_param(ctx, dash, target, "date/all-options", "2025-02-01~2025-02-28"), ch(FEB))


@check("mb.dashboard.filter_values")
def _(ctx):
    dash, _t = dash_with_param(ctx, "values", "string/=", "string", STA)
    vals = ctx.mb.api("GET", "/api/dashboard/%d/params/p1/values" % dash["id"])["values"]
    assert_rows(values_of(vals), ch("SELECT DISTINCT status FROM orders"))


@check("mb.dashboard.filter_search")
def _(ctx):
    dash, _t = dash_with_param(ctx, "psearch", "string/=", "string", STA)
    vals = ctx.mb.api("GET", "/api/dashboard/%d/params/p1/search/plet" % dash["id"])["values"]
    assert_rows(values_of(vals), ch("SELECT DISTINCT status FROM orders WHERE status LIKE '%plet%'"))


@check("mb.dashboard.linked_filter")
def _(ctx):
    card = ctx.mb.mbql_card("dash linked", "users", agg=["m:users.count"])
    params = [{"id": "st", "name": "State", "slug": "state", "type": "string/=", "sectionId": "string"},
              {"id": "ci", "name": "City", "slug": "city", "type": "string/=", "sectionId": "string", "filteringParameters": ["st"]}]
    wires = [("st", ["dimension", ctx.mb.ref("d:users.state")]), ("ci", ["dimension", ctx.mb.ref("d:users.city")])]
    dash = ctx.mb.dashboard("linked", params, [(card["id"], wires)])
    vals = ctx.mb.api("GET", "/api/dashboard/%d/params/ci/values?st=TX" % dash["id"])["values"]
    assert_rows(values_of(vals), ch("SELECT DISTINCT city FROM users WHERE state = 'TX'"))


@check("mb.dashboard.native_field_filter")
def _(ctx):
    card = ctx.mb.card("dash native", {"database": ctx.mb.need_db(), "type": "native",
                                       "native": {"query": "SELECT MEASURE(count) AS n FROM orders WHERE {{f}}", "template-tags": field_tag(ctx, "f", STA, "string/=")}})
    target = ["dimension", ["template-tag", "f"]]
    dash = ctx.mb.dashboard("native", [{"id": "p1", "name": "P", "slug": "p", "type": "string/=", "sectionId": "string"}], [(card["id"], [("p1", target)])])
    assert_rows(run_param(ctx, dash, target, "string/=", ["shipped"]), ch(SHIPPED))


# ----- Metabase: X-rays


def xray_count(ctx, table, sql):
    dash = ctx.mb.api("GET", "/api/automagic-dashboards/table/%d" % ctx.mb.table(table)["id"])
    cards = [c["card"] for c in dash.get("dashcards", []) if c.get("card")]
    if not cards:
        raise Fail("X-ray returned no cards")
    for card in cards:
        dq = card.get("dataset_query") or {}
        q = dq.get("query") or (dq.get("stages") or [{}])[0]
        aggs = q.get("aggregation") or []
        if len(aggs) == 1 and aggs[0][0] == "count" and not (q.get("breakout") or q.get("filter") or q.get("filters")):
            assert_rows(ctx.mb.dataset(dict(dq, database=ctx.mb.db))["data"]["rows"], ch(sql))
            return
    raise Fail("X-ray has %d cards, none is a plain count to compare with the truth" % len(cards))


@check("mb.xray.table")
def _(ctx):
    xray_count(ctx, ORD, CNT_O)


@check("mb.xray.view")
def _(ctx):
    xray_count(ctx, "sales", CNT_O)


@check("mb.xray.candidates")
def _(ctx):
    res = ctx.mb.api("GET", "/api/automagic-dashboards/database/%d/candidates" % ctx.mb.need_db())
    if not [t for c in res for t in c.get("tables", [])]:
        raise Fail("no X-ray candidates")
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.count"])["rows"], ch(CNT_O))


@check("mb.report_timezone")
def _(ctx):
    ctx.mb.need_db()
    prev = ctx.mb.api("GET", "/api/setting/report-timezone")
    ctx.mb.api("PUT", "/api/setting/report-timezone", {"value": "Asia/Tokyo"})
    try:
        rows = ctx.mb.mbql(ORD, agg=[["count"]], by=["d:orders.created_at@year"])["rows"]
    finally:
        ctx.mb.api("PUT", "/api/setting/report-timezone", {"value": prev if isinstance(prev, str) and prev else None})
    want = ch("SELECT toString(toStartOfYear(toTimeZone(created_at, 'Asia/Tokyo'))) AS y, count() FROM orders GROUP BY y")
    assert_rows([[str(r[0])[:10], r[1]] for r in rows], [[str(r[0])[:10], r[1]] for r in want])


# ----- MCP


@check("mcp.connect_oauth")
def _(ctx):
    names = {t["name"] for t in ctx.mcp.rpc("tools/list")["tools"]}
    want = {"search", "get_content", "execute_query", "execute_sql", "question_write", "dashboard_write", "get_parameter_values", "browse_data"}
    if want - names:
        raise Fail("tools missing: %s" % sorted(want - names))
    mcp_rows(ctx, ORD, CNT_O, agg=["m:orders.count"])


@check("mcp.execute_query.measure")
def _(ctx):
    mcp_rows(ctx, ORD, REV, agg=["m:orders.revenue"])


@check("mcp.execute_query.breakout")
def _(ctx):
    mcp_rows(ctx, ORD, STATUS_REV, agg=["m:orders.revenue"], by=[STA])


@check("mcp.execute_query.date_bucket")
def _(ctx):
    mcp_rows(ctx, ORD, "SELECT toStartOfQuarter(created_at), count() FROM orders GROUP BY 1", agg=["m:orders.count"], by=["d:orders.created_at@quarter"])


@check("mcp.execute_query.filter")
def _(ctx):
    mcp_rows(ctx, ORD, SHIPPED, agg=["m:orders.count"], where=[["=", STA, "shipped"]])


@check("mcp.execute_query.segment")
def _(ctx):
    mcp_rows(ctx, ORD, "SELECT count() FROM orders WHERE %s" % PAID, agg=["m:orders.count"], where=["s:orders.paid"])


@check("mcp.execute_query.view")
def _(ctx):
    mcp_rows(ctx, "sales", "SELECT u.city, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY u.city", agg=["m:sales.revenue"], by=["d:sales.users_city"])


@check("mcp.execute_query.implicit_join")
def _(ctx):
    mcp_rows(ctx, ORD, ORD_USERS % ("city", "city"), agg=["m:orders.count"], by=["d:orders>users.city"])


@check("mcp.execute_query.paging")
def _(ctx):
    query = mcp_query(ctx.mb, ORD, by=["d:orders.id"])
    query["stages"][0]["order-by"] = [["asc", {}, to_mbql5(ctx.mb.ref("d:orders.id"))]]
    query["stages"][0]["limit"] = 6
    page1 = ctx.mcp.call("execute_query", query=query, row_limit=3)
    if not page1.get("next_cursor"):
        raise Fail("no next_cursor: %s" % str(page1)[:200])
    page2 = ctx.mcp.call("execute_query", cursor=page1["next_cursor"])
    assert_rows(page1["rows"] + page2["rows"], ch("SELECT id FROM orders ORDER BY id LIMIT 6"), ordered=True)


@check("mcp.execute_sql")
def _(ctx):
    data = ctx.mcp.call("execute_sql", database_id=ctx.mb.need_db(), sql="SELECT status, MEASURE(revenue) AS r FROM orders GROUP BY 1")
    assert_rows(data["rows"], ch(STATUS_REV))


@check("mcp.search.table_by_description")
def _(ctx):
    res = ctx.mcp.call("search", term_queries=["Customer orders"], type=["table"])["data"]
    tid = ctx.mb.table(ORD)["id"]
    if tid not in [r["id"] for r in res if r["type"] == "table"]:
        raise Fail("search did not return the orders table: %s" % [(r["id"], r["name"]) for r in res][:8])
    assert_rows(ctx.mcp.call("execute_query", query=mcp_stage(tid, aggregation=[["count", {}]]))["rows"], ch(CNT_O))


@check("mcp.search.view_description")
def _(ctx):
    res = ctx.mcp.call("search", term_queries=["Curated view of orders"], type=["table"])["data"]
    if ctx.mb.table("sales")["id"] not in [r["id"] for r in res]:
        raise Fail("search did not return the sales view: %s" % [r["name"] for r in res][:8])
    mcp_rows(ctx, "sales", REV, agg=["m:sales.revenue"])


def table_of_measure(ctx, measure_id):
    tid = deep_find(ctx.mcp.call("get_content", items=[{"type": "measure", "id": measure_id}]), "table_id")
    if tid is None:
        raise Fail("get_content does not name the table of measure %s" % measure_id)
    return tid


@check("mcp.search.measure")
def _(ctx):
    res = ctx.mcp.call("search", term_queries=["Revenue"], type=["measure"])["data"]
    hit = [r for r in res if _slug(r["name"]) == "revenue"]
    if not hit:
        raise Fail("search has no measure Revenue: %s" % [r["name"] for r in res][:8])
    query = mcp_stage(table_of_measure(ctx, hit[0]["id"]), aggregation=[["measure", {}, hit[0]["id"]]])
    assert_rows(ctx.mcp.call("execute_query", query=query)["rows"], ch(REV))


@check("mcp.search.segment")
def _(ctx):
    res = ctx.mcp.call("search", term_queries=["Paid"], type=["segment"])["data"]
    hit = [r for r in res if _slug(r["name"]) == "paid"]
    if not hit:
        raise Fail("search has no segment Paid: %s" % [r["name"] for r in res][:8])
    query = mcp_stage(ctx.mb.table(ORD)["id"], aggregation=[["count", {}]], filters=[["segment", {}, hit[0]["id"]]])
    assert_rows(ctx.mcp.call("execute_query", query=query)["rows"], ch("SELECT count() FROM orders WHERE %s" % PAID))


def schema_args(ctx):
    names = ctx.mcp.call("browse_data", action="list_schemas", database_id=ctx.mb.need_db()).get("data") or []
    if not names:
        return {}
    return {"schema": names[0].get("name") if isinstance(names[0], dict) else names[0]}


@check("mcp.browse.list_databases")
def _(ctx):
    res = ctx.mcp.call("browse_data", action="list_databases")["data"]
    if ctx.mb.need_db() not in [d["id"] for d in res]:
        raise Fail("database %s not listed" % ctx.mb.db)
    mcp_rows(ctx, ORD, CNT_O, agg=["m:orders.count"])


@check("mcp.browse.list_tables")
def _(ctx):
    res = ctx.mcp.call("browse_data", action="list_tables", database_id=ctx.mb.need_db(), limit=100, **schema_args(ctx))["data"]
    names = {t["name"].lower() for t in res}
    if not {"orders", "users", "sales"} <= names or "secret_audit" in names:
        raise Fail("tables: %s" % sorted(names))
    mcp_rows(ctx, "users", CNT_U, agg=["m:users.count"])


@check("mcp.browse.get_fields_measures")
def _(ctx):
    tid = ctx.mb.table(ORD)["id"]
    measures = {_slug(m["name"]): m for m in get_fields_table(ctx, tid).get("measures") or []}
    if "revenue" not in measures:
        raise Fail("get_fields lists no measure Revenue. It lists: %s" % sorted(measures)[:10])
    query = mcp_stage(tid, aggregation=[["measure", {}, measures["revenue"]["id"]]])
    assert_rows(ctx.mcp.call("execute_query", query=query)["rows"], ch(REV))


@check("mcp.browse.get_fields_segments")
def _(ctx):
    tid = ctx.mb.table(ORD)["id"]
    segs = {_slug(m["name"]): m for m in get_fields_table(ctx, tid).get("segments") or []}
    if "paid" not in segs:
        raise Fail("get_fields lists no segment Paid. It lists: %s" % sorted(segs)[:10])
    query = mcp_stage(tid, aggregation=[["count", {}]], filters=[["segment", {}, segs["paid"]["id"]]])
    assert_rows(ctx.mcp.call("execute_query", query=query)["rows"], ch("SELECT count() FROM orders WHERE %s" % PAID))


@check("mcp.browse.get_fields_dimensions")
def _(ctx):
    tid = ctx.mb.table("users")["id"]
    fields = {f["name"]: f for f in get_fields_table(ctx, tid).get("fields") or []}
    if "city" not in fields:
        raise Fail("get_fields lists no field city")
    if fields["city"].get("description") != "Billing city.":
        raise Fail("field city description %r" % fields["city"].get("description"))
    query = mcp_stage(tid, aggregation=[["count", {}]], breakout=[["field", {}, fields["city"]["id"]]])
    assert_rows(ctx.mcp.call("execute_query", query=query)["rows"], ch("SELECT city, count() FROM users GROUP BY city"))


@check("mcp.browse.descriptions_in_get_fields")
def _(ctx):
    blob = json.dumps(get_fields_table(ctx, ctx.mb.table(ORD)["id"]))
    for text in ["Total order amount in USD.", "Completed or shipped orders.", "Revenue questions start here."]:
        if text not in blob:
            raise Fail("get_fields does not show %r" % text)
    mcp_rows(ctx, ORD, REV, agg=["m:orders.revenue"])


@check("mcp.question_write.query")
def _(ctx):
    res = ctx.mcp.call("question_write", method="create", name="conformance mcp q", collection_id=ctx.mb.collection(),
                       query=mcp_query(ctx.mb, ORD, agg=["m:orders.revenue"], by=[STA]))
    assert_rows(ctx.mb.run_card(deep_find(res, "id"))["rows"], ch(STATUS_REV))


@check("mcp.question_write.query_handle")
def _(ctx):
    data = ctx.mcp.call("execute_query", query=mcp_query(ctx.mb, ORD, agg=["m:orders.count"], by=[STA]))
    res = ctx.mcp.call("question_write", method="create", name="conformance mcp handle", query_handle=data["query_handle"], collection_id=ctx.mb.collection())
    assert_rows(ctx.mb.run_card(deep_find(res, "id"))["rows"], ch(STATUS_CNT))


@check("mcp.question_write.native")
def _(ctx):
    res = ctx.mcp.call("question_write", method="create", name="conformance mcp native", collection_id=ctx.mb.collection(),
                       native={"database_id": ctx.mb.need_db(), "sql": "SELECT status, MEASURE(count) AS n FROM orders GROUP BY 1"})
    assert_rows(ctx.mb.run_card(deep_find(res, "id"))["rows"], ch(STATUS_CNT))


@check("mcp.get_content.question")
def _(ctx):
    card = ctx.mb.mbql_card("mcp get", ORD, agg=["m:orders.revenue"])
    content = ctx.mcp.call("get_content", items=[{"type": "question", "id": card["id"]}], include=["definition"])
    definition = deep_find(content, "definition")
    if not definition:
        raise Fail("get_content has no definition: %s" % str(content)[:300])
    assert_rows(ctx.mcp.call("execute_query", query=definition)["rows"], ch(REV))


@check("mcp.run_saved_question")
def _(ctx):
    card = ctx.mb.mbql_card("mcp run", ORD, agg=["m:orders.revenue"], by=[STA])
    assert_rows(ctx.mcp.call("run_saved_question", id=card["id"])["rows"], ch(STATUS_REV))


@check("mcp.dashboard_write.create_and_filter")
def _(ctx):
    card = ctx.mb.mbql_card("mcp dash", ORD, agg=["m:orders.count"])
    res = ctx.mcp.call("dashboard_write", method="create", name="conformance mcp dash", collection_id=ctx.mb.collection(), ops=[
        {"op": "add_card", "id": -1, "card_id": card["id"], "tab": None, "position": None, "size": None, "series": None, "inline_parameters": None},
        {"op": "add_parameter", "parameter_id": "st", "name": "Status", "type": "string/=", "sectionId": "string"},
        {"op": "wire_parameter", "parameter_id": "st", "dashcard_id": -1, "target_field": ctx.mb.field(ORD, "status")["id"]},
    ])
    dash = ctx.mb.api("GET", "/api/dashboard/%d" % deep_find(res, "id"))
    target = dash["dashcards"][0]["parameter_mappings"][0]["target"]
    rows = ctx.mb.run_dashcard(dash, 0, [{"id": "st", "type": "string/=", "value": ["shipped"], "target": target}])["rows"]
    assert_rows(rows, ch(SHIPPED))


def parameter_values(res):
    return values_of(res.get("values") or res.get("data") or [])


@check("mcp.get_parameter_values.dashboard")
def _(ctx):
    dash, _t = dash_with_param(ctx, "mcp values", "string/=", "string", STA)
    res = ctx.mcp.call("get_parameter_values", target="dashboard", id=dash["id"], parameter_id="p1")
    assert_rows(parameter_values(res), ch("SELECT DISTINCT status FROM orders"))


@check("mcp.get_parameter_values.search")
def _(ctx):
    dash, _t = dash_with_param(ctx, "mcp valsearch", "string/=", "string", STA)
    res = ctx.mcp.call("get_parameter_values", target="dashboard", id=dash["id"], parameter_id="p1", query="plet")
    assert_rows(parameter_values(res), ch("SELECT DISTINCT status FROM orders WHERE status LIKE '%plet%'"))


@check("mcp.get_parameter_values.question")
def _(ctx):
    query = {"database": ctx.mb.need_db(), "type": "native", "native": {"query": "SELECT MEASURE(count) AS n FROM orders WHERE {{f}}", "template-tags": field_tag(ctx, "f", STA, "string/=")}}
    card = ctx.mb.card("mcp param q", query, parameters=[{"id": "f", "slug": "f", "name": "f", "type": "string/=", "target": ["dimension", ["template-tag", "f"]]}])
    res = ctx.mcp.call("get_parameter_values", target="question", id=card["id"], parameter_id="f")
    assert_rows(parameter_values(res), ch("SELECT DISTINCT status FROM orders"))


@check("mcp.metric_write")
def _(ctx):
    res = ctx.mcp.call("metric_write", method="create", name="conformance mcp metric", collection_id=ctx.mb.collection(),
                       definition=mcp_query(ctx.mb, ORD, agg=["m:orders.revenue"]))
    assert_rows(ctx.mb.run_card(deep_find(res, "id"))["rows"], ch(REV))


@check("mcp.measure_write")
def _(ctx):
    tid = ctx.mb.table(ORD)["id"]
    res = ctx.mcp.call("measure_write", method="create", table_id=tid, name="conformance mcp units", definition=["sum", {}, to_mbql5(ctx.mb.ref(QTY))])
    mid = deep_find(res, "id")
    ctx.mb.created.append(("/api/measure/%d" % mid, {"archived": True, "revision_message": "conformance run"}))
    assert_rows(ctx.mcp.call("execute_query", query=mcp_stage(tid, aggregation=[["measure", {}, mid]]))["rows"], ch("SELECT sum(quantity) FROM orders"))


@check("mcp.segment_write")
def _(ctx):
    tid = ctx.mb.table(ORD)["id"]
    res = ctx.mcp.call("segment_write", method="create", table_id=tid, name="conformance mcp shipped", definition=["=", {}, to_mbql5(ctx.mb.ref(STA)), "shipped"])
    sid = deep_find(res, "id")
    ctx.mb.created.append(("/api/segment/%d" % sid, {"archived": True, "revision_message": "conformance run"}))
    query = mcp_stage(tid, aggregation=[["count", {}]], filters=[["segment", {}, sid]])
    assert_rows(ctx.mcp.call("execute_query", query=query)["rows"], ch(SHIPPED))


@check("mcp.duplicate_content")
def _(ctx):
    card = ctx.mb.mbql_card("mcp dup", ORD, agg=["m:orders.count"])
    res = ctx.mcp.call("duplicate_content", type="question", id=card["id"], collection_id=ctx.mb.collection())
    assert_rows(ctx.mb.run_card(deep_find(res, "id"))["rows"], ch(CNT_O))


@check("mcp.agent_journey_no_cube_knowledge")
def _(ctx):
    cube_tables = {t["id"] for t in ctx.mb.tables.values()}
    hits = ctx.mcp.call("search", term_queries=["revenue"], type=["table", "measure"])["data"]
    tables = [r["id"] for r in hits if r["type"] == "table" and r["id"] in cube_tables]
    if not tables and not [r for r in hits if r["type"] == "measure" and _slug(r["name"]) == "revenue"]:
        raise Fail("search for revenue gives nothing from the Cube database: %s" % [(r["type"], r["name"]) for r in hits][:8])
    tid = tables[0] if tables else ctx.mb.table(ORD)["id"]
    table = get_fields_table(ctx, tid)
    found = [m for m in table.get("measures") or [] if _slug(m["name"]) == "revenue"]
    if not found:
        raise Fail("table %s lists no revenue measure: %s" % (table.get("name"), [m["name"] for m in table.get("measures") or []][:8]))
    query = mcp_stage(tid, aggregation=[["measure", {}, found[0]["id"]]])
    assert_rows(ctx.mcp.call("execute_query", query=query)["rows"], ch(REV))


# ----------------------------------------------------------------- main


def report(path, final, reasons, line):
    if not path:
        return
    counts = dict.fromkeys(["pass", "partial", "blocked", "unsupported", "fail"], 0)
    for status in final.values():
        counts[status] += 1
    counts["total"] = len(final)
    features = [{"id": fid, "status": st, "reason": reasons.get(fid, "")} for fid, st in final.items()]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"schema": 1, "counts": counts, "coverage": line, "features": features}, f, indent=1)
        f.write("\n")


def run_suite(args):
    features = load_features()
    ids = [f["id"] for f in features]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate ids in features.yaml")
    orphans = sorted(set(CHECKS) - {f.get("check") for f in features})
    if orphans:
        print("warning: checks without an entry: %s" % orphans)

    mb = Metabase(os.environ.get("METABASE_ADMIN_PASSWORD"), args.db_name)
    setup_error = None
    try:
        mb.login()
        mb.discover()
    except Fail as e:
        setup_error = str(e)
    ctx = Ctx(mb, Mcp(mb))

    final = {}
    reasons = {}
    only = re.compile(args.only) if args.only else None
    for f in features:
        fid = f["id"]
        if only and not only.search(fid):
            continue
        cid = f.get("check")
        if not cid:
            final[fid] = f["status"]
            reasons[fid] = f.get("reason", "no check")
            print("%-8s %s: %s" % (final[fid], fid, reasons[fid]))
            continue
        try:
            if setup_error:
                raise Fail(setup_error)
            if cid not in CHECKS:
                raise Fail("unknown check %s" % cid)
            CHECKS[cid](ctx)
        except Fail as e:
            final[fid], reasons[fid] = "fail", str(e)
        except Exception as e:  # a reply of an unexpected shape must not stop the run
            final[fid], reasons[fid] = "fail", "unexpected %s: %s" % (type(e).__name__, str(e)[:300])
            if args.verbose:
                traceback.print_exc()
        else:
            final[fid] = "pass"
        if final[fid] == "pass":
            print("pass     %s" % fid)
        else:
            print("fail     %s: %s" % (fid, reasons[fid]))

    if not setup_error:
        mb.cleanup()
    if not args.no_write:
        write_statuses(final)

    passed = sum(1 for st in final.values() if st == "pass")
    line = "coverage: %d/%d pass" % (passed, len(final))
    if args.only:
        line += " (only %s)" % args.only
    print(line)
    report(args.json, final, reasons, line)
    return 1 if "fail" in final.values() else 0


def main():
    ap = argparse.ArgumentParser(description="Run the conformance suite.")
    ap.add_argument("--only", help="run only the features whose id matches this regex")
    ap.add_argument("--no-write", action="store_true", help="do not rewrite features.yaml")
    ap.add_argument("--db-name", help="test the runner on a database with this name; results say nothing about the driver, so add --no-write")
    ap.add_argument("--json", help="write the result contract to this path")
    ap.add_argument("--verbose", action="store_true", help="print a stack trace for unexpected errors")
    args = ap.parse_args()
    try:
        return run_suite(args)
    except Exception as e:  # CI reads only the JSON, so a crash must also leave one
        line = "coverage: 0/0 pass (runner error: %s: %s)" % (type(e).__name__, e)
        print(line)
        traceback.print_exc()
        report(args.json, {"runner": "fail"}, {"runner": line}, line)
        return 2


if __name__ == "__main__":
    sys.exit(main())
