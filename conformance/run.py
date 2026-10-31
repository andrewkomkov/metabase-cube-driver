#!/usr/bin/env python3
"""Conformance runner. See conformance/README.md.

Run: METABASE_ADMIN_PASSWORD=... python3 conformance/run.py [--only REGEX] [--no-write] [--json PATH]
"""
import argparse
import base64
import datetime as dt
import hashlib
import hmac
import html.parser
import json
import os
import re
import secrets
import socket
import struct
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
    "cube-api-secret-value": "cube-dev-only-secret-0123456789abcdef",
    "ssl": False,
}

DATE_RANGE = ["2025-01-01", "2025-03-31"]

# off is stock Cube, on is Cube with cube-clickhouse. See cube-clickhouse/README.md.
DIALECT = os.environ.get("CUBE_CLICKHOUSE_FIXES", "on") != "off"
VARIANT_NAME = "Cube with the ClickHouse dialect" if DIALECT else "stock Cube"
METABASE_INTERNAL = os.environ.get("METABASE_INTERNAL_URL", "http://metabase:3000").rstrip("/")
MAIL_URL = os.environ.get("MAIL_URL", "http://127.0.0.1:8025").rstrip("/")
BROWSER_URL = os.environ.get("BROWSER_URL", "http://127.0.0.1:9222").rstrip("/")
CUBE_LEGACY = os.environ.get("CUBE_LEGACY_REST_URL", "http://127.0.0.1:4402").rstrip("/")
CUBE_LEGACY_SECRET = "cube-legacy-dev-only-secret-0123456789"


class Blocked(Exception):
    """The check ran and a limit of a version outside this repository stops the feature. The text is the reason."""


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
    """Replace only the status lines. `status` is the result with the dialect, `status_stock` the result on stock Cube."""
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    out = []
    current = None
    for line in lines:
        m = re.match(r"^- id: (.*)$", line)
        if m:
            current = m.group(1).strip()
        if current not in results:
            out.append(line)
        elif line.startswith("  status_stock: ") and not DIALECT:
            continue
        elif line.startswith("  status: ") and DIALECT:
            out.append("  status: %s\n" % results[current])
        elif line.startswith("  status: "):
            out.append(line)
            out.append("  status_stock: %s\n" % results[current])
        else:
            out.append(line)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)


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
        return d.isoformat()
    return v


def sort_key(row):
    return [(0 if x is None else 1, round(x, 3) if isinstance(x, float) else str(x)) if not isinstance(x, float) else (1, "%020.3f" % x) for x in row]


def _same(a, b, tol):
    """With tol=0, two numbers differ only by the rounding of a double. A check that has a real tolerance names it."""
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= max(abs(b) * max(tol, 1e-12), 1e-9)
    return a == b


def ch_write(sql):
    """Change the demo ClickHouse. A check that needs a change of data uses this, for a table of its own."""
    cmd = [
        "docker", "compose", "-f", os.path.join(REPO, "dev", "docker-compose.yml"), "exec", "-T", "clickhouse",
        "clickhouse-client", "--user", "shop", "--password", "shop-dev-only", "--database", "shop", "--query", sql,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise Fail("SQL failed: %s: %s" % (sql, p.stderr.strip()[:300]))


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
            err = Fail("%s %s: HTTP %s: %s" % (method, path, status, text[:300]))
            err.body = text
            raise err
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
        # The status says complete before the analysis ends. The checks of fingerprints and classified types need it, and also
        # the semantic sync after the analysis, which puts back the format type of thumb_url. At the deadline the checks run and
        # tell what is missing.
        while time.time() < deadline:
            meta = self.api("GET", "/api/database/%d/metadata?include_hidden=true" % db["id"])
            fields = {(t["name"], f["name"]): f for t in meta["tables"] for f in t["fields"]}
            name = fields.get(("users", "name"), {})
            if name.get("fingerprint") and name.get("semantic_type") and fields.get(("orders", "thumb_url"), {}).get("semantic_type") == "type/ImageURL":
                return
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

    def with_ui(self):
        """A second session of a client that renders MCP Apps. Metabase lists visualize_query and render_drill_through only for it."""
        if getattr(self, "ui", None):
            return self.ui
        self.connect()
        ui = Mcp(self.mb)
        headers = {k: v for k, v in self.headers.items() if k != "Mcp-Session-Id"}
        status, text, resp = http("POST", MB + MCP_PATH, {
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26",
                       "capabilities": {"extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}},
                       "clientInfo": {"name": "conformance-ui", "version": "1"}}}, headers)
        if status != 200 or not resp.get("Mcp-Session-Id"):
            raise Fail("initialize with UI: HTTP %s %s" % (status, text[:200]))
        headers["Mcp-Session-Id"] = resp["Mcp-Session-Id"]
        http("POST", MB + MCP_PATH, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers)
        ui.headers = headers
        self.ui = ui
        return ui

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
        text = (res.get("content") or [{}])[0].get("text", "")
        if res.get("isError"):
            raise Fail("%s: %s" % (tool, text[:500]))
        return mcp_payload(text)


def mcp_payload(text):
    """The JSON of a tool reply. Metabase 0.65 puts it in a <data boundary="..."> block. Metabase 0.64 sends it bare, sometimes with text after it."""
    block = re.search(r'<data boundary="[^"]*">\s*', text)
    try:
        return json.JSONDecoder().raw_decode(text[block.end():] if block else text)[0]
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


PERCENT_TYPES = ("type/Percentage", "type/Share")


def expect_format(style=None, currency=None, decimals=None, semantic=None):
    """The last result column carries the display format of the Cube member."""
    def post(ctx, data):
        col = data["cols"][-1]
        settings, sem = dict(col.get("settings") or {}), col.get("semantic_type")
        if semantic and sem in semantic:
            return
        if style and settings.get("number_style") != style:
            raise Fail("column settings %s, semantic %s: want number_style %s" % (settings, sem, style))
        if currency and settings.get("currency") != currency:
            raise Fail("column settings %s: want currency %s" % (settings, currency))
        if decimals is not None and settings.get("decimals") != decimals:
            raise Fail("column settings %s: want decimals %s" % (settings, decimals))
    if not (style or currency or decimals is not None):
        raise ValueError("expect_format needs a value to compare")
    return post


def expect_format_as_text(table, member, style, decimals, text):
    """Metabase has no setting for this format. The value is right, the column has the nearest format, and the description
    keeps the text of Cube. So this check can prove only a partial feature."""
    def post(ctx, data):
        expect = expect_format(style, decimals=decimals)
        expect(ctx, data)
        obj = ctx.mb.entity(ctx.mb.measures, table, member) or ctx.mb.field(table, member)
        if text not in (obj.get("description") or ""):
            raise Fail("%s.%s description %r has no %r" % (table, member, obj.get("description"), text))
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


def expect_map_columns(ctx, data):
    """The Metabase map reads these semantic types from the columns of the result."""
    sems = [c.get("semantic_type") for c in data["cols"]]
    if not {"type/Latitude", "type/Longitude"} <= set(sems):
        raise Fail("the result columns have semantic types %s, a map needs Latitude and Longitude" % sems)


def expect_field_format(table, name, style=None, currency=None):
    semantic = {"currency": ("type/Currency",), "percent": PERCENT_TYPES}.get(style, ())

    def post(ctx, data):
        f = ctx.mb.field(table, name)
        settings = f.get("settings") or {}
        if not settings and f.get("semantic_type") not in semantic:
            raise Fail("%s.%s has no format settings" % (table, name))
        if style and settings.get("number_style") != style and f.get("semantic_type") not in semantic:
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


def expect_description(table, want, member=None):
    """The description starts with the text of the model. Notes about hierarchies, folders and meta can follow it."""
    def post(ctx, data):
        if member:
            got = (ctx.mb.entity(ctx.mb.measures, table, member) or ctx.mb.field(table, member)).get("description")
        else:
            got = ctx.mb.table(table).get("description")
        if not str(got).startswith(want):
            raise Fail("%s.%s description=%r, want it to start with %r" % (table, member, got, want))
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
            "addSeconds(toDateTime(addMonths(toDateTime(x, 'UTC'), 1), 'UTC'), -1) AS e FROM (SELECT arrayJoin(%s) AS x)) b "
            "GROUP BY m ORDER BY m" % (expr, ROLL))


# ----- measures: types

Q("measure.type.count", ORD, "SELECT count() FROM orders", agg=["m:orders.count"])
Q("measure.type.count_distinct", ORD, "SELECT uniqExact(user_id) FROM orders", agg=["m:orders.unique_customers"])
Q("measure.type.count_distinct_approx", ORD, "SELECT uniqExact(user_id) FROM orders", tol=0.05, agg=["m:orders.approx_customers"])
Q("measure.type.sum", ORD, "SELECT sum(amount) FROM orders", agg=["m:orders.revenue"])
Q("measure.type.avg", ORD, "SELECT avg(amount) FROM orders", agg=["m:orders.avg_order_value"])
Q("measure.type.min", ORD, "SELECT min(amount) FROM orders", agg=["m:orders.min_amount"])
Q("measure.type.max", ORD, "SELECT max(amount) FROM orders", agg=["m:orders.max_amount"])
Q("measure.type.number", ORD, "SELECT sumIf(toFloat64(amount), %s) / sum(toFloat64(amount)) FROM orders" % PAID, agg=["m:orders.paid_share"])
Q("measure.type.number_agg", ORD, "SELECT quantile(0.5)(amount) FROM orders", agg=["m:orders.p50_amount"])
Q("measure.type.rank", ORD, "SELECT status, rank() OVER (ORDER BY sum(amount) DESC) FROM orders GROUP BY status",
  agg=["m:orders.revenue_rank"], by=["d:orders.status"])
Q("measure.type.string", ORD, "SELECT arrayStringConcat(arraySort(groupUniqArray(status)), ',') FROM orders", agg=["m:orders.statuses"])
Q("measure.type.time", ORD, "SELECT min(created_at) FROM orders", agg=["m:orders.first_order_time"])
Q("measure.type.boolean", ORD, "SELECT count() > 0 FROM orders", agg=["m:orders.has_orders"])

# ----- measures: options

REV = "SELECT sum(amount) FROM orders"
Q("measure.opt.filters", ORD, "SELECT sumIf(amount, %s) FROM orders" % PAID, agg=["m:orders.paid_revenue"])
Q("measure.opt.filters_on_count", "users", "SELECT countIf(is_vip) FROM users", agg=["m:users.vip_count"])
Q("measure.opt.title", ORD, "SELECT avg(amount) FROM orders", agg=["m:orders.avg_order_value"], post=expect_title("orders", "avg_order_value", "AOV"))
Q("measure.opt.description", ORD, REV, agg=["m:orders.revenue"], post=expect_description("orders", "Total order amount in USD.\n\nTable: Orders (orders, id ", "revenue"))
Q("measure.opt.public_false", ORD, REV, agg=["m:orders.revenue"], post=expect_hidden("orders", "public_false_measure"))
Q("measure.opt.meta", ORD, REV, agg=["m:orders.revenue_meta"], post=expect_text("orders", "Sum of order amount."))
Q("measure.opt.drill_members", ORD, "SELECT id, status, created_at FROM orders ORDER BY id LIMIT 3", ordered=True,
  fields=["d:orders.id", "d:orders.status", "d:orders.created_at"], order=[["asc", "d:orders.id"]], limit=3, post=expect_text("orders", "drill"))
Q("measure.opt.mask", "masked_sales", "SELECT count(), -1 FROM orders", agg=["m:masked_sales.count", "m:masked_sales.revenue_masked"])
Q("measure.opt.cross_cube_reference", ORD, "SELECT (SELECT 2 * count() FROM orders) / count() FROM orders", agg=["m:orders.payments_per_order"])
Q("measure.format.currency", ORD, REV, agg=["m:orders.revenue"], post=expect_format("currency", semantic=("type/Currency",)))
Q("measure.format.currency_code", ORD, REV, agg=["m:orders.revenue_eur"], post=expect_format("currency", "EUR"))
Q("measure.format.percent", ORD, "SELECT sumIf(toFloat64(amount), %s) / sum(toFloat64(amount)) FROM orders" % PAID, agg=["m:orders.paid_share"], post=expect_format("percent", semantic=PERCENT_TYPES))
Q("measure.format.percent_n", ORD, "SELECT sumIf(toFloat64(amount), %s) / sum(toFloat64(amount)) FROM orders" % PAID, agg=["m:orders.paid_share_1"], post=expect_format("percent", decimals=1))
Q("measure.format.number_n", ORD, REV, agg=["m:orders.revenue_number"], post=expect_format(decimals=0))
Q("measure.format.abbr", ORD, REV, agg=["m:orders.revenue_abbr"], post=expect_format_as_text("orders", "revenue_abbr", "decimal", 2, "Number format: .2~s."))
Q("measure.format.accounting", ORD, REV, agg=["m:orders.revenue_accounting"], post=expect_format_as_text("orders", "revenue_accounting", "decimal", 2, "Number format: (,.2~f."))
Q("measure.format.d3", ORD, REV, agg=["m:orders.revenue_d3"], post=expect_format("currency", decimals=2))

# ----- measures: rolling_window

Q("measure.rolling.trailing", ORD, monthly("sumIf(o.amount, o.created_at >= addDays(e, -30) AND o.created_at <= e)"),
  ordered=True, agg=["m:orders.revenue_trailing_30d"], **BY_MONTH)
Q("measure.rolling.leading_offset", ORD,
  "SELECT d, sumIf(o.amount, o.created_at >= d AND o.created_at < d + INTERVAL 7 DAY) FROM (SELECT toDateTime('2025-03-03', 'UTC') + toIntervalDay(number) AS d FROM numbers(2)) AS w CROSS JOIN orders o GROUP BY d ORDER BY d",
  ordered=True, agg=["m:orders.revenue_leading_7d"], by=["d:orders.created_at@day"], where=[[">=", "d:orders.created_at", "2025-03-03"], ["<", "d:orders.created_at", "2025-03-05"]], order=[["asc", "d:orders.created_at@day"]])
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
Q("measure.multi_stage.filter_exclude", ORD, REV, agg=["m:orders.revenue_all_statuses"], where=[["=", "d:orders.status", "completed"]])
Q("measure.multi_stage.grain_keep_only", ORD, BY_STATUS_SIZE, agg=["m:orders.revenue_keep_status"], by=["d:orders.status", "d:orders.size"])
Q("measure.multi_stage.grain_exclude", ORD, BY_STATUS_SIZE, agg=["m:orders.revenue_excl_size"], by=["d:orders.status", "d:orders.size"])
Q("measure.multi_stage.grain_include", ORD,
  "SELECT status, max(r) FROM (SELECT status, %s AS size, sum(amount) AS r FROM orders GROUP BY status, size) GROUP BY status" % SIZE,
  agg=["m:orders.revenue_incl_size_max"], by=["d:orders.status"])
Q("measure.multi_stage.group_by_legacy", ORD, BY_STATUS_SIZE, agg=["m:orders.revenue_group_by_legacy"], by=["d:orders.status", "d:orders.size"])
Q("measure.multi_stage.share_of_total", ORD, "SELECT status, sum(toFloat64(amount)) / (SELECT sum(toFloat64(amount)) FROM orders) FROM orders GROUP BY status",
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
GEO = ["d:users.location__latitude", "d:users.location__longitude"]
Q("dimension.type.geo.map_points", "users", "SELECT latitude, longitude, count() FROM users GROUP BY 1, 2",
  agg=["m:users.count"], by=GEO, post=expect_map_columns)
Q("dimension.type.geo.map_grid", "users", "SELECT floor(latitude / 10) * 10, floor(longitude / 10) * 10, count() FROM users GROUP BY 1, 2",
  agg=["m:users.count"], by=[g + "#width=10" for g in GEO], post=expect_map_columns)
Q("dimension.type.geo.raw_rows", "users", "SELECT id, latitude, longitude FROM users ORDER BY id LIMIT 5", ordered=True,
  fields=["d:users.id"] + GEO, order=[["asc", "d:users.id"]], limit=5, post=expect_map_columns)
Q("dimension.type.geo.filter", "users", "SELECT count() FROM users WHERE latitude > 40 AND longitude > -80",
  agg=["m:users.count"], where=[[">", GEO[0], 40], [">", GEO[1], -80]])
Q("dimension.type.geo.implicit_join", ORD, "SELECT u.latitude, u.longitude, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1, 2",
  agg=[["count"]], by=["d:orders>users.location__latitude", "d:orders>users.location__longitude"], post=expect_map_columns)


def geo_view_on_stock_cube(ctx):
    """The view must keep working without the geo dimension."""
    if ctx.mb.has_field("customer_locations", "location__latitude"):
        raise Fail("the view has a latitude field that Cube cannot read")
    if ctx.mb.field("customer_locations", "location").get("visibility_type") != "hidden":
        raise Fail("customer_locations.location is visible, and Cube cannot read it")
    if not ctx.mb.field("customer_locations", "location").get("fingerprint"):
        raise Fail("the sample of the view failed: customer_locations.location has no fingerprint")
    data = ctx.mb.mbql("customer_locations", order=[["asc", "d:customer_locations.id"]], limit=3)
    assert_rows([r[:1] for r in data["rows"]], ch("SELECT id FROM users ORDER BY id LIMIT 3"), ordered=True)
    # A person can ask for the hidden column too.
    data = ctx.mb.mbql("customer_locations", fields=["d:customer_locations.id", "d:customer_locations.location"], order=[["asc", "d:customer_locations.id"]], limit=3)
    assert_rows(data["rows"], ch("SELECT id, NULL FROM users ORDER BY id LIMIT 3"), ordered=True)


def geo_view_on_fixed_cube(ctx):
    """The driver must show the coordinates, the same as on a cube."""
    parts = ["d:customer_locations.location__latitude", "d:customer_locations.location__longitude"]
    if ctx.mb.field("customer_locations", "location").get("visibility_type") == "hidden":
        raise Fail("Cube reads a geo dimension of this view, and customer_locations.location is hidden")
    data = ctx.mb.mbql("customer_locations", agg=["m:customer_locations.count"], by=parts)
    expect_map_columns(ctx, data)
    assert_rows(data["rows"], ch("SELECT latitude, longitude, count() FROM users GROUP BY 1, 2"))
    data = ctx.mb.mbql("customer_locations", fields=["d:customer_locations.id", "d:customer_locations.location"], order=[["asc", "d:customer_locations.id"]], limit=3)
    assert_rows(data["rows"], ch("SELECT id, concat(toString(latitude), ',', toString(longitude)) FROM users ORDER BY id LIMIT 3"), ordered=True)


@check("dimension.type.geo.view")
def _(ctx):
    """Asks Cube if it reads a geo dimension of a view, and then tests what the driver must do on that Cube."""
    query = json.dumps({"dimensions": ["customer_locations.location"], "measures": ["customer_locations.count"], "limit": 1})
    for _try in range(30):
        _status, text, _h = http("GET", CUBE + "/cubejs-api/v1/load?query=" + urllib.parse.quote(query))
        if "Continue wait" not in text:
            break
        time.sleep(1)
    if "Cannot read properties of undefined" in text:
        geo_view_on_stock_cube(ctx)
    elif '"data":[' in text:
        geo_view_on_fixed_cube(ctx)
    else:
        raise Fail("Cube has an unknown answer for a geo dimension of a view: %s" % text[:200])


@check("dimension.type.geo.auto_bin")
def _(ctx):
    """The first option for a coordinate in the Metabase query builder. The default width is 10 degrees."""
    by = [ctx.mb.ref(g)[:2] + [{"binning": {"strategy": "default"}}] for g in GEO]
    data = ctx.mb.raw(ctx.mb.inner("users", agg=["m:users.count"], by=by))
    assert_rows(data["rows"], ch("SELECT floor(latitude / 10) * 10, floor(longitude / 10) * 10, count() FROM users GROUP BY 1, 2"))
    expect_map_columns(ctx, data)


@check("dimension.type.geo.second_stage")
def _(ctx):
    inner = ctx.mb.inner("users", agg=["m:users.count"], by=GEO)
    outer = {"source-query": inner, "filter": [">", ["field", "location__latitude", {"base-type": "type/Float"}], 40]}
    assert_rows(ctx.mb.raw(outer)["rows"], ch("SELECT latitude, longitude, count() FROM users WHERE latitude > 40 GROUP BY 1, 2"))


@check("dimension.type.geo.fingerprint")
def _(ctx):
    """Metabase computes the bins from the fingerprint, so the sample of the table must read these columns."""
    got = []
    for name in ("location__latitude", "location__longitude"):
        number = (ctx.mb.field("users", name).get("fingerprint") or {}).get("type", {}).get("type/Number") or {}
        got += [number.get("min"), number.get("max")]
    assert_rows([got], ch("SELECT min(latitude), max(latitude), min(longitude), max(longitude) FROM users"))
Q("dimension.type.switch", ORD, "SELECT toFloat64(sum(amount)) * 0.5 FROM orders", agg=["m:orders.revenue_in_currency"], where=[["=", "d:orders.display_currency", "EUR"]])
Q("dimension.opt.case_sql", ORD, "SELECT %s, count() FROM orders GROUP BY 1" % SIZE, agg=["m:orders.count"], by=["d:orders.size"])
Q("dimension.opt.case_label_sql", "users",
  "SELECT multiIf(created_at < toDateTime('2024-04-01 00:00:00', 'UTC'), 'Q1', created_at < toDateTime('2024-07-01 00:00:00', 'UTC'), 'Q2', 'later'), count() FROM users GROUP BY 1",
  agg=["m:users.count"], by=["d:users.signup_cohort"])
Q("dimension.opt.sub_query", "users", "SELECT u.id, count() FROM users u JOIN orders o ON o.user_id = u.id WHERE u.id <= 3 GROUP BY u.id",
  fields=["d:users.id", "d:users.lifetime_orders"], where=[["<=", "d:users.id", 3]])

def _lifetime(column, order_filter):
    return ("SELECT u.id, u.%s FROM users u CROSS JOIN orders o WHERE u.__cubeJoinField = o.__cubeJoinField AND %s AND u.id <= 3 "
            "GROUP BY 1, 2" % (column, order_filter))


_EARLY = "o.created_at >= '2025-01-01' AND o.created_at < '2025-01-15'"
_EARLY_TRUTH = "created_at >= '2025-01-01' AND created_at < '2025-01-15'"


def _orders_of_user(where=None):
    return "SELECT user_id, count() FROM orders WHERE user_id <= 3%s GROUP BY user_id" % (" AND " + where if where else "")


@check("dimension.opt.propagate_filters")
def _(ctx):
    """The filter on orders reaches the subquery of this dimension, and it does not reach a dimension with no such option."""
    if ch(_orders_of_user(_EARLY_TRUTH)) == ch(_orders_of_user()):
        raise Fail("this filter excludes no order, so the check shows nothing")
    assert_rows(ctx.mb.native(_lifetime("lifetime_paid_orders", _EARLY))["rows"], ch(_orders_of_user(_EARLY_TRUTH)), label="date filter")
    assert_rows(ctx.mb.native(_lifetime("lifetime_paid_orders", "o.status = 'shipped'"))["rows"], ch(_orders_of_user("status = 'shipped'")), label="status filter")
    assert_rows(ctx.mb.native(_lifetime("lifetime_orders", _EARLY))["rows"], ch(_orders_of_user()), label="no propagation")


@check("dimension.opt.propagate_filters_next_to_another_sub_query")
def _(ctx):
    """Two sub_query dimensions with equal SQL: with a filter Cube reads the first subquery for both, so the driver stops this, and raw rows show one of them."""
    both = ("SELECT u.id, u.lifetime_orders, u.lifetime_paid_orders FROM users u CROSS JOIN orders o WHERE u.__cubeJoinField = o.__cubeJoinField "
            "AND %s AND u.id <= 3 GROUP BY 1, 2, 3" % _EARLY)
    expect_native_refusal(ctx, both, "have equal SQL")
    # With no filter both columns are right.
    expect_native_rows(ctx, "SELECT id, lifetime_orders, lifetime_paid_orders FROM users ORDER BY id LIMIT 3",
                       [[r[0], r[1], r[1]] for r in sorted(ch(_orders_of_user()))[:3]])
    query = ctx.mb.inner("users", agg=[["count"]], by=["d:users.lifetime_orders", "d:users.lifetime_paid_orders"], where=[["<=", "d:users.id", 3]])
    expect_clear_error(ctx, query, "have equal SQL")
    # Raw rows with a filter show the first column and NULL in the other, so no column has a wrong value.
    rows = ctx.mb.mbql("users", fields=["d:users.id", "d:users.lifetime_orders", "d:users.lifetime_paid_orders"], where=[["<=", "d:users.id", 3]])["rows"]
    truth = dict(map(tuple, ch(_orders_of_user())))
    for uid, lifetime, paid in rows:
        if paid is not None or lifetime != truth.get(uid):
            raise Fail("raw row of user %s: %s, %s, truth %s and NULL" % (uid, lifetime, paid, truth.get(uid)))


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
Q("dimension.format.abbr", ORD, "SELECT amount FROM orders WHERE id = 7", fields=["d:orders.amount_abbr"], where=ID7, post=expect_format_as_text("orders", "amount_abbr", "decimal", 2, "Number format: .2~s."))
Q("dimension.format.id", ORD, "SELECT id FROM orders WHERE id = 7", fields=["d:orders.order_ref"], where=ID7, post=expect_field_format("orders", "order_ref"))
_INV = "SELECT concat('https://example.com/invoice/', toString(id)) FROM orders WHERE id = 7"
Q("dimension.format.link", ORD, _INV, fields=["d:orders.invoice_url"], where=ID7, post=expect_field("orders", "invoice_url", semantic_type="type/URL"))
Q("dimension.format.link_label", ORD, _INV, fields=["d:orders.invoice_labeled"], where=ID7, post=expect_text("orders", "Open invoice"))
Q("dimension.format.image_url", ORD, "SELECT concat('https://example.com/thumb/', toString(id), '.png') FROM orders WHERE id = 7",
  fields=["d:orders.thumb_url"], where=ID7, post=expect_field("orders", "thumb_url", semantic_type="type/ImageURL"))
Q("dimension.format.time", ORD, "SELECT created_at FROM orders WHERE id = 7", fields=["d:orders.created_label"], where=ID7, post=expect_text("orders", "%Y-%m-%d %H:%M:%S"))

# ----- custom granularities, through proxy dimensions because the SQL API has no other way

Q("granularity.custom.negative_offset", ORD, "SELECT toStartOfWeek(created_at, 0) AS w, count() FROM orders GROUP BY w ORDER BY w LIMIT 3", ordered=True,
  agg=["m:orders.count"], by=["d:orders.created_week_sunday"], order=[["asc", "d:orders.created_week_sunday"]], limit=3)
Q("granularity.custom.origin", ORD, "SELECT addMonths(toStartOfYear(addMonths(created_at, -3)), 3) AS y, count() FROM orders GROUP BY y",
  agg=["m:orders.count"], by=["d:orders.created_fiscal_year"])
Q("granularity.custom.interval_hours", ORD, "SELECT toStartOfInterval(created_at, INTERVAL 12 HOUR) AS h, count() FROM orders GROUP BY h ORDER BY h LIMIT 3", ordered=True,
  agg=["m:orders.count"], by=["d:orders.created_half_day@default"], order=[["asc", "d:orders.created_half_day@default"]], limit=3)
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
  # Cube joins are LEFT from users, so a customer with no orders is a row with no category.
  "SELECT nullIf(p.category, ''), uniqExact(u.id) FROM users u LEFT JOIN orders o ON o.user_id = u.id LEFT JOIN products p ON o.product_id = p.id GROUP BY 1")

# ----- cubes

CNT_U = "SELECT count() FROM users"
Q("cube.sql_table", "products", "SELECT count() FROM products", agg=["m:products.count"])
Q("cube.sql", "payments", "SELECT 2 * count() FROM orders", agg=["m:payments.count"])
Q("cube.title", "users", CNT_U, agg=["m:users.count"], post=expect_table_attr("users", "display_name", "Customers"))
Q("cube.description", "users", CNT_U, agg=["m:users.count"], post=expect_description("users", "People who have an account in the shop."))
Q("cube.public_false", ORD, "SELECT count() FROM orders", agg=["m:orders.count"], post=expect_no_table("secret_audit"))
Q("cube.meta", "users", CNT_U, agg=["m:users.count"], post=expect_text("users", "One row per customer account."))
Q("cube.extends", "cheap_products", "SELECT count() FROM products WHERE price < 20", agg=["m:cheap_products.count"])
Q("cube.sql_alias", ORD, "SELECT status, count() FROM orders GROUP BY status", agg=["m:orders.count"], by=["d:orders.status"])


@check("cube.refresh_key")
def _(ctx):
    """A new row changes the value of the refresh key, and Cube reads the table again. The refresh key is a SQL query with no `every`."""
    ensure_table(ctx, "refresh_marks")
    query = lambda: ctx.mb.mbql("refresh_marks", agg=["m:refresh_marks.count"])["rows"]
    assert_rows(query(), ch("SELECT count() FROM refresh_marks"), label="before")
    next_id = ch("SELECT count() + 1 FROM refresh_marks")[0][0]
    ch_write("INSERT INTO refresh_marks VALUES (%d)" % next_id)
    truth = ch("SELECT count() FROM refresh_marks")
    deadline = time.time() + 90
    while True:
        got = query()
        if got == truth:
            return
        if time.time() > deadline:
            raise Fail("Cube did not read the new row in 90 s: %s, truth %s" % (got, truth))
        time.sleep(3)


Q("cube.data_source", "second_source_tickets", "SELECT priority, count(), sum(minutes) FROM shop_second.tickets GROUP BY 1",
  agg=["m:second_source_tickets.count", "m:second_source_tickets.total_minutes"], by=["d:second_source_tickets.priority"])


@check("cube.calendar_granularity_sql")
def _(ctx):
    """A granularity of a calendar cube with its own SQL: the fiscal month starts 10 days before the calendar month."""
    ensure_table(ctx, "calendar_sales")
    fiscal = "toStartOfMonth(toDate(created_at) + 10)"
    expect_native_rows(ctx, "SELECT DATE_TRUNC('month', fiscal_calendar_fiscal_date) AS m, MEASURE(revenue) AS r FROM calendar_sales GROUP BY 1",
                       "SELECT %s AS m, sum(amount) FROM orders GROUP BY m" % fiscal)
    assert_rows(ctx.mb.mbql("calendar_sales", agg=["m:calendar_sales.revenue"], by=["d:calendar_sales.fiscal_calendar_fiscal_date@month"])["rows"],
                ch("SELECT %s AS m, sum(amount) FROM orders GROUP BY m" % fiscal), label="query builder")


@check("context.filter_group")
def _(ctx):
    """FILTER_GROUP keeps an OR of two filters inside the SQL of the cube."""
    ensure_table(ctx, "filter_group_orders")
    assert_rows(ctx.mb.mbql("filter_group_orders", agg=["m:filter_group_orders.count"],
                            where=[["or", ["=", "d:filter_group_orders.status", "shipped"], ["=", "d:filter_group_orders.quantity", 2]]])["rows"],
                ch("SELECT count() FROM orders WHERE status = 'shipped' OR quantity = 2"))
    assert_rows(ctx.mb.mbql("filter_group_orders", agg=["m:filter_group_orders.count"], where=[["=", "d:filter_group_orders.status", "shipped"]])["rows"],
                ch("SELECT count() FROM orders WHERE status = 'shipped'"), label="one filter")


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
  post=expect_description("paid_sales", "Completed orders only. Default filters enforce this."))
Q("view.meta", "sales", CNT_O, agg=["m:sales.count"], post=expect_text("sales", "Preferred entry point for agents."))
Q("view.public_false", "sales", CNT_O, agg=["m:sales.count"], post=expect_no_table("hidden_sales"))
Q("view.includes", "sales", CNT_O, agg=["m:sales.count"], post=expect_absent("sales", "min_amount"))
Q("view.excludes", "paid_sales", "SELECT u.city, uniqExact(o.user_id) FROM orders o JOIN users u ON o.user_id = u.id WHERE o.status = 'completed' GROUP BY u.city",
  agg=["m:paid_sales.users_count"], by=["d:paid_sales.users_city"], post=expect_absent("paid_sales", "users_email"))
Q("view.prefix", "sales", "SELECT category, count() FROM orders o JOIN products p ON o.product_id = p.id GROUP BY category", agg=["m:sales.count"], by=["d:sales.products_category"])
Q("view.member_alias", "paid_sales", COMPLETED, agg=["m:paid_sales.net_sales"])
Q("view.member_title", "paid_sales", COMPLETED, agg=["m:paid_sales.net_sales"], post=expect_title("paid_sales", "net_sales", "Net sales"))
Q("view.member_description", "paid_sales", COMPLETED, agg=["m:paid_sales.net_sales"], post=expect_description("paid_sales", "Revenue under another name.", "net_sales"))
Q("view.member_format", "paid_sales", COMPLETED, agg=["m:paid_sales.net_sales"], post=expect_format("currency", semantic=("type/Currency",)))
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
Q("preagg.indexes", ORD, "SELECT status, sumIfOrNull(amount, %s) FROM orders GROUP BY status" % PAID, agg=["m:orders.paid_revenue"], by=["d:orders.status"],
  post=used_rollup({"measures": ["orders.paid_revenue"], "dimensions": ["orders.status"]}, "revenue_by_status_indexed"))

# ----- SQL API through a native query

STATUS_REV = "SELECT status, sum(amount) FROM orders GROUP BY status"
N("sqlapi.measure_function", "SELECT status, MEASURE(revenue) AS r FROM orders GROUP BY 1", STATUS_REV)
N("sqlapi.aggregate_function", "SELECT status, SUM(revenue) AS r FROM orders GROUP BY 1", STATUS_REV)
N("sqlapi.ungrouped", "SELECT id, status FROM orders WHERE id = 7", "SELECT id, status FROM orders WHERE id = 7")
N("sqlapi.date_trunc", "SELECT DATE_TRUNC('month', created_at) AS m, MEASURE(count) AS n FROM orders GROUP BY 1", "SELECT toStartOfMonth(created_at), count() FROM orders GROUP BY 1")
N("sqlapi.where_like", "SELECT MEASURE(count) AS n FROM orders WHERE STRPOS(status, 'comp') = 1", "SELECT count() FROM orders WHERE status LIKE 'comp%'")
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
Q("mb.agg.sum_where", ORD, "SELECT sumIf(quantity, %s) FROM orders" % PAID, agg=[["sum-where", "d:orders.quantity_dim", ["=", "d:orders.is_paid", True]]])
Q("mb.agg.count_where", ORD, "SELECT countIf(%s) FROM orders" % PAID, agg=[["count-where", ["=", "d:orders.is_paid", True]]])
Q("mb.agg.share", ORD, "SELECT countIf(%s) / count() FROM orders" % PAID, agg=[["share", ["=", "d:orders.is_paid", True]]])
Q("mb.agg.custom_expression", ORD, "SELECT sum(toFloat64(amount)) / count() FROM orders", agg=[["/", ["sum", "d:orders.amount_dim"], ["count"]]])
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
    ("week-of-year", "ceiling(toDayOfYear(toStartOfWeek(created_at, 0)) / 7)"), ("month-of-year", "toMonth(created_at)"), ("quarter-of-year", "toQuarter(created_at)"),
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
    """Metabase chooses the edges from the fingerprint and reports them. The truth puts each row in a bin with those edges."""
    data = ctx.mb.mbql(ORD, agg=[["count"]], by=["d:orders.amount_dim#bins=8"])
    info = data["cols"][0].get("binning_info") or {}
    width, low = info.get("bin_width"), info.get("min_value")
    if not width or low is None or len(data["rows"]) < 2:
        raise Fail("binning returned %d rows, with binning_info %s" % (len(data["rows"]), info))
    sql = "SELECT %r + floor((toFloat64(amount) - %r) / %r) * %r AS b, count() FROM orders GROUP BY b" % (low, low, width, width)
    assert_rows(data["rows"], ch(sql))


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
    ("date_after", [">", CRT, "2025-12-01"], "toDate(created_at) > '2025-12-01'"),
    ("date_between", ["between", CRT, "2025-02-01", "2025-02-28"], "toDate(created_at) BETWEEN '2025-02-01' AND '2025-02-28'"),
    ("date_month", ["=", CRT + "@month", "2025-02-01"], "toStartOfMonth(created_at) = '2025-02-01'"),
    ("relative_current", ["time-interval", CRT, "current", "year"], "toYear(created_at) = toYear(today())"),
    ("exclude_day_of_week", ["!=", CRT + "@day-of-week", 2], "toDayOfWeek(created_at, 3) != 2"),
    ("exclude_month_of_year", ["!=", CRT + "@month-of-year", "2025-02-01"], "toMonth(created_at) != 2"),
    ("and", ["and", ["=", STA, "completed"], [">", QTY, 2]], "status = 'completed' AND quantity > 2"),
    ("or", ["or", ["=", STA, "shipped"], [">", QTY, 3]], "status = 'shipped' OR quantity > 3"),
    ("not", ["not", ["=", STA, "shipped"]], "NOT status = 'shipped'"),
]:
    Q("mb.filter.%s" % _fid, ORD, "SELECT count() FROM orders WHERE %s" % _sql, agg=["m:orders.count"], where=[_clause])

# The demo data ends in 2025, so the number of days moves with today. With this many days, the filter drops the first half
# of the year.
PAST_DAYS = (dt.date.today() - dt.date(2025, 7, 1)).days
PAST_TRUTH = "SELECT count() FROM orders WHERE toDate(created_at) >= today() - %d AND toDate(created_at) < today()" % PAST_DAYS


def past_truth():
    want = ch(PAST_TRUTH)
    if want[0][0] in (0, ch(CNT_O)[0][0]):
        raise Fail("the relative date filter keeps %s rows, so the check shows nothing" % want[0][0])
    return want


@check("mb.filter.relative_past")
def _(ctx):
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.count"], where=[["time-interval", CRT, -PAST_DAYS, "day"]])["rows"], past_truth())


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
    ("datetime_diff", ["datetime-diff", "d:orders.created_fiscal_quarter", CRT, "day"], "dateDiff('day', addMonths(toStartOfQuarter(addMonths(created_at, -1)), 1), created_at)"),
]:
    Q("mb.expr.%s" % _name, ORD, "SELECT %s FROM orders WHERE id = 7" % _sql, fields=[["expression", _name]], exprs={_name: _clause}, where=ID7)


# ----- metadata


@check("meta.tables_match_public_members")
def _(ctx):
    ctx.mb.need_db()
    ctx.mb.member_meta(ORD, "count")
    want = {c["name"].lower() for c in ctx.mb.cube_meta if c.get("public", True)}
    if not want:
        raise Fail("Cube /meta lists no public cubes")
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
    want = ctx.mb.entity(ctx.mb.measures, ORD, "revenue")
    hit = [r for r in res if r.get("model") == "measure" and want and r["id"] == want["id"]]
    if not hit:
        raise Fail("search has no measure Revenue of orders. Found: %s" % [(r.get("model"), r["name"]) for r in res][:10])
    assert_rows(ctx.mb.raw({"source-table": ctx.mb.table(ORD)["id"], "aggregation": [["measure", hit[0]["id"]]]})["rows"], ch(REV))


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
                                              "definition": {"database": ctx.mb.db, "type": "query", "query": {"source-table": t, "filter": ["=", ctx.mb.ref(STA), "shipped"]}}})
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
    rows = ctx.mb.run_card(card["id"], [{"id": "s", "type": "category", "target": ["variable", ["template-tag", "s"]], "value": "shipped"}])["rows"]
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
    # Metabase mints no cursor for a breakout with its own limit, by design. See next-page-query in metabase.mcp.v2.query.
    ref = to_mbql5(ctx.mb.ref("d:orders.id"))
    query = mcp_stage(ctx.mb.table(ORD)["id"], fields=[ref], limit=6, **{"order-by": [["asc", {}, ref]]})
    page1 = ctx.mcp.call("execute_query", query=query, row_limit=3)
    if not page1.get("next_cursor"):
        raise Fail("no next_cursor: %s" % str(page1)[:200])
    page2 = ctx.mcp.call("execute_query", cursor=page1["next_cursor"])
    assert_rows(page1["rows"] + page2["rows"], ch("SELECT id FROM orders ORDER BY id LIMIT 6"), ordered=True)


@check("mcp.execute_query.paging_measure")
def _(ctx):
    query = mcp_query(ctx.mb, ORD, agg=["m:orders.revenue"], by=[STA])
    page1 = ctx.mcp.call("execute_query", query=query, row_limit=2)
    if not page1.get("next_cursor"):
        raise Fail("no next_cursor: %s" % str(page1)[:200])
    page2 = ctx.mcp.call("execute_query", cursor=page1["next_cursor"], row_limit=2)
    assert_rows(page1["rows"] + page2["rows"], ch(STATUS_REV))


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
    want = ctx.mb.entity(ctx.mb.measures, ORD, "revenue")
    hit = [r for r in res if want and r["id"] == want["id"]]
    if not hit:
        raise Fail("search has no measure Revenue of orders: %s" % [r["name"] for r in res][:8])
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


def revenue_clause(table):
    """The MBQL 5 aggregation for revenue, from a get_fields reply: a Measure entity or a measure field."""
    for m in table.get("measures") or []:
        if _slug(m["name"]) == "revenue":
            return ["measure", {}, m["id"]]
    for f in table.get("fields") or []:
        if f["name"] == "revenue":
            return ["sum", {}, ["field", {}, f["id"]]]
    return None


@check("mcp.browse.get_fields_measures")
def _(ctx):
    tid = ctx.mb.table(ORD)["id"]
    clause = revenue_clause(get_fields_table(ctx, tid))
    if not clause:
        raise Fail("get_fields lists no revenue measure")
    assert_rows(ctx.mcp.call("execute_query", query=mcp_stage(tid, aggregation=[clause]))["rows"], ch(REV))


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
    for tid in tables or [ctx.mb.table(ORD)["id"]]:
        clause = revenue_clause(get_fields_table(ctx, tid))
        if clause:
            assert_rows(ctx.mcp.call("execute_query", query=mcp_stage(tid, aggregation=[clause]))["rows"], ch(REV))
            return
    raise Fail("no table from the search lists a revenue measure")


# ----- Cube semantics as Metabase objects. See docs/semantic-sync.md.

PROBE_FILE = os.path.join(REPO, "dev", "cube", "model", "zz_conformance_probe.yml")
PROBE_TRUTH = "SELECT sum(amount) FROM orders"
PROBE_SEGMENT_TRUTH = "SELECT count() FROM orders WHERE status = 'completed'"


def probe_model(cube_name, total_title="Probe total", total_description="First text.", field_title="Probe status", members=True):
    """A cube that the checks change. It uses the demo table orders, so ClickHouse can give the truth."""
    extra_measure = """
      - { name: probe_total, sql: amount, type: sum, title: "%s", description: "%s" }""" % (total_title, total_description) if members else ""
    extra_segment = """
    segments:
      - { name: probe_completed, sql: "{CUBE}.status = 'completed'", title: Probe completed }""" if members else ""
    join = """
    joins:
      - { name: users, relationship: many_to_one, sql: "{CUBE}.user_id = {users.id}" }""" if members else ""
    return """cubes:
  - name: %s
    sql_table: orders
    title: Probe cube
    description: Probe cube text.%s
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: status, sql: status, type: string, title: "%s", description: Probe status text. }
      - { name: user_id, sql: user_id, type: number }
    measures:
      - { name: probe_count, type: count, title: Probe count, description: Probe count text. }
      - { name: probe_edited, type: count, title: Probe edited, description: Cube text. }
      - { name: probe_user_archived, type: count, title: Probe user archived }%s%s
""" % (cube_name, join, field_title, extra_measure, extra_segment)


class Probe:
    """Runs one model through its life once and keeps what it saw. Each check reads one part of this."""

    def __init__(self, ctx):
        self.mb = ctx.mb
        self.db = ctx.mb.need_db()
        self.seen = {}
        # A new name for each run: Metabase keeps the edits of a person for a table that comes back.
        self.name = "conformance_probe_%d" % int(time.time())

    def write(self, **kw):
        with open(PROBE_FILE, "w", encoding="utf-8") as f:
            f.write(probe_model(self.name, **kw))

    def cube_shows(self, pred, seconds=90):
        deadline = time.time() + seconds
        while time.time() < deadline:
            _, text, _ = http("GET", CUBE + "/cubejs-api/v1/meta?extended=true")
            cube = [c for c in json.loads(text).get("cubes", []) if c["name"] == self.name]
            if pred(cube[0] if cube else None):
                return
            time.sleep(2)
        raise Fail("Cube did not reload the probe model in %d s" % seconds)

    def resync(self, pred, label, seconds=120):
        """Sync until `pred` is true. A sync is asynchronous, and a second sync can run after the first."""
        deadline = time.time() + seconds
        while True:
            self.mb.api("POST", "/api/database/%d/sync_schema" % self.db)
            time.sleep(4)
            state = self.state()
            if pred(state):
                return state
            if time.time() > deadline:
                raise Fail("%s: not seen after %d s. State: %s" % (label, seconds, json.dumps(state, default=str)[:600]))

    def state(self):
        meta = self.mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % self.db)
        table = next((t for t in meta["tables"] if t["name"] == self.name), None)
        out = {"table": table, "measures": {}, "segments": {}, "fields": {}}
        all_tables = self.mb.api("GET", "/api/table")
        tid = table["id"] if table else next((t["id"] for t in all_tables if t["name"] == self.name), None)
        out["tid"] = tid or self.seen.get("tid")
        if table:
            out["fields"] = {f["name"]: f for f in table["fields"]}
        # /api/measure lists only unarchived objects, so read each known id too.
        for kind in ("measure", "segment"):
            listed = self.mb.api("GET", "/api/%s" % kind)
            listed = listed.get("data", []) if isinstance(listed, dict) else listed
            items = [m for m in listed if m.get("table_id") == out["tid"]]
            known = {m["id"] for m in items}
            for mid in self.seen.get(kind + "_ids", ()):
                if mid not in known:
                    items.append(self.mb.api("GET", "/api/%s/%d" % (kind, mid)))
            out[kind + "s"] = items
        return out

    def by_name(self, state, kind, name):
        return next((m for m in state[kind] if m["name"] == name and not m["archived"]), None)

    def remember(self, state):
        self.seen["tid"] = state["tid"]
        self.seen["measure_ids"] = {m["id"] for m in state["measures"]}
        self.seen["segment_ids"] = {m["id"] for m in state["segments"]}

    def run(self):
        mb = self.mb
        try:
            # 1. A new cube.
            self.write()
            self.cube_shows(lambda c: bool(c))
            s = self.resync(lambda st: self.by_name(st, "measures", "Probe total") and self.by_name(st, "segments", "Probe completed")
                            and st["fields"].get("user_id", {}).get("fk_target_field_id"), "new cube")
            self.remember(s)
            self.seen["created"] = s
            total = self.by_name(s, "measures", "Probe total")
            self.seen["created_rows"] = mb.raw({"source-table": s["tid"], "aggregation": [["measure", total["id"]]]})["rows"]
            seg = self.by_name(s, "segments", "Probe completed")
            self.seen["segment_rows"] = mb.raw({"source-table": s["tid"], "aggregation": [["count"]], "filter": ["segment", seg["id"]]})["rows"]

            # 2. Two more syncs change nothing.
            for _ in range(2):
                mb.api("POST", "/api/database/%d/sync_schema" % self.db)
                time.sleep(6)
            again = self.state()
            self.seen["resynced"] = again

            # 3. A person edits a measure, a field and the table, and archives a measure.
            edited = self.by_name(s, "measures", "Probe edited")
            mb.api("PUT", "/api/measure/%d" % edited["id"], {"name": "My measure", "description": "My text.", "revision_message": "conformance"})
            archived = self.by_name(s, "measures", "Probe user archived")
            mb.api("PUT", "/api/measure/%d" % archived["id"], {"archived": True, "revision_message": "conformance"})
            mb.api("PUT", "/api/field/%d" % s["fields"]["status"]["id"], {"display_name": "My field", "description": "My field text."})
            mb.api("PUT", "/api/table/%d" % s["tid"], {"display_name": "My table", "description": "My table text."})
            self.seen["edited_id"], self.seen["user_archived_id"] = edited["id"], archived["id"]

            # 4. The model changes.
            self.write(total_title="Probe total two", total_description="Second text.", field_title="Probe status two")
            self.cube_shows(lambda c: c and any(m.get("title") == "Probe total two" or m.get("shortTitle") == "Probe total two" for m in c["measures"]))
            s = self.resync(lambda st: self.by_name(st, "measures", "Probe total two"), "changed title")
            self.seen["changed"] = s

            # A person gives the edited Measure back.
            mb.api("PUT", "/api/measure/%d" % edited["id"], {"description": "Give it back.", "revision_message": "cube-sync:reset"})
            self.seen["reset"] = self.resync(lambda st: any(m["id"] == edited["id"] and m["name"] == "Probe edited" for m in st["measures"]), "reset of an edit")

            # 5. Members and the join go away.
            self.write(members=False)
            self.cube_shows(lambda c: c and not c["segments"])
            s = self.resync(lambda st: not self.by_name(st, "measures", "Probe total two") and not self.by_name(st, "segments", "Probe completed"), "removed members")
            self.seen["removed"] = s

            # 6. The members come back.
            self.write(total_title="Probe total two", total_description="Second text.", field_title="Probe status two")
            self.cube_shows(lambda c: c and c["segments"])
            s = self.resync(lambda st: self.by_name(st, "measures", "Probe total two") and self.by_name(st, "segments", "Probe completed")
                            and st["fields"].get("user_id", {}).get("fk_target_field_id"), "restored members")
            self.seen["restored"] = s
            total = self.by_name(s, "measures", "Probe total two")
            self.seen["restored_rows"] = mb.raw({"source-table": s["tid"], "aggregation": [["measure", total["id"]]]})["rows"]
        finally:
            # 7. The cube goes away. This also removes the file, so a failed run leaves no model behind.
            if os.path.exists(PROBE_FILE):
                os.remove(PROBE_FILE)
        self.cube_shows(lambda c: not c)
        self.seen["dropped"] = self.resync(lambda st: not st["table"] and not [m for m in st["measures"] if not m["archived"]], "dropped cube")


def probe(ctx, step):
    if not hasattr(ctx, "probe"):
        ctx.probe = Probe(ctx)
        try:
            ctx.probe.run()
        except Fail as e:
            ctx.probe.error = str(e)
    if step not in ctx.probe.seen:
        raise Fail("the probe did not reach %s: %s" % (step, getattr(ctx.probe, "error", "unknown")))
    return ctx.probe.seen[step]


def only_active(items):
    return [m for m in items if not m["archived"]]


def measure_rows(ctx, table, name):
    entity = ctx.mb.entity(ctx.mb.measures, table, name)
    if not entity:
        raise Fail("no Measure for %s.%s" % (table, name))
    if entity.get("creator_id") != 13371338:
        raise Fail("the Measure of %s.%s has creator %s, not the sync user" % (table, name, entity.get("creator_id")))
    return ctx.mb.raw({"source-table": ctx.mb.table(table)["id"], "aggregation": [["measure", entity["id"]]]})


def cube_members(kind):
    status, text, _ = http("GET", CUBE + "/cubejs-api/v1/meta?extended=true")
    if status != 200:
        raise Fail("Cube meta: HTTP %s" % status)
    return [(c["name"], m["name"].split(".")[-1])
            for c in json.loads(text)["cubes"] if c.get("public") is not False
            for m in c[kind] if m.get("public") is not False]


def expect_one_entity_each(ctx, kind, entities):
    missing, doubled = [], []
    for table, name in cube_members(kind):
        tid = ctx.mb.table(table)["id"]
        entity = ctx.mb.entity(entities, table, name)
        if not entity:
            missing.append("%s.%s" % (table, name))
        elif len([e for e in entities if e["table_id"] == tid and e["name"] == entity["name"] and not e["archived"] and e["creator_id"] == 13371338]) != 1:
            doubled.append("%s.%s" % (table, name))
    if missing or doubled:
        raise Fail("missing: %s; duplicated: %s" % (missing[:10], doubled[:10]))


@check("semantic.measure.every_member")
def _(ctx):
    expect_one_entity_each(ctx, "measures", ctx.mb.measures)
    assert_rows(measure_rows(ctx, ORD, "revenue")["rows"], ch(REV))


@check("semantic.measure.in_summarize")
def _(ctx):
    meta = ctx.mb.api("GET", "/api/table/%d/query_metadata" % ctx.mb.table(ORD)["id"])
    measures = {m["name"]: m for m in meta.get("measures") or []}
    if "Revenue" not in measures:
        raise Fail("query_metadata has no Measure Revenue: %s" % sorted(measures)[:10])
    if any(f["name"] == "revenue" for f in meta["fields"]):
        raise Fail("the measure is also a plain column of the table")
    res = ctx.mb.raw({"source-table": ctx.mb.table(ORD)["id"], "aggregation": [["measure", measures["Revenue"]["id"]]]})
    assert_rows(res["rows"], ch(REV))
    if res["cols"][0]["display_name"] != "Revenue":
        raise Fail("the result column is %r" % res["cols"][0]["display_name"])


@check("semantic.measure.type_number")
def _(ctx):
    assert_rows(measure_rows(ctx, ORD, "paid_share")["rows"],
                ch("SELECT sumIf(toFloat64(amount), %s) / sum(toFloat64(amount)) FROM orders" % PAID), tol=1e-9)


@check("semantic.measure.type_string")
def _(ctx):
    assert_rows(measure_rows(ctx, ORD, "statuses")["rows"], ch("SELECT arrayStringConcat(arraySort(groupUniqArray(status)), ',') FROM orders"))


@check("semantic.measure.type_time")
def _(ctx):
    assert_rows(measure_rows(ctx, ORD, "first_order_time")["rows"], ch("SELECT min(created_at) FROM orders"))


@check("semantic.measure.type_boolean")
def _(ctx):
    got = measure_rows(ctx, ORD, "has_orders")["rows"]
    want = ch("SELECT count() > 0 FROM orders")
    if bool(got[0][0]) != bool(want[0][0]):
        raise Fail("got %s, truth %s" % (got, want))


@check("semantic.segment.every_member")
def _(ctx):
    expect_one_entity_each(ctx, "segments", ctx.mb.segments)
    e = ctx.mb.entity(ctx.mb.segments, ORD, "paid")
    if e.get("creator_id") != 13371338:
        raise Fail("the Segment has creator %s" % e.get("creator_id"))
    assert_rows(ctx.mb.raw({"source-table": ctx.mb.table(ORD)["id"], "aggregation": [["count"]], "filter": ["segment", e["id"]]})["rows"],
                ch("SELECT count() FROM orders WHERE %s" % PAID))


@check("semantic.display_names")
def _(ctx):
    if ctx.mb.table("users")["display_name"] != "Customers":
        raise Fail("users is %r" % ctx.mb.table("users")["display_name"])
    if ctx.mb.field(ORD, "id")["display_name"] != "Order ID":
        raise Fail("orders.id is %r" % ctx.mb.field(ORD, "id")["display_name"])
    if not ctx.mb.entity(ctx.mb.measures, ORD, "avg_order_value")["name"] == "AOV":
        raise Fail("avg_order_value has another name")
    assert_rows(ctx.mb.mbql("users", agg=["m:users.count"])["rows"], ch(CNT_U))


@check("semantic.descriptions")
def _(ctx):
    expect_description("users", "People who have an account in the shop.")(ctx, None)
    if ctx.mb.field(ORD, "user_id")["description"] != "Customer who placed the order.":
        raise Fail("orders.user_id: %r" % ctx.mb.field(ORD, "user_id")["description"])
    expect_description(ORD, "Total order amount in USD.", "revenue")(ctx, None)
    if not str(ctx.mb.entity(ctx.mb.segments, ORD, "paid")["description"]).startswith("Completed or shipped orders.\n\nTable: Orders (orders, id "):
        raise Fail("the Segment paid has another description")
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.count"])["rows"], ch(CNT_O))


def semantic_type_check(check_id, field, want, truth, *measure):
    @check(check_id)
    def _(ctx):
        got = ctx.mb.field(ORD, field).get("semantic_type")
        if got != want:
            raise Fail("orders.%s semantic_type %s, want %s" % (field, got, want))
        assert_rows(ctx.mb.mbql(ORD, fields=["d:orders.%s" % field], where=ID7)["rows"], ch(truth))
        for name in measure:
            data = measure_rows(ctx, ORD, name)
            if data["cols"][0].get("semantic_type") != want:
                raise Fail("the result of Measure %s has semantic type %s" % (name, data["cols"][0].get("semantic_type")))


semantic_type_check("semantic.format.currency", "amount_dim", "type/Currency", "SELECT amount FROM orders WHERE id = 7", "revenue")
semantic_type_check("semantic.format.percent", "amount_pct", "type/Percentage", "SELECT amount / 1000 FROM orders WHERE id = 7", "paid_share")
semantic_type_check("semantic.format.link", "invoice_url", "type/URL", _INV)


@check("semantic.fk.from_joins")
def _(ctx):
    pairs = {("orders", "user_id", "users", "id"), ("orders", "product_id", "products", "id"),
             ("payments", "order_id", "orders", "id"), ("level_two", "product_id", "products", "id")}
    for source, column, target, key in pairs:
        got = ctx.mb.field(source, column)
        if got.get("fk_target_field_id") != ctx.mb.field(target, key)["id"] or got.get("semantic_type") != "type/FK":
            raise Fail("%s.%s has target %s and type %s" % (source, column, got.get("fk_target_field_id"), got.get("semantic_type")))
    # orders joins promos with an expression on id, so that column holds no key.
    if ctx.mb.field(ORD, "id").get("fk_target_field_id"):
        raise Fail("an expression join became a foreign key")
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.count"], by=["d:orders>users.city"])["rows"],
                ch("SELECT u.city, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY u.city"))


@check("semantic.fk.join_removed")
def _(ctx):
    if not probe(ctx, "created")["fields"]["user_id"].get("fk_target_field_id"):
        raise Fail("the probe join gave no foreign key")
    f = probe(ctx, "removed")["fields"]["user_id"]
    if f.get("fk_target_field_id") or f.get("semantic_type") == "type/FK":
        raise Fail("the foreign key stays after the join went away: %s" % f.get("fk_target_field_id"))
    assert_rows(probe(ctx, "created_rows"), ch(PROBE_TRUTH))


@check("semantic.drill_members")
def _(ctx):
    text = ctx.mb.entity(ctx.mb.measures, ORD, "count").get("description") or ""
    if "drill into this measure, use: id, status, created_at" not in text:
        raise Fail("the Measure count has the description %r" % text)
    assert_rows(ctx.mb.mbql(ORD, fields=["d:orders.id", "d:orders.status"], order=[["asc", "d:orders.id"]], limit=2)["rows"],
                ch("SELECT id, status FROM orders ORDER BY id LIMIT 2"))


@check("semantic.agent_reads_notes")
def _(ctx):
    """An agent reads this through the MCP server, with no knowledge of Cube."""
    wanted = {ORD: ["Hierarchies: Fulfilment: status > size", '"ai_hint":"Revenue questions start here."'],
              "paid_sales": ["Folders: Money: net_sales, [Ratios: count]", "View groups: Revenue views"]}
    for table, texts in wanted.items():
        reply = json.dumps(get_fields_table(ctx, ctx.mb.table(table)["id"]))
        for text in texts:
            if text.replace('"', '\\"') not in reply and text not in reply:
                raise Fail("get_fields of %s has no text %r" % (table, text))
    mcp_rows(ctx, ORD, CNT_O, agg=["m:orders.count"])


@check("semantic.resync_no_duplicates")
def _(ctx):
    first, again = probe(ctx, "created"), probe(ctx, "resynced")
    key = lambda items: sorted((m["id"], m["entity_id"], m["name"], m["archived"]) for m in items)
    for kind in ("measures", "segments"):
        if key(first[kind]) != key(again[kind]):
            raise Fail("%s changed on resync: %s -> %s" % (kind, key(first[kind]), key(again[kind])))
    if [m["updated_at"] for m in sorted(first["measures"], key=lambda m: m["id"])] != [m["updated_at"] for m in sorted(again["measures"], key=lambda m: m["id"])]:
        raise Fail("a resync wrote to a Measure that did not change")
    assert_rows(probe(ctx, "created_rows"), ch(PROBE_TRUTH))
    assert_rows(probe(ctx, "segment_rows"), ch(PROBE_SEGMENT_TRUTH))


@check("semantic.model_change_updates")
def _(ctx):
    changed = probe(ctx, "changed")
    m = next((x for x in only_active(changed["measures"]) if x["name"] == "Probe total two"), None)
    if not m or not m["description"].startswith("Second text.\n\nTable: "):
        raise Fail("the Measure did not follow the model: %s" % m)
    if m["id"] not in {x["id"] for x in probe(ctx, "created")["measures"]}:
        raise Fail("the change made a new Measure")
    assert_rows(probe(ctx, "created_rows"), ch(PROBE_TRUTH))


@check("semantic.removed_archived")
def _(ctx):
    removed = probe(ctx, "removed")
    created = probe(ctx, "created")
    for kind, title in (("measures", "Probe total two"), ("segments", "Probe completed")):
        ids = {m["id"] for m in created[kind]}
        gone = [m for m in removed[kind] if m["id"] in ids and m["name"] == title]
        if not gone or not gone[0]["archived"]:
            raise Fail("%s is not archived: %s" % (title, gone))
    dropped = probe(ctx, "dropped")
    if only_active(dropped["measures"]) or only_active(dropped["segments"]):
        raise Fail("objects of a dropped cube stay active: %s" % [m["name"] for m in only_active(dropped["measures"])])
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.count"])["rows"], ch(CNT_O))


@check("semantic.restored_member")
def _(ctx):
    created, restored = probe(ctx, "created"), probe(ctx, "restored")
    active = [m for m in restored["measures"] if m["name"] == "Probe total two" and not m["archived"]]
    if len(active) != 1 or active[0]["id"] not in {m["id"] for m in created["measures"]}:
        raise Fail("the member did not get its object back: %s" % active)
    assert_rows(probe(ctx, "restored_rows"), ch(PROBE_TRUTH))


@check("semantic.user_edit_reset")
def _(ctx):
    edited = next(m for m in probe(ctx, "reset")["measures"] if m["id"] == ctx.probe.seen["edited_id"])
    if edited["name"] != "Probe edited" or not edited["description"].startswith("Cube text.\n\nTable: "):
        raise Fail("the Measure did not take the Cube values: %s, %s" % (edited["name"], edited["description"]))
    assert_rows(probe(ctx, "created_rows"), ch(PROBE_TRUTH))


@check("semantic.user_edit_kept")
def _(ctx):
    ctx_changed = probe(ctx, "changed")
    edited = next(m for m in ctx_changed["measures"] if m["id"] == ctx.probe.seen["edited_id"])
    if edited["name"] != "My measure" or edited["description"] != "My text.":
        raise Fail("a sync replaced the edit of a Measure: %s, %s" % (edited["name"], edited["description"]))
    archived = next(m for m in ctx_changed["measures"] if m["id"] == ctx.probe.seen["user_archived_id"])
    if not archived["archived"]:
        raise Fail("a sync restored a Measure that a person archived")
    field = ctx_changed["fields"]["status"]
    if field["display_name"] != "My field" or field["description"] != "My field text.":
        raise Fail("a sync replaced the edit of a field: %s, %s" % (field["display_name"], field["description"]))
    table = ctx_changed["table"]
    if table["display_name"] != "My table" or table["description"] != "My table text.":
        raise Fail("a sync replaced the edit of a table: %s, %s" % (table["display_name"], table["description"]))
    assert_rows(probe(ctx, "created_rows"), ch(PROBE_TRUTH))


# ----------------------------------------------------------------- main


# ----- query compilation: what Cube does with each query shape


class PgError(Exception):
    """An error from the Cube SQL API."""


def cube_sql(sql):
    """Run SQL on the Cube SQL API with the simple Postgres protocol. Only for EXPLAIN and for errors, never for truth."""
    host = os.environ.get("CUBE_SQL_API_HOST", "127.0.0.1")
    port = int(os.environ.get("CUBE_SQL_API_PORT", "15433"))
    sock = socket.create_connection((host, port), timeout=HTTP_TIMEOUT)
    buf = bytearray()

    def read(n):
        while len(buf) < n:
            chunk = sock.recv(65536)
            if not chunk:
                raise PgError("the connection closed")
            buf.extend(chunk)
        out = bytes(buf[:n])
        del buf[:n]
        return out

    def message():
        tag = read(1)
        size = struct.unpack("!i", read(4))[0]
        return tag, read(size - 4)

    def text_of_error(body):
        return "".join(f[1:].decode("utf-8", "replace") for f in body.split(b"\0") if f[:1] == b"M")

    try:
        start = b"user\0cube\0database\0cube\0\0"
        sock.sendall(struct.pack("!ii", len(start) + 8, 196608) + start)
        while True:
            tag, body = message()
            if tag == b"R" and struct.unpack("!i", body[:4])[0] == 3:
                password = b"cube-dev-only\0"
                sock.sendall(b"p" + struct.pack("!i", len(password) + 4) + password)
            elif tag == b"E":
                raise PgError(text_of_error(body))
            elif tag == b"Z":
                break
        query = sql.encode() + b"\0"
        sock.sendall(b"Q" + struct.pack("!i", len(query) + 4) + query)
        rows, error = [], None
        while True:
            tag, body = message()
            if tag == b"D":
                count, pos, row = struct.unpack("!h", body[:2])[0], 2, []
                for _ in range(count):
                    size = struct.unpack("!i", body[pos:pos + 4])[0]
                    pos += 4
                    row.append(None if size < 0 else body[pos:pos + size].decode("utf-8", "replace"))
                    pos += max(size, 0)
                rows.append(row)
            elif tag == b"E":
                error = text_of_error(body)
            elif tag == b"Z":
                break
    finally:
        sock.close()
    if error:
        raise PgError(error)
    return rows


def plan_of(sql):
    """(logical plan, list of Cube requests) from EXPLAIN."""
    rows = cube_sql("EXPLAIN " + sql)
    logical = next(r[1] for r in rows if r[0] == "logical_plan")
    physical = next(r[1] for r in rows if r[0] == "physical_plan")
    requests = [json.JSONDecoder().raw_decode(physical[m.end():])[0] for m in re.finditer(r"Request:\s*", physical)]
    return logical, requests


def kind_of(sql):
    """regular: Cube runs one CubeScan. post: Cube computes over a CubeScan. pushdown: Cube writes SQL for the database."""
    logical, _ = plan_of(sql)
    if re.search(r"CubeScanWrapp|WrappedSelect", logical):
        return "pushdown"
    return "regular" if logical.lstrip().startswith("CubeScan") else "post"


def compile_query(ctx, query):
    res = ctx.mb.api("POST", "/api/dataset/native", {"database": ctx.mb.need_db(), "type": "query", "query": query, "pretty": False})
    return res["query"]


def ensure_table(ctx, name):
    if name.lower() in ctx.mb.tables:
        return
    db = ctx.mb.need_db()
    ctx.mb.api("POST", "/api/database/%d/sync_schema" % db)
    for _ in range(40):
        time.sleep(3)
        ctx.mb.discover()
        if name.lower() in ctx.mb.tables and ctx.mb.table(name)["fields"]:
            return
    raise Fail("no table %s after a sync" % name)


def C(check_id, table, sql, kind=None, ordered=False, tol=0.0, sql_has=None, setting=None, **kw):
    """Run MBQL through Metabase, compare with the truth, then check the plan of Cube and the compiled SQL."""
    def run(ctx):
        ensure_table(ctx, table)
        restore = None
        if setting:
            restore = {k: ctx.mb.api("GET", "/api/setting/" + k) for k in setting}
            for k, v in setting.items():
                ctx.mb.api("PUT", "/api/setting/" + k, {"value": v})
        try:
            query = ctx.mb.inner(table, **kw)
            text = compile_query(ctx, query)
            if sql_has and sql_has not in text:
                raise Fail("the SQL has no %s: %s" % (sql_has, text[:400]))
            if kind:
                got = kind_of(text)
                if got not in ((kind,) if isinstance(kind, str) else kind):
                    raise Fail("Cube runs this as %s, want %s: %s" % (got, kind, text[:300]))
            assert_rows(ctx.mb.raw(query)["rows"], ch(sql), ordered=ordered, tol=tol)
        finally:
            for k, v in (restore or {}).items():
                ctx.mb.api("PUT", "/api/setting/" + k, {"value": v if isinstance(v, str) and v else None})
    check(check_id)(run)


def expect_clear_error(ctx, query, needle):
    """Metabase must show a message that says what to do, and no bare Cube planner text."""
    try:
        res = ctx.mb.api("POST", "/api/dataset", {"database": ctx.mb.need_db(), "type": "query", "query": query}, ok=(200, 202, 400, 500))
    except Fail as e:
        raise Fail("no error reply: %s" % e)
    text = str(res.get("error") or res.get("message") or "")
    if needle not in text:
        raise Fail("the error has no %r: %s" % (needle, text[:400]))


# weeks, with both settings of metabase/metabase#42255

WK = "d:orders.created_at@week"
BIGR = "big_orders_raw"
SUN = {"start-of-week": "sunday"}
MON = {"start-of-week": "monday"}
C("compile.week.sunday_with_granularity", ORD, "SELECT toStartOfWeek(created_at, 0), count() FROM orders GROUP BY 1", kind="regular", setting=SUN,
  sql_has="week_from_sunday", agg=[["count"]], by=[WK])
C("compile.week.sunday_non_additive", ORD, "SELECT toStartOfWeek(created_at, 0), uniqExact(user_id) FROM orders GROUP BY 1", kind="regular", setting=SUN,
  agg=["m:orders.unique_customers"], by=[WK])
C("compile.week.monday", ORD, "SELECT toStartOfWeek(created_at, 1), count() FROM orders GROUP BY 1", kind="regular", setting=MON, agg=[["count"]], by=[WK])
C("compile.week.monday_measure", ORD, "SELECT toStartOfWeek(created_at, 1), sum(amount) FROM orders GROUP BY 1", kind="regular", setting=MON, agg=["m:orders.revenue"], by=[WK])
C("compile.week.sunday_without_granularity", BIGR, "SELECT toStartOfWeek(created_at, 0), sum(amount) FROM big_orders GROUP BY 1", kind="pushdown", setting=SUN,
  agg=["m:big_orders_raw.revenue"], by=["d:big_orders_raw.created_at@week"])
C("compile.week.sunday_without_granularity_distinct", BIGR, "SELECT toStartOfWeek(created_at, 0), status, uniqExact(user_id) FROM big_orders GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 9", kind="pushdown", setting=SUN, ordered=True,
  agg=["m:big_orders_raw.unique_users"], by=["d:big_orders_raw.created_at@week", "d:big_orders_raw.status"], order=[["asc", "d:big_orders_raw.created_at@week"], ["asc", "d:big_orders_raw.status"]], limit=9)
C("compile.week.saturday_without_granularity", ORD, "SELECT toStartOfWeek(addDays(created_at, 2), 1) - 2, count() FROM orders GROUP BY 1", kind="pushdown", setting={"start-of-week": "saturday"},
  agg=[["count"]], by=[WK])
C("compile.week.day_of_week_monday", ORD, "SELECT toDayOfWeek(created_at), count() FROM orders GROUP BY 1", kind="pushdown", setting=MON, agg=[["count"]], by=["d:orders.created_at@day-of-week"])
C("compile.week.day_of_week_sunday", ORD, "SELECT toDayOfWeek(created_at, 3), count() FROM orders GROUP BY 1", kind="pushdown", setting=SUN, agg=[["count"]], by=["d:orders.created_at@day-of-week"])
C("compile.week.week_of_year_monday", ORD, "SELECT ceiling(toDayOfYear(toStartOfWeek(created_at, 1)) / 7), count() FROM orders GROUP BY 1", kind="pushdown", setting=MON,
  agg=[["count"]], by=["d:orders.created_at@week-of-year"])

# date parts stay right on 10 million rows, where post-processing would see only a part of the rows

BC = "d:big_orders_raw.created_at"
for _unit, _truth in [("minute-of-hour", "toMinute(created_at)"), ("second-of-minute", "toSecond(created_at)"), ("hour-of-day", "toHour(created_at)"),
                      ("day-of-month", "toDayOfMonth(created_at)"), ("day-of-year", "toDayOfYear(created_at)"), ("month-of-year", "toMonth(created_at)"),
                      ("quarter-of-year", "toQuarter(created_at)"), ("day-of-week", "toDayOfWeek(created_at, 3)")]:
    C("compile.part.%s" % _unit, BIGR, "SELECT %s, count(), uniqExact(user_id) FROM big_orders GROUP BY 1" % _truth, kind="pushdown", setting=SUN,
      agg=[["count"], "m:big_orders_raw.unique_users"], by=["%s@%s" % (BC, _unit)])
C("compile.part.non_additive_avg", ORD, "SELECT toMonth(created_at), avg(amount) FROM orders GROUP BY 1", kind="pushdown", agg=["m:orders.avg_order_value"], by=["d:orders.created_at@month-of-year"])
C("compile.part.number_measure", ORD, "SELECT toMonth(created_at), toFloat64(sumIf(amount, %s)) / toFloat64(sum(amount)) FROM orders GROUP BY 1" % PAID, kind="pushdown", agg=["m:orders.paid_share"], by=["d:orders.created_at@month-of-year"])
C("compile.part.filter_measure", ORD, "SELECT sum(amount) FROM orders WHERE toHour(created_at) != 3", kind="pushdown", agg=["m:orders.revenue"], where=[["!=", "d:orders.created_at@hour-of-day", 3]])
C("compile.part.filter_with_breakout", ORD, "SELECT status, count() FROM orders WHERE toQuarter(created_at) = 2 GROUP BY 1", kind="pushdown", agg=[["count"]], by=["d:orders.status"], where=[["=", "d:orders.created_at@quarter-of-year", 2]])

# plain buckets: Cube can answer from one CubeScan, so a rollup can serve them

for _unit, _truth in [("minute", "toStartOfMinute(created_at)"), ("hour", "toStartOfHour(created_at)"), ("day", "toDate(created_at)"), ("month", "toStartOfMonth(created_at)"),
                      ("quarter", "toStartOfQuarter(created_at)"), ("year", "toStartOfYear(created_at)")]:
    C("compile.bucket.%s" % _unit, ORD, "SELECT %s, sum(amount) FROM orders GROUP BY 1" % _truth, kind="regular", agg=["m:orders.revenue"], by=["d:orders.created_at@%s" % _unit])

# custom granularities: a proxy dimension with no bin, and native SQL

_HALF = "fromUnixTimestamp(intDiv(toUnixTimestamp(created_at), 43200) * 43200)"
C("compile.granularity.proxy_no_bin", ORD, "SELECT %s, count() FROM orders GROUP BY 1" % _HALF, kind="regular", agg=[["count"]], by=["d:orders.created_half_day@default"])
N("compile.granularity.native", "SELECT DATE_TRUNC('half_day', created_at) AS d, MEASURE(count) AS n FROM orders GROUP BY 1", "SELECT %s, count() FROM orders GROUP BY 1" % _HALF)
N("compile.granularity.native_fiscal_year", "SELECT DATE_TRUNC('fiscal_year_april', created_at) AS d, MEASURE(count) AS n FROM orders GROUP BY 1",
  "SELECT addMonths(toStartOfYear(addMonths(created_at, -3)), 3), count() FROM orders GROUP BY 1")
N("compile.granularity.native_offset_no_rollup", "SELECT DATE_TRUNC('fiscal_quarter', created_at) AS d, MEASURE(unique_customers) AS n, MEASURE(avg_order_value) AS a FROM orders GROUP BY 1",
  "SELECT addMonths(toStartOfQuarter(addMonths(created_at, -1)), 1), uniqExact(user_id), avg(amount) FROM orders GROUP BY 1")
N("compile.granularity.native_sunday", "SELECT DATE_TRUNC('week_from_sunday', created_at) AS d, MEASURE(count) AS n FROM orders GROUP BY 1", "SELECT toStartOfWeek(created_at, 0), count() FROM orders GROUP BY 1")

# filters on the demo types, with the NULL rule of Metabase: != keeps the NULL rows

TE = "typed_events"
for _fid, _clause, _sql in [
    ("string_ne_null", ["!=", "d:typed_events.n_str", "s1"], "n_str != 's1' OR n_str IS NULL"),
    ("string_is_null", ["is-null", "d:typed_events.n_str"], "n_str IS NULL"),
    ("string_not_null", ["not-null", "d:typed_events.n_str"], "n_str IS NOT NULL"),
    ("string_is_empty", ["is-empty", "d:typed_events.n_str"], "n_str IS NULL OR n_str = ''"),
    ("string_not_empty", ["not-empty", "d:typed_events.n_str"], "n_str IS NOT NULL AND n_str != ''"),
    ("string_contains", ["contains", "d:typed_events.n_str", "1"], "n_str LIKE '%1%'"),
    ("string_not_contains", ["does-not-contain", "d:typed_events.n_str", "1"], "n_str NOT LIKE '%1%' OR n_str IS NULL"),
    ("string_starts", ["starts-with", "d:typed_events.lc", "al"], "lc LIKE 'al%'"),
    ("string_ends", ["ends-with", "d:typed_events.lc", "ma"], "lc LIKE '%ma'"),
    ("string_ends_ci", ["ends-with", "d:typed_events.lc", "MA", {"case-sensitive": False}], "lc ILIKE '%MA'"),
    ("string_multi_ne", ["!=", "d:typed_events.lc", "alpha", "beta"], "lc NOT IN ('alpha', 'beta')"),
    ("number_ne_null", ["!=", "d:typed_events.n_int", 1], "n_int != 1 OR n_int IS NULL"),
    ("number_is_null", ["is-null", "d:typed_events.n_int"], "n_int IS NULL"),
    ("number_gt_null", [">", "d:typed_events.n_int", 5], "n_int > 5"),
    ("number_between", ["between", "d:typed_events.n_dec", 10, 20], "n_dec BETWEEN 10 AND 20"),
    ("number_decimal_eq", ["=", "d:typed_events.dec_amount", 0.875], "dec_amount = 0.875"),
    ("number_multi", ["=", "d:typed_events.n_int", 1, 2, 3], "n_int IN (1, 2, 3)"),
    ("bool_true", ["=", "d:typed_events.flag", True], "flag"),
    ("bool_false", ["=", "d:typed_events.flag", False], "NOT flag"),
    ("bool_null", ["is-null", "d:typed_events.n_bool"], "n_bool IS NULL"),
    ("bool_nullable_true", ["=", "d:typed_events.n_bool", True], "n_bool = true"),
    ("bool_nullable_ne", ["!=", "d:typed_events.n_bool", True], "n_bool = false OR n_bool IS NULL"),
    ("bool_expr", ["=", "d:typed_events.has_t1", True], "has(tags, 't1')"),
    ("time_gte", [">=", "d:typed_events.ts_utc", "2025-03-10T12:00:00"], "ts_utc >= toDateTime('2025-03-10 12:00:00', 'UTC')"),
    ("time_lte", ["<=", "d:typed_events.ts_utc", "2025-03-10T12:00:00"], "ts_utc <= toDateTime('2025-03-10 12:00:00', 'UTC')"),
    ("time_between_minutes", ["between", "d:typed_events.ts_utc", "2025-03-10T12:00:00", "2025-03-11T06:00:00"], "ts_utc BETWEEN toDateTime('2025-03-10 12:00:00', 'UTC') AND toDateTime('2025-03-11 06:00:00', 'UTC')"),
    ("time_day_eq", ["=", "d:typed_events.ts_utc@day", "2025-03-10"], "toDate(ts_utc) = '2025-03-10'"),
    ("time_month_eq", ["=", "d:typed_events.ts_utc@month", "2025-03-01"], "toStartOfMonth(ts_utc) = '2025-03-01'"),
    ("time_ne_day", ["!=", "d:typed_events.ts_utc@day", "2025-03-10"], "toDate(ts_utc) != '2025-03-10'"),
    ("time_is_null", ["is-null", "d:typed_events.n_ts"], "n_ts IS NULL"),
    ("time_not_null", ["not-null", "d:typed_events.n_ts"], "n_ts IS NOT NULL"),
    ("time_on_date_column", ["=", "d:typed_events.day", "2025-03-10"], "day = '2025-03-10'"),
    ("time_on_date32_column", [">=", "d:typed_events.day32", "2025-04-01"], "day32 >= '2025-04-01'"),
    ("time_on_ny_column", ["=", "d:typed_events.ts_ny@day", "2025-03-10"], "toDate(ts_ny, 'UTC') = '2025-03-10'"),
    ("time_on_berlin_column", ["=", "d:typed_events.ts64_berlin@day", "2025-03-10"], "toDate(ts64_berlin, 'UTC') = '2025-03-10'"),
    ("time_next_days", ["time-interval", "d:typed_events.ts_utc", 30, "day"], "toDate(ts_utc) >= today() + 1 AND toDate(ts_utc) <= today() + 30"),
    ("not_group", ["not", ["or", ["=", "d:typed_events.lc", "alpha"], ["is-null", "d:typed_events.n_int"]]], "NOT (lc = 'alpha' OR n_int IS NULL)"),
]:
    C("compile.filter.%s" % _fid, TE, "SELECT count() FROM typed_events WHERE %s" % _sql, agg=[["count"]], where=[_clause])
    if _fid in ("string_ne_null", "number_ne_null", "bool_true", "time_day_eq", "time_is_null"):
        C("compile.filter_measure.%s" % _fid, TE, "SELECT sum(dec_amount) FROM typed_events WHERE %s" % _sql, tol=1e-6, agg=["m:typed_events.sum_dec"], where=[_clause])

# text that could break out of a literal must stay a value

C("compile.filter.quote_in_value", ORD, "SELECT count() FROM orders WHERE status = 'it\\'s'", agg=[["count"]], where=[["=", STA, "it's"]])
C("compile.filter.backslash_quote_in_value", ORD, "SELECT count() FROM orders WHERE status = 'x\\\\\\' OR 1=1 -- '", agg=[["count"]], where=[["=", STA, "x\\' OR 1=1 -- "]])
C("compile.filter.quote_in_contains", ORD, "SELECT count() FROM orders WHERE position(status, 'a\\'b') > 0", agg=[["count"]], where=[["contains", STA, "a'b"]])
C("compile.filter.backslash_in_case", ORD, "SELECT if(status = 'shipped', 'a\\\\b', 'c') FROM orders WHERE id = 7", fields=[["expression", "k"]],
  exprs={"k": ["case", [[["=", STA, "shipped"], "a\\b"]], {"default": "c"}]}, where=ID7)

# a count over no rows

C("compile.empty.count", ORD, "SELECT count() FROM orders WHERE status = 'nope'", agg=[["count"]], where=[["=", STA, "nope"]])
C("compile.empty.count_measure", ORD, "SELECT count() FROM orders WHERE status = 'nope'", agg=["m:orders.count"], where=[["=", STA, "nope"]])
C("compile.empty.distinct_measure", ORD, "SELECT uniqExact(user_id) FROM orders WHERE status = 'nope'", agg=["m:orders.unique_customers"], where=[["=", STA, "nope"]])
C("compile.empty.breakout", ORD, "SELECT status, count() FROM orders WHERE status = 'nope' GROUP BY 1", kind="regular", agg=[["count"]], by=[STA], where=[["=", STA, "nope"]])


@check("compile.empty.sum_is_null")
def _(ctx):
    rows = ctx.mb.mbql(ORD, agg=["m:orders.revenue"], where=[["=", STA, "nope"]])["rows"]
    if rows not in ([[None]], [[0]], [[0.0]]):
        raise Fail("got %s" % rows)
    assert_rows(ch("SELECT sum(amount) FROM orders WHERE status = 'nope'"), [[0]])


# joins

_JO = "SELECT u.%s, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1"
C("compile.join.implicit_uses_join_field", ORD, _JO % "city", kind=("regular", "pushdown"), sql_has="__cubeJoinField", agg=[["count"]], by=["d:orders>users.city"])
C("compile.join.implicit_two_tables", ORD, "SELECT u.city, p.category, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id JOIN products p ON o.product_id = p.id GROUP BY 1, 2", kind="regular", sql_has="__cubeJoinField",
  agg=["m:orders.revenue"], by=["d:orders>users.city", "d:orders>products.category"])
C("compile.join.filter_on_both_sides", ORD, "SELECT u.city, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id WHERE u.is_vip AND o.status = 'completed' GROUP BY 1", kind="regular",
  agg=["m:orders.revenue"], by=["d:orders>users.city"], where=[["=", "d:orders>users.is_vip", True], ["=", STA, "completed"]])


def J(check_id, table, sql, join_table, alias, strategy, left, right, kind=None, **kw):
    def run(ctx):
        ensure_table(ctx, table)
        ensure_table(ctx, join_table)
        joins = [{"source-table": ctx.mb.table(join_table)["id"], "alias": alias, "strategy": strategy, "condition": ["=", left, right]}]
        query = ctx.mb.inner(table, joins=joins, **kw)
        text = compile_query(ctx, query)
        if "__cubeJoinField" not in text:
            raise Fail("the SQL has no __cubeJoinField: %s" % text[:300])
        if kind and kind_of(text) != kind:
            raise Fail("Cube runs this as %s: %s" % (kind_of(text), text[:300]))
        assert_rows(ctx.mb.raw(query)["rows"], ch(sql))
    check(check_id)(run)


J("compile.join.explicit_two_cubes", ORD, _JO % "state", "users", "U", "left-join", "d:orders.user_id", "d:users.id|U", kind="pushdown", agg=[["count"]], by=["d:users.state|U"])
J("compile.join.explicit_measure_regular", ORD, "SELECT u.state, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1", "users", "U", "left-join", "d:orders.user_id", "d:users.id|U", kind="regular", agg=["m:orders.revenue"], by=["d:users.state|U"])
J("compile.join.left_keeps_rows_with_no_match", "orphan_orders", "SELECT c.name, count() FROM orphan_orders o LEFT JOIN orphan_customers c ON o.customer_id = c.id GROUP BY 1 SETTINGS join_use_nulls = 1",
  "orphan_customers", "C", "left-join", "d:orphan_orders.customer_id", "d:orphan_customers.id|C", agg=[["count"]], by=["d:orphan_customers.name|C"])
J("compile.join.inner_drops_rows_with_no_match", "orphan_orders", "SELECT c.name, count() FROM orphan_orders o INNER JOIN orphan_customers c ON o.customer_id = c.id GROUP BY 1",
  "orphan_customers", "C", "inner-join", "d:orphan_orders.customer_id", "d:orphan_customers.id|C", agg=[["count"]], by=["d:orphan_customers.name|C"])
J("compile.join.inner_total", "orphan_orders", "SELECT sum(o.amount) FROM orphan_orders o INNER JOIN orphan_customers c ON o.customer_id = c.id",
  "orphan_customers", "C", "inner-join", "d:orphan_orders.customer_id", "d:orphan_customers.id|C", agg=["m:orphan_orders.total"])
J("compile.join.no_fan_out", ORD, "SELECT p.method, count() FROM (SELECT id FROM orders) o JOIN (SELECT id AS order_id, 'card' AS method FROM orders UNION ALL SELECT id, 'fee' FROM orders) p ON o.id = p.order_id GROUP BY 1",
  "payments", "P", "left-join", "d:orders.id", "d:payments.order_id|P", agg=["m:orders.count"], by=["d:payments.method|P"])


@check("compile.join.other_columns_give_error")
def _(ctx):
    joins = [{"source-table": ctx.mb.table("users")["id"], "alias": "U", "strategy": "left-join", "condition": ["=", "d:orders.status", "d:users.city|U"]}]
    expect_clear_error(ctx, ctx.mb.inner(ORD, agg=[["count"]], by=["d:users.city|U"], joins=joins), "joins only through the data model")


@check("compile.join.subquery_gives_error")
def _(ctx):
    joins = [{"source-query": ctx.mb.inner("users", where=[["=", "d:users.is_vip", True]]), "alias": "U", "strategy": "left-join", "condition": ["=", "d:orders.user_id", ["field", "id", {"base-type": "type/Integer", "join-alias": "U"}]]}]
    expect_clear_error(ctx, ctx.mb.inner(ORD, agg=[["count"]], joins=joins), "one equality of two columns")


# segments

C("compile.segment.with_breakout", ORD, "SELECT status, sum(amount) FROM orders WHERE %s GROUP BY 1" % PAID, kind="regular", agg=["m:orders.revenue"], by=[STA], where=["s:orders.paid"])
C("compile.segment.two", ORD, "SELECT count() FROM orders WHERE %s AND quantity >= 3" % PAID, agg=["m:orders.count"], where=["s:orders.paid", "s:orders.large"])
C("compile.segment.with_filter_and_month", ORD, "SELECT toStartOfMonth(created_at), count() FROM orders WHERE %s AND quantity = 2 GROUP BY 1" % PAID, kind="regular", agg=[["count"]],
  by=["d:orders.created_at@month"], where=["s:orders.paid", ["=", QTY, 2]])

# order, limit, offset

C("compile.order.by_measure_desc", ORD, "SELECT status, sum(amount) r FROM orders GROUP BY 1 ORDER BY r DESC", kind="regular", ordered=True, agg=["m:orders.revenue"], by=[STA], order=[["desc", ["aggregation", 0]]])
C("compile.order.two_columns", ORD, "SELECT status, if(quantity >= 3, 'large', 'small') s, count() FROM orders GROUP BY 1, 2 ORDER BY 1 DESC, 2 ASC", kind="regular", ordered=True, agg=[["count"]],
  by=[STA, "d:orders.size"], order=[["desc", STA], ["asc", "d:orders.size"]])
C("compile.order.limit", ORD, "SELECT status, count() c FROM orders GROUP BY 1 ORDER BY 1 LIMIT 2", kind="regular", ordered=True, agg=[["count"]], by=[STA], order=[["asc", STA]], limit=2)


@check("compile.order.page_offset")
def _(ctx):
    query = ctx.mb.inner(ORD, fields=["d:orders.id"], order=[["asc", "d:orders.id"]])
    query["page"] = {"page": 3, "items": 4}
    text = compile_query(ctx, query)
    if kind_of(text) != "regular":
        raise Fail("Cube runs this as %s" % kind_of(text))
    assert_rows(ctx.mb.raw(query)["rows"], ch("SELECT id FROM orders ORDER BY id LIMIT 4 OFFSET 8"), ordered=True)


@check("compile.order.page_offset_grouped")
def _(ctx):
    query = ctx.mb.inner(ORD, agg=[["count"]], by=["d:orders.created_at@day"], order=[["asc", "d:orders.created_at@day"]])
    query["page"] = {"page": 2, "items": 5}
    assert_rows(ctx.mb.raw(query)["rows"], ch("SELECT toDate(created_at) d, count() FROM orders GROUP BY d ORDER BY d LIMIT 5 OFFSET 5"), ordered=True)


# expressions and custom columns

for _name, _clause, _sql in [
    ("trim", ["trim", ["concat", " ", STA, " "]], "status"),
    ("ltrim", ["ltrim", ["concat", " ", STA]], "status"),
    ("rtrim", ["rtrim", ["concat", STA, " "]], "status"),
    ("exp", ["exp", ["/", QTY, 2]], "exp(quantity / 2)"),
    ("log_of_expression", ["log", ["*", _AMT, 10]], "log10(toFloat64(amount) * 10)"),
    ("get_day", ["get-day", CRT], "toDayOfMonth(created_at)"),
    ("get_hour", ["get-hour", CRT], "toHour(created_at)"),
    ("get_minute", ["get-minute", CRT], "toMinute(created_at)"),
    ("get_second", ["get-second", CRT], "toSecond(created_at)"),
    ("get_quarter", ["get-quarter", CRT], "toQuarter(created_at)"),
    ("get_day_of_week", ["get-day-of-week", CRT], "toDayOfWeek(created_at, 3)"),
    ("datediff_hour", ["datetime-diff", "d:orders.created_fiscal_quarter", CRT, "hour"], "intDiv(dateDiff('second', addMonths(toStartOfQuarter(addMonths(created_at, -1)), 1), created_at), 3600)"),
    ("datediff_minute", ["datetime-diff", "d:orders.created_fiscal_quarter", CRT, "minute"], "intDiv(dateDiff('second', addMonths(toStartOfQuarter(addMonths(created_at, -1)), 1), created_at), 60)"),
    ("datediff_second", ["datetime-diff", "d:orders.created_fiscal_quarter", CRT, "second"], "dateDiff('second', addMonths(toStartOfQuarter(addMonths(created_at, -1)), 1), created_at)"),
    ("datediff_day", ["datetime-diff", "d:orders.created_fiscal_quarter", CRT, "day"], "dateDiff('day', addMonths(toStartOfQuarter(addMonths(created_at, -1)), 1), created_at)"),
    ("datediff_week", ["datetime-diff", "d:orders.created_fiscal_quarter", CRT, "week"], "intDiv(dateDiff('day', addMonths(toStartOfQuarter(addMonths(created_at, -1)), 1), created_at), 7)"),
    ("integer_division", ["/", QTY, 3], "quantity / 3"),
    ("case_two_branches", ["case", [[["=", STA, "shipped"], 1], [["=", STA, "completed"], 2]], {"default": 0}], "multiIf(status = 'shipped', 1, status = 'completed', 2, 0)"),
    ("boolean_is_true", ["case", [[["=", "d:orders.is_paid", True], "y"]], {"default": "n"}], "if(%s, 'y', 'n')" % PAID),
]:
    Q("compile.expr.%s" % _name, ORD, "SELECT %s FROM orders WHERE id = 7" % _sql, fields=[["expression", _name]], exprs={_name: _clause}, where=ID7)

C("compile.expr.in_breakout", ORD, "SELECT if(quantity > 2, 'big', 'small') k, sum(amount) FROM orders GROUP BY 1", agg=["m:orders.revenue"],
  by=[["expression", "k"]], exprs={"k": ["case", [[[">", QTY, 2], "big"]], {"default": "small"}]})
C("compile.expr.in_filter", ORD, "SELECT count() FROM orders WHERE quantity * 2 > 4", agg=[["count"]], where=[[">", ["expression", "d"], 4]], exprs={"d": ["*", QTY, 2]})
C("compile.expr.in_filter_measure", ORD, "SELECT sum(amount) FROM orders WHERE upper(status) = 'SHIPPED'", agg=["m:orders.revenue"], where=[["=", ["expression", "u"], "SHIPPED"]], exprs={"u": ["upper", STA]})
C("compile.expr.in_aggregation", ORD, "SELECT status, sum(quantity * 2) FROM orders GROUP BY 1", agg=[["sum", ["*", QTY, 2]]], by=[STA])
C("compile.expr.measure_arithmetic", ORD, "SELECT status, sum(amount) / count() FROM orders GROUP BY 1", agg=[["/", "m:orders.revenue", "m:orders.count"]], by=[STA])
C("compile.expr.sum_where", ORD, "SELECT status, sumIf(quantity, quantity > 2) FROM orders GROUP BY 1", agg=[["sum-where", QTY, [">", QTY, 2]]], by=[STA])
C("compile.expr.count_where_breakout", ORD, "SELECT status, countIf(quantity > 2) FROM orders GROUP BY 1", agg=[["count-where", [">", QTY, 2]]], by=[STA])
C("compile.expr.distinct_where", ORD, "SELECT uniqExactIf(user_id, quantity > 2) FROM orders", agg=[["distinct-where", "d:orders.user_id", [">", QTY, 2]]])
C("compile.expr.sum_of_dimension", ORD, "SELECT sum(amount), min(amount), max(amount), avg(amount), uniqExact(amount) FROM orders",
  agg=[["sum", _AMT], ["min", _AMT], ["max", _AMT], ["avg", _AMT], ["distinct", _AMT]])


@check("compile.expr.datetime_add_in_aggregated_query_gives_error")
def _(ctx):
    """Stock Cube has no template for an interval on ClickHouse, so this stops with a message. The dialect has the template."""
    query = ctx.mb.inner(ORD, agg=[["count"]], by=[["expression", "d"]], exprs={"d": ["datetime-add", CRT, 1, "day"]}, order=[["asc", ["expression", "d"]]])
    if DIALECT:
        expect_rows(ctx, query, "SELECT created_at + INTERVAL 1 DAY AS d, count() FROM orders GROUP BY d", "with the dialect")
    else:
        expect_clear_error(ctx, query, "cannot add an interval")


@check("compile.expr.datetime_diff_month_gives_error")
def _(ctx):
    query = ctx.mb.inner(ORD, fields=[["expression", "d"]], exprs={"d": ["datetime-diff", "d:orders.created_fiscal_quarter", CRT, "month"]}, where=ID7)
    expect_clear_error(ctx, query, "cannot count whole months")


@check("compile.join.total_of_dimension_refused")
def _(ctx):
    """Cube adds each row of the joined cube once: SUM(products.price) by a column of orders is the total of the products. So the driver stops this."""
    price = "d:orders>products.price"
    for name, query in (("sum by status", ctx.mb.inner(ORD, agg=[["sum", price]], by=["d:orders.status"])),
                        ("sum", ctx.mb.inner(ORD, agg=[["sum", price]])),
                        ("avg by status", ctx.mb.inner(ORD, agg=[["avg", price]], by=["d:orders.status"])),
                        ("explicit join", ctx.mb.inner(ORD, agg=[["sum", "d:products.price|P"]], joins=[{
                            "source-table": ctx.mb.table("products")["id"], "alias": "P", "strategy": "left-join",
                            "condition": ["=", "d:orders.product_id", "d:products.id|P"]}]))):
        try:
            expect_clear_error(ctx, query, XRAY_TOTAL_REFUSAL)
        except Fail as e:
            raise Fail("%s: %s" % (name, e))
    # These do not depend on how often a row counts, and the side with the foreign key has no such rule.
    joined = "SELECT o.status, %s FROM orders o JOIN products p ON o.product_id = p.id GROUP BY 1"
    for agg, sql in ((["min", price], "min(p.price)"), (["max", price], "max(p.price)"), (["distinct", price], "uniqExact(p.price)")):
        assert_rows(ctx.mb.mbql(ORD, agg=[agg], by=["d:orders.status"])["rows"], ch(joined % sql), label=agg[0])
    assert_rows(ctx.mb.mbql(ORD, agg=[["sum", "d:orders.quantity_dim"]], by=["d:orders.status"])["rows"],
                ch("SELECT status, sum(quantity) FROM orders GROUP BY 1"), label="own column")


@check("compile.agg.median_gives_error")
def _(ctx):
    expect_clear_error(ctx, ctx.mb.inner(ORD, agg=[["median", _AMT]]), "Cube cannot")


C("compile.agg.other_aggregation_of_measure", ORD, "SELECT max(amount) FROM orders", agg=[["max", "d:orders.revenue"]], sql_has="MAX(")
C("compile.agg.count_of_measure", ORD, "SELECT count(amount) FROM orders", agg=[["count", "d:orders.revenue"]], sql_has="COUNT(")


@check("compile.agg.other_aggregation_can_give_error")
def _(ctx):
    expect_clear_error(ctx, ctx.mb.inner(ORD, agg=[["max", "d:orders.count"]]), "Use the measure")


@check("compile.agg.offset_measure")
def _(ctx):
    month = ctx.mb.ref("d:orders.created_at@month")
    query = {"source-table": ctx.mb.table(ORD)["id"], "breakout": [month], "order-by": [["asc", month]],
             "aggregation": [["offset", {"name": "p", "display-name": "p"}, ctx.mb.ref("m:orders.revenue"), -1]]}
    assert_rows(ctx.mb.raw(query)["rows"], ch("SELECT m, lagInFrame(r, 1, NULL) OVER (ORDER BY m ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM (SELECT toStartOfMonth(created_at) m, toNullable(sum(amount)) r FROM orders GROUP BY 1)"), ordered=True)


# nested queries and models

def _source(ctx, **kw):
    return {"source-query": ctx.mb.inner(ORD, **kw)}


@check("compile.nested.aggregate_of_grouped_rows")
def _(ctx):
    query = dict(_source(ctx, agg=["m:orders.revenue"], by=[STA]), aggregation=[["sum", ["field", "sum", {"base-type": "type/Float"}]]])
    assert_rows(ctx.mb.raw(query)["rows"], ch(REV))


@check("compile.nested.more_than_the_default_row_limit")
def _(ctx):
    ensure_table(ctx, BIGR)
    query = {"source-query": ctx.mb.inner(BIGR, agg=["m:big_orders_raw.count"], by=["d:big_orders_raw.user_id"]), "aggregation": [["count"]]}
    assert_rows(ctx.mb.raw(query)["rows"], ch("SELECT uniqExact(user_id) FROM big_orders"))


@check("compile.nested.raw_rows_with_filter")
def _(ctx):
    query = dict(_source(ctx, fields=["d:orders.id", STA]), filter=["=", ["field", "status", {"base-type": "type/Text"}], "shipped"], aggregation=[["count"]])
    assert_rows(ctx.mb.raw(query)["rows"], ch(SHIPPED))


@check("compile.nested.custom_column_on_question")
def _(ctx):
    card = ctx.mb.mbql_card("nested expr", ORD, agg=["m:orders.count"], by=[STA])
    n = ["field", "sum", {"base-type": "type/Float"}]
    query = {"source-table": "card__%d" % card["id"], "expressions": {"twice": ["*", n, 2]}, "fields": [["field", "status", {"base-type": "type/Text"}], ["expression", "twice"]]}
    assert_rows(ctx.mb.raw(query)["rows"], ch("SELECT status, count() * 2 FROM orders GROUP BY 1"))


@check("compile.nested.model_raw_rows_then_group")
def _(ctx):
    card = ctx.mb.mbql_card("model rows", ORD, kind="model", fields=[STA, QTY, _AMT])
    query = {"source-table": "card__%d" % card["id"], "aggregation": [["sum", ["field", "amount_dim", {"base-type": "type/Float"}]]], "breakout": [["field", "status", {"base-type": "type/Text"}]]}
    assert_rows(ctx.mb.raw(query)["rows"], ch(STATUS_REV))


@check("compile.nested.model_with_measure_then_filter")
def _(ctx):
    card = ctx.mb.mbql_card("model agg", ORD, kind="model", agg=["m:orders.revenue"], by=[STA])
    query = {"source-table": "card__%d" % card["id"], "filter": [">", ["field", "sum", {"base-type": "type/Float"}], 70000]}
    assert_rows(ctx.mb.raw(query)["rows"], ch("SELECT status, sum(amount) r FROM orders GROUP BY 1 HAVING r > 70000"))


@check("compile.nested.model_of_a_native_query")
def _(ctx):
    card = ctx.mb.card("native model", {"database": ctx.mb.need_db(), "type": "native", "native": {"query": "SELECT status, MEASURE(count) AS n FROM orders GROUP BY 1", "template-tags": {}}}, "model")
    ctx.mb.run_card(card["id"])
    query = {"source-table": "card__%d" % card["id"], "aggregation": [["sum", ["field", "n", {"base-type": "type/Float"}]]]}
    assert_rows(ctx.mb.raw(query)["rows"], ch(CNT_O))


@check("compile.nested.week_on_question_is_clear")
def _(ctx):
    card = ctx.mb.mbql_card("week source", ORD, fields=[CRT])
    query = {"source-table": "card__%d" % card["id"], "aggregation": [["count"]], "breakout": [["field", "created_at", {"base-type": "type/DateTime", "temporal-unit": "month"}]]}
    assert_rows(ctx.mb.raw(query)["rows"], ch("SELECT toStartOfMonth(created_at), count() FROM orders GROUP BY 1"))


# native SQL

@check("compile.native.field_filter_multi")
def _(ctx):
    tags = field_tag(ctx, "f", STA, "string/=")
    data = ctx.mb.native("SELECT MEASURE(count) AS n FROM orders WHERE {{f}}", tags=tags,
                         parameters=[{"type": "string/=", "target": ["dimension", ["template-tag", "f"]], "value": ["completed", "shipped"]}])
    assert_rows(data["rows"], ch("SELECT count() FROM orders WHERE %s" % PAID))


@check("compile.native.field_filter_number_range")
def _(ctx):
    tags = field_tag(ctx, "f", QTY, "number/between")
    data = ctx.mb.native("SELECT MEASURE(count) AS n FROM orders WHERE {{f}}", tags=tags,
                         parameters=[{"type": "number/between", "target": ["dimension", ["template-tag", "f"]], "value": [2, 3]}])
    assert_rows(data["rows"], ch("SELECT count() FROM orders WHERE quantity BETWEEN 2 AND 3"))


@check("compile.native.field_filter_relative_date")
def _(ctx):
    tags = field_tag(ctx, "f", CRT, "date/all-options")
    data = ctx.mb.native("SELECT MEASURE(count) AS n FROM orders WHERE {{f}}", tags=tags,
                         parameters=[{"type": "date/all-options", "target": ["dimension", ["template-tag", "f"]], "value": "past%ddays" % PAST_DAYS}])
    assert_rows(data["rows"], past_truth())


@check("compile.native.field_filter_empty_is_all_rows")
def _(ctx):
    tags = field_tag(ctx, "f", STA, "string/=")
    assert_rows(ctx.mb.native("SELECT MEASURE(count) AS n FROM orders WHERE {{f}}", tags=tags)["rows"], ch(CNT_O))


@check("compile.native.explain_regular_with_parameter")
def _(ctx):
    data = ctx.mb.native("SELECT status, MEASURE(revenue) AS r FROM orders WHERE created_at >= {{d}} GROUP BY 1", tags=tag("d", "date"),
                         parameters=[{"type": "date/single", "target": ["variable", ["template-tag", "d"]], "value": "2025-12-01"}])
    assert_rows(data["rows"], ch("SELECT status, sum(amount) FROM orders WHERE created_at >= '2025-12-01' GROUP BY 1"))
    if kind_of("SELECT status, MEASURE(revenue) AS r FROM orders WHERE created_at >= timestamp '2025-12-01 00:00:00' GROUP BY 1") != "regular":
        raise Fail("the native form is not a regular query")


@check("compile.native.error_keeps_cube_text")
def _(ctx):
    res = ctx.mb.api("POST", "/api/dataset", {"database": ctx.mb.need_db(), "type": "native", "native": {"query": "SELECT EXTRACT(MONTH FROM created_at) m, MEASURE(unique_customers) FROM orders GROUP BY 1", "template-tags": {}}}, ok=(200, 202, 400, 500))
    text = str(res.get("error") or "")
    if "Cube cannot plan this query" not in text or "Can't detect Cube query" not in text:
        raise Fail("error text: %s" % text[:400])


N("compile.native.cross_join", "SELECT u.city, MEASURE(o.revenue) AS r FROM orders o CROSS JOIN users u GROUP BY 1", "SELECT u.city, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1")
N("compile.native.join_field", "SELECT u.city, MEASURE(o.revenue) AS r FROM orders o JOIN users u ON o.__cubeJoinField = u.__cubeJoinField GROUP BY 1", "SELECT u.city, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1")


# field values and search for filter dropdowns, on the large table

@check("compile.values.big_table_status")
def _(ctx):
    ensure_table(ctx, BIGR)
    fid = ctx.mb.field(BIGR, "status")["id"]
    ctx.mb.api("POST", "/api/field/%d/rescan_values" % fid, {})
    vals = []
    for _i in range(20):
        vals = ctx.mb.api("GET", "/api/field/%d/values" % fid)["values"]
        if vals:
            break
        time.sleep(2)
    assert_rows(values_of(vals), ch("SELECT DISTINCT status FROM big_orders"))


@check("compile.values.search_lowcardinality")
def _(ctx):
    ensure_table(ctx, TE)
    fid = ctx.mb.field(TE, "lc")["id"]
    assert_rows(values_of(ctx.mb.api("GET", "/api/field/%d/search/%d?value=lph" % (fid, fid))), ch("SELECT DISTINCT lc FROM typed_events WHERE lc LIKE '%lph%'"))


@check("compile.values.remapped_nullable")
def _(ctx):
    ensure_table(ctx, TE)
    fid = ctx.mb.field(TE, "lc_n")["id"]
    ctx.mb.api("POST", "/api/field/%d/rescan_values" % fid, {})
    vals = []
    for _i in range(20):
        vals = ctx.mb.api("GET", "/api/field/%d/values" % fid)["values"]
        if vals:
            break
        time.sleep(2)
    got = [[v[0]] for v in vals if v[0] is not None]
    assert_rows(got, ch("SELECT DISTINCT lc_n FROM typed_events WHERE lc_n IS NOT NULL"))


# pivot tables


def pivot(ctx, inner, rows, cols):
    query = {"database": ctx.mb.need_db(), "type": "query", "query": inner, "pivot_rows": rows, "pivot_cols": cols}
    res = ctx.mb.api("POST", "/api/dataset/pivot", query)
    if res.get("error"):
        raise Fail("pivot: %s" % str(res["error"])[:500])
    return res["data"]


@check("compile.pivot.two_dimensions")
def _(ctx):
    inner = ctx.mb.inner(ORD, agg=["m:orders.revenue"], by=[STA, "d:orders.size"])
    data = pivot(ctx, inner, [0], [1])
    names = [c["name"] for c in data["cols"]]
    grouping = names.index("pivot-grouping")
    value = names.index("sum")
    by_group = {}
    for row in data["rows"]:
        by_group.setdefault(row[grouping], []).append(row)
    detail = [[r[0], r[1], r[value]] for r in by_group[0]]
    assert_rows(detail, ch("SELECT status, if(quantity >= 3, 'large', 'small'), sum(amount) FROM orders GROUP BY 1, 2"))
    total = [[r[value]] for r in by_group[3]]
    assert_rows(total, ch(REV))
    if len(by_group) < 3:
        raise Fail("the pivot has only groupings %s" % sorted(by_group))


@check("compile.pivot.date_and_string")
def _(ctx):
    inner = ctx.mb.inner(ORD, agg=[["count"]], by=["d:orders.created_at@quarter", STA])
    data = pivot(ctx, inner, [0], [1])
    names = [c["name"] for c in data["cols"]]
    grouping, value = names.index("pivot-grouping"), names.index("count")
    detail = [[r[0], r[1], r[value]] for r in data["rows"] if r[grouping] == 0]
    assert_rows(detail, ch("SELECT toStartOfQuarter(created_at), status, count() FROM orders GROUP BY 1, 2"))
    assert_rows([[r[value]] for r in data["rows"] if r[grouping] == 3], ch(CNT_O))


# ClickHouse types through Cube

@check("compile.ch.uint64_above_double")
def _(ctx):
    """The Cube SQL API reads a number as a Double. The driver stops a number above 2^53, and a text dimension gives the full digits."""
    ensure_table(ctx, TE)
    truth = ch("SELECT toString(ui64) FROM typed_events WHERE id = 2")
    if int(truth[0][0]) < 2 ** 53:
        raise Fail("the seed has no number above 2^53 here: %s" % truth)
    expect_clear_error(ctx, ctx.mb.inner(TE, fields=["d:typed_events.ui64"], where=[["=", "d:typed_events.id", 2]]), "2^53")
    expect_clear_error(ctx, ctx.mb.inner(TE, agg=["m:typed_events.sum_ui64"]), "2^53")
    expect_native_refusal(ctx, "SELECT ui64 FROM typed_events WHERE id = 2", "2^53")
    assert_rows(ctx.mb.mbql(TE, fields=["d:typed_events.ui64_text"], where=[["=", "d:typed_events.id", 2]])["rows"], truth, label="text dimension")
    # A smaller value of the same column has all its digits.
    assert_rows(ctx.mb.mbql(TE, fields=["d:typed_events.id"], where=[["=", "d:typed_events.id", 2]])["rows"], [[2]], label="other column")


@check("compile.ch.decimal_above_double")
def _(ctx):
    """A limit: a long decimal below 2^53 fits the range of a Double, so the driver cannot see that digits are lost. A text dimension keeps them."""
    ensure_table(ctx, TE)
    truth = ch("SELECT toString(dec_big) FROM typed_events WHERE id = 3")
    assert_rows(ctx.mb.mbql(TE, fields=["d:typed_events.dec_big_text"], where=[["=", "d:typed_events.id", 3]])["rows"], truth, label="text dimension")
    got = ctx.mb.mbql(TE, fields=["d:typed_events.dec_big"], where=[["=", "d:typed_events.id", 3]])["rows"][0][0]
    if str(got) == truth[0][0]:
        raise Fail("the number column now has all digits. Give this entry its full status")


C("compile.ch.decimal_sum", TE, "SELECT sum(dec_amount) FROM typed_events", kind=("regular", "post"), agg=["m:typed_events.sum_dec"])
C("compile.ch.decimal_row", TE, "SELECT dec_amount FROM typed_events WHERE id = 7", fields=["d:typed_events.dec_amount"], where=[["=", "d:typed_events.id", 7]])
C("compile.ch.float32", TE, "SELECT f32 FROM typed_events WHERE id = 7", fields=["d:typed_events.f32"], where=[["=", "d:typed_events.id", 7]])
C("compile.ch.nullable_int_avg", TE, "SELECT avg(n_int) FROM typed_events", agg=["m:typed_events.avg_n_int"])
C("compile.ch.nullable_decimal_sum", TE, "SELECT sum(n_dec) FROM typed_events", agg=["m:typed_events.sum_n_dec"])
C("compile.ch.nullable_string_group", TE, "SELECT n_str, count() FROM typed_events GROUP BY 1", kind="regular", agg=[["count"]], by=["d:typed_events.n_str"])
C("compile.ch.nullable_string_distinct", TE, "SELECT uniqExact(n_str) FROM typed_events", agg=["m:typed_events.distinct_n_str"])
C("compile.ch.nullable_bool_group", TE, "SELECT n_bool, count() FROM typed_events GROUP BY 1", agg=[["count"]], by=["d:typed_events.n_bool"])
C("compile.ch.nullable_time_month", TE, "SELECT if(n_ts IS NULL, NULL, toStartOfMonth(assumeNotNull(n_ts))), count() FROM typed_events GROUP BY 1", agg=[["count"]], by=["d:typed_events.n_ts@month"])
C("compile.ch.lowcardinality_group", TE, "SELECT lc, sum(dec_amount) FROM typed_events GROUP BY 1", kind="regular", agg=["m:typed_events.sum_dec"], by=["d:typed_events.lc"])
C("compile.ch.lowcardinality_nullable_group", TE, "SELECT lc_n, count() FROM typed_events GROUP BY 1", agg=[["count"]], by=["d:typed_events.lc_n"])
C("compile.ch.lowcardinality_distinct", TE, "SELECT uniqExact(lc) FROM typed_events", agg=["m:typed_events.distinct_lc"])
C("compile.ch.bool_group", TE, "SELECT flag, count() FROM typed_events GROUP BY 1", kind="regular", agg=[["count"]], by=["d:typed_events.flag"])
C("compile.ch.enum_group", TE, "SELECT toString(en), count() FROM typed_events GROUP BY 1", agg=[["count"]], by=["d:typed_events.en"])
C("compile.ch.uuid_filter", TE, "SELECT count() FROM typed_events WHERE uid = toUUID('00000000-0000-0000-0000-000000000007')", agg=[["count"]], where=[["=", "d:typed_events.uid", "00000000-0000-0000-0000-000000000007"]])
C("compile.ch.array_as_text", TE, "SELECT arrayStringConcat(tags, ',') FROM typed_events WHERE id = 8", fields=["d:typed_events.tags_text"], where=[["=", "d:typed_events.id", 8]])
C("compile.ch.array_length", TE, "SELECT length(nums), count() FROM typed_events GROUP BY 1", agg=[["count"]], by=["d:typed_events.nums_len"])
C("compile.ch.array_has", TE, "SELECT has(tags, 't1'), count() FROM typed_events GROUP BY 1", agg=[["count"]], by=["d:typed_events.has_t1"])
C("compile.ch.array_measure", TE, "SELECT sum(length(tags)) FROM typed_events", agg=["m:typed_events.tags_total"])
C("compile.ch.datetime_ny_day", TE, "SELECT toDate(ts_ny, 'UTC'), count() FROM typed_events GROUP BY 1", kind="regular", agg=[["count"]], by=["d:typed_events.ts_ny@day"])
C("compile.ch.datetime_ny_raw", TE, "SELECT toDateTime(ts_ny, 'UTC') FROM typed_events WHERE id = 300", fields=["d:typed_events.ts_ny"], where=[["=", "d:typed_events.id", 300]])
C("compile.ch.datetime64_berlin_hour", TE, "SELECT toStartOfHour(ts64_berlin, 'UTC'), count() FROM typed_events GROUP BY 1 ORDER BY 1 LIMIT 3", ordered=True, kind="regular", agg=[["count"]],
  by=["d:typed_events.ts64_berlin@hour"], order=[["asc", "d:typed_events.ts64_berlin@hour"]], limit=3)
C("compile.ch.date_month", TE, "SELECT toStartOfMonth(day), count() FROM typed_events GROUP BY 1", kind="regular", agg=[["count"]], by=["d:typed_events.day@month"])
C("compile.ch.date32_week", TE, "SELECT toStartOfWeek(day32, 1), count() FROM typed_events GROUP BY 1", kind="regular", setting=MON, agg=[["count"]], by=["d:typed_events.day32@week"])
C("compile.ch.date_part_on_ny", TE, "SELECT toHour(ts_ny, 'UTC'), count() FROM typed_events GROUP BY 1", kind="pushdown", agg=[["count"]], by=["d:typed_events.ts_ny@hour-of-day"])


@check("clickhouse.datetime64_millis")
def _(ctx):
    """Stock Cube reads a time as whole seconds. The dialect keeps the milliseconds."""
    ensure_table(ctx, TE)
    truth = ch("SELECT toString(toTimeZone(ts64_berlin, 'UTC')) FROM typed_events WHERE id = 7")[0][0]
    if not truth.endswith(".007"):
        raise Fail("the seed has no milliseconds here: %s" % truth)
    got = ctx.mb.mbql(TE, fields=["d:typed_events.ts64_berlin"], where=[["=", "d:typed_events.id", 7]])["rows"]
    assert_rows(ctx.mb.mbql(TE, agg=["m:typed_events.count"], by=["d:typed_events.ts64_berlin@hour"], where=[["=", "d:typed_events.id", 7]])["rows"],
                ch("SELECT toStartOfHour(ts64_berlin, 'UTC'), count() FROM typed_events WHERE id = 7 GROUP BY 1"), label="hour bucket")
    if DIALECT:
        assert_rows(got, [[truth + "Z"]], label="milliseconds")
        filtered = ctx.mb.native("SELECT MEASURE(count) AS n FROM typed_events WHERE ts64_berlin >= '%s' AND ts64_berlin < '%s8'" % (truth, truth[:-1]))["rows"]
        assert_rows(filtered, [[1]], label="filter on milliseconds")
        return
    if norm(got[0][0]) == norm(truth + "Z"):
        raise Fail("Cube now keeps milliseconds. Give this entry its full status")
    assert_rows(got, [[truth[:-4] + "Z"]], label="whole seconds")


@check("granularity.custom.offset")
def _(ctx):
    """Cube Store puts a time before the origin in the wrong bin. A rollup serves `revenue`, so the driver stops this. A measure that it does not serve is right."""
    expect_clear_error(ctx, ctx.mb.inner(ORD, agg=["m:orders.revenue"], by=["d:orders.created_fiscal_quarter"]), "wrong bin")
    expect_native_refusal(ctx, "SELECT DATE_TRUNC('fiscal_quarter', created_at), MEASURE(revenue) FROM orders GROUP BY 1", "wrong bin")
    fiscal = "addMonths(toStartOfQuarter(addMonths(created_at, -1)), 1)"
    expect_native_rows(ctx, "SELECT DATE_TRUNC('fiscal_quarter', created_at), MEASURE(unique_customers) FROM orders GROUP BY 1",
                       "SELECT %s AS q, uniqExact(user_id) FROM orders GROUP BY q" % fiscal)
    # The origin of this one is before all rows, so a rollup gives the truth.
    if ch("SELECT min(created_at) >= toDateTime('2024-04-01 00:00:00', 'UTC') FROM orders") != [[1]]:
        raise Fail("the seed has rows before the origin of fiscal_year_april")
    expect_native_rows(ctx, "SELECT DATE_TRUNC('fiscal_year_april', created_at), MEASURE(revenue) FROM orders GROUP BY 1",
                       "SELECT addMonths(toStartOfYear(addMonths(created_at, -3)), 3) AS y, sum(amount) FROM orders GROUP BY y")


@check("compile.ch.integer_within_double")
def _(ctx):
    ensure_table(ctx, TE)
    got = ctx.mb.mbql(TE, fields=["d:typed_events.id"], order=[["desc", "d:typed_events.id"]], limit=1)["rows"][0][0]
    assert_rows([[got]], ch("SELECT max(id) FROM typed_events"))


@check("compile.ch.approx_distinct_low_cardinality")
def _(ctx):
    ensure_table(ctx, "big_orders")
    assert_approx(ctx, "approx_users", "user_id")


@check("compile.ch.approx_distinct_high_cardinality")
def _(ctx):
    ensure_table(ctx, "big_orders")
    assert_approx(ctx, "approx_ids", "id")


APPROX_TOLERANCE = 0.02


def assert_approx(ctx, measure, column):
    """Cube sends uniq() for count_distinct_approx on ClickHouse. It must be equal to uniq(), and uniq() must be within
    the tolerance of uniqExact(). The tolerance comes from measured values on this table, see docs/query-compilation.md."""
    got = float(ctx.mb.mbql("big_orders", agg=["m:big_orders.%s" % measure])["rows"][0][0])
    truth = float(ch("SELECT uniqExact(%s) FROM big_orders" % column)[0][0])
    uniq = float(ch("SELECT uniq(%s) FROM big_orders" % column)[0][0])
    error = abs(got - truth) / truth
    print("         %s: cube %d, uniq %d, uniqExact %d, error %.4f%%" % (measure, got, uniq, truth, error * 100))
    if got != uniq:
        raise Fail("Cube gave %d, uniq() gives %d" % (got, uniq))
    if error > APPROX_TOLERANCE:
        raise Fail("error %.4f is above %.4f" % (error, APPROX_TOLERANCE))


# rolling windows and time zones

# A window that ends on each day. The bounds of the filter are boundaries of a day.
C("compile.rolling.by_day_on_boundaries", ORD,
  "SELECT m, sumIf(o.amount, o.created_at >= addDays(e, -30) AND o.created_at <= e) FROM orders o CROSS JOIN "
  "(SELECT toDateTime(x, 'UTC') AS m, addSeconds(toDateTime(x, 'UTC'), 86399) AS e FROM (SELECT arrayJoin(['2025-03-01','2025-03-02']) AS x)) b GROUP BY m ORDER BY m",
  ordered=True, agg=["m:orders.revenue_trailing_30d"], by=["d:orders.created_at@day"],
  where=[["between", CRT, "2025-03-01", "2025-03-02"]], order=[["asc", "d:orders.created_at@day"]])


@check("compile.rolling.no_time_bucket_gives_error")
def _(ctx):
    for agg, by in ((["m:orders.revenue_trailing_30d"], None), (["m:orders.revenue_mtd"], [STA]), (["m:orders.cumulative_count"], None)):
        expect_clear_error(ctx, ctx.mb.inner(ORD, agg=agg, by=by), "Group by a time bucket")
    expect_clear_error(ctx, ctx.mb.inner(ORD, agg=["m:orders.revenue_mtd"], where=RANGE_WHERE), "Group by a time bucket")


@check("compile.rolling.filter_inside_bucket_gives_error")
def _(ctx):
    inside = [["between", CRT, "2025-02-15", "2025-03-10"]]
    for where in (inside, [[">=", CRT, "2025-02-15"]], [["<", CRT, "2025-03-11"]], [["time-interval", CRT, -PAST_DAYS, "day"]]):
        expect_clear_error(ctx, ctx.mb.inner(ORD, agg=["m:orders.revenue_mtd"], by=MONTH, where=where), "Use a filter on whole months")
    # A plain measure with the same filter has no such limit.
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.revenue"], by=MONTH, where=inside)["rows"],
                ch("SELECT toStartOfMonth(created_at), sum(amount) FROM orders WHERE toDate(created_at) BETWEEN '2025-02-15' AND '2025-03-10' GROUP BY 1"))


@check("compile.rolling.no_date_filter_gives_clear_error")
def _(ctx):
    expect_clear_error(ctx, ctx.mb.inner(ORD, agg=["m:orders.revenue_trailing_30d"], by=MONTH), "Add a date filter")


@check("measure.multi_stage.time_shift_no_bucket_limit")
def _(ctx):
    """With no time bucket and no date filter, Cube gives the total of all rows for a time shift, so the driver stops this."""
    query = lambda m: ctx.mb.inner(ORD, agg=[m])
    for measure in ("m:orders.revenue_prior_month", "m:orders.revenue_next_month"):
        expect_clear_error(ctx, query(measure), "has a time shift")
    expect_clear_error(ctx, ctx.mb.inner("calendar_sales", agg=["m:calendar_sales.revenue_prior_fiscal"]), "has a time shift")
    expect_native_refusal(ctx, "SELECT MEASURE(revenue_prior_month) FROM orders", "has a time shift")
    expect_native_refusal(ctx, "SELECT status, MEASURE(revenue_prior_month) FROM orders GROUP BY 1", "has a time shift")
    # The same measure with a bucket or a date filter is right, and so is a measure that reads another one with no shift.
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.revenue_prior_month"], where=[["between", CRT, "2025-03-01", "2025-03-31"]])["rows"],
                ch("SELECT sum(amount) FROM orders WHERE created_at >= '2025-02-01' AND created_at < '2025-03-01'"), tol=0.01, label="date filter")
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.revenue_all_statuses"])["rows"], ch(REV), label="no shift")


C("compile.rolling.with_status_filter", ORD, monthly("sumIf(o.amount, o.created_at >= addDays(e, -30) AND o.created_at <= e AND o.status = 'shipped')"), ordered=True,
  agg=["m:orders.revenue_trailing_30d"], by=MONTH, where=RANGE_WHERE + [["=", STA, "shipped"]], order=[["asc", MONTH[0]]])
C("compile.rolling.by_week_sunday", ORD, "SELECT w, sumIf(o.amount, o.created_at >= addDays(e, -30) AND o.created_at <= e) FROM (SELECT toDateTime(toStartOfWeek(toDate('2025-01-15') + number * 7, 0), 'UTC') w, w + INTERVAL 7 DAY - INTERVAL 1 SECOND e FROM numbers(4)) d CROSS JOIN orders o GROUP BY w, e ORDER BY w",
  ordered=True, setting=SUN, agg=["m:orders.revenue_trailing_30d"], by=[WK], where=[["between", CRT, "2025-01-12", "2025-02-08"]], order=[["asc", WK]])


TOKYO = "Asia/Tokyo"


def with_report_timezone(ctx, zone, fn):
    prev = ctx.mb.api("GET", "/api/setting/report-timezone")
    ctx.mb.api("PUT", "/api/setting/report-timezone", {"value": zone})
    try:
        fn()
    finally:
        ctx.mb.api("PUT", "/api/setting/report-timezone", {"value": prev if isinstance(prev, str) and prev else None})


def tokyo_bucket(fn):
    """The bucket in Tokyo time, as an instant. Metabase returns it with the offset of the zone."""
    return "toTimeZone(toDateTime(%s(toTimeZone(created_at, '%s')), '%s'), 'UTC')" % (fn, TOKYO, TOKYO)


@check("mb.report_timezone")
def _(ctx):
    """The driver sets the time zone of the SQL session. Cube makes each bucket in that zone and reads a time literal in it."""
    grid = Grid(ctx)

    def body():
        for unit, fn in (("day", "toStartOfDay"), ("month", "toStartOfMonth"), ("year", "toStartOfYear")):
            grid.add(unit, ctx.mb.inner(ORD, agg=["m:orders.revenue"], by=["d:orders.created_at@%s" % unit]),
                     "SELECT %s AS b, sum(amount) FROM orders GROUP BY b" % tokyo_bucket(fn))
        local = "toDateTime('2025-05-10 00:00:00', '%s')" % TOKYO
        upper = "toDateTime('2025-05-20 00:00:00', '%s')" % TOKYO
        grid.add("date filter", ctx.mb.inner(ORD, agg=["m:orders.count"], where=[[">=", CRT, "2025-05-10"], ["<", CRT, "2025-05-20"]]),
                 "SELECT count() FROM orders WHERE created_at >= %s AND created_at < %s" % (local, upper))
        grid.add("strict bound", ctx.mb.inner(ORD, agg=["m:orders.count"], where=[[">", CRT, "2025-05-10T00:00:00"], ["<=", CRT, "2025-05-20T00:00:00"]]),
                 "SELECT count() FROM orders WHERE created_at > %s AND created_at <= %s" % (local, upper))
        grid.add("hour of day", ctx.mb.inner(ORD, agg=["m:orders.count"], by=["d:orders.created_at@hour-of-day"]),
                 "SELECT toHour(toTimeZone(created_at, '%s')) AS h, count() FROM orders GROUP BY h" % TOKYO)
        grid.add("by a model", over(raw_model(ctx), aggregation=[["count"]], breakout=[named("created_at", "type/DateTime", **{"temporal-unit": "month"})]),
                 "SELECT %s AS b, count() FROM orders GROUP BY b" % tokyo_bucket("toStartOfMonth"))
    with_report_timezone(ctx, TOKYO, body)
    # A connection from the pool goes back to UTC for the next query.
    grid.add("back to UTC", ctx.mb.inner(ORD, agg=["m:orders.revenue"], by=["d:orders.created_at@day"]),
             "SELECT toStartOfDay(created_at), sum(amount) FROM orders GROUP BY 1")
    grid.done()


@check("compile.tz.set_time_zone_error")
def _(ctx):
    # The setting belongs to one session. This connection has it, and the next one stays in UTC.
    try:
        rows = cube_sql("SET TIME ZONE 'Asia/Tokyo'")
    except PgError as e:
        rows = str(e)
    plain = cube_sql("SELECT DATE_TRUNC('year', created_at) AS y, MEASURE(count) AS n FROM orders GROUP BY 1 ORDER BY 1")
    assert_rows([[r[0][:10], float(r[1])] for r in plain], ch("SELECT toString(toStartOfYear(created_at)), count() FROM orders GROUP BY 1 ORDER BY 1"), ordered=True)
    if rows and "rror" not in str(rows):
        raise Fail("SET TIME ZONE returned %s" % rows)


C("compile.tz.filter_is_utc", TE, "SELECT count() FROM typed_events WHERE ts_ny >= toDateTime('2025-03-09 07:00:00', 'UTC') AND ts_ny < toDateTime('2025-03-09 08:00:00', 'UTC')", agg=[["count"]],
  where=[["between", "d:typed_events.ts_ny", "2025-03-09T07:00:00", "2025-03-09T07:59:59"]])
C("compile.tz.dst_day_in_new_york_column", TE, "SELECT toStartOfDay(ts_ny, 'UTC'), count() FROM typed_events WHERE toDate(ts_ny, 'UTC') BETWEEN '2025-03-08' AND '2025-03-10' GROUP BY 1", agg=[["count"]],
  by=["d:typed_events.ts_ny@day"], where=[["between", "d:typed_events.ts_ny@day", "2025-03-08", "2025-03-10"]])


# X-rays run the queries of the generated dashboard


# The ClickHouse expression of each column that an X-ray card reads, from the demo model. {t} is the alias of the table. A proxy of
# a granularity has the formula of that granularity.
XRAY_COLUMNS = {
    "orders": dict(
        {name: "{t}." + name for name in ("id", "user_id", "product_id", "status", "created_at")},
        status_sorted="{t}.status", status_meta="{t}.status", status_linked="{t}.status", masked_status="{t}.status",
        cancel_status="nullIf({t}.status, 'completed')",
        size="if({t}.quantity >= 3, 'large', 'small')",
        quantity_dim="{t}.quantity", amount_dim="{t}.amount", amount_eur="{t}.amount", amount_abbr="{t}.amount",
        amount_pct="{t}.amount / 1000", order_ref="{t}.id",
        is_paid="{t}.status IN ('completed', 'shipped')",
        created_label="{t}.created_at",
        created_month_proxy="toStartOfMonth({t}.created_at)",
        created_half_day="toStartOfInterval({t}.created_at, INTERVAL 12 HOUR)",
        created_week_sunday="toStartOfWeek({t}.created_at, 0)",
        created_fiscal_year="addMonths(toStartOfYear(addMonths({t}.created_at, -3)), 3)",
        created_fiscal_quarter="addMonths(toStartOfMonth({t}.created_at), -((toMonth({t}.created_at) + 1) % 3))"),
    "users": dict(
        {name: "{t}." + name for name in ("id", "name", "email", "city", "state", "is_vip", "created_at", "lifetime_orders")},
        location="concat(toString({t}.latitude), ',', toString({t}.longitude))",
        location__latitude="{t}.latitude", location__longitude="{t}.longitude"),
    "products": {name: "{t}." + name for name in ("id", "name", "category", "price")},
    "typed_events": dict(
        {name: "{t}." + name for name in ("id", "dec_amount", "dec_big", "ui64", "f32", "ts_utc", "ts_ny", "ts64_berlin", "day", "day32",
                                          "n_int", "n_str", "n_dec", "n_ts", "n_bool", "lc", "lc_n", "flag", "en")},
        ui64_text="toString({t}.ui64)", dec_big_text="toString({t}.dec_big)", uid="toString({t}.uid)",
        tags_text="arrayStringConcat({t}.tags, ',')", nums_len="length({t}.nums)", has_t1="has({t}.tags, 't1')"),
}
XRAY_JOINS = {
    "users": ("(SELECT u.*, c.n AS lifetime_orders FROM users AS u LEFT JOIN (SELECT user_id, count() AS n FROM orders GROUP BY user_id) AS c "
              "ON u.id = c.user_id)", "user_id"),
    "products": ("products", "product_id"),
}
XRAY_UNITS = {
    None: "{}",
    "default": "{}",
    "minute": "toStartOfMinute({})",
    "hour": "toStartOfHour({})",
    "day": "toDate({})",
    "week": "toStartOfWeek({}, 0)",
    "month": "toStartOfMonth({})",
    "quarter": "toStartOfQuarter({})",
    "year": "toStartOfYear({})",
    "day-of-week": "toDayOfWeek({}, 3)",
    "hour-of-day": "toHour({})",
    "day-of-month": "toDayOfMonth({})",
    "month-of-year": "toMonth({})",
    "quarter-of-year": "toQuarter({})",
}


class XrayTruth:
    """Writes the ClickHouse query of one X-ray card. A shape that it does not know is a failure, so no card goes with no compare.

    Metabase selects the width of a bin, so the truth reads this width from the result, and it counts the rows of each bin itself."""

    def __init__(self, ctx, table, cols):
        self.table = table
        self.cols = cols
        self.fields = {f["id"]: (t["name"], f) for t in ctx.mb.tables.values() for f in t["fields"]}
        self.joins = {}

    def column(self, ref):
        # MBQL 5 is ["field", options, id], and the older form is ["field", id, options].
        options, field_id = (ref[1], ref[2]) if isinstance(ref[1], dict) else (ref[2] or {}, ref[1])
        table, field = self.fields[field_id]
        alias = "base"
        if table != self.table:
            if table not in XRAY_JOINS:
                raise Fail("no truth for the column %s.%s of the card" % (table, field["name"]))
            source, key = XRAY_JOINS[table]
            alias = "joined_" + table
            self.joins[alias] = "LEFT JOIN %s AS %s ON base.%s = %s.id" % (source, alias, key, alias)
        expr = XRAY_COLUMNS.get(table, {}).get(field["name"])
        if expr is None:
            raise Fail("no truth for the column %s.%s of the card" % (table, field["name"]))
        return expr.format(t=alias), options, field

    def bucketed(self, ref, position=None):
        expr, options, field = self.column(ref)
        unit = options.get("temporal-unit")
        if options.get("binning"):
            info = self.cols[position].get("binning_info") if position is not None else None
            if not info or not info.get("bin_width"):
                raise Fail("the result has no width of the bin of %s" % field["name"])
            width = info["bin_width"]
            # A double cannot hold each value of this column, so the bin of a value comes from decimal arithmetic.
            if field["name"] == "dec_big":
                return "toFloat64(intDiv(%s, toDecimal128(%r, 8))) * %r" % (expr, width, width)
            return "floor(toFloat64(%s) / %r) * %r" % (expr, width, width)
        if unit not in XRAY_UNITS:
            raise Fail("no truth for the bucket of %s" % json.dumps(ref)[:200])
        if field.get("base_type") in ("type/DateTime", "type/Date", "type/DateTimeWithLocalTZ"):
            expr = "toDateTime(%s, 'UTC')" % expr
        return XRAY_UNITS[unit].format(expr)

    def aggregation(self, clause):
        kinds = {"distinct": "uniqExact(%s)", "sum": "sum(%s)", "avg": "avg(%s)", "min": "min(%s)", "max": "max(%s)"}
        args = [c for c in clause[1:] if isinstance(c, list)]
        if clause[0] == "count" and not args:
            return "count()"
        if clause[0] in kinds and args:
            return kinds[clause[0]] % self.bucketed(args[0])
        raise Fail("no truth for the aggregation %s" % json.dumps(clause)[:200])

    def condition(self, clause):
        args = [c for c in clause[1:] if not isinstance(c, dict)]
        if clause[0] == "is-null":
            return "%s IS NULL" % self.column(args[0])[0]
        if clause[0] == "not-null":
            return "%s IS NOT NULL" % self.column(args[0])[0]
        if clause[0] == "time-interval" and args[2] == "day" and isinstance(args[1], int) and args[1] < 0:
            day = "toDate(toDateTime(%s, 'UTC'))" % self.column(args[0])[0]
            return "%s >= today() - %d AND %s < today()" % (day, -args[1], day)
        raise Fail("no truth for the filter %s" % json.dumps(clause)[:200])

    def sql(self, query):
        stages = query.get("stages")
        if stages and len(stages) != 1:
            raise Fail("no truth for a card with %d stages" % len(stages))
        stage = stages[0] if stages else query["query"]
        if stage.get("limit") is not None:
            raise Fail("no truth for a card with a limit")
        filters = stage.get("filters") or ([stage["filter"]] if stage.get("filter") else [])
        keys = [self.bucketed(b, i) for i, b in enumerate(stage.get("breakout") or [])]
        aggregations = [self.aggregation(a) for a in stage.get("aggregation") or []]
        if not aggregations:
            raise Fail("no truth for a card with no aggregation: %s" % json.dumps(stage)[:200])
        where = [self.condition(f) for f in filters]
        return "SELECT %s FROM %s AS base %s %s %s SETTINGS join_use_nulls = 1" % (
            ", ".join(keys + aggregations), self.table, " ".join(self.joins.values()),
            "WHERE " + " AND ".join(where) if where else "",
            "GROUP BY " + ", ".join(str(i + 1) for i in range(len(keys))) if keys else "")


XRAY_TOTAL_REFUSAL = "Cube cannot add up the dimension"
XRAY_ABOVE_2_53 = re.compile(
    r"Cube read (\S+) in the column \S+ as a Double, so a number above 2\^53 has lost digits\. Add a dimension with SQL "
    r"toString\(column\) to the data model to read the full digits as text, or filter out these rows\.")


def xray_cards(ctx, path, table, above_2_53_stops=False):
    """Runs each card of the X-ray. A card must equal a query on ClickHouse, or stop with the documented message.
    A card that adds up a dimension of a joined cube stops on stock Cube. A patched Cube can answer it, so no count of stopped cards is fixed."""
    dash = ctx.mb.api("GET", "/api/automagic-dashboards/" + path)
    cards = [dc["card"] for dc in dash.get("dashcards", []) if dc.get("card") and dc["card"].get("dataset_query")]
    if not cards:
        raise Fail("the X-ray has no cards")
    failed, compared = [], 0
    for card in cards:
        query = card["dataset_query"]
        try:
            res = ctx.mb.api("POST", "/api/dataset", query, ok=(200, 202, 400, 500))
            if XRAY_TOTAL_REFUSAL in str(res.get("error") or ""):
                continue
            above = above_2_53_stops and XRAY_ABOVE_2_53.fullmatch(str(res.get("error") or ""))
            if above and abs(float(above.group(1))) >= 2 ** 53:
                continue
            if res.get("error") or res.get("status") == "failed":
                raise Fail("query failed: %s" % str(res.get("error") or res.get("error_type"))[:300])
            data = res["data"]
            assert_rows(data["rows"], ch(XrayTruth(ctx, table, data["cols"]).sql(query)), label="card")
            compared += 1
        except Fail as e:
            failed.append((card.get("name"), str(e)[:300]))
    if failed:
        raise Fail("%d of %d cards failed: %s" % (len(failed), len(cards), failed[:6]))
    if not compared:
        raise Fail("no card of %d gave a number to compare" % len(cards))
    return cards


@check("compile.xray.table_all_cards_run")
def _(ctx):
    xray_cards(ctx, "table/%d" % ctx.mb.table(ORD)["id"], ORD)


@check("compile.xray.field_all_cards_run")
def _(ctx):
    xray_cards(ctx, "field/%d" % ctx.mb.field(ORD, "status")["id"], ORD)


@check("compile.xray.time_field_all_cards_run")
def _(ctx):
    xray_cards(ctx, "field/%d" % ctx.mb.field(ORD, "created_at")["id"], ORD)


@check("compile.xray.typed_table_all_cards_run")
def _(ctx):
    ensure_table(ctx, TE)
    # A bin label above 2^53 cannot keep its digits, so the driver stops that card with the documented message.
    xray_cards(ctx, "table/%d" % ctx.mb.table(TE)["id"], TE, above_2_53_stops=True)


# which queries read a rollup


def request_of(text):
    _logical, requests = plan_of(text)
    if len(requests) != 1:
        raise Fail("the plan has %d Cube requests: %s" % (len(requests), text[:300]))
    return requests[0]


def load_used(query, needle):
    for _ in range(120):
        status, text, _h = http("POST", CUBE + "/cubejs-api/v1/load", {"query": query})
        res = json.loads(text)
        if res.get("error") == "Continue wait":
            time.sleep(2)
            continue
        if status != 200:
            raise Fail("Cube load: HTTP %s %s" % (status, text[:300]))
        used = json.dumps(res.get("usedPreAggregations") or {})
        if needle not in used:
            raise Fail("not served from %s: %s, request %s" % (needle, used[:200], json.dumps(query)[:300]))
        return
    raise Fail("Cube load: timeout")


def P(check_id, table, sql, needle, setting=None, tol=0.0, **kw):
    """The SQL that Metabase writes gives the Cube request that the rollup serves, and the number is the truth."""
    def run(ctx):
        ensure_table(ctx, table)
        restore = None
        if setting:
            restore = {k: ctx.mb.api("GET", "/api/setting/" + k) for k in setting}
            for k, v in setting.items():
                ctx.mb.api("PUT", "/api/setting/" + k, {"value": v})
        try:
            query = ctx.mb.inner(table, **kw)
            load_used(request_of(compile_query(ctx, query)), needle)
            assert_rows(ctx.mb.raw(query)["rows"], ch(sql), tol=tol)
        finally:
            for k, v in (restore or {}).items():
                ctx.mb.api("PUT", "/api/setting/" + k, {"value": v if isinstance(v, str) and v else None})
    check(check_id)(run)


BIG = "big_orders"
BCT = "d:big_orders.created_at"
P("compile.rollup.total", BIG, "SELECT sum(amount) FROM big_orders", "daily", agg=["m:big_orders.revenue"])
P("compile.rollup.count_rows", BIG, "SELECT count() FROM big_orders", "daily", agg=[["count"]])
P("compile.rollup.month", BIG, "SELECT toStartOfMonth(created_at), sum(amount) FROM big_orders GROUP BY 1", "daily", agg=["m:big_orders.revenue"], by=[BCT + "@month"])
P("compile.rollup.day_status", BIG, "SELECT toDate(created_at), status, count() FROM big_orders GROUP BY 1, 2", "daily", agg=[["count"]], by=[BCT + "@day", "d:big_orders.status"])
P("compile.rollup.week_monday", BIG, "SELECT toStartOfWeek(created_at, 1), sum(amount) FROM big_orders GROUP BY 1", "daily", setting=MON, agg=["m:big_orders.revenue"], by=[BCT + "@week"])
P("compile.rollup.week_sunday", BIG, "SELECT toStartOfWeek(created_at, 0), sum(amount) FROM big_orders GROUP BY 1", "daily", setting=SUN, agg=["m:big_orders.revenue"], by=[BCT + "@week"])
C("compile.rollup.avg_reads_the_table", BIG, "SELECT status, avg(amount) FROM big_orders GROUP BY 1", kind="regular", agg=["m:big_orders.avg_amount"], by=["d:big_orders.status"])
P("compile.rollup.filter_status_month", BIG, "SELECT toStartOfQuarter(created_at), sum(amount) FROM big_orders WHERE status = 'shipped' GROUP BY 1", "daily",
  agg=["m:big_orders.revenue"], by=[BCT + "@quarter"], where=[["=", "d:big_orders.status", "shipped"]])
P("compile.rollup.date_range_aligned", BIG, "SELECT sum(amount) FROM big_orders WHERE toDate(created_at) BETWEEN '2024-03-05' AND '2024-03-09'", "daily",
  agg=["m:big_orders.revenue"], where=[["between", BCT, "2024-03-05", "2024-03-09"]])


@check("compile.rollup.date_range_not_aligned_is_right")
def _(ctx):
    # A rollup of days cannot answer a range of hours. Cube must read the table, because the default of Cube would give a wrong number.
    ensure_table(ctx, BIG)
    query = ctx.mb.inner(BIG, agg=["m:big_orders.revenue"], where=[[">=", BCT, "2024-03-05T06:00:00"], ["<", BCT, "2024-03-05T07:00:00"]])
    assert_rows(ctx.mb.raw(query)["rows"], ch("SELECT sum(amount) FROM big_orders WHERE created_at >= toDateTime('2024-03-05 06:00:00', 'UTC') AND created_at < toDateTime('2024-03-05 07:00:00', 'UTC')"))
    status, text, _h = http("POST", CUBE + "/cubejs-api/v1/load", {"query": request_of(compile_query(ctx, query))})
    if "daily" in json.dumps(json.loads(text).get("usedPreAggregations") or {}):
        raise Fail("the rollup of days served a range of hours")


@check("compile.rollup.small_month_rollup_on_day_filter")
def _(ctx):
    # The first date filter that this suite found wrong: a month rollup and a range inside a month gave NULL.
    query = ctx.mb.inner(ORD, agg=["m:orders.revenue"], where=[["=", "d:orders.created_at@day", "2025-02-03"]])
    assert_rows(ctx.mb.raw(query)["rows"], ch("SELECT sum(amount) FROM orders WHERE toDate(created_at) = '2025-02-03'"))


@check("compile.latency.ten_million_rows")
def _(ctx):
    ensure_table(ctx, BIG)
    ensure_table(ctx, BIGR)
    total = ch("SELECT count() FROM big_orders")[0][0]
    if total < 10000000:
        raise Fail("the table has %d rows" % total)

    def run(table):
        def one(i):
            # Another date range each time, so that the result cache of Cube cannot answer.
            start = "2024-01-%02d" % (1 + i)
            end = "2025-12-%02d" % (28 - i)
            query = ctx.mb.inner(table, agg=["m:%s.revenue" % table], by=["d:%s.status" % table], where=[["between", "d:%s.created_at" % table, start, end]])
            started = time.time()
            rows = ctx.mb.raw(query)["rows"]
            times.append(time.time() - started)
            truth = ch("SELECT status, sum(amount) FROM big_orders WHERE toDate(created_at) BETWEEN '%s' AND '%s' GROUP BY 1" % (start, end))
            assert_rows(rows, truth)
        return one

    times = []
    for i in range(5):
        run(BIG)(i)
    with_rollup = sorted(times)[2]
    times = []
    for i in range(5):
        run(BIGR)(i)
    without = sorted(times)[2]
    print("         latency over 10000000 rows through Metabase, median of 5: with rollup %.3f s, without rollup %.3f s" % (with_rollup, without))

    def direct(table):
        times = []
        for i in range(5):
            started = time.time()
            cube_sql("SELECT status, DATE_TRUNC('day', created_at) AS d, MEASURE(revenue) AS r FROM %s WHERE created_at >= timestamp '2024-01-%02d 00:00:00' AND created_at < timestamp '2025-12-%02d 00:00:00' GROUP BY 1, 2" % (table, 1 + i, 28 - i))
            times.append(time.time() - started)
        return sorted(times)[2]

    print("         latency over 10000000 rows on the Cube SQL API, by day and status, median of 5: with rollup %.3f s, without rollup %.3f s" % (direct(BIG), direct(BIGR)))
    query = ctx.mb.inner(BIG, agg=["m:big_orders.revenue"], by=["d:big_orders.status"], where=[["between", BCT, "2024-01-01", "2024-08-28"]])
    load_used(request_of(compile_query(ctx, query)), "daily")


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


# ----- The agent path: each check uses MCP tools only to find ids and to run the query. See docs/agents.md.


def cube_database(ctx):
    for d in ctx.mcp.call("browse_data", action="list_databases")["data"]:
        if d["engine"] == "cube" and d["id"] == ctx.mb.need_db():
            return d
    raise Fail("list_databases does not list the Cube database")


def agent_table(ctx, name, *keywords):
    """Find a table with search, as an agent does, from the words of the question."""
    hits = ctx.mcp.call("search", term_queries=list(keywords), type=["table"], limit=50)["data"]
    found = [h for h in hits if h["name"] == name]
    if not found:
        raise Fail("search %s does not find table %s: %s" % (keywords, name, [h["name"] for h in hits][:10]))
    return get_fields_table(ctx, found[0]["id"]), found[0]


def by_title(items, title):
    found = [i for i in items if title in (i["name"], i.get("display_name"))]
    if not found:
        raise Fail("no %s in %s" % (title, [i.get("display_name", i["name"]) for i in items]))
    return found[0]


def agent_query(tid, **stage):
    return mcp_stage(tid, **stage)


REV_BY_CITY = "SELECT u.city, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY u.city"


@check("mcp.agent.list_tables_without_schema")
def _(ctx):
    res = ctx.mcp.call("browse_data", action="list_tables", database_id=cube_database(ctx)["id"], limit=100)["data"]
    names = {t["name"] for t in res}
    if not {"orders", "users", "sales"} <= names:
        raise Fail("list_tables with no schema: %s" % sorted(names))
    tid = [t["id"] for t in res if t["name"] == "users"][0]
    mid = by_title(get_fields_table(ctx, tid)["measures"], "Customer count")["id"]
    assert_rows(ctx.mcp.call("execute_query", query=agent_query(tid, aggregation=[["measure", {}, mid]]))["rows"], ch(CNT_U))


@check("mcp.agent.find_revenue_by_city")
def _(ctx):
    table, hit = agent_table(ctx, "sales", "revenue", "city")
    for word in ("Revenue", "Users City"):
        if word not in hit.get("description", ""):
            raise Fail("the search result does not list %s: %r" % (word, hit.get("description")))
    query = agent_query(table["id"], aggregation=[["measure", {}, by_title(table["measures"], "Revenue")["id"]]],
                        breakout=[["field", {}, by_title(table["fields"], "Users City")["id"]]])
    assert_rows(ctx.mcp.call("execute_query", query=query)["rows"], ch(REV_BY_CITY))


@check("mcp.agent.search_names_owner_of_measure")
def _(ctx):
    hits = ctx.mcp.call("search", term_queries=["Revenue"], type=["measure"], limit=50)["data"]
    for hit in hits:
        m = re.search(r"Table: .* \((\w+), id (\d+)\)\.", hit.get("description") or "")
        if not m:
            raise Fail("the search result of measure %s does not name its table: %r" % (hit["id"], hit.get("description")))
        if m.group(1) == "orders" and hit["name"] == "Revenue":
            query = agent_query(int(m.group(2)), aggregation=[["measure", {}, hit["id"]]])
            assert_rows(ctx.mcp.call("execute_query", query=query)["rows"], ch(REV))
            return
    raise Fail("search for Revenue does not find the measure of orders")


@check("mcp.agent.find_segment_owner")
def _(ctx):
    hits = ctx.mcp.call("search", term_queries=["Paid"], type=["segment"], limit=50)["data"]
    for hit in hits:
        m = re.search(r"Table: .* \((\w+), id (\d+)\)\.", hit.get("description") or "")
        if m and m.group(1) == "orders":
            query = agent_query(int(m.group(2)), aggregation=[["count", {}]], filters=[["segment", {}, hit["id"]]])
            assert_rows(ctx.mcp.call("execute_query", query=query)["rows"], ch("SELECT count() FROM orders WHERE %s" % PAID))
            return
    raise Fail("no segment of orders names its table: %s" % [h.get("description") for h in hits][:5])


@check("mcp.agent.table_description_lists_members")
def _(ctx):
    hits = ctx.mcp.call("browse_data", action="list_tables", database_id=ctx.mb.need_db(), search="orders", limit=100)["data"]
    text = [h for h in hits if h["name"] == "orders"][0]["description"]
    for want in ("Measures: ", "Revenue", "Dimensions: ", "Segments: ", "Joins: users"):
        if want not in text:
            raise Fail("the description of orders has no %r: %r" % (want, text[:400]))
    if "Internal Note" in text or "internal_note" in text:
        raise Fail("the description lists a private member")
    mcp_rows(ctx, ORD, REV, agg=["m:orders.revenue"])


@check("mcp.agent.database_guidance_works")
def _(ctx):
    """Each form in the description of the database must run and give the truth."""
    text = cube_database(ctx)["description"] or ""
    for want in ('["measure", {}, <measure id>]', '["segment", {}, <segment id>]', '["field", {}, <field id>]', "MEASURE(name)", "__cubeJoinField"):
        if want not in text:
            raise Fail("the guidance has no %s: %r" % (want, text[:300]))
    orders = get_fields_table(ctx, [t["id"] for t in ctx.mcp.call("browse_data", action="list_tables", database_id=ctx.mb.need_db(), search="orders", limit=100)["data"] if t["name"] == "orders"][0])
    users = get_fields_table(ctx, by_title(orders["related_tables"], "users")["id"])
    query = agent_query(orders["id"], aggregation=[["measure", {}, by_title(orders["measures"], "Revenue")["id"]]],
                        breakout=[["field", {}, by_title(users["fields"], "city")["id"]]])
    assert_rows(ctx.mcp.call("execute_query", query=query)["rows"], ch(REV_BY_CITY))
    sql = "SELECT b.city, MEASURE(a.revenue) FROM orders a CROSS JOIN users b WHERE a.__cubeJoinField = b.__cubeJoinField GROUP BY 1"
    assert_rows(ctx.mcp.call("execute_sql", database_id=ctx.mb.need_db(), sql=sql)["rows"], ch(REV_BY_CITY))


@check("mcp.agent.question_from_measure")
def _(ctx):
    table, _hit = agent_table(ctx, "sales", "revenue", "city")
    query = agent_query(table["id"], aggregation=[["measure", {}, by_title(table["measures"], "Revenue")["id"]]],
                        breakout=[["field", {}, by_title(table["fields"], "Users City")["id"]]])
    handle = ctx.mcp.call("execute_query", query=query)["query_handle"]
    res = ctx.mcp.call("question_write", method="create", name="conformance agent revenue by city", query_handle=handle, collection_id=ctx.mb.collection())
    assert_rows(ctx.mcp.call("run_saved_question", id=deep_find(res, "id"))["rows"], ch(REV_BY_CITY))


def agent_dashboard(ctx):
    table, _hit = agent_table(ctx, "sales", "revenue", "city")
    city = by_title(table["fields"], "Users City")["id"]
    query = agent_query(table["id"], aggregation=[["measure", {}, by_title(table["measures"], "Revenue")["id"]]])
    handle = ctx.mcp.call("execute_query", query=query)["query_handle"]
    card = deep_find(ctx.mcp.call("question_write", method="create", name="conformance agent revenue", query_handle=handle, collection_id=ctx.mb.collection()), "id")
    res = ctx.mcp.call("dashboard_write", method="create", name="conformance agent dash", collection_id=ctx.mb.collection(), ops=[
        {"op": "add_card", "id": -1, "card_id": card, "tab": None, "position": None, "size": None, "series": None, "inline_parameters": None},
        {"op": "add_parameter", "parameter_id": "city", "name": "City", "type": "string/=", "sectionId": "location"},
        {"op": "wire_parameter", "parameter_id": "city", "dashcard_id": -1, "target_field": city},
    ])
    return deep_find(res, "id")


@check("mcp.agent.dashboard_wired_filter")
def _(ctx):
    dash = ctx.mb.api("GET", "/api/dashboard/%d" % agent_dashboard(ctx))
    target = dash["dashcards"][0]["parameter_mappings"][0]["target"]
    rows = ctx.mb.run_dashcard(dash, 0, [{"id": "city", "type": "string/=", "value": ["Boston"], "target": target}])["rows"]
    assert_rows(rows, ch("SELECT sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id WHERE u.city = 'Boston'"))


@check("mcp.agent.filter_values")
def _(ctx):
    res = ctx.mcp.call("get_parameter_values", target="dashboard", id=agent_dashboard(ctx), parameter_id="city")
    assert_rows(parameter_values(res), ch("SELECT DISTINCT city FROM users"))


def agent_error(ctx, sql):
    res = ctx.mcp.rpc("tools/call", {"name": "execute_sql", "arguments": {"database_id": ctx.mb.need_db(), "sql": sql}})
    if not res.get("isError"):
        raise Fail("no error for %s" % sql)
    return (res.get("content") or [{}])[0].get("text", "")


@check("mcp.agent.error_unknown_column")
def _(ctx):
    text = agent_error(ctx, "SELECT city, MEASURE(revenue) FROM orders GROUP BY 1")
    for want in ("browse_data", "get_fields", "__cubeJoinField"):
        if want not in text:
            raise Fail("the error does not tell the next step (%s): %s" % (want, text[:300]))
    # The next step in the error must work.
    sql = "SELECT b.city, MEASURE(a.revenue) FROM orders a CROSS JOIN users b WHERE a.__cubeJoinField = b.__cubeJoinField GROUP BY 1"
    assert_rows(ctx.mcp.call("execute_sql", database_id=ctx.mb.need_db(), sql=sql)["rows"], ch(REV_BY_CITY))


@check("mcp.agent.error_join_on")
def _(ctx):
    text = agent_error(ctx, "SELECT u.city, MEASURE(o.revenue) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1")
    if "CROSS JOIN" not in text or "__cubeJoinField" not in text:
        raise Fail("the error does not show the join form of Cube: %s" % text[:300])


# ----- Per-person data. The second Cube in dev/cube-secure has a row-level rule. See docs/security.md.

SECURE_PASSWORD = "cube-secure-dev-only"
SECURE_API_SECRET = "cube-secure-dev-only-secret-0123456789"
SECURE_DETAILS = {
    "host": os.environ.get("CUBE_SECURE_SQL_HOST", "cube-secure"),
    "port": int(os.environ.get("CUBE_SECURE_SQL_PORT", "15432")),
    "dbname": "cube",
    "user": "metabase",
    "password": SECURE_PASSWORD,
    "cube-rest-url": os.environ.get("CUBE_SECURE_REST_INTERNAL_URL", "http://cube-secure:4000"),
    "cube-api-secret-value": SECURE_API_SECRET,
    "ssl": False,
    "cube-user-attribute": "email",
}
SECURE_DB = "Cube secure conformance"
ATTRIBUTE_DB = "Cube secure conformance attribute"
PERSON_PASSWORD = "Conformance-person-1"
CITY_OF = {"alice@example.com": "Austin", "bob@example.com": "Boston"}


def city_rev(city):
    return ch("SELECT u.city, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id WHERE u.city = '%s' GROUP BY u.city" % city)


def city_scalar(expr, city):
    return ch("SELECT %s FROM orders o JOIN users u ON o.user_id = u.id WHERE u.city = '%s'" % (expr, city))


class Secure:
    """Databases on the second Cube, and people. The suite removes the databases at the end."""

    def __init__(self, ctx):
        self.admin = ctx.mb
        self.people = {}
        self.dbs = {}
        self.error = None

    def create_db(self, name, details):
        old = [d for d in self.admin.api("GET", "/api/database")["data"] if d["name"] == name]
        for d in old:
            self.delete_db(d["id"])
        db = self.admin.api("POST", "/api/database", {"engine": "cube", "name": name, "details": details})
        deadline = time.time() + 240
        while self.admin.api("GET", "/api/database/%d" % db["id"]).get("initial_sync_status") != "complete":
            if time.time() > deadline:
                raise Fail("first sync of %s did not end" % name)
            time.sleep(2)
        while not [m for m in self.admin.api("GET", "/api/measure") if m["name"] == "Revenue" and m["table_id"] in
                   [t["id"] for t in self.admin.api("GET", "/api/database/%d/metadata" % db["id"])["tables"]]]:
            if time.time() > deadline:
                raise Fail("the measures of %s did not appear" % name)
            time.sleep(2)
        self.dbs[name] = db["id"]
        return db["id"]

    def ensure_db(self, name=SECURE_DB, details=None):
        if name not in self.dbs:
            self.create_db(name, details or SECURE_DETAILS)
        return self.dbs[name]

    def person(self, email, name=SECURE_DB, attributes=None):
        self.ensure_db(name)
        key = (email, name)
        if key not in self.people:
            users = self.admin.api("GET", "/api/user?query=%s" % urllib.parse.quote(email))["data"]
            if users:
                uid = users[0]["id"]
                self.admin.api("PUT", "/api/user/%d/password" % uid, {"password": PERSON_PASSWORD})
            else:
                uid = self.admin.api("POST", "/api/user", {"email": email, "first_name": email.split("@")[0], "last_name": "Conformance", "password": PERSON_PASSWORD})["id"]
            if attributes is not None:
                self.admin.api("PUT", "/api/user/%d" % uid, {"login_attributes": attributes})
            mb = Metabase(PERSON_PASSWORD, name)
            mb.sid = mb.api("POST", "/api/session", {"username": email, "password": PERSON_PASSWORD})["id"]
            mb.discover()
            if mb.db is None:
                raise Fail("%s does not see database %s: %s" % (email, name, mb.db_error))
            mb.collection_id = self.admin.collection()
            self.people[key] = mb
        return self.people[key]

    def admin_view(self, name=SECURE_DB):
        return self.person(ADMIN_EMAIL, name) if ADMIN_EMAIL != "admin@example.com" else self._admin(name)

    def _admin(self, name):
        self.ensure_db(name)
        mb = Metabase(self.admin.password, name)
        mb.sid = self.admin.sid
        mb.discover()
        mb.collection_id = self.admin.collection()
        return mb

    def delete_db(self, db_id):
        """Metabase v0.64.3: after DELETE of a database, its Measures stay in the search index with no table, and each
        search for a Measure gives HTTP 500. Archive them first, so that search skips them."""
        tables = {t["id"] for t in self.admin.api("GET", "/api/database/%d/metadata" % db_id)["tables"]}
        for path, items in (("/api/measure", self.admin.api("GET", "/api/measure")), ("/api/segment", self.admin.api("GET", "/api/segment"))):
            for item in items if isinstance(items, list) else items.get("data", []):
                if item["table_id"] in tables:
                    self.admin.api("PUT", "%s/%d" % (path, item["id"]), {"archived": True, "revision_message": "conformance cleanup"})
        time.sleep(8)  # The search index takes the archive from a queue.
        self.admin.api("DELETE", "/api/database/%d" % db_id)

    def cleanup(self):
        for db_id in self.dbs.values():
            try:
                self.delete_db(db_id)
            except Fail as e:
                print("warning: could not remove database %s: %s" % (db_id, e))
        self.dbs = {}


def secure(ctx):
    if not getattr(ctx, "secure", None):
        ctx.secure = Secure(ctx)
    return ctx.secure


def expect_failure(run, *fragments):
    """The call must fail, and no row may come back."""
    try:
        data = run()
    except Fail as e:
        text = getattr(e, "body", str(e))
    else:
        raise Fail("no error, the rows are %s" % str(data.get("rows") if isinstance(data, dict) else data)[:200])
    if fragments and not any(f in text for f in fragments):
        raise Fail("the error does not say why: %s" % text[:300])


@check("security.security_context_row_filter")
def _(ctx):
    sec = secure(ctx)
    for email, city in CITY_OF.items():
        rows = sec.person(email).mbql("sales", agg=["m:sales.revenue"], by=["d:sales.users_city"])["rows"]
        assert_rows(rows, city_rev(city), label=email)
    assert_rows(sec._admin(SECURE_DB).mbql("sales", agg=["m:sales.revenue"], by=["d:sales.users_city"])["rows"], ch(REV_BY_CITY), label="admin")


@check("security.row_filter.measure_with_no_dimension")
def _(ctx):
    for email, city in CITY_OF.items():
        rows = secure(ctx).person(email).mbql("orders", agg=["m:orders.count", "m:orders.revenue"])["rows"]
        assert_rows(rows, city_scalar("count(), sum(o.amount)", city), label=email)


@check("security.row_filter.native")
def _(ctx):
    for email, city in CITY_OF.items():
        rows = secure(ctx).person(email).native("SELECT status, MEASURE(count) AS n FROM orders GROUP BY 1")["rows"]
        assert_rows(rows, ch("SELECT o.status, count() FROM orders o JOIN users u ON o.user_id = u.id WHERE u.city = '%s' GROUP BY 1" % city), label=email)


@check("security.row_filter.push_down")
def _(ctx):
    """DATEDIFF goes to SQL push-down, so the rule must apply there too. Raw rows also."""
    sql = "SELECT DATEDIFF('year', created_at, DATE '2026-01-01') AS d, MEASURE(count) AS n FROM orders GROUP BY 1"
    truth = "SELECT dateDiff('year', o.created_at, toDate('2026-01-01')), count() FROM orders o JOIN users u ON o.user_id = u.id WHERE u.city = '%s' GROUP BY 1"
    for email, city in CITY_OF.items():
        assert_rows(secure(ctx).person(email).native(sql)["rows"], ch(truth % city), label=email)
    rows = secure(ctx).person("bob@example.com").native("SELECT SUM(amount_dim) FROM orders")["rows"]
    assert_rows(rows, city_scalar("sum(o.amount)", "Boston"), label="post-processing")


@check("security.row_filter.mcp")
def _(ctx):
    for email, city in CITY_OF.items():
        person = secure(ctx).person(email)
        mcp = Mcp(person)
        tid = person.table("sales")["id"]
        table = get_fields_table(Ctx(person, mcp), tid)
        query = mcp_stage(tid, aggregation=[["measure", {}, by_title(table["measures"], "Revenue")["id"]]],
                          breakout=[["field", {}, by_title(table["fields"], "Users City")["id"]]])
        assert_rows(mcp.call("execute_query", query=query)["rows"], city_rev(city), label=email)
        assert_rows(mcp.call("execute_sql", database_id=person.db, sql="SELECT users_city, MEASURE(revenue) FROM sales GROUP BY 1")["rows"], city_rev(city), label=email + " sql")


@check("security.row_filter.saved_question")
def _(ctx):
    sec = secure(ctx)
    card = sec._admin(SECURE_DB).mbql_card("per person", "sales", agg=["m:sales.revenue"], by=["d:sales.users_city"])
    for email, city in CITY_OF.items():
        assert_rows(sec.person(email).run_card(card["id"])["rows"], city_rev(city), label=email)


@check("security.row_filter.filter_values")
def _(ctx):
    """Metabase keeps one list of values per field, so with per-person data it must keep none."""
    sec = secure(ctx)
    admin = sec._admin(SECURE_DB)
    card = admin.mbql_card("values", "sales", agg=["m:sales.revenue"])
    field = admin.field("sales", "users_city")
    dash = admin.api("POST", "/api/dashboard", {"name": "conformance values", "collection_id": admin.collection()})
    admin.api("PUT", "/api/dashboard/%d" % dash["id"], {
        "parameters": [{"id": "city", "slug": "city", "name": "City", "type": "string/="}],
        "dashcards": [{"id": -1, "card_id": card["id"], "row": 0, "col": 0, "size_x": 6, "size_y": 4,
                       "parameter_mappings": [{"parameter_id": "city", "card_id": card["id"], "target": ["dimension", ["field", field["id"], None]]}]}]})
    stored = admin.api("GET", "/api/field/%d/values" % field["id"]).get("values")
    if stored:
        raise Fail("Metabase stores values of a per-person field: %s" % stored)
    for email, city in CITY_OF.items():
        person = sec.person(email)
        res = Mcp(person).call("get_parameter_values", target="dashboard", id=dash["id"], parameter_id="city", query="o")
        values = sorted(v[0] if isinstance(v, list) else v for v in (res.get("values") or res.get("data") or []))
        want = sorted(r[0] for r in ch("SELECT DISTINCT city FROM users WHERE city = '%s' AND city LIKE '%%o%%'" % city))
        if values != want:
            raise Fail("%s sees filter values %s, want %s" % (email, values, want))


@check("security.row_filter.no_fingerprint_of_all_rows")
def _(ctx):
    sec = secure(ctx)
    admin = sec._admin(SECURE_DB)
    field = admin.field("orders", "amount_dim")
    fp = admin.api("GET", "/api/field/%d" % field["id"]).get("fingerprint") or {}
    number = ((fp.get("type") or {}).get("type/Number") or {})
    if number.get("max") is not None or number.get("avg") is not None:
        raise Fail("the fingerprint has values of all rows: %s" % fp)
    assert_rows(sec.person("alice@example.com").mbql("orders", agg=["m:orders.count"])["rows"], city_scalar("count()", "Austin"))


@check("security.no_switch.native_user_filter")
def _(ctx):
    person = secure(ctx).person("alice@example.com")
    for target in ("admin@example.com", "metabase", "bob@example.com"):
        sql = "SELECT users_city, MEASURE(revenue) FROM sales WHERE __user = '%s' GROUP BY 1" % target
        expect_failure(lambda: person.native(sql), "another person")
    assert_rows(person.native("SELECT users_city, MEASURE(revenue) FROM sales GROUP BY 1")["rows"], city_rev("Austin"))


@check("security.no_switch.native_set_user")
def _(ctx):
    person = secure(ctx).person("alice@example.com")
    for sql in ("SET user = 'admin@example.com'", "SET ROLE 'admin@example.com'",
                "SET user = 'admin@example.com'; SELECT users_city, MEASURE(revenue) FROM sales GROUP BY 1"):
        try:
            rows = person.native(sql).get("rows") or []
        except Fail:
            rows = []
        if len(rows) > 1:
            raise Fail("%s gives rows of more than one city: %s" % (sql, rows))
    assert_rows(person.native("SELECT users_city, MEASURE(revenue) FROM sales GROUP BY 1")["rows"], city_rev("Austin"))


@check("security.no_mixing_of_people")
def _(ctx):
    """Requests of two people run in turns and at the same time, and each one gets the rows of that person."""
    import concurrent.futures
    sec = secure(ctx)
    people = [(e, sec.person(e)) for e in CITY_OF]

    def one(item):
        email, person = item
        return email, person.mbql("sales", agg=["m:sales.count"], by=["d:sales.users_city"])["rows"]
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        for email, rows in pool.map(one, people * 12):
            if [r[0] for r in rows] != [CITY_OF[email]]:
                raise Fail("%s got %s" % (email, rows))
    for email, city in CITY_OF.items():
        assert_rows(sec.person(email).mbql("sales", agg=["m:sales.revenue"], by=["d:sales.users_city"])["rows"], city_rev(city))


@check("security.unknown_person_fails")
def _(ctx):
    person = secure(ctx).person("carol@example.com")
    expect_failure(lambda: person.mbql("orders", agg=["m:orders.count"]), "checkSqlAuth", "refused")


@check("security.user_attribute")
def _(ctx):
    sec = secure(ctx)
    sec.ensure_db(ATTRIBUTE_DB, dict(SECURE_DETAILS, **{"cube-user-attribute": "cube_user"}))
    person = sec.person("dana@example.com", ATTRIBUTE_DB, attributes={"cube_user": "tenant-seattle"})
    assert_rows(person.mbql("sales", agg=["m:sales.revenue"], by=["d:sales.users_city"])["rows"], city_rev("Seattle"))
    other = sec.person("erin@example.com", ATTRIBUTE_DB)
    expect_failure(lambda: other.mbql("sales", agg=["m:sales.revenue"]), "no value for the attribute")


@check("security.public_link_fails")
def _(ctx):
    sec = secure(ctx)
    admin = sec._admin(SECURE_DB)
    card = admin.mbql_card("public", "sales", agg=["m:sales.revenue"], by=["d:sales.users_city"])
    before = admin.api("GET", "/api/setting/enable-public-sharing")
    admin.api("PUT", "/api/setting/enable-public-sharing", {"value": True})
    try:
        uuid = admin.api("POST", "/api/card/%d/public_link" % card["id"])["uuid"]
        status, text, _h = http("GET", MB + "/api/public/card/%s/query" % uuid)
        try:
            data = json.loads(text)
        except ValueError:
            data = {}
        rows = (data.get("data") or {}).get("rows") or []
        if rows:
            raise Fail("a public link shows rows with no person: %s" % rows)
        if status < 400 or data.get("error_type") != "missing-required-permissions":
            raise Fail("HTTP %s, the query did not fail on permissions: %s" % (status, text[:300]))
    finally:
        admin.api("PUT", "/api/setting/enable-public-sharing", {"value": before})


@check("security.compile_context")
def _(ctx):
    sec = secure(ctx)
    rows = sec.person("alice@example.com").native("SELECT email FROM users ORDER BY email LIMIT 1")["rows"]
    if rows != [["hidden"]]:
        raise Fail("alice reads %s" % rows)
    rows = sec._admin(SECURE_DB).native("SELECT email FROM users ORDER BY email LIMIT 1")["rows"]
    assert_rows(rows, ch("SELECT email FROM users ORDER BY email LIMIT 1"), label="admin")


@check("security.access_policy_groups")
def _(ctx):
    sec = secure(ctx)
    sql = "SELECT users_city, MEASURE(revenue) FROM analyst_sales GROUP BY 1"
    assert_rows(sec.person("alice@example.com").native(sql)["rows"], city_rev("Austin"), label="analyst")
    try:
        rows = sec.person("bob@example.com").native(sql)["rows"]
    except Fail:
        rows = []
    if rows:
        raise Fail("a person outside the group reads %s" % rows)
    if "analyst_sales" not in sec._admin(SECURE_DB).tables:
        raise Fail("sync does not find the view: the context of the connection user has no effect on the REST API")


def secret_values():
    return [SECURE_API_SECRET, SECURE_PASSWORD]


JWT = re.compile(r"eyJhbGciOi[\w-]+\.[\w-]+\.[\w-]+")


def signed_with(token, secret):
    head, payload, signature = token.split(".")
    want = base64.urlsafe_b64encode(hmac.new(secret.encode(), ("%s.%s" % (head, payload)).encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(want, signature)


def assert_no_secret(text, where):
    for value in secret_values():
        if value in text:
            raise Fail("%s shows a secret value" % where)


@check("security.secret.not_in_api")
def _(ctx):
    sec = secure(ctx)
    admin = sec._admin(SECURE_DB)
    db = sec.ensure_db()
    for path in ("/api/database/%d" % db, "/api/database", "/api/database/%d/metadata" % db, "/api/database?include=tables",
                 "/api/database/%d/fields" % db, "/api/setting", "/api/session/properties"):
        assert_no_secret(json.dumps(admin.api("GET", path)), "admin GET " + path)
    person = sec.person("alice@example.com")
    for path in ("/api/database/%d" % db, "/api/database", "/api/database/%d/metadata" % db):
        assert_no_secret(json.dumps(person.api("GET", path)), "person GET " + path)
    details = admin.api("GET", "/api/database/%d" % db).get("details") or {}
    if details.get("password") != "**MetabasePass**":
        raise Fail("the password in the API is %r" % str(details.get("password"))[:4])
    mcp = Mcp(admin)
    for tool, args in (("browse_data", {"action": "list_databases", "response_format": "detailed"}),
                       ("search", {"term_queries": ["secure"], "type": ["database"]})):
        res = mcp.rpc("tools/call", {"name": tool, "arguments": args})
        assert_no_secret(json.dumps(res), "MCP " + tool)
    assert_rows(person.mbql("orders", agg=["m:orders.count"])["rows"], city_scalar("count()", "Austin"))


@check("security.secret.rest_requires_it")
def _(ctx):
    """The driver signs its REST requests with the secret: a wrong secret must make the connection fail."""
    sec = secure(ctx)
    bad = dict(SECURE_DETAILS, **{"cube-api-secret-value": "wrong-secret-wrong-secret-wrong-secret"})
    status, text, _h = http("POST", MB + "/api/database", {"engine": "cube", "name": "conformance bad secret", "details": bad},
                            {"X-Metabase-Session": sec.admin.sid})
    if status < 400:
        created = json.loads(text)
        sec.admin.api("DELETE", "/api/database/%d" % created["id"])
        raise Fail("a wrong API secret connects")
    assert_no_secret(text, "the error for a wrong secret")
    if "wrong-secret-wrong-secret" in text:
        raise Fail("the error shows the secret that was sent")
    assert_rows(sec.person("bob@example.com").mbql("orders", agg=["m:orders.count"])["rows"], city_scalar("count()", "Boston"))


@check("security.secret.not_in_logs")
def _(ctx):
    sec = secure(ctx)
    bad = dict(SECURE_DETAILS, **{"password": "wrong-sql-password-for-log-check"})
    http("POST", MB + "/api/database", {"engine": "cube", "name": "conformance bad password", "details": bad}, {"X-Metabase-Session": sec.admin.sid})
    bad = dict(SECURE_DETAILS, **{"cube-rest-url": "http://cube-secure:1"})
    http("POST", MB + "/api/database", {"engine": "cube", "name": "conformance bad url", "details": bad}, {"X-Metabase-Session": sec.admin.sid})
    sec.person("carol@example.com")
    try:
        sec.person("carol@example.com").mbql("orders", agg=["m:orders.count"])
    except Fail:
        pass
    container = os.environ.get("METABASE_CONTAINER", "metabase-cube-driver-metabase-1")
    out = subprocess.run(["docker", "logs", container], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise Fail("docker logs %s: %s" % (container, out.stderr[:200]))
    log = out.stdout + out.stderr
    if "Cube" not in log and "cube" not in log:
        raise Fail("the log has no text about Cube, so this is the wrong container")
    assert_no_secret(log, "the Metabase log")
    if "wrong-sql-password-for-log-check" in log:
        raise Fail("the Metabase log shows a password that was sent")
    if JWT.search(log):
        raise Fail("the Metabase log shows a JWT")
    # Cube writes the token of a request that it refuses to its own log. See docs/security.md. Such a token must be useless.
    cube_container = os.environ.get("CUBE_SECURE_CONTAINER", "metabase-cube-driver-cube-secure-1")
    out = subprocess.run(["docker", "logs", cube_container], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise Fail("docker logs %s: %s" % (cube_container, out.stderr[:200]))
    cube_log = out.stdout + out.stderr
    assert_no_secret(cube_log, "the Cube log")
    for token in JWT.findall(cube_log):
        if signed_with(token, SECURE_API_SECRET):
            raise Fail("the Cube log shows a JWT with a valid signature")
    assert_rows(sec.person("alice@example.com").mbql("orders", agg=["m:orders.count"])["rows"], city_scalar("count()", "Austin"))


@check("security.metabase_cache")
def _(ctx):
    """Metabase makes the cache key from the query, with no person. A cached result must not reach another person."""
    sec = secure(ctx)
    admin = sec._admin(SECURE_DB)
    card = admin.mbql_card("cache", "sales", agg=["m:sales.revenue"], by=["d:sales.users_city"])
    plain = ctx.mb.mbql_card("cache control", "sales", agg=["m:sales.revenue"], by=["d:sales.users_city"])
    admin.api("PUT", "/api/cache", {"model": "root", "model_id": 0, "strategy": {"type": "ttl", "multiplier": 100000, "min_duration_ms": 0}})
    try:
        # The control: the same question on the Cube with no per-person data comes from the cache, so the cache is on.
        # Metabase writes the average time of a query in a batch. With no average, the time to live is 0, so the first hit can take a while.
        for _i in range(90):
            res = ctx.mb.api("POST", "/api/card/%d/query" % plain["id"], {"parameters": []})
            if res.get("cached"):
                break
            time.sleep(1)
        else:
            raise Fail("Metabase did not cache the control question, so this check shows nothing")
        assert_rows(res["data"]["rows"], ch(REV_BY_CITY), label="control from the cache")
        # The same question on per-person data, by two people in turn. Each run reads Cube, so no run has the rows of the person before it.
        for _i in range(4):
            for email in CITY_OF:
                res = sec.person(email).api("POST", "/api/card/%d/query" % card["id"], {"parameters": []})
                assert_rows(res["data"]["rows"], city_rev(CITY_OF[email]), label="%s, cached=%s" % (email, res.get("cached")))
                if res.get("cached"):
                    raise Fail("%s got a result from the cache on per-person data" % email)
            time.sleep(1)
        # The same through a dashboard, which has its own path to the query processor.
        dashboard = admin.api("POST", "/api/dashboard", {"name": "cache"})
        admin.api("PUT", "/api/dashboard/%d" % dashboard["id"], {"dashcards": [{"id": -1, "card_id": card["id"], "row": 0, "col": 0, "size_x": 4, "size_y": 4}]})
        dashcard = admin.api("GET", "/api/dashboard/%d" % dashboard["id"])["dashcards"][0]["id"]
        for _i in range(2):
            for email in CITY_OF:
                res = sec.person(email).api("POST", "/api/dashboard/%d/dashcard/%d/card/%d/query" % (dashboard["id"], dashcard, card["id"]), {"parameters": []})
                assert_rows(res["data"]["rows"], city_rev(CITY_OF[email]), label="dashboard, %s, cached=%s" % (email, res.get("cached")))
                if res.get("cached"):
                    raise Fail("%s got a result from the cache on a dashboard on per-person data" % email)
    finally:
        admin.api("DELETE", "/api/cache", {"model": "root", "model_id": [0]})


# ----------------------------------------------------------------- review 0.1.0
#
# The checks below come from docs/review/0.1.0.md. Each finding has a check, and the grids search for more shapes of the same
# defect: outer stages, models, columns by name, boundary values, and each date type. Each truth is direct SQL on ClickHouse.


def named(name, base_type="type/Decimal", **opts):
    """A column of an earlier stage by name, as a question on a model uses it."""
    return ["field", name, dict({"base-type": base_type}, **opts)]


def over(card_id, **stage):
    return dict({"source-table": "card__%d" % card_id}, **stage)


def card_of(ctx, name, query, kind="question"):
    return ctx.mb.card(name, {"database": ctx.mb.need_db(), "type": "query", "query": query}, kind)["id"]


RAW_COLUMNS = ["id", "status", "amount_dim", "created_at", "revenue", "count", "avg_order_value", "unique_customers",
               "paid_revenue", "min_amount", "max_amount", "revenue_trailing_30d", "revenue_mtd"]


def raw_model(ctx):
    """One model for all checks in a run, with dimensions and measures as columns."""
    if not getattr(ctx, "review_model", None):
        ctx.review_model = card_of(ctx, "raw model", ctx.mb.inner(ORD, fields=["d:orders.%s" % c for c in RAW_COLUMNS]), "model")
    return ctx.review_model


def outcome(ctx, query):
    """(rows, None) or (None, error text)."""
    res = ctx.mb.api("POST", "/api/dataset", {"database": ctx.mb.need_db(), "type": "query", "query": query}, ok=(200, 202, 400, 403, 500))
    error = res.get("error") or res.get("message")
    if error:
        return None, str(error)
    return res["data"]["rows"], None


def expect_rows(ctx, query, sql, label="", ordered=False, tol=0.0):
    rows, error = outcome(ctx, query)
    if error:
        raise Fail("%s: query failed: %s" % (label, error[:300]))
    assert_rows(rows, sql if isinstance(sql, list) else ch(sql), ordered=ordered, tol=tol, label=label or "rows")


def native_outcome(ctx, sql, tags=None, parameters=None):
    query = {"database": ctx.mb.need_db(), "type": "native", "native": {"query": sql, "template-tags": tags or {}}}
    if parameters:
        query["parameters"] = parameters
    res = ctx.mb.api("POST", "/api/dataset", query, ok=(200, 202, 400, 403, 500))
    error = res.get("error") or res.get("message")
    if error:
        return None, str(error)
    return res["data"]["rows"], None


def expect_native_refusal(ctx, sql, needle, tags=None, parameters=None):
    rows, error = native_outcome(ctx, sql, tags, parameters)
    if error is None:
        raise Fail("no refusal for %s: it gave %s" % (sql[:120], rows[:3]))
    if needle not in error:
        raise Fail("the error for %s has no %r: %s" % (sql[:120], needle, error[:300]))


def expect_native_rows(ctx, sql, truth, tags=None, parameters=None):
    rows, error = native_outcome(ctx, sql, tags, parameters)
    if error:
        raise Fail("%s: query failed: %s" % (sql[:120], error[:300]))
    assert_rows(rows, truth if isinstance(truth, list) else ch(truth), label=sql[:80])


class Grid:
    """Runs many queries and reports all differences together. A refusal counts only if the grid names its text."""

    def __init__(self, ctx, refusal=None):
        self.ctx = ctx
        self.refusal = refusal
        self.wrong = []
        self.count = 0
        self.refused = 0

    def add(self, label, query, truth, tol=1e-9):
        self.count += 1
        rows, error = outcome(self.ctx, query)
        self.compare(label, rows, error, truth, tol)

    def compare(self, label, rows, error, truth, tol=1e-9):
        if error:
            if self.refusal and self.refusal in error:
                self.refused += 1
                return
            self.wrong.append("%s: ERROR %s" % (label, error[:160].replace("\n", " ")))
            return
        try:
            assert_rows(rows, truth if isinstance(truth, list) else ch(truth), tol=tol, label=label)
        except Fail as e:
            self.wrong.append(str(e)[:260])

    def done(self):
        print("         %d queries, %d refused, %d differ" % (self.count, self.refused, len(self.wrong)))
        if self.wrong:
            raise Fail("%d of %d differ. %s" % (len(self.wrong), self.count, " | ".join(self.wrong[:6])))


BOUND_X = "2025-03-15 05:00:00"
BOUND_Y = "2025-03-16 05:00:00"
# The rows exist at these two times, so a bound that includes or excludes a row changes the number.


def bound_filters(ref, x=BOUND_X, y=BOUND_Y):
    """(label, MBQL filter, truth condition with {c} for the column). X and Y are times with a row."""
    ix, iy = x.replace(" ", "T"), y.replace(" ", "T")
    sx, sy = "toDateTime('%s','UTC')" % x, "toDateTime('%s','UTC')" % y
    return [
        (">", [">", ref, ix], "{c} > %s" % sx),
        (">=", [">=", ref, ix], "{c} >= %s" % sx),
        ("<", ["<", ref, iy], "{c} < %s" % sy),
        ("<=", ["<=", ref, iy], "{c} <= %s" % sy),
        ("> and <=", ["and", [">", ref, ix], ["<=", ref, iy]], "{c} > %s AND {c} <= %s" % (sx, sy)),
        ("> and <", ["and", [">", ref, ix], ["<", ref, iy]], "{c} > %s AND {c} < %s" % (sx, sy)),
        (">= and <=", ["and", [">=", ref, ix], ["<=", ref, iy]], "{c} >= %s AND {c} <= %s" % (sx, sy)),
        (">= and <", ["and", [">=", ref, ix], ["<", ref, iy]], "{c} >= %s AND {c} < %s" % (sx, sy)),
        ("<= and >", ["and", ["<=", ref, iy], [">", ref, ix]], "{c} <= %s AND {c} > %s" % (sy, sx)),
        ("between", ["between", ref, ix, iy], "{c} >= %s AND {c} <= %s" % (sx, sy)),
        ("=", ["=", ref, ix], "{c} = %s" % sx),
        ("not >", ["not", [">", ref, ix]], "NOT ({c} > %s)" % sx),
        ("not <=", ["not", ["<=", ref, ix]], "NOT ({c} <= %s)" % sx),
        ("or", ["or", ["<=", ref, ix], [">", ref, iy]], "{c} <= %s OR {c} > %s" % (sx, sy)),
        ("two lower", ["and", [">", ref, ix], [">", ref, iy]], "{c} > %s AND {c} > %s" % (sx, sy)),
    ]


MEASURE_TRUTH = [
    ("count", "count()"),
    ("revenue", "sum(amount)"),
    ("paid_revenue", "sumIf(amount, status IN ('completed','shipped'))"),
    ("unique_customers", "uniqExact(user_id)"),
    ("avg_order_value", "avg(amount)"),
    ("min_amount", "min(amount)"),
    ("max_amount", "max(amount)"),
]


@check("compile.bound.strict_lower_with_upper")
def _(ctx):
    """Finding B1, on a cube and in MCP."""
    where = [[">", CRT, "2025-03-15T05:00:00"], ["<=", CRT, "2025-03-16T05:00:00"]]
    truth = "SELECT count(), sum(amount) FROM orders WHERE created_at > '2025-03-15 05:00:00' AND created_at <= '2025-03-16 05:00:00'"
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.count", "m:orders.revenue"], where=where)["rows"], ch(truth))
    assert_rows(ctx.mcp.call("execute_query", query=mcp_query(ctx.mb, ORD, agg=["m:orders.count", "m:orders.revenue"], where=where))["rows"], ch(truth), label="MCP")


@check("compile.bound.grid_measures")
def _(ctx):
    """Each kind of bound with each kind of measure on a cube."""
    grid = Grid(ctx)
    for m, expr in MEASURE_TRUTH:
        for label, where, cond in bound_filters(CRT):
            grid.add("%s %s" % (m, label), ctx.mb.inner(ORD, agg=["m:orders.%s" % m], where=[where]),
                     "SELECT %s FROM orders WHERE %s" % (expr, cond.format(c="created_at")))
    grid.done()


@check("compile.bound.grid_date_types")
def _(ctx):
    """Each kind of bound on each date type. A nullable column has rows with no value. See clickhouse.datetime64_millis for DateTime64."""
    ensure_table(ctx, TE)
    grid = Grid(ctx)
    cases = [("ts_utc", "ts_utc", "2025-03-01 06:00:00", "2025-03-01 09:00:00"),
             ("ts_ny", "toDateTime(ts_ny, 'UTC')", "2025-03-01 06:00:00", "2025-03-01 09:00:00"),
             ("day", "toDateTime(day, 'UTC')", "2025-03-02 00:00:00", "2025-03-04 00:00:00"),
             ("day32", "toDateTime(day32, 'UTC')", "2025-03-02 00:00:00", "2025-03-04 00:00:00"),
             ("n_ts", "n_ts", "2025-03-08 00:00:00", "2025-03-10 00:00:00")]
    for column, truth_col, x, y in cases:
        for label, where, cond in bound_filters("d:typed_events.%s" % column, x, y):
            grid.add("%s %s" % (column, label), ctx.mb.inner(TE, agg=[["count"]], where=[where]),
                     "SELECT count() FROM typed_events WHERE %s" % cond.format(c=truth_col))
    grid.done()


@check("compile.bound.grid_boundary_values")
def _(ctx):
    """A bound on the last second of a day, of a month and of a year, and the first second after it."""
    grid = Grid(ctx)
    for x, y in (("2025-03-14 23:59:59", "2025-03-15 00:00:00"), ("2025-03-31 23:59:59", "2025-04-30 23:59:59"), ("2024-12-31 23:59:59", "2025-01-01 00:00:00")):
        for label, where, cond in bound_filters(CRT, x, y):
            grid.add("%s..%s %s" % (x, y, label), ctx.mb.inner(ORD, agg=["m:orders.count"], where=[where]),
                     "SELECT count() FROM orders WHERE %s" % cond.format(c="created_at"))
    grid.done()


@check("compile.bound.grid_view_and_join")
def _(ctx):
    """The same bounds on a view, on a column of a joined cube, and next to a breakout."""
    grid = Grid(ctx)
    for label, where, cond in bound_filters("d:sales.created_at"):
        grid.add("view " + label, ctx.mb.inner("sales", agg=["m:sales.count", "m:sales.revenue"], where=[where]),
                 "SELECT count(), sum(amount) FROM orders WHERE %s" % cond.format(c="created_at"))
    for label, where, cond in bound_filters("d:orders.created_at"):
        grid.add("by status " + label, ctx.mb.inner(ORD, agg=["m:orders.revenue"], by=[STA], where=[where]),
                 "SELECT status, sum(amount) FROM orders WHERE %s GROUP BY status" % cond.format(c="created_at"))
    x = ch("SELECT toString(created_at) FROM users ORDER BY created_at LIMIT 1 OFFSET 20")[0][0]
    y = ch("SELECT toString(created_at) FROM users ORDER BY created_at LIMIT 1 OFFSET 100")[0][0]
    for label, where, cond in bound_filters("d:orders>users.created_at", x, y):
        grid.add("joined " + label, ctx.mb.inner(ORD, agg=["m:orders.count"], where=[where]),
                 "SELECT count() FROM orders o JOIN users u ON o.user_id = u.id WHERE %s" % cond.format(c="u.created_at"))
    grid.done()


@check("compile.bound.grid_model_by_name")
def _(ctx):
    """The same bounds on a model, by the name of the column, as a question on a model writes them."""
    model = raw_model(ctx)
    grid = Grid(ctx)
    col = named("created_at", "type/DateTime")
    for label, where, cond in bound_filters(col):
        text = cond.format(c="created_at")
        grid.add("count " + label, over(model, aggregation=[["count"]], filter=where), "SELECT count() FROM orders WHERE %s" % text)
        grid.add("sum " + label, over(model, aggregation=[["sum", named("amount_dim")]], filter=where), "SELECT sum(amount) FROM orders WHERE %s" % text)
        grid.add("measure " + label, over(model, aggregation=[["sum", named("revenue")]], filter=where), "SELECT sum(amount) FROM orders WHERE %s" % text)
    grid.done()


@check("compile.bound.grid_mcp")
def _(ctx):
    """The same bounds in MCP, on a table and on a model with source-card."""
    tid = ctx.mb.table(ORD)["id"]
    model = raw_model(ctx)
    wrong = []
    created = ["field", {}, ctx.mb.field(ORD, "created_at")["id"]]
    measure = to_mbql5(ctx.mb.walk("m:orders.revenue"))
    named5 = ["field", {"base-type": "type/DateTime"}, "created_at"]
    for label, where, cond in bound_filters("d:orders.created_at"):
        truth = ch("SELECT sum(amount) FROM orders WHERE %s" % cond.format(c="created_at"))
        for shape, stage in (
                ("table", {"source-table": tid, "aggregation": [measure], "filters": [to_mbql5(ctx.mb.walk(where))]}),
                ("model", {"source-card": model, "aggregation": [["sum", {}, ["field", {"base-type": "type/Decimal"}, "amount_dim"]]],
                           "filters": [_with_column(to_mbql5(ctx.mb.walk(where)), created, named5)]})):
            try:
                rows = ctx.mcp.call("execute_query", query={"lib/type": "mbql/query", "database": ctx.mb.need_db(),
                                                            "stages": [dict({"lib/type": "mbql.stage/mbql"}, **stage)]})["rows"]
                assert_rows(rows, truth, label="%s %s" % (shape, label))
            except Fail as e:
                wrong.append(str(e)[:220])
    if wrong:
        raise Fail("%d differ. %s" % (len(wrong), " | ".join(wrong[:5])))


@check("mcp.query.between_keeps_time")
def _(ctx):
    """Metabase reads each bound of `between` in MCP execute_query as a whole day. The driver keeps the time, so the rows are the rows of the bounds."""
    x, y = "2025-03-15T05:00:00", "2025-03-16T05:00:00"
    got = ctx.mcp.call("execute_query", query=mcp_query(ctx.mb, ORD, agg=["m:orders.count"], where=[["between", CRT, x, y]]))["rows"]
    days = ch("SELECT count() FROM orders WHERE toDate(created_at) BETWEEN '2025-03-15' AND '2025-03-16'")
    literal = ch("SELECT count() FROM orders WHERE created_at BETWEEN '2025-03-15 05:00:00' AND '2025-03-16 05:00:00'")
    if days == literal:
        raise Fail("these bounds cannot tell a whole day from the time: %s" % days)
    assert_rows(got, literal, label="bounds with a time")
    # A bound with no time stays a whole day.
    assert_rows(ctx.mcp.call("execute_query", query=mcp_query(ctx.mb, ORD, agg=["m:orders.count"], where=[["between", CRT, "2025-03-15", "2025-03-16"]]))["rows"],
                days, label="bounds with no time")


def _with_column(clause, by_id, by_name):
    """`clause` with each reference to the column `by_id` replaced by `by_name`."""
    if isinstance(clause, list):
        if len(clause) == 3 and clause[0] == "field" and clause[2] == by_id[2]:
            return by_name
        return [_with_column(c, by_id, by_name) for c in clause]
    return clause


# The driver wraps Metabase vars, and it adds a topic to the Metabase events. These checks use Sample Database, on another driver.
# ClickHouse has no truth for it, so its truth is a REST query that does not go through the wrapped code.


def other_database(ctx):
    dbs = ctx.mb.api("GET", "/api/database")
    dbs = dbs.get("data", dbs) if isinstance(dbs, dict) else dbs
    for db in dbs:
        if db.get("is_sample") and db["engine"] != "cube":
            meta = ctx.mb.api("GET", "/api/database/%d/metadata" % db["id"])
            table = next(t for t in meta["tables"] if t["name"].lower() == "orders")
            field = next(f for f in table["fields"] if f["name"].lower() == "created_at")
            return db, meta, table["id"], field["id"]
    raise Fail("Metabase has no sample database on another driver, so this check shows nothing")


def metabase_minor():
    status, text, _ = http("GET", MB + "/api/session/properties")
    if status != 200:
        raise Fail("session properties: HTTP %s" % status)
    return int(json.loads(text)["version"]["tag"].split(".")[1])


@check("isolation.other_driver_mcp_between")
def _(ctx):
    """On another database, the MCP tool gives what Metabase itself gives: whole days on 0.64, the time of the bounds on 0.65."""
    db, _meta, table, field = other_database(ctx)
    def rest(ref, lo, hi):
        return ctx.mb.dataset({"database": db["id"], "type": "query", "query": {
            "source-table": table, "aggregation": [["count"]], "filter": ["between", ref, lo, hi]}})["data"]["rows"]

    latest = ctx.mb.dataset({"database": db["id"], "type": "query", "query": {
        "source-table": table, "aggregation": [["max", ["field", field, None]]]}})["data"]["rows"][0][0]
    last = dt.date.fromisoformat(str(latest)[:10])
    # The sample has some rows each day. These bounds must cut rows from the days at their ends.
    for back in range(1, 60):
        lo, hi = last - dt.timedelta(days=back + 1), last - dt.timedelta(days=back)
        x, y = lo.isoformat() + "T05:00:00", hi.isoformat() + "T05:00:00"
        days = rest(["field", field, {"temporal-unit": "day"}], lo.isoformat(), hi.isoformat())
        literal = rest(["field", field, None], x, y)
        if days != literal:
            break
    else:
        raise Fail("no two days of the sample can tell a whole day from the time")
    got = ctx.mcp.call("execute_query", query={"lib/type": "mbql/query", "database": db["id"], "stages": [{
        "lib/type": "mbql.stage/mbql", "source-table": table, "aggregation": [["count", {}]],
        "filters": [["between", {}, ["field", {}, field], x, y]]}]})["rows"]
    assert_rows(got, days if metabase_minor() < 65 else literal, label="MCP between on %s" % db["engine"])


@check("isolation.other_driver_cache")
def _(ctx):
    """The cache middleware has a wrap of the driver. A question on another database still comes from the cache."""
    db, _meta, table, _field = other_database(ctx)
    card = ctx.mb.card("isolation cache", {"database": db["id"], "type": "query", "query": {"source-table": table, "aggregation": [["count"]]}})
    truth = ctx.mb.dataset({"database": db["id"], "type": "query", "query": {"source-table": table, "aggregation": [["count"]]}})["data"]["rows"]
    ctx.mb.api("PUT", "/api/cache", {"model": "root", "model_id": 0, "strategy": {"type": "ttl", "multiplier": 100000, "min_duration_ms": 0}})
    try:
        # Metabase writes the average time of a query in a batch. With no average, the time to live is 0, so the first hit can take a while.
        for _i in range(90):
            res = ctx.mb.api("POST", "/api/card/%d/query" % card["id"], {"parameters": []})
            if res.get("cached"):
                break
            time.sleep(1)
        else:
            raise Fail("a question on %s did not come from the cache" % db["engine"])
        assert_rows(res["data"]["rows"], truth, label="from the cache")
    finally:
        ctx.mb.api("DELETE", "/api/cache", {"model": "root", "model_id": [0]})


def other_database_state(ctx, db_id):
    meta = ctx.mb.api("GET", "/api/database/%d/metadata" % db_id)
    fields = sorted((f["id"], f.get("display_name"), f.get("description"), f.get("semantic_type"), json.dumps(f.get("settings"), sort_keys=True))
                    for t in meta["tables"] for f in t["fields"])
    tables = {t["id"] for t in meta["tables"]}
    extras = []
    for kind in ("measure", "segment"):
        rows = ctx.mb.api("GET", "/api/%s" % kind)
        extras += [(kind, r["id"]) for r in (rows.get("data", rows) if isinstance(rows, dict) else rows) if r.get("table_id") in tables]
    return {"description": meta.get("description"), "fields": fields, "extras": sorted(extras)}


@check("isolation.other_driver_sync")
def _(ctx):
    """The driver adds a topic to the Metabase events. A sync of another database goes on, and the driver writes nothing to it."""
    db, _meta, _table, _field = other_database(ctx)
    container = os.environ.get("METABASE_CONTAINER", "metabase-cube-driver-metabase-1")
    finished = "FINISHED: Sync metadata for %s Database %d" % (db["engine"], db["id"])

    def log():
        return subprocess.run(["docker", "logs", container], capture_output=True, text=True, timeout=120).stdout

    before = other_database_state(ctx, db["id"])
    runs = log().count(finished)
    ctx.mb.api("POST", "/api/database/%d/sync_schema" % db["id"])
    for _i in range(60):
        if log().count(finished) > runs:
            break
        time.sleep(2)
    else:
        raise Fail("the sync of %s did not finish" % db["name"])
    # The event that the driver reads comes after the step in the log.
    time.sleep(3)
    after = other_database_state(ctx, db["id"])
    if before != after:
        raise Fail("the sync changed the database of another driver: before %s, after %s" % (str(before)[:300], str(after)[:300]))
    if "Could not sync the Cube data model to database %d" % db["id"] in log():
        raise Fail("the driver acted on the sync of another database")


@check("compile.bound.fraction_of_a_second")
def _(ctx):
    """Stock Cube drops the fraction of a bound, so the driver stops a bound with a fraction. With the dialect it is the truth, on whole seconds and on a DateTime64."""
    x, y = "2025-03-15T05:00:00.500", "2025-03-16T05:00:00.500"
    forms = ((">=", [">=", CRT, x]), (">", [">", CRT, x]), ("<", ["<", CRT, y]), ("<=", ["<=", CRT, y]), ("=", ["=", CRT, x]),
             (">= and <=", ["and", [">=", CRT, x], ["<=", CRT, y]]), ("> and <", ["and", [">", CRT, x], ["<", CRT, y]]), ("between", ["between", CRT, x, y]))
    if not DIALECT:
        for _label, where in forms:
            expect_clear_error(ctx, ctx.mb.inner(ORD, agg=["m:orders.count"], where=[where]), "fraction of a second")
        expect_native_refusal(ctx, "SELECT MEASURE(count) FROM orders WHERE created_at >= '2025-03-15 05:00:00.500'", "fraction of a second")
        return
    a, b = "toDateTime64('2025-03-15 05:00:00.500', 3, 'UTC')", "toDateTime64('2025-03-16 05:00:00.500', 3, 'UTC')"
    grid = Grid(ctx)
    for label, where, cond in ((">=", forms[0][1], "created_at >= %s" % a), (">", forms[1][1], "created_at > %s" % a), ("<", forms[2][1], "created_at < %s" % b),
                               ("<=", forms[3][1], "created_at <= %s" % b), ("=", forms[4][1], "created_at = %s" % a),
                               (">= and <=", forms[5][1], "created_at >= %s AND created_at <= %s" % (a, b)),
                               ("> and <", forms[6][1], "created_at > %s AND created_at < %s" % (a, b)), ("between", forms[7][1], "created_at >= %s AND created_at <= %s" % (a, b))):
        grid.add(label, ctx.mb.inner(ORD, agg=["m:orders.count"], where=[where]), "SELECT count() FROM orders WHERE " + cond)
    ts = "d:typed_events.ts64_berlin"
    t, u = "2025-03-01T06:00:00.006", "2025-03-01T08:00:00.008"
    c, d = "toDateTime64('2025-03-01 06:00:00.006', 3, 'UTC')", "toDateTime64('2025-03-01 08:00:00.008', 3, 'UTC')"
    for label, where, cond in (("64 >", [">", ts, t], "ts64_berlin > %s" % c), ("64 =", ["=", ts, t], "ts64_berlin = %s" % c),
                               ("64 between", ["between", ts, t, u], "ts64_berlin >= %s AND ts64_berlin <= %s" % (c, d)),
                               ("64 > and <", ["and", [">", ts, t], ["<", ts, u]], "ts64_berlin > %s AND ts64_berlin < %s" % (c, d))):
        grid.add(label, ctx.mb.inner(TE, agg=["m:typed_events.count"], where=[where]), "SELECT count() FROM typed_events WHERE " + cond)
    # SQL push-down, because the breakout has an expression.
    grid.add("push-down", ctx.mb.inner(TE, agg=[["count"]], by=[["expression", "k"]], exprs={"k": ["case", [[[">", "d:typed_events.id", 0], "all"]], {"default": "none"}]}, where=[[">", ts, t]]),
             "SELECT 'all', count() FROM typed_events WHERE ts64_berlin > %s" % c)
    grid.done()


@check("compile.bound.datetime64_whole_seconds_limit")
def _(ctx):
    """A DateTime64 keeps the rows inside one second next to a whole bound, on stock Cube too: first second after >, last second before <."""
    ensure_table(ctx, TE)
    ts = "d:typed_events.ts64_berlin"
    a, b = "2025-03-01 06:00:00", "2025-03-01 09:00:00"
    c, d = "toDateTime64('%s', 3, 'UTC')" % a, "toDateTime64('%s', 3, 'UTC')" % b
    if ch("SELECT count() FROM typed_events WHERE ts64_berlin > %s AND ts64_berlin < %s + INTERVAL 1 SECOND" % (c, c)) == [[0]]:
        raise Fail("the seed has no row in the first second after the bound")
    grid = Grid(ctx)
    for label, where, cond in (("> and <=", [[">", ts, a.replace(" ", "T")], ["<=", ts, b.replace(" ", "T")]], "ts64_berlin > %s AND ts64_berlin <= %s" % (c, d)),
                               ("> and <", [[">", ts, a.replace(" ", "T")], ["<", ts, b.replace(" ", "T")]], "ts64_berlin > %s AND ts64_berlin < %s" % (c, d)),
                               (">= and <", [[">=", ts, a.replace(" ", "T")], ["<", ts, b.replace(" ", "T")]], "ts64_berlin >= %s AND ts64_berlin < %s" % (c, d)),
                               # A row at 08:00:00.008 is in the last second before this bound.
                               (">= and < inside", [[">=", ts, a.replace(" ", "T")], ["<", ts, "2025-03-01T08:00:01"]],
                                "ts64_berlin >= %s AND ts64_berlin < toDateTime64('2025-03-01 08:00:01', 3, 'UTC')" % c)):
        grid.add(label, ctx.mb.inner(TE, agg=[["count"]], where=where), "SELECT count() FROM typed_events WHERE " + cond)
    grid.done()


@check("native.bound.strict_lower_refused")
def _(ctx):
    """Finding B1 in native SQL. The driver cannot change the text, so it stops the query. Right forms still run."""
    x, y = "'2025-03-15 05:00:00'", "'2025-03-16 05:00:00'"
    truth = "SELECT count() FROM orders WHERE created_at %s AND created_at %s"
    shapes = [("created_at > %s AND created_at <= %s" % (x, y), "created_at > %s AND created_at <= %s" % (x, y)),
              ("created_at > %s AND created_at < %s" % (x, y), "created_at > %s AND created_at < %s" % (x, y)),
              ("created_at > TIMESTAMP %s AND created_at <= TIMESTAMP %s" % (x, y), "created_at > %s AND created_at <= %s" % (x, y)),
              ("created_at <= %s AND status = 'shipped' AND created_at > %s" % (y, x), "created_at <= %s AND status = 'shipped' AND created_at > %s" % (y, x)),
              # The shapes that a scan of `column op literal` alone does not read.
              ("%s < created_at AND created_at <= %s" % (x, y), "created_at > %s AND created_at <= %s" % (x, y)),
              ("(created_at) > %s AND created_at <= %s" % (x, y), "created_at > %s AND created_at <= %s" % (x, y)),
              ("((created_at) > (%s)) AND ((created_at) <= (%s))" % (x, y), "created_at > %s AND created_at <= %s" % (x, y)),
              ("orders.created_at > %s AND orders.created_at <= %s" % (x, y), "created_at > %s AND created_at <= %s" % (x, y)),
              ("created_at > CAST(%s AS TIMESTAMP) AND created_at <= CAST(%s AS TIMESTAMP)" % (x, y), "created_at > %s AND created_at <= %s" % (x, y)),
              ("created_at > %s::timestamp AND created_at <= %s::timestamp" % (x, y), "created_at > %s AND created_at <= %s" % (x, y)),
              ("CAST(created_at AS TIMESTAMP) > %s AND created_at <= %s" % (x, y), "created_at > %s AND created_at <= %s" % (x, y)),
              ("%s >= created_at AND %s < created_at" % (y, x), "created_at > %s AND created_at <= %s" % (x, y))]
    for where, cond in shapes:
        sql = "SELECT MEASURE(count) FROM orders WHERE " + where
        if DIALECT:
            expect_native_rows(ctx, sql, "SELECT count() FROM orders WHERE " + cond)
        else:
            expect_native_refusal(ctx, sql, "next second")
    for lower, upper in ((">= " + x, "<= " + y), (">= " + x, "< " + y), (">= '2025-03-15 05:00:01'", "<= " + y)):
        expect_native_rows(ctx, "SELECT MEASURE(count) FROM orders WHERE created_at %s AND created_at %s" % (lower, upper), truth % (lower, upper))
    expect_native_rows(ctx, "SELECT MEASURE(count) FROM orders WHERE created_at > %s" % x, "SELECT count() FROM orders WHERE created_at > %s" % x)
    expect_native_rows(ctx, "SELECT MEASURE(count) FROM orders WHERE created_at > %s OR created_at < %s" % (y, x),
                       "SELECT count() FROM orders WHERE created_at > %s OR created_at < %s" % (y, x))
    # The same words in a string or a comment are not a filter.
    expect_native_rows(ctx, "SELECT MEASURE(count) FROM orders WHERE status <> 'created_at > 1 AND created_at <= 2' -- created_at > 1 AND created_at <= 2",
                       "SELECT count() FROM orders")


def bucket_sql(unit, column="created_at"):
    return {"day": "toStartOfDay(%s)", "month": "toStartOfMonth(%s)", "quarter": "toStartOfQuarter(%s)", "year": "toStartOfYear(%s)"}[unit] % column


OUTER_MEASURES = [("revenue", "sum(amount)"), ("unique_customers", "uniqExact(user_id)"), ("avg_order_value", "avg(amount)"),
                  ("paid_revenue", "sumIf(amount, status IN ('completed','shipped'))"), ("count", "count()")]


@check("nested.outer_date_filter.keeps_inner_rows")
def _(ctx):
    """Finding B2: a filter on a saved question must not change the rows of the question."""
    question = card_of(ctx, "revenue by month", ctx.mb.inner(ORD, agg=["m:orders.revenue"], by=MONTH))
    col = named("created_at", "type/DateTime")
    expect_rows(ctx, over(question, filter=[">=", col, "2025-05-15"]),
                "SELECT m, r FROM (SELECT toStartOfMonth(created_at) m, sum(amount) r FROM orders GROUP BY m) WHERE m >= '2025-05-15'", "rows")
    expect_rows(ctx, over(question, aggregation=[["sum", named("sum")]], filter=[">=", col, "2025-05-15"]),
                "SELECT sum(r) FROM (SELECT toStartOfMonth(created_at) m, sum(amount) r FROM orders GROUP BY m) WHERE m >= '2025-05-15'", "sum")


@check("nested.outer_date_filter.rolling")
def _(ctx):
    """Finding B2 with a rolling measure in the question."""
    question = card_of(ctx, "rolling by month", ctx.mb.inner(ORD, agg=["m:orders.revenue_trailing_30d"], by=MONTH,
                                                             where=[[">=", CRT, "2025-04-01"], ["<", CRT, "2025-07-01"]]))
    expect_rows(ctx, over(question, filter=[">=", named("created_at", "type/DateTime"), "2025-05-15"]),
                monthly_window("2025-06-01"), "rolling")


def monthly_window(month):
    return ("SELECT toDateTime('%s','UTC'), sumIf(amount, created_at >= addDays(toLastDayOfMonth(toDateTime('%s','UTC')) + INTERVAL 86399 SECOND, -30) "
            "AND created_at <= toLastDayOfMonth(toDateTime('%s','UTC')) + INTERVAL 86399 SECOND) FROM orders" % (month, month, month))


@check("nested.outer_date_filter.grid")
def _(ctx):
    """Each bucket with each measure kind, and each kind of outer filter. The filter value is inside a bucket, or on its boundary."""
    grid = Grid(ctx)
    col = named("created_at", "type/DateTime")
    for unit in ("day", "month", "quarter", "year"):
        for m, expr in OUTER_MEASURES:
            question = card_of(ctx, "%s by %s" % (m, unit), ctx.mb.inner(ORD, agg=["m:orders.%s" % m], by=["d:orders.created_at@%s" % unit]))
            inner = "(SELECT %s AS b, %s AS v FROM orders GROUP BY b)" % (bucket_sql(unit), expr)
            bound_a, bound_b = ("2025-05-15", "2025-08-20") if unit != "year" else ("2024-06-15", "2025-02-20")
            for label, where, cond in ((">=", [">=", col, bound_a], "b >= '%s'" % bound_a),
                                       (">", [">", col, bound_a], "b > '%s'" % bound_a),
                                       ("<", ["<", col, bound_b], "b < '%s'" % bound_b),
                                       ("<=", ["<=", col, bound_b], "b <= '%s'" % bound_b),
                                       ("two bounds", ["and", [">", col, bound_a], ["<=", col, bound_b]], "b > '%s' AND b <= '%s'" % (bound_a, bound_b)),
                                       ("between", ["between", col, bound_a, bound_b], "b >= '%s' AND b <= '%s'" % (bound_a, bound_b))):
                base = "%s %s by %s" % (m, label, unit)
                grid.add(base, over(question, filter=where), "SELECT b, v FROM %s WHERE %s" % (inner, cond))
                if m != "avg_order_value":
                    grid.add(base + " sum", over(question, aggregation=[["sum", named("sum")]], filter=where),
                             "SELECT sum(v) FROM %s WHERE %s" % (inner, cond))
    grid.done()


@check("nested.outer_filter.multi_stage_measure")
def _(ctx):
    """An outer filter on a dimension must not change a measure that reads other rows, such as a share or a rank."""
    grid = Grid(ctx)
    col = named("status", "type/Text")
    totals = "(SELECT status, sum(amount) AS r FROM orders GROUP BY status)"
    share = card_of(ctx, "share", ctx.mb.inner(ORD, agg=["m:orders.revenue_share_of_total"], by=[STA]))
    grid.add("share", over(share, filter=["=", col, "shipped"]),
             "SELECT status, toFloat64(r) / (SELECT toFloat64(sum(amount)) FROM orders) FROM %s WHERE status = 'shipped'" % totals)
    rank = card_of(ctx, "rank", ctx.mb.inner(ORD, agg=["m:orders.revenue_rank"], by=[STA]))
    grid.add("rank", over(rank, filter=["=", col, "shipped"]),
             "SELECT status, k FROM (SELECT status, rank() OVER (ORDER BY r DESC) AS k FROM %s) WHERE status = 'shipped'" % totals)
    plain = card_of(ctx, "plain", ctx.mb.inner(ORD, agg=["m:orders.revenue"], by=[STA]))
    grid.add("plain", over(plain, filter=["=", col, "shipped"]), "SELECT status, r FROM %s WHERE status = 'shipped'" % totals)
    grid.add("on the result", over(plain, filter=[">", named("sum"), 100000]), "SELECT status, r FROM %s WHERE r > 100000" % totals)
    grid.done()


@check("nested.outer_stage.grid_breakout_and_aggregation")
def _(ctx):
    """An outer stage that groups again. The inner rows stay as they are, also for a measure that is not additive."""
    grid = Grid(ctx)
    by_year = named("created_at", "type/DateTime", **{"temporal-unit": "year"})
    for m, expr in OUTER_MEASURES:
        question = card_of(ctx, "%s monthly" % m, ctx.mb.inner(ORD, agg=["m:orders.%s" % m], by=MONTH))
        inner = "(SELECT toStartOfMonth(created_at) AS b, %s AS v FROM orders GROUP BY b)" % expr
        output = named("avg" if m == "avg_order_value" else "sum")
        grid.add(m + " sum", over(question, aggregation=[["sum", output]]), "SELECT sum(v) FROM %s" % inner)
        grid.add(m + " max", over(question, aggregation=[["max", output]]), "SELECT max(v) FROM %s" % inner)
        grid.add(m + " count", over(question, aggregation=[["count"]]), "SELECT count() FROM %s" % inner)
        grid.add(m + " by year", over(question, aggregation=[["sum", output]], breakout=[by_year]),
                 "SELECT toStartOfYear(b), sum(v) FROM %s GROUP BY 1" % inner)
    grid.done()


@check("nested.outer_date_filter.native_source")
def _(ctx):
    """Finding B2 with a native question as the source. The driver cannot read a filter inside the text, so it limits the text."""
    question = ctx.mb.card("native by month", {"database": ctx.mb.need_db(), "type": "native", "native": {
        "query": "SELECT DATE_TRUNC('month', created_at) AS created_at, MEASURE(revenue) AS revenue FROM orders GROUP BY 1 -- comment", "template-tags": {}}})["id"]
    expect_rows(ctx, over(question, aggregation=[["sum", named("revenue", "type/Float")]], filter=[">=", named("created_at", "type/DateTime"), "2025-05-15"]),
                "SELECT sum(r) FROM (SELECT toStartOfMonth(created_at) m, sum(amount) r FROM orders GROUP BY m) WHERE m >= '2025-05-15'", "native source")


@check("nested.outer_date_filter.week_numbers")
def _(ctx):
    """A question by week on a cube with no granularity for the first day. Its subquery has a week number, and the outer filter reads a date."""
    prev = ctx.mb.api("GET", "/api/setting/start-of-week")
    ctx.mb.api("PUT", "/api/setting/start-of-week", {"value": "sunday"})
    try:
        question = card_of(ctx, "users by week", ctx.mb.inner("users", agg=["m:users.count"], by=["d:users.created_at@week"]))
        expect_rows(ctx, over(question, filter=[">=", named("created_at", "type/DateTime"), "2024-03-05"]),
                    "SELECT w, c FROM (SELECT toDateTime(toStartOfWeek(created_at, 0), 'UTC') w, count() c FROM users GROUP BY w) WHERE w >= '2024-03-05'", "week")
    finally:
        ctx.mb.api("PUT", "/api/setting/start-of-week", {"value": prev if isinstance(prev, str) and prev else None})


@check("mb.dashboard.date_filter_on_question")
def _(ctx):
    """Finding B2 and M1 on a dashboard: a date filter on a card of a saved question and of a model."""
    question = card_of(ctx, "dash question", ctx.mb.inner(ORD, agg=["m:orders.revenue"], by=MONTH))
    model = raw_model(ctx)
    over_model = card_of(ctx, "dash model", over(model, aggregation=[["sum", named("amount_dim")]]))
    param = {"id": "p1", "name": "P", "slug": "p", "type": "date/all-options", "sectionId": "date"}
    col = ["dimension", named("created_at", "type/DateTime"), {"stage-number": 0}]
    dash = ctx.mb.dashboard("date filter", [param], [(question, [("p1", col)]), (over_model, [("p1", col)])])
    value = [{"id": "p1", "type": "date/all-options", "value": "2025-05-15~", "target": col}]
    # The question has months, and the filter is on its result column, so each month from June is in.
    rows = ctx.mb.run_dashcard(dash, 1, value)["rows"]
    assert_rows(rows, ch("SELECT sum(amount) FROM orders WHERE created_at >= '2025-05-16'"), label="model")


@check("measure.rolling.through_model")
def _(ctx):
    """Finding B3: the rolling check also runs for a column by name, through a model and through MCP source-card."""
    model = raw_model(ctx)
    roll = named("revenue_trailing_30d")
    col = named("created_at", "type/DateTime")
    by_month = named("created_at", "type/DateTime", **{"temporal-unit": "month"})
    expect_clear_error(ctx, over(model, aggregation=[["sum", roll]]), "Group by a time bucket")
    expect_clear_error(ctx, over(model, aggregation=[["sum", roll]], breakout=[named("status", "type/Text")]), "Group by a time bucket")
    expect_clear_error(ctx, over(model, aggregation=[["sum", roll]], breakout=[by_month],
                                 filter=["and", [">=", col, "2025-04-15"], ["<", col, "2025-06-01"]]), "Use a filter on whole months")
    expect_clear_error(ctx, over(model, aggregation=[["sum", ["field", ctx.mb.field(ORD, "revenue_trailing_30d")["id"], None]]]), "Group by a time bucket")
    expect_clear_error(ctx, over(model, aggregation=[["max", roll]]), "rolling window")
    res = ctx.mcp.rpc("tools/call", {"name": "execute_query", "arguments": {"query": {
        "lib/type": "mbql/query", "database": ctx.mb.need_db(),
        "stages": [{"lib/type": "mbql.stage/mbql", "source-card": model,
                    "aggregation": [["sum", {}, ["field", {"base-type": "type/Decimal"}, "revenue_trailing_30d"]]]}]}}})
    text = (res.get("content") or [{}])[0].get("text", "")
    if not res.get("isError") or "rolling window" not in text:
        raise Fail("MCP source-card gave no refusal: %s" % text[:200])


@check("measure.rolling.through_model_whole_buckets")
def _(ctx):
    """A right shape stays right: whole months through a model give the window of each month."""
    model = raw_model(ctx)
    col = named("created_at", "type/DateTime")
    by_month = named("created_at", "type/DateTime", **{"temporal-unit": "month"})
    expect_rows(ctx, over(model, aggregation=[["sum", named("revenue_trailing_30d")]], breakout=[by_month],
                          filter=["and", [">=", col, "2025-04-01"], ["<", col, "2025-06-01"]]),
                "%s UNION ALL %s" % (monthly_window("2025-04-01"), monthly_window("2025-05-01")), "whole months")


@check("measure.rolling.filter_in_the_model")
def _(ctx):
    """A date filter inside a model goes to the same scan as the window, so it counts for the check."""
    inner = card_of(ctx, "filtered model", ctx.mb.inner(ORD, fields=["d:orders.created_at", "d:orders.revenue_mtd"], where=[[">=", CRT, "2025-04-15"]]), "model")
    expect_clear_error(ctx, over(inner, aggregation=[["sum", named("revenue_mtd")]],
                                 breakout=[named("created_at", "type/DateTime", **{"temporal-unit": "month"})],
                                 filter=["<", named("created_at", "type/DateTime"), "2025-06-01"]), "Use a filter on whole months")


@check("measure.rolling.strict_bound_on_boundary")
def _(ctx):
    """`>` with the last second of a month is the start of the next month, so April is the first bucket."""
    expect_rows(ctx, ctx.mb.inner(ORD, agg=["m:orders.revenue_trailing_30d"], by=MONTH, where=[[">", CRT, "2025-03-31T23:59:59"], ["<", CRT, "2025-05-01"]]),
                monthly_window("2025-04-01"), "strict bound")


ROW_MEASURES = [("revenue", "amount"), ("avg_order_value", "amount"), ("max_amount", "amount"), ("count", "1")]


@check("nested.measure_column.filter_gives_row_values")
def _(ctx):
    """Finding B4: a model with the primary key has one value for each row, so a filter on a measure column filters rows."""
    model = raw_model(ctx)
    grid = Grid(ctx)
    status = named("status", "type/Text")
    for measure, row in ROW_MEASURES:
        col = named(measure)
        for label, where, cond in (("> 50", [">", col, 50], "%s > 50" % row), ("between", ["between", col, 1, 150], "%s BETWEEN 1 AND 150" % row),
                                   ("not", ["not", [">", col, 50]], "NOT (%s > 50)" % row),
                                   ("and status", ["and", [">", col, 50], ["=", status, "shipped"]], "%s > 50 AND status = 'shipped'" % row),
                                   ("or status", ["or", [">", col, 150], ["=", status, "shipped"]], "%s > 150 OR status = 'shipped'" % row)):
            grid.add("%s %s sum" % (measure, label), over(model, aggregation=[["sum", named("amount_dim")]], filter=where),
                     "SELECT sumOrNull(amount) FROM orders WHERE %s" % cond)
            grid.add("%s %s count" % (measure, label), over(model, aggregation=[["count"]], filter=where), "SELECT count() FROM orders WHERE %s" % cond)
    grid.add("sum of the same column", over(model, aggregation=[["sum", named("revenue")]], filter=[">", named("revenue"), 150]),
             "SELECT sum(amount) FROM orders WHERE amount > 150")
    grid.add("by status", over(model, aggregation=[["sum", named("amount_dim")]], breakout=[status], filter=[">", named("revenue"), 150]),
             "SELECT status, sum(amount) FROM orders WHERE amount > 150 GROUP BY status")
    grid.add("with date", over(model, aggregation=[["count"]], filter=["and", [">", named("revenue"), 150], [">=", named("created_at", "type/DateTime"), "2025-05-10"]]),
             "SELECT count() FROM orders WHERE amount > 150 AND created_at >= '2025-05-10'")
    grid.done()
    # Raw rows with this filter have the value of each row.
    expect_rows(ctx, over(model, fields=[named("id"), named("revenue")], filter=["and", [">", named("revenue"), 150], ["<", named("id"), 200]]),
                "SELECT id, amount FROM orders WHERE amount > 150 AND id < 200", "raw rows")


@check("nested.measure_column.filter_with_no_key_refused")
def _(ctx):
    """With no primary key in the model, a group is more than one row, so the driver stops the query."""
    model = card_of(ctx, "model with no key", ctx.mb.inner(ORD, fields=["d:orders.status", "d:orders.amount_dim", "d:orders.revenue"]), "model")
    expect_clear_error(ctx, over(model, aggregation=[["count"]], filter=[">", named("revenue"), 150]), "Cube cannot filter on the measure column")
    expect_clear_error(ctx, over(model, aggregation=[["count"]], breakout=[named("revenue")]), "Cube cannot group by the measure")
    expect_clear_error(ctx, over(raw_model(ctx), aggregation=[["count"]], filter=[">", named("revenue_trailing_30d"), 150]), "Cube cannot filter on the measure column")


@check("nested.measure_column.breakout_gives_row_values")
def _(ctx):
    """Finding B4: a breakout on a measure column of a model with the primary key groups by the value of each row."""
    model = raw_model(ctx)
    expect_rows(ctx, over(model, aggregation=[["count"]], breakout=[named("revenue")]), "SELECT amount, count() FROM orders GROUP BY amount", "count")
    expect_rows(ctx, over(model, aggregation=[["sum", named("amount_dim")]], breakout=[named("revenue")]), "SELECT amount, sum(amount) FROM orders GROUP BY amount", "sum")


@check("compile.filter.hidden_measure_column_refused")
def _(ctx):
    """Finding B4 on a table: a filter on the hidden measure column reads as a filter on the group."""
    for by in ([STA], None):
        expect_clear_error(ctx, ctx.mb.inner(ORD, agg=["m:orders.count"], by=by, where=[[">", "d:orders.revenue", 100000]]), "Cube cannot filter on the measure column")


@check("nested.measure_column.right_shapes_stay")
def _(ctx):
    """A refusal must not break a right shape. These use a measure column of a model with no filter on it."""
    model = raw_model(ctx)
    grid = Grid(ctx)
    col = named("created_at", "type/DateTime")
    status = named("status", "type/Text")
    for m, expr in (("revenue", "sum(amount)"), ("count", "count()"), ("unique_customers", "uniqExact(user_id)"), ("paid_revenue", "sumIfOrNull(amount, status IN ('completed','shipped'))")):
        grid.add(m, over(model, aggregation=[["sum", named(m)]]), "SELECT %s FROM orders" % expr)
        grid.add(m + " by status", over(model, aggregation=[["sum", named(m)]], breakout=[status]), "SELECT status, %s FROM orders GROUP BY status" % expr)
        grid.add(m + " with date", over(model, aggregation=[["sum", named(m)]], filter=[">=", col, "2025-05-10"]), "SELECT %s FROM orders WHERE created_at >= '2025-05-10'" % expr)
        grid.add(m + " with status", over(model, aggregation=[["sum", named(m)]], filter=["=", status, "shipped"]), "SELECT %s FROM orders WHERE status = 'shipped'" % expr)
        grid.add(m + " by month", over(model, aggregation=[["sum", named(m)]], breakout=[named("created_at", "type/DateTime", **{"temporal-unit": "month"})]),
                 "SELECT toStartOfMonth(created_at), %s FROM orders GROUP BY 1" % expr)
    grid.add("avg", over(model, aggregation=[["avg", named("avg_order_value")]]), "SELECT avg(amount) FROM orders")
    grid.add("min", over(model, aggregation=[["min", named("min_amount")]]), "SELECT min(amount) FROM orders")
    grid.add("max", over(model, aggregation=[["max", named("max_amount")]]), "SELECT max(amount) FROM orders")
    grid.done()
    # With the value of each row, a distinct count does not add up, so the driver stops it.
    expect_clear_error(ctx, over(model, aggregation=[["sum", named("unique_customers")]], filter=[">", named("revenue"), 150]), "do not add up")
    expect_clear_error(ctx, over(model, aggregation=[["sum", named("unique_customers")]], breakout=[named("revenue")]), "do not add up")
    expect_rows(ctx, over(model, aggregation=[["avg", named("avg_order_value")]], filter=[">", named("revenue"), 150]), "SELECT avg(amount) FROM orders WHERE amount > 150", "avg of rows")


@check("nested.measure_column.other_aggregation")
def _(ctx):
    """Another aggregation of a measure column gives the row values or a refusal, on a table and through a model."""
    model = raw_model(ctx)
    grid = Grid(ctx, refusal="Measure aggregation type")
    grid.add("max of a sum", over(model, aggregation=[["max", named("revenue")]]), "SELECT max(amount) FROM orders")
    grid.add("min of a sum", over(model, aggregation=[["min", named("revenue")]]), "SELECT min(amount) FROM orders")
    grid.add("avg of a sum", over(model, aggregation=[["avg", named("revenue")]]), "SELECT avg(amount) FROM orders")
    grid.done()


@check("nested.model_of_model.measure_column")
def _(ctx):
    """A model on a model: the name goes through two stages, and the checks still find the measure."""
    model = raw_model(ctx)
    upper = card_of(ctx, "model of model", over(model, fields=[named("status", "type/Text"), named("created_at", "type/DateTime"), named("revenue"), named("revenue_trailing_30d")]), "model")
    expect_clear_error(ctx, over(upper, aggregation=[["count"]], filter=[">", named("revenue"), 150]), "Cube cannot filter on the measure column")
    expect_clear_error(ctx, over(upper, aggregation=[["sum", named("revenue_trailing_30d")]]), "Group by a time bucket")
    keyed = card_of(ctx, "keyed model of model", over(model, fields=[named("id"), named("status", "type/Text"), named("revenue")]), "model")
    expect_rows(ctx, over(keyed, aggregation=[["count"]], filter=[">", named("revenue"), 150]), "SELECT count() FROM orders WHERE amount > 150", "filter")
    expect_rows(ctx, over(upper, aggregation=[["sum", named("revenue")]], breakout=[named("status", "type/Text")]), STATUS_REV, "sum")


@check("nested.model.raw_rows_of_a_measure_are_null")
def _(ctx):
    """Finding m8: raw rows of a model show the same as raw rows of the table. A rolling or a multi-stage measure has no row value."""
    model = raw_model(ctx)
    expect_rows(ctx, over(model, fields=[named("id"), named("revenue"), named("revenue_trailing_30d")], filter=["=", named("id"), 7]), [[7, None, None]], "model")
    assert_rows(ctx.mb.mbql(ORD, fields=["d:orders.id", "d:orders.revenue"], where=[["=", "d:orders.id", 7]])["rows"], [[7, None]], label="table")


@check("view.two_paths_in_one_query_refused")
def _(ctx):
    """Finding B5, on a view with no join alias: Cube joins a cube once in a query, so both columns would show one value."""
    view = "region_sales_one_join"
    ensure_table(ctx, view)
    both = ["d:%s.customer_region" % view, "d:%s.destination_region" % view]
    needle = "through different join paths"
    expect_clear_error(ctx, ctx.mb.inner(view, agg=["m:%s.count" % view], by=both), needle)
    expect_clear_error(ctx, ctx.mb.inner(view, agg=["m:%s.count" % view], by=both[:1], where=[["=", both[1], "South"]]), needle)
    expect_native_refusal(ctx, "SELECT customer_region, destination_region, MEASURE(count) FROM %s GROUP BY 1, 2" % view, needle)
    res = ctx.mcp.rpc("tools/call", {"name": "execute_query", "arguments": {"query": mcp_query(ctx.mb, view, agg=["m:%s.count" % view], by=both)}})
    if not res.get("isError"):
        raise Fail("MCP gave no refusal")
    # A question on a model of this view goes through the same scan of the SQL.
    model = card_of(ctx, "two paths model", ctx.mb.inner(view, fields=both + ["d:%s.count" % view]), "model")
    expect_clear_error(ctx, over(model, aggregation=[["count"]], breakout=[named("customer_region", "type/Text"), named("destination_region", "type/Text")]), needle)


@check("view.two_paths_one_column_stays")
def _(ctx):
    """A right shape stays right: each column alone. Raw rows show the first column, and the second one is NULL, not a wrong value."""
    view = "region_sales_one_join"
    ensure_table(ctx, view)
    expect_rows(ctx, ctx.mb.inner(view, agg=["m:%s.count" % view], by=["d:%s.destination_region" % view]),
                "SELECT %s AS r, count() FROM orders GROUP BY r" % (REGION_SQL % "['TX','IL','WA','MA'][1 + id % 4]"), "destination")
    expect_rows(ctx, ctx.mb.inner(view, agg=["m:%s.count" % view], by=["d:%s.customer_region" % view]),
                "SELECT %s AS r, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY r" % (REGION_SQL % "u.state"), "customer")
    data = ctx.mb.mbql(view, fields=["d:%s.customer_region" % view, "d:%s.destination_region" % view], limit=20)
    if any(r[1] is not None for r in data["rows"]) or not all(r[0] for r in data["rows"]):
        raise Fail("raw rows must show the first column and NULL in the second one: %s" % data["rows"][:3])


@check("view.two_paths_with_join_alias")
def _(ctx):
    """Finding B5: with a join alias for each path, one query shows both columns."""
    expect_rows(ctx, ctx.mb.inner("region_sales", agg=["m:region_sales.count"], by=["d:region_sales.customer_region", "d:region_sales.destination_region"]),
                "SELECT %s AS a, %s AS b, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY a, b" % (REGION_SQL % "u.state", REGION_SQL % "['TX','IL','WA','MA'][1 + o.id % 4]"), "both regions")


@check("measure.rolling.native_refused")
def _(ctx):
    """Finding B6, of unclear intent in Cube: the scan of native text finds a rolling measure with no bucket, or with a filter inside a bucket."""
    for sql in ("SELECT MEASURE(revenue_trailing_30d) FROM orders",
                "SELECT status, MEASURE(revenue_mtd) FROM orders GROUP BY 1",
                'SELECT MEASURE("orders"."revenue_trailing_30d") FROM "public"."orders"',
                "select measure(cumulative_count) from orders"):
        expect_native_refusal(ctx, sql, "With no time bucket")
    for where in ("created_at >= '2025-04-15' AND created_at < '2025-06-01'", "created_at >= '2025-04-01' AND created_at < '2025-05-20'",
                  "created_at >= TIMESTAMP '2025-04-15 00:00:00'", "created_at BETWEEN '2025-04-15 00:00:00' AND '2025-05-31 23:59:59'",
                  "'2025-04-15' <= created_at AND created_at < '2025-06-01'", "(created_at) >= '2025-04-15'"):
        expect_native_refusal(ctx, "SELECT DATE_TRUNC('month', created_at), MEASURE(revenue_trailing_30d) FROM orders WHERE %s GROUP BY 1" % where, "Use a filter on whole months")
    # The shapes that the scan cannot read as a bound stop too.
    for where in ("created_at IN ('2025-04-15', '2025-05-15')", "toDate(created_at) >= '2025-04-15'", "EXTRACT(MONTH FROM created_at) = 4"):
        expect_native_refusal(ctx, "SELECT DATE_TRUNC('month', created_at), MEASURE(revenue_trailing_30d) FROM orders WHERE %s GROUP BY 1" % where, "cannot read")
    res = ctx.mcp.rpc("tools/call", {"name": "execute_sql", "arguments": {"database_id": ctx.mb.need_db(), "sql": "SELECT MEASURE(revenue_trailing_30d) FROM orders"}})
    if not res.get("isError"):
        raise Fail("MCP execute_sql gave no refusal: %s" % str(res)[:200])


@check("measure.rolling.native_whole_buckets")
def _(ctx):
    """A right shape stays right: native SQL with whole buckets gives the window."""
    for where in ("created_at >= '2025-04-01' AND created_at < '2025-06-01'", "created_at >= TIMESTAMP '2025-04-01 00:00:00' AND created_at <= TIMESTAMP '2025-05-31 23:59:59'",
                  "created_at BETWEEN TIMESTAMP '2025-04-01 00:00:00' AND TIMESTAMP '2025-05-31 23:59:59'"):
        expect_native_rows(ctx, "SELECT DATE_TRUNC('month', created_at), MEASURE(revenue_trailing_30d) FROM orders WHERE %s GROUP BY 1" % where,
                           "%s UNION ALL %s" % (monthly_window("2025-04-01"), monthly_window("2025-05-01")))
    # A column with that name on another cube is not a rolling measure, and a word in a string is not a column.
    expect_native_rows(ctx, "SELECT MEASURE(count) FROM orders WHERE status <> 'revenue_trailing_30d'", "SELECT count() FROM orders")


@check("compile.raw.switch_dimension_keeps_one_row")
def _(ctx):
    """Cube gives each row once for each value of a switch dimension. Raw rows of a table must stay one row for each row, also through a model."""
    tid = ctx.mb.table(ORD)["id"]
    status = ctx.mb.field(ORD, "status")["id"]
    res = ctx.mb.api("POST", "/api/dataset", {"database": ctx.mb.need_db(), "type": "query", "query": {"source-table": tid, "filter": ["=", ["field", status, None], "shipped"], "limit": 5000}})
    truth = ch(SHIPPED)[0][0]
    if len(res["data"]["rows"]) != truth:
        raise Fail("raw rows: %d, truth %d" % (len(res["data"]["rows"]), truth))
    model = ctx.mb.card("switch model", {"database": ctx.mb.need_db(), "type": "query", "query": {"source-table": tid}}, "model")
    assert_rows(ctx.mb.api("POST", "/api/dataset", {"database": ctx.mb.need_db(), "type": "query", "query": {"source-table": "card__%d" % model["id"], "aggregation": [["count"]]}})["data"]["rows"],
                ch(CNT_O), label="count through a model")
    # With a measure, the values of the switch are the groups.
    assert_rows(ctx.mb.mbql(ORD, agg=["m:orders.revenue_in_currency"], by=["d:orders.display_currency"], where=[["=", "d:orders.display_currency", "EUR"]])["rows"],
                ch("SELECT 'EUR', toFloat64(sum(amount)) * 0.5 FROM orders"), tol=0.01, label="groups")


@check("native.subquery_filter_refused")
def _(ctx):
    """B2 and B4 in native SQL: an outer filter over a subquery. The driver cannot change the text, so it stops the query. A limit in the subquery gives the truth."""
    month = "SELECT DATE_TRUNC('month', created_at) AS m, MEASURE(revenue) AS r FROM orders GROUP BY 1"
    expect_native_refusal(ctx, "SELECT * FROM (%s) AS s WHERE m <= '2025-05-01'" % month, "LIMIT 2147483647")
    expect_native_refusal(ctx, "SELECT * FROM (%s) s WHERE r > 100" % month, "LIMIT 2147483647")
    expect_native_refusal(ctx, "WITH c AS (%s) SELECT * FROM c WHERE m <= '2025-05-01'" % month, "LIMIT 2147483647")
    expect_native_rows(ctx, "SELECT * FROM (%s LIMIT 2147483647) AS s WHERE m <= '2025-05-01'" % month,
                       "SELECT toStartOfMonth(created_at) AS m, sum(amount) FROM orders GROUP BY m HAVING m <= '2025-05-01'")
    expect_native_refusal(ctx, "SELECT SUM(revenue) FROM (SELECT id, revenue FROM orders) AS s WHERE revenue > 100", "filter on the aggregate")
    # With no filter on a measure, a subquery of raw rows gives the truth, and so does an outer filter on a dimension.
    expect_native_rows(ctx, "SELECT MEASURE(revenue) FROM (SELECT status, revenue FROM orders) AS s WHERE status = 'shipped'",
                       "SELECT sum(amount) FROM orders WHERE status = 'shipped'")


@check("native.bound.alias_in_a_subquery")
def _(ctx):
    """The scan reads an alias of a time column too."""
    sql = ("SELECT MEASURE(revenue) FROM (SELECT created_at AS c, revenue FROM orders) AS s WHERE c > '2025-03-15 05:00:00' AND c <= '2025-03-16 05:00:00'")
    truth = "SELECT sum(amount) FROM orders WHERE created_at > '2025-03-15 05:00:00' AND created_at <= '2025-03-16 05:00:00'"
    if DIALECT:
        expect_native_rows(ctx, sql, truth)
    else:
        expect_native_refusal(ctx, sql, "next second")


@check("native.bound.datetime64_last_second_limit")
def _(ctx):
    """On stock Cube, native `x >= a AND x < b` on a DateTime64 ends at the last whole second before b, and /v1/meta does not show the type. The dialect gives the truth."""
    ensure_table(ctx, TE)
    cond = "ts64_berlin >= '2025-03-01 06:00:00' AND ts64_berlin < '2025-03-01 08:00:01'"
    sql = "SELECT MEASURE(count) FROM typed_events WHERE " + cond
    truth = ch("SELECT count() FROM typed_events WHERE ts64_berlin >= toDateTime64('2025-03-01 06:00:00', 3, 'UTC') AND ts64_berlin < toDateTime64('2025-03-01 08:00:01', 3, 'UTC')")
    got = ctx.mb.native(sql)["rows"]
    if DIALECT:
        assert_rows(got, truth)
    elif got[0][0] != truth[0][0] - 1:
        raise Fail("stock Cube gave %s, truth %s. If this is the truth, give this entry its full status" % (got, truth))


@check("native.between_date_text_refused")
def _(ctx):
    """Finding B7: Cube pads a date with no time at the end of a date range, which is documented for its REST API, and SQL reads it as midnight."""
    needle = "BETWEEN"
    a, b = "'2025-03-11'", "'2025-04-13'"
    for where in ("created_at BETWEEN %s AND %s" % (a, b), "created_at NOT BETWEEN %s AND %s" % (a, b), "created_at between '2025-03-11 00:00:00' and %s" % b,
                  "(created_at) BETWEEN (%s) AND (%s)" % (a, b), "created_at BETWEEN CAST(%s AS TEXT) AND CAST(%s AS TEXT)" % (a, b)):
        expect_native_refusal(ctx, "SELECT MEASURE(count) FROM orders WHERE " + where, needle)
    for where in ("created_at BETWEEN '2025-03-11 00:00:00' AND '2025-04-13 00:00:00'", "created_at >= %s AND created_at <= %s" % (a, b),
                  "created_at >= %s AND created_at < %s" % (a, b), "created_at <= %s" % b):
        expect_native_rows(ctx, "SELECT MEASURE(count) FROM orders WHERE " + where, "SELECT count() FROM orders WHERE " + where)
    expect_native_rows(ctx, "SELECT MEASURE(count) FROM orders WHERE created_at BETWEEN DATE %s AND DATE %s" % (a, b),
                       "SELECT count() FROM orders WHERE created_at BETWEEN %s AND %s" % (a, b))


@check("native.between_date_parameter")
def _(ctx):
    """Finding B7 with date parameters: the driver sends a time literal, so this gives the truth, or the same refusal."""
    tags = dict(tag("a", "date"), **tag("b", "date"))
    parameters = [{"type": "date/single", "target": ["variable", ["template-tag", "a"]], "value": "2025-03-11"},
                  {"type": "date/single", "target": ["variable", ["template-tag", "b"]], "value": "2025-04-13"}]
    rows, error = native_outcome(ctx, "SELECT MEASURE(count) FROM orders WHERE created_at BETWEEN {{a}} AND {{b}}", tags, parameters)
    if error:
        if "BETWEEN" not in error:
            raise Fail("not a clear refusal: %s" % error[:300])
    else:
        assert_rows(rows, ch("SELECT count() FROM orders WHERE created_at BETWEEN '2025-03-11' AND '2025-04-13'"), label="date parameters")
    # The optional and the typed forms of a right query keep working.
    expect_native_rows(ctx, "SELECT MEASURE(count) FROM orders WHERE created_at >= {{a}} AND created_at < {{b}}",
                       "SELECT count() FROM orders WHERE created_at >= '2025-03-11' AND created_at < '2025-04-13'", tags, parameters)


@check("docs.install.plugins_directory_writable")
def _(ctx):
    """Finding B8: the Metabase image runs as uid 2000 and it ignores a plugins directory that it cannot write, with no error."""
    for path in ("docs/install.md", "README.md"):
        with open(os.path.join(REPO, path), encoding="utf-8") as f:
            text = f.read()
        for word in ("chmod", "2000", "Registered driver"):
            if word not in text:
                raise Fail("%s has no %r" % (path, word))


@check("docs.known_wrong_numbers")
def _(ctx):
    """The list of the numbers that stay wrong, in the docs and in the README."""
    for path in ("docs/query-compilation.md", "README.md"):
        with open(os.path.join(REPO, path), encoding="utf-8") as f:
            text = f.read()
        if "Known wrong numbers" not in text:
            raise Fail("%s has no section Known wrong numbers" % path)


@check("nested.model_date_filter")
def _(ctx):
    """Finding M1: a date filter on a model or a question gave `Can't parse date: 'replaced_placeholder'`."""
    model = raw_model(ctx)
    col = named("created_at", "type/DateTime")
    expect_rows(ctx, over(model, aggregation=[["sum", named("amount_dim")]], filter=[">=", col, "2025-05-10"]), "SELECT sum(amount) FROM orders WHERE created_at >= '2025-05-10'", "sum")
    expect_rows(ctx, over(model, aggregation=[["count"]], filter=["between", col, "2025-05-10", "2025-05-20"]), "SELECT count() FROM orders WHERE toDate(created_at) BETWEEN '2025-05-10' AND '2025-05-20'", "count")
    expect_rows(ctx, over(model, aggregation=[["count"]], breakout=[named("status", "type/Text")], filter=["<", col, "2025-05-10T12:30:00"]),
                "SELECT status, count() FROM orders WHERE created_at < '2025-05-10 12:30:00' GROUP BY status", "by status")


@check("nested.model_date_filter_on_expression")
def _(ctx):
    """Finding M1: an expression of a rolling measure by month with whole buckets, which is post-processing."""
    month, window = ch(monthly_window("2025-04-01"))[0]
    expect_rows(ctx, ctx.mb.inner(ORD, agg=[["+", "m:orders.revenue_trailing_30d", 1]], by=MONTH, where=[[">=", CRT, "2025-04-01"], ["<", CRT, "2025-05-01"]]),
                [[month, window + 1]], "plus one")


def with_start_of_week(ctx, day, fn):
    prev = ctx.mb.api("GET", "/api/setting/start-of-week")
    ctx.mb.api("PUT", "/api/setting/start-of-week", {"value": day})
    try:
        fn()
    finally:
        ctx.mb.api("PUT", "/api/setting/start-of-week", {"value": prev if isinstance(prev, str) and prev else None})


def week_start(column, day):
    """The first day of the week of `column`, for a first day that ClickHouse has no mode for."""
    offset = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6}[day]
    return "toDateTime(toDate(%s) - toUInt16(((toDayOfWeek(%s) %% 7) + 7 - %d) %% 7), 'UTC')" % (column, column, offset)


@check("compile.week.default_with_date_filter")
def _(ctx):
    """Finding M2: a week number with a date filter on each first day of the week."""
    grid = Grid(ctx)

    def run(day):
        def body():
            grid.add("%s users" % day, ctx.mb.inner("users", agg=["m:users.count"], by=["d:users.created_at@week"], where=[[">=", "d:users.created_at", "2024-03-01"]]),
                     "SELECT %s AS w, count() FROM users WHERE created_at >= '2024-03-01' GROUP BY w" % week_start("created_at", day))
            grid.add("%s level_one" % day, ctx.mb.inner("level_one", agg=["m:level_one.revenue"], by=["d:level_one.created_at@week"], where=[["between", "d:level_one.created_at", "2025-03-05", "2025-04-17"]]),
                     "SELECT %s AS w, sum(amount * 2) FROM orders WHERE toDate(created_at) BETWEEN '2025-03-05' AND '2025-04-17' GROUP BY w" % week_start("created_at", day))
            grid.add("%s orders" % day, ctx.mb.inner(ORD, agg=["m:orders.count"], by=[WK], where=[[">=", CRT, "2025-12-01"]]),
                     "SELECT %s AS w, count() FROM orders WHERE created_at >= '2025-12-01' GROUP BY w" % week_start("created_at", day))
        with_start_of_week(ctx, day, body)
    for day in ("sunday", "monday", "wednesday", "saturday"):
        run(day)
    grid.done()


@check("mcp.query.count_in_a_date_range")
def _(ctx):
    """Finding M3: count with an absolute date filter in MCP."""
    where = [[">=", CRT, "2025-05-10"], ["<", CRT, "2025-05-21"]]
    truth = "SELECT %s FROM orders WHERE created_at >= '2025-05-10' AND created_at < '2025-05-21'"
    mcp_rows(ctx, ORD, truth % "count()", agg=["m:orders.count"], where=where)
    mcp_rows(ctx, ORD, truth % "count()", agg=[["count"]], where=where)
    mcp_rows(ctx, ORD, truth % "sum(amount), count()", agg=["m:orders.revenue", "m:orders.count"], where=where)


@check("compile.week.relative_on_each_first_day")
def _(ctx):
    """Finding M4: a relative filter in weeks, on each first day of the week. The truth starts at the first day of a week."""
    grid = Grid(ctx)

    def run(day):
        def body():
            today = "now('UTC')"
            start = "(%s - INTERVAL 90 WEEK)" % week_start(today, day)
            end = week_start(today, day)
            grid.add(day, ctx.mb.inner(ORD, agg=["m:orders.count"], where=[["time-interval", CRT, -90, "week"]]),
                     "SELECT count() FROM orders WHERE created_at >= %s AND created_at < %s" % (start, end))
        with_start_of_week(ctx, day, body)
    for day in ("sunday", "monday", "wednesday", "saturday"):
        run(day)
    grid.done()


@check("compile.error.segment_errors_are_clear")
def _(ctx):
    """Finding m1."""
    expect_clear_error(ctx, ctx.mb.inner(ORD, agg=[["count"]], where=[["or", "s:orders.paid", "s:orders.large"]]), "add a segment")
    expect_clear_error(ctx, ctx.mb.inner(ORD, agg=["m:orders.revenue"], where=["s:users.vip"]), "use a view")


@check("docs.fan_out_rule")
def _(ctx):
    """Finding m2: the docs tell which rule applies over a fan-out, and the numbers agree with it."""
    with open(os.path.join(REPO, "docs/query-compilation.md"), encoding="utf-8") as f:
        text = f.read()
    if "fan-out" not in text.lower() and "fan out" not in text.lower():
        raise Fail("docs/query-compilation.md has no rule for a fan-out")
    join = [{"source-table": ctx.mb.table("payments")["id"], "alias": "P", "strategy": "left-join", "condition": ["=", "d:orders.id", "d:payments.order_id|P"]}]
    assert_rows(ctx.mb.mbql(ORD, agg=[["count"], ["sum", "d:orders.quantity_dim"]], joins=join)["rows"],
                ch("SELECT (SELECT count() FROM orders o JOIN (SELECT id AS order_id FROM orders UNION ALL SELECT id FROM orders) p ON o.id = p.order_id), (SELECT sum(quantity) FROM orders)"), label="fan-out")


@check("native.count_on_no_rows")
def _(ctx):
    """Findings m3 and m2. A rollup answers a count with SUM, so Cube gives NULL on no rows. The driver writes COALESCE around a count in a text with one SELECT and no grouping."""
    for sql in ("SELECT COUNT(*) FROM orders WHERE status = 'nope'", "SELECT MEASURE(count) FROM orders WHERE status = 'nope'", "SELECT COUNT(*) FROM typed_events WHERE lc = 'nope'"):
        assert_rows(ctx.mb.native(sql)["rows"], [[0]], label=sql)
    assert_rows(ctx.mb.mbql(ORD, agg=[["count"]], where=[["=", STA, "nope"]])["rows"], ch("SELECT count() FROM orders WHERE status = 'nope'"), label="MBQL")


UNBOUNDED_TWO_MONTHS = ("SELECT toDateTime('2025-04-01','UTC'), countIf(created_at <= '2025-04-30 23:59:59') FROM orders "
                        "UNION ALL SELECT toDateTime('2025-05-01','UTC'), countIf(created_at <= '2025-05-31 23:59:59') FROM orders")


@check("measure.rolling.unbounded_one_bucket")
def _(ctx):
    """Finding m4: `trailing: unbounded` works for one bucket. For more, ClickHouse 26.10 runs the join and gives the truth, and an older ClickHouse rejects it, so the driver
    stops with a message that tells this."""
    expect_rows(ctx, ctx.mb.inner(ORD, agg=["m:orders.cumulative_count"], by=MONTH, where=[[">=", CRT, "2025-04-01"], ["<", CRT, "2025-05-01"]]),
                "SELECT toDateTime('2025-04-01','UTC'), countIf(created_at <= '2025-04-30 23:59:59') FROM orders", "one month")
    query = ctx.mb.inner(ORD, agg=["m:orders.cumulative_count"], by=MONTH, where=[[">=", CRT, "2025-04-01"], ["<", CRT, "2025-06-01"]])
    rows, error = outcome(ctx, query)
    if error:
        if "for more than one bucket" not in error:
            raise Fail("two months: the error has no 'for more than one bucket': %s" % error[:300])
    else:
        assert_rows(rows, ch(UNBOUNDED_TWO_MONTHS), label="two months")
    expect_clear_error(ctx, ctx.mb.inner(ORD, agg=[["max", "d:orders.revenue_trailing_30d"]]), "cannot compute max")


@check("measure.rolling.unbounded")
def _(ctx):
    """`trailing: unbounded` for more than one bucket. Cube writes a join with only an inequality. ClickHouse 26.10 runs it. An older ClickHouse rejects it, and this entry is then blocked."""
    query = ctx.mb.inner(ORD, agg=["m:orders.cumulative_count"], by=MONTH, where=[[">=", CRT, "2025-04-01"], ["<", CRT, "2025-06-01"]])
    rows, error = outcome(ctx, query)
    if error and "for more than one bucket" in error:
        raise Blocked("ClickHouse before 26.10 rejects the join with only an inequality that Cube writes for more than one bucket: Cannot determine join keys in JOIN ON expression. The driver stops this query with a clear message. trailing: 100 year in the data model works.")
    if error:
        raise Fail(error[:300])
    assert_rows(rows, ch(UNBOUNDED_TWO_MONTHS), label="two months")


@check("measure.rolling.window_shorter_than_bucket")
def _(ctx):
    """Finding M1. A window that is shorter than the bucket has no one meaning, so the driver stops it, in the query builder and in native SQL."""
    half = [[">=", CRT, "2025-01-01"], ["<", CRT, "2025-07-01"]]
    for measure, unit, where in (("revenue_mtd", "quarter", half), ("revenue_trailing_30d", "quarter", half), ("revenue_leading_7d", "month", half)):
        expect_clear_error(ctx, ctx.mb.inner(ORD, agg=["m:orders.%s" % measure], by=[CRT + "@" + unit], where=where), "shorter than a %s" % unit)
        expect_native_refusal(ctx, "SELECT DATE_TRUNC('%s', created_at), MEASURE(%s) FROM orders WHERE created_at >= '2025-01-01' AND created_at < '2025-07-01' GROUP BY 1" % (unit, measure), "shorter than a %s" % unit)
    # A window as long as the bucket has a truth.
    expect_rows(ctx, ctx.mb.inner(ORD, agg=["m:orders.revenue_ytd"], by=[CRT + "@quarter"], where=half),
                "SELECT toDateTime(q, 'UTC'), sumIf(amount, created_at >= toStartOfYear(q) AND created_at < addMonths(q, 3)) FROM (SELECT toStartOfQuarter(addMonths(toDate('2025-01-01'), toInt64(number) * 3)) q FROM numbers(2)) AS s CROSS JOIN orders GROUP BY q", "year to date by a quarter")


# ----------------------------------------------------------------- a compact form of the review grid
#
# dev/review has the whole grid, see `make grid`. These checks run a stable part of its shapes, so that CI catches them. A wrong number or a missing refusal that is not in
# dev/review/known_limits.py fails the check.


def review_grid():
    sys.path.insert(0, os.path.join(REPO, "dev", "review"))
    import grid as review  # noqa: PLC0415
    import lib as review_lib  # noqa: PLC0415
    import run_grid  # noqa: PLC0415
    import settings_grid  # noqa: PLC0415
    import shapes  # noqa: PLC0415

    # The truth comes from the same place as the truth of each other check.
    review_lib.ch = lambda sql: ch(sql)
    return review, review_lib, run_grid, settings_grid, shapes


def compact_context(ctx):
    if not getattr(ctx, "review_ctx", None):
        review, review_lib, run_grid, _settings, _shapes = review_grid()
        mb = review_lib.Metabase(MB, ctx.mb.password, ADMIN_EMAIL).login()
        ctx.review_mb = mb
        ctx.review_ctx = run_grid.Ctx(mb, ctx.mb.api("GET", "/api/database/%d" % ctx.mb.need_db())["name"])
    return ctx.review_ctx


def run_compact(ctx, shapes_list, channel):
    review, review_lib, run_grid, _settings, _shapes = review_grid()
    rctx = compact_context(ctx)
    mcp = review_lib.Mcp(ctx.review_mb) if channel == "mcp" else None
    results = [run_grid.one(rctx, mcp, sh, channel) for sh in shapes_list]
    counts, unexpected = review.judge(results, "dialect" if DIALECT else "stock", set())
    print("         %d shapes, %d pass, %d wrong, %d no refusal, %d refused, %d on the list of known limits" % (len(results), counts["pass"], counts["wrong"], counts["no refusal"], counts["refused"], counts["known"]))
    if unexpected:
        raise Fail("%d of %d not expected: %s" % (len(unexpected), len(results), " | ".join("%s %s %s" % (r["id"], r["verdict"], str(r.get("diff") or r.get("error") or r.get("got"))[:160]) for r in unexpected[:6])))


def stable_part(items, every, keep=r"^(follow|time/multi)/"):
    """Each shape that came from a finding, and each `every`-th other shape. The choice does not change between runs."""
    return [s for s in items if re.search(keep, s["id"]) or int(hashlib.md5(s["id"].encode()).hexdigest(), 16) % every == 0]


def zone_part(items, settings):
    keep = r"(filter (one date|ge time|two times|not ge|bound in or|equal|hour of day)$|hour-of-day/count|week-of-year/count|rolling revenue_mtd|prior month|stage|model filter|Date|native)"
    return [s for s in items if re.search(keep, s["id"])]


@check("grid.compact.dataset")
def _(ctx):
    """A part of the shapes of the review grid through /api/dataset, each with its truth from ClickHouse."""
    _r, _l, _g, _s, shapes = review_grid()
    run_compact(ctx, stable_part(shapes.shapes(), 8), "dataset")


@check("grid.compact.mcp")
def _(ctx):
    """A smaller part of the same shapes through the MCP tools execute_query and execute_sql."""
    _r, _l, _g, _s, shapes = review_grid()
    run_compact(ctx, [s for s in stable_part(shapes.shapes(), 32, keep=r"^(follow/(eq|case|window_short|shift|not|count_native))")], "mcp")


@check("grid.compact.report_zone")
def _(ctx):
    """Shapes under a report time zone: one with a clock change and one with an offset of part of an hour."""
    _r, _l, _g, settings, _s = review_grid()
    for zone in ("America/New_York", "Asia/Kathmandu"):
        with_report_timezone(ctx, zone, lambda z=zone: run_compact(ctx, zone_part(settings.zone_shapes(z), settings), "dataset"))


@check("grid.compact.mcp_report_zone")
def _(ctx):
    """The same shapes under a report time zone, through MCP."""
    _r, _l, _g, settings, _s = review_grid()
    with_report_timezone(ctx, "America/New_York", lambda: run_compact(ctx, zone_part(settings.zone_shapes("America/New_York"), settings), "mcp"))


@check("grid.compact.start_of_week")
def _(ctx):
    """Week, day of week and week of year for each first day of the week. The truth is the rule of Metabase core, and the grid compares it with Metabase on Postgres."""
    _r, _l, _g, settings, _s = review_grid()
    prev = ctx.mb.api("GET", "/api/setting/start-of-week")
    try:
        for day, n in settings.DAYS.items():
            ctx.mb.api("PUT", "/api/setting/start-of-week", {"value": day})
            run_compact(ctx, [s for s in settings.week_shapes(day, n) if s["id"].endswith(("/count", "first days"))], "dataset")
    finally:
        ctx.mb.api("PUT", "/api/setting/start-of-week", {"value": prev if isinstance(prev, str) and prev else None})


@check("demo.lambda_cube_is_hidden")
def _(ctx):
    """Finding m5: the demo has no table that fails on each query."""
    if "lambda_orders" in ctx.mb.tables:
        raise Fail("the table lambda_orders is public, and each query on it fails")


@check("security.dev_mode_warning_for_hidden_members")
def _(ctx):
    """Finding m6: native SQL reads hidden members in dev mode. The docs tell this, and the check shows why."""
    with open(os.path.join(REPO, "docs/security.md"), encoding="utf-8") as f:
        text = f.read()
    if not re.search(r"(?s)CUBEJS_DEV_MODE.*hidden|hidden.*CUBEJS_DEV_MODE", text):
        raise Fail("docs/security.md has no warning about hidden members in dev mode")
    rows, error = native_outcome(ctx, "SELECT COUNT(*) FROM secret_audit")
    if error or not rows:
        raise Fail("the demo Cube has dev mode on, and the query must read the hidden cube: %s" % error)


@check("security.validate_echoes_the_request_limit")
def _(ctx):
    """Finding m7, a limit of Metabase: this admin endpoint repeats the request body for each driver. A stored value stays masked."""
    secret = CUBE_DB_DETAILS["cube-api-secret-value"]
    status, text, _h = http("POST", MB + "/api/database/validate", {"details": {"engine": "cube", "details": CUBE_DB_DETAILS}}, {"X-Metabase-Session": ctx.mb.sid})
    if status != 200:
        raise Fail("HTTP %s" % status)
    stored = json.dumps(ctx.mb.api("GET", "/api/database/%d" % ctx.mb.need_db()))
    if secret in stored or CUBE_DB_DETAILS["password"] in stored:
        raise Fail("a read of the stored database shows a secret")
    if secret not in text:
        raise Fail("Metabase no longer repeats the request. Give this entry its full status")


def package_with_broken_jar(tmp):
    """Finding m9: copy what the script needs, and put a `jar` on the PATH that fails, as the stub on a Mac with no Java does."""
    for name in ("scripts", "src", "resources"):
        subprocess.run(["cp", "-R", os.path.join(REPO, name), tmp], check=True)
    fake = os.path.join(tmp, "fakebin")
    os.mkdir(fake)
    with open(os.path.join(fake, "jar"), "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\necho 'Unable to locate a Java Runtime' >&2\nexit 1\n")
    os.chmod(os.path.join(fake, "jar"), 0o755)
    env = dict(os.environ, PATH=fake + os.pathsep + os.environ["PATH"])
    return subprocess.run(["sh", os.path.join(tmp, "scripts", "package.sh")], capture_output=True, text=True, env=env, timeout=120)


@check("build.package_with_no_java")
def _(ctx):
    import tempfile
    import zipfile
    with tempfile.TemporaryDirectory() as tmp:
        p = package_with_broken_jar(tmp)
        out = os.path.join(tmp, "dev", "plugins", "cube.metabase-driver.jar")
        if p.returncode != 0 or not os.path.exists(out):
            raise Fail("package.sh failed with a jar stub: %s" % (p.stderr[-200:]))
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
        if "metabase-plugin.yaml" not in names or "metabase/driver/cube.clj" not in names:
            raise Fail("the jar has no manifest or driver at its root: %s" % names[:6])
    with open(os.path.join(REPO, "CONTRIBUTING.md"), encoding="utf-8") as f:
        if "Java" not in f.read():
            raise Fail("CONTRIBUTING.md does not list Java")


@check("build.load_check_sends_the_api_secret")
def _(ctx):
    """Finding m10: the load check sends the key that the driver reads, and it takes its address from the environment."""
    with open(os.path.join(REPO, ".github/scripts/load_check.py"), encoding="utf-8") as f:
        text = f.read()
    if '"cube-api-secret-value"' not in text:
        raise Fail("the load check does not send cube-api-secret-value")
    if "METABASE_URL" not in text:
        raise Fail("the address of the load check is fixed")


@check("docs.query_compilation_agrees_with_code")
def _(ctx):
    """Finding m11."""
    with open(os.path.join(REPO, "docs/query-compilation.md"), encoding="utf-8") as f:
        text = f.read()
    for wrong in ("A left join from `products` to `orders` drops products",
                  "The first two checks read the database type of the field, so they work on the query builder and on the MCP tool `execute_query`. They cannot read native SQL.",
                  "A rolling window with `trailing: unbounded` fails on ClickHouse"):
        if wrong in text:
            raise Fail("docs/query-compilation.md still has: %s" % wrong)
    expect_clear_error(ctx, ctx.mb.inner("products", agg=[["count"]], joins=[{"source-table": ctx.mb.table(ORD)["id"], "alias": "O", "strategy": "left-join", "condition": ["=", "d:products.id", "d:orders.product_id|O"]}]), "join path")


# More shapes of B2, B3 and B4, with a truth or a refusal for each.

MULTI_STAGE_REFUSAL = "cannot"


@check("nested.model.multi_stage_measure_column")
def _(ctx):
    """A multi-stage measure or a measure with an aggregate in its SQL, as a column of a model: the truth or a refusal. Never another number."""
    model = card_of(ctx, "multi stage model", ctx.mb.inner(ORD, fields=["d:orders.status", "d:orders.created_at", "d:orders.revenue_share_of_total", "d:orders.revenue_rank",
                                                                         "d:orders.revenue_all_statuses", "d:orders.revenue_prior_month", "d:orders.paid_share"]), "model")
    status = named("status", "type/Text")
    by_month = named("created_at", "type/DateTime", **{"temporal-unit": "month"})
    grid = Grid(ctx, refusal=MULTI_STAGE_REFUSAL)
    totals = "(SELECT status, toFloat64(sum(amount)) AS r FROM orders GROUP BY status)"
    grid.add("share by status", over(model, aggregation=[["max", named("revenue_share_of_total")]], breakout=[status]),
             "SELECT status, r / (SELECT sum(r) FROM %s) FROM %s" % (totals, totals))
    grid.add("rank by status", over(model, aggregation=[["max", named("revenue_rank")]], breakout=[status]),
             "SELECT status, rank() OVER (ORDER BY r DESC) FROM %s" % totals)
    grid.add("all statuses", over(model, aggregation=[["max", named("revenue_all_statuses")]], breakout=[status]),
             "SELECT status, r FROM %s" % totals)
    grid.add("prior month", over(model, aggregation=[["sum", named("revenue_prior_month")]], breakout=[by_month], filter=["and", [">=", named("created_at", "type/DateTime"), "2025-04-01"], ["<", named("created_at", "type/DateTime"), "2025-06-01"]]),
             "SELECT addMonths(toStartOfMonth(created_at), 1) AS m, sum(amount) FROM orders WHERE m >= '2025-04-01' AND m < '2025-06-01' GROUP BY m")
    grid.add("paid share", over(model, aggregation=[["max", named("paid_share")]]),
             "SELECT sumIf(toFloat64(amount), status IN ('completed','shipped')) / sum(toFloat64(amount)) FROM orders")
    grid.done()


@check("nested.question.multi_stage_outer_date_filter")
def _(ctx):
    """A filter on a saved question with a time shift or a rolling measure keeps the months of the question."""
    grid = Grid(ctx)
    col = named("created_at", "type/DateTime")
    prior = card_of(ctx, "prior month question", ctx.mb.inner(ORD, agg=["m:orders.revenue_prior_month"], by=MONTH, where=[[">=", CRT, "2025-04-01"], ["<", CRT, "2025-09-01"]]))
    grid.add("prior month", over(prior, filter=[">=", col, "2025-06-15"]),
             "SELECT addMonths(toStartOfMonth(created_at), 1) AS m, sum(amount) FROM orders WHERE m > '2025-06-15' AND m < '2025-09-01' GROUP BY m")
    mtd = card_of(ctx, "mtd question", ctx.mb.inner(ORD, agg=["m:orders.revenue_mtd"], by=MONTH, where=[[">=", CRT, "2025-04-01"], ["<", CRT, "2025-09-01"]]))
    grid.add("month to date", over(mtd, filter=["<", col, "2025-07-15"]),
             "SELECT toStartOfMonth(created_at) AS m, sum(amount) FROM orders WHERE m >= '2025-04-01' AND m < '2025-07-15' GROUP BY m")
    grid.done()


@check("nested.model.measure_column_bounds")
def _(ctx):
    """A model with a column of a joined cube, then a date bound or a filter on that column, by name."""
    model = card_of(ctx, "joined model", ctx.mb.inner(ORD, fields=["d:orders.id", "d:orders.amount_dim", "d:orders.created_at", "d:orders.revenue"]), "model")
    grid = Grid(ctx)
    col = named("created_at", "type/DateTime")
    for label, where, cond in bound_filters(col):
        grid.add(label, over(model, aggregation=[["sum", named("revenue")], ["count"]], filter=where),
                 "SELECT sum(amount), count() FROM orders WHERE %s" % cond.format(c="created_at"))
    grid.done()


@check("nested.model.relative_and_bucket_filters")
def _(ctx):
    """Relative dates and a filter on a bucket, by column name, on a model. The bucket value can be inside the bucket."""
    model = raw_model(ctx)
    col = named("created_at", "type/DateTime")
    month = named("created_at", "type/DateTime", **{"temporal-unit": "month"})
    grid = Grid(ctx)
    grid.add("relative days", over(model, aggregation=[["count"]], filter=["time-interval", col, -PAST_DAYS, "day"]),
             "SELECT count() FROM orders WHERE created_at >= today() - %d AND created_at < today()" % PAST_DAYS)
    grid.add("relative months", over(model, aggregation=[["count"]], filter=["time-interval", col, -30, "month"]),
             "SELECT count() FROM orders WHERE created_at >= addMonths(toStartOfMonth(now('UTC')), -30) AND created_at < toStartOfMonth(now('UTC'))")
    grid.add("= month", over(model, aggregation=[["count"]], filter=["=", month, "2025-05-01"]),
             "SELECT count() FROM orders WHERE toStartOfMonth(created_at) = '2025-05-01'")
    grid.add("> month", over(model, aggregation=[["count"]], filter=[">", month, "2025-05-01"]),
             "SELECT count() FROM orders WHERE toStartOfMonth(created_at) > '2025-05-01'")
    grid.add("<= month", over(model, aggregation=[["count"]], filter=["<=", month, "2025-05-01"]),
             "SELECT count() FROM orders WHERE toStartOfMonth(created_at) <= '2025-05-01'")
    grid.add("not null", over(model, aggregation=[["count"]], filter=["not-null", col]), "SELECT count() FROM orders")
    grid.add("is null", over(model, aggregation=[["count"]], filter=["is-null", col]), "SELECT count() FROM orders WHERE created_at IS NULL")
    grid.done()


@check("compile.bound.relative_strict")
def _(ctx):
    """`>` with a relative date, alone and with an upper bound. The truth uses the same start of the day."""
    start = "toDateTime(today() - 300, 'UTC')"
    end = "toDateTime(today() - 200, 'UTC')"
    grid = Grid(ctx)
    relative = lambda n: ["relative-datetime", n, "day"]
    grid.add("alone", ctx.mb.inner(ORD, agg=["m:orders.count"], where=[[">", CRT, relative(-300)]]),
             "SELECT count() FROM orders WHERE created_at > %s" % start)
    grid.add("two bounds", ctx.mb.inner(ORD, agg=["m:orders.count"], where=[[">", CRT, relative(-300)], ["<=", CRT, relative(-200)]]),
             "SELECT count() FROM orders WHERE created_at > %s AND created_at <= %s" % (start, end))
    grid.done()


@check("compile.case.date_bounds_in_an_aggregation")
def _(ctx):
    """A date bound inside count-where and sum-where, where Cube makes no date range."""
    x, y = "2025-03-15T05:00:00", "2025-03-16T05:00:00"
    cond = ["and", [">", CRT, x], ["<=", CRT, y]]
    expect_rows(ctx, ctx.mb.inner(ORD, agg=[["count-where", cond], ["sum-where", "d:orders.amount_dim", cond]]),
                "SELECT countIf(created_at > '2025-03-15 05:00:00' AND created_at <= '2025-03-16 05:00:00'), sumIf(amount, created_at > '2025-03-15 05:00:00' AND created_at <= '2025-03-16 05:00:00') FROM orders", "where")


@check("nested.measure_column.expression")
def _(ctx):
    """An expression of a measure column gives the truth or a refusal, on a model and in a condition of an aggregation."""
    model = raw_model(ctx)
    revenue = named("revenue")
    grid = Grid(ctx, refusal="Cube cannot")
    grid.add("sum of an expression", over(model, aggregation=[["sum", ["+", revenue, 1]]]), "SELECT sum(amount + 1) FROM orders")
    grid.add("expression of the sum", over(model, aggregation=[["+", ["sum", revenue], 1]]), "SELECT sum(amount) + 1 FROM orders")
    grid.add("count where", over(model, aggregation=[["count-where", [">", revenue, 150]]]), "SELECT countIf(amount > 150) FROM orders")
    grid.add("sum where", over(model, aggregation=[["sum-where", named("amount_dim"), [">", revenue, 150]]]), "SELECT sumIf(amount, amount > 150) FROM orders")
    grid.add("table count where", ctx.mb.inner(ORD, agg=[["count-where", [">", "d:orders.revenue", 150]]]), "SELECT countIf(amount > 150) FROM orders")
    grid.done()


@check("nested.model.segment_column")
def _(ctx):
    """A segment column of a model: a filter by name is the segment, or a refusal. A breakout is a refusal."""
    model = card_of(ctx, "segment model", ctx.mb.inner(ORD, fields=["d:orders.id", "d:orders.status", "d:orders.amount_dim", "d:orders.paid"]), "model")
    paid = named("paid", "type/Boolean")
    grid = Grid(ctx, refusal="Cube cannot")
    grid.add("filter", over(model, aggregation=[["sum", named("amount_dim")]], filter=["=", paid, True]), "SELECT sum(amount) FROM orders WHERE status IN ('completed','shipped')")
    grid.done()
    expect_clear_error(ctx, over(model, aggregation=[["count"]], breakout=[paid]), "Cube cannot group by the segment")


@check("nested.question_on_question.date_filter")
def _(ctx):
    """Three stages: a question on a question, each with a filter."""
    first = card_of(ctx, "stage one", ctx.mb.inner(ORD, agg=["m:orders.revenue"], by=["d:orders.created_at@day"], where=[["=", STA, "shipped"]]))
    second = card_of(ctx, "stage two", over(first, aggregation=[["sum", named("sum")]], breakout=[named("created_at", "type/DateTime", **{"temporal-unit": "month"})],
                                            filter=[">=", named("created_at", "type/DateTime"), "2025-02-15"]))
    expect_rows(ctx, over(second, filter=["<", named("created_at", "type/DateTime"), "2025-05-15"]),
                "SELECT toStartOfMonth(d) AS m, sum(r) FROM (SELECT toStartOfDay(created_at) d, sum(amount) r FROM orders WHERE status = 'shipped' GROUP BY d) "
                "WHERE d >= '2025-02-15' GROUP BY m HAVING m < '2025-05-15'", "three stages")


@check("preagg.rollup_lambda")
def _(ctx):
    """Inside the range of the rollup, the lambda gives the truth. With a range that reads source rows, ClickHouse fails, and the driver tells this."""
    ensure_table(ctx, "lambda_sales")
    inside = "SELECT status, toStartOfMonth(created_at), sum(amount) FROM orders WHERE created_at >= '2025-02-01' AND created_at < '2025-03-01' GROUP BY 1, 2"
    used_rollup({"measures": ["lambda_sales.revenue"], "dimensions": ["lambda_sales.status"],
                 "timeDimensions": [{"dimension": "lambda_sales.created_at", "granularity": "month", "dateRange": ["2025-02-01", "2025-02-28"]}]}, "lambda")(ctx, None)
    expect_rows(ctx, ctx.mb.inner("lambda_sales", agg=["m:lambda_sales.revenue"], by=["d:lambda_sales.status", "d:lambda_sales.created_at@month"],
                                  where=[[">=", "d:lambda_sales.created_at", "2025-02-01"], ["<", "d:lambda_sales.created_at", "2025-03-01"]]), inside, "inside the rollup")
    # On ClickHouse, the whole range fails or not with the state of the rollup. So this is the truth, or the clear message.
    grid = Grid(ctx, refusal="Cube cannot read a rollup_lambda")
    grid.add("whole range", ctx.mb.inner("lambda_sales", agg=["m:lambda_sales.revenue"], by=["d:lambda_sales.status"]), "SELECT status, sum(amount) FROM orders GROUP BY status")
    grid.done()


@check("preagg.rollup_join")
def _(ctx):
    ensure_table(ctx, "join_sales")
    query = ctx.mb.inner("join_sales", agg=["m:join_sales.revenue"], by=["d:join_sales>join_customers.state"])
    expect_rows(ctx, query, "SELECT u.state, sum(o.amount) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1", "join")
    # Cube names the rollups of the rollup_join in the result.
    used_rollup({"measures": ["join_sales.revenue"], "dimensions": ["join_customers.state"]}, "customers_rollup")(ctx, None)


Q("semantic.measure.min_on_time_column", ORD, "SELECT min(created_at), max(created_at) FROM orders", agg=["m:orders.first_order_at", "m:orders.last_order_at"])


@check("compile.expr.measure_with_expression_breakout")
def _(ctx):
    """A breakout on an expression makes a subquery of rows. Sum, avg, min and max of the rows are right, and a distinct count stops."""
    big = {"k": ["case", [[[">", QTY, 2], "big"]], {"default": "small"}]}
    key = "if(quantity > 2, 'big', 'small')"
    grid = Grid(ctx)
    for measure, expr in (("revenue", "sum(amount)"), ("count", "count()"), ("avg_order_value", "avg(amount)"), ("min_amount", "min(amount)"), ("max_amount", "max(amount)")):
        grid.add(measure, ctx.mb.inner(ORD, agg=["m:orders.%s" % measure], by=[["expression", "k"]], exprs=big), "SELECT %s AS k, %s FROM orders GROUP BY k" % (key, expr))
    grid.done()
    expect_clear_error(ctx, ctx.mb.inner(ORD, agg=["m:orders.unique_customers"], by=[["expression", "k"]], exprs=big), "do not add up")


# ----------------------------------------------------------------- the ClickHouse dialect


@check("dialect.variant")
def _(ctx):
    """The stack is the kind of Cube that this run says. Without this, a run on the wrong stack reports a false result."""
    query = urllib.parse.quote(json.dumps({"dimensions": ["orders.status"],
                                           "timeDimensions": [{"dimension": "orders.created_at", "dateRange": ["2025-01-01", "2025-01-02"]}], "limit": 1}))
    _s, text, _h = http("GET", CUBE + "/cubejs-api/v1/sql?query=" + query)
    has_fix = "parseDateTime64BestEffort" in text
    if has_fix != DIALECT:
        raise Fail("this run is for %s, and Cube writes: %s" % (VARIANT_NAME, text[:300]))


_B1_TRUTH = "SELECT count() FROM orders WHERE created_at > '2025-03-15 05:00:00' AND created_at <= '2025-03-16 05:00:00'"


@check("dialect.b1_strict_lower_bound")
def _(ctx):
    """B1. The driver keeps the query builder right on each Cube. With the dialect, native SQL runs, and on stock Cube the driver stops it."""
    query = ctx.mb.inner(ORD, agg=["m:orders.count"], where=[[">", CRT, "2025-03-15T05:00:00"], ["<=", CRT, "2025-03-16T05:00:00"]])
    expect_rows(ctx, query, _B1_TRUTH, "query builder")
    sql = "SELECT MEASURE(count) FROM orders WHERE created_at > '2025-03-15 05:00:00' AND created_at <= '2025-03-16 05:00:00'"
    if DIALECT:
        expect_native_rows(ctx, sql, _B1_TRUTH)
    else:
        expect_native_refusal(ctx, sql, "next second")


@check("dialect.interval_in_native_sql")
def _(ctx):
    """interval. Stock Cube fails this with a message, and the dialect has the template."""
    sql = "SELECT created_at + INTERVAL '1 day' AS d, MEASURE(count) AS n FROM orders WHERE id <= 3 GROUP BY 1 ORDER BY 1"
    truth = "SELECT created_at + INTERVAL 1 DAY AS d, count() FROM orders WHERE id <= 3 GROUP BY d ORDER BY d"
    if DIALECT:
        expect_native_rows(ctx, sql, truth)
    else:
        expect_native_refusal(ctx, sql, "interval")


@check("dialect.n2_legacy_planner")
def _(ctx):
    """N2. The service cube-legacy has the legacy planner. Stock Cube gives each bucket the same wrong number, and the dialect gives the truth."""
    now = int(time.time())
    def b64(x):
        return base64.urlsafe_b64encode(x).rstrip(b"=").decode()
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"iat": now, "exp": now + 600}).encode())
    signature = b64(hmac.new(CUBE_LEGACY_SECRET.encode(), (head + "." + body).encode(), hashlib.sha256).digest())
    query = urllib.parse.quote(json.dumps({"measures": ["orders.revenue_trailing_30d"], "order": {"orders.created_at": "asc"},
                                           "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month", "dateRange": ["2025-02-01", "2025-03-31"]}]}))
    deadline = time.time() + 120
    while True:
        status, text, _h = http("GET", CUBE_LEGACY + "/cubejs-api/v1/load?query=" + query, headers={"Authorization": head + "." + body + "." + signature})
        data = json.loads(text) if text.startswith("{") else {}
        if status == 200 and data.get("error") != "Continue wait":
            break
        if time.time() > deadline:
            raise Fail("cube-legacy gave no result: HTTP %s %s" % (status, text[:300]))
        time.sleep(2)
    got = [float(r["orders.revenue_trailing_30d"]) for r in data.get("data", [])]
    truth = [float(r[1]) for r in ch("SELECT m, sumIf(o.amount, o.created_at >= addDays(e, -30) AND o.created_at <= e) FROM "
                                     "(SELECT toDateTime(addMonths(toDate('2025-02-01'), toInt64(number)), 'UTC') m, toDateTime(toLastDayOfMonth(m), 'UTC') + INTERVAL 86399 SECOND e FROM numbers(2)) d "
                                     "CROSS JOIN orders o GROUP BY m, e ORDER BY m")]
    close = len(got) == len(truth) and all(abs(a - b) < 0.01 for a, b in zip(got, truth))
    if DIALECT and not close:
        raise Fail("with the dialect: %s, truth %s" % (got, truth))
    if not DIALECT and close:
        raise Fail("stock Cube now gives the truth on the legacy planner. Give this entry its full status")
    # Finding M4. /v1/sql shows the planner, so the driver stops this query on stock Cube. With the dialect it gives the truth.
    details = {"host": os.environ.get("CUBE_LEGACY_SQL_HOST", "cube-legacy"), "port": 15432, "dbname": "cube", "user": "cube", "password": "cube-dev-only",
               "cube-rest-url": os.environ.get("CUBE_LEGACY_REST_INTERNAL_URL", "http://cube-legacy:4000"), "cube-api-secret-value": CUBE_LEGACY_SECRET, "ssl": False}
    db = ctx.mb.api("POST", "/api/database", {"engine": "cube", "name": "Cube legacy planner", "details": details})
    sql = ("SELECT DATE_TRUNC('month', created_at), MEASURE(revenue_trailing_30d) FROM orders "
           "WHERE created_at >= '2025-02-01' AND created_at < '2025-04-01' GROUP BY 1")
    try:
        res = ctx.mb.api("POST", "/api/dataset", {"database": db["id"], "type": "native", "native": {"query": sql, "template-tags": {}}}, ok=(200, 202, 400, 403, 500))
        error = res.get("error") or res.get("message")
        if DIALECT:
            if error:
                raise Fail("native on the legacy planner with the dialect: %s" % str(error)[:300])
            assert_close = [float(r[1]) for r in res["data"]["rows"]]
            if len(assert_close) != len(truth) or any(abs(a - b) >= 0.01 for a, b in zip(assert_close, truth)):
                raise Fail("native on the legacy planner with the dialect: %s, truth %s" % (assert_close, truth))
        elif "CUBEJS_TESSERACT_SQL_PLANNER" not in str(error):
            raise Fail("stock Cube on the legacy planner did not stop: %s" % str(error or res["data"]["rows"])[:300])
    finally:
        ctx.mb.api("DELETE", "/api/database/%d" % db["id"])


@check("preagg.original_sql")
def _(ctx):
    """m5. The dialect builds an original_sql pre-aggregation and a rollup over it. Stock Cube stops each form on ClickHouse with its own error."""
    sql = "SELECT status, MEASURE(revenue) FROM original_sql_orders GROUP BY 1"
    truth = "SELECT status, sum(amount) FROM orders GROUP BY status"
    if DIALECT:
        expect_native_rows(ctx, sql, truth)
        used_rollup({"measures": ["original_sql_orders.revenue"], "dimensions": ["original_sql_orders.status"]}, "base")(ctx, None)
        expect_native_rows(ctx, "SELECT status, MEASURE(revenue) FROM lambda_orders GROUP BY 1", truth)
        used_rollup({"measures": ["lambda_orders.revenue"], "dimensions": ["lambda_orders.status"]}, "daily")(ctx, None)
    else:
        expect_native_refusal(ctx, sql, "Missing columns")
        rows, error = native_outcome(ctx, "SELECT status, MEASURE(revenue) FROM lambda_orders GROUP BY 1", None, None)
        if error is None:
            raise Fail("a rollup over original_sql now works on stock Cube: %s" % rows[:3])


# ----------------------------------------------------------------- delivery by mail


def mail_messages():
    status, text, _h = http("GET", MAIL_URL + "/api/v1/messages?limit=200")
    if status != 200:
        raise Fail("the mail catcher: HTTP %s" % status)
    return json.loads(text).get("messages") or []


def mail_text(message_id):
    status, text, _h = http("GET", MAIL_URL + "/api/v1/message/" + message_id)
    if status != 200:
        raise Fail("the mail catcher: HTTP %s" % status)
    message = json.loads(text)
    return (message.get("Text") or "") + "\n" + (message.get("HTML") or "")


def wait_for_mail(address, known, needle=None, seconds=120):
    """The text of a new message to `address`. `known` has the ids of the messages from before."""
    deadline = time.time() + seconds
    while True:
        for m in mail_messages():
            # Metabase sends to many people with Bcc.
            if m["ID"] not in known and any(t.get("Address") == address for t in (m.get("To") or []) + (m.get("Bcc") or [])):
                text = mail_text(m["ID"])
                if needle is None or needle in text:
                    return text
        if time.time() > deadline:
            raise Fail("no mail to %s in %d s" % (address, seconds))
        time.sleep(2)


def number_in_mail(text, number):
    plain = re.sub(r"<[^>]+>", " ", text).replace(",", "")
    wanted = float(number)
    return any(abs(float(found) - wanted) < 0.5 for found in re.findall(r"\d+(?:\.\d+)?", plain))


@check("mcp.alert_write")
def _(ctx):
    """An agent makes an alert on a question through MCP. Metabase runs the question through the driver and mails the value of Cube."""
    known = {m["ID"] for m in mail_messages()}
    address = "alert-%s@example.com" % secrets.token_hex(4)
    card = ctx.mb.mbql_card("mcp alert", ORD, agg=["m:orders.count"], where=[["=", STA, "shipped"]])
    res = ctx.mcp.call("alert_write", method="create", card_id=card["id"], recipients=[address], channel="email",
                       schedule={"schedule_type": "daily", "schedule_hour": 3})
    alert_id = deep_find(res, "id")
    ctx.mb.api("POST", "/api/notification/%d/send" % alert_id, {}, ok=(200, 202, 204))
    text = wait_for_mail(address, known)
    truth = ch("SELECT count() FROM orders WHERE status = 'shipped'")[0][0]
    if not number_in_mail(text, truth):
        raise Fail("the mail has no %s: %s" % (truth, re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))[:500]))
    ctx.mcp.call("alert_write", method="update", id=alert_id, active=False)


@check("mcp.subscription_write")
def _(ctx):
    """An agent subscribes a person to a dashboard through MCP. Metabase runs the card through the driver and mails the value of Cube."""
    known = {m["ID"] for m in mail_messages()}
    address = "subscription-%s@example.com" % secrets.token_hex(4)
    card = ctx.mb.mbql_card("mcp subscription", ORD, agg=["m:orders.count"], where=[["=", STA, "completed"]])
    made = ctx.mcp.call("dashboard_write", method="create", name="conformance mcp subscription", collection_id=ctx.mb.collection(), ops=[
        {"op": "add_card", "id": -1, "card_id": card["id"], "tab": None, "position": None, "size": None, "series": None, "inline_parameters": None}])
    dash_id = deep_find(made, "id")
    res = ctx.mcp.call("subscription_write", method="create", dashboard_id=dash_id, recipients=[address], channel="email",
                       schedule={"schedule_type": "daily", "schedule_hour": 3})
    pulse_id = deep_find(res, "id")
    pulse = ctx.mb.api("GET", "/api/pulse/%d" % pulse_id)
    ctx.mb.api("POST", "/api/pulse/test", pulse, ok=(200, 202, 204))
    text = wait_for_mail(address, known)
    truth = ch("SELECT count() FROM orders WHERE status = 'completed'")[0][0]
    if not number_in_mail(text, truth):
        raise Fail("the mail has no %s: %s" % (truth, re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))[:500]))
    ctx.mcp.call("subscription_write", method="update", id=pulse_id, archived=True)


# ----------------------------------------------------------------- MCP tools for content


@check("mcp.collection_write")
def _(ctx):
    """An agent makes a collection and puts a question in it. The question in the collection gives the value of Cube."""
    made = ctx.mcp.call("collection_write", method="create", name="conformance mcp collection %s" % secrets.token_hex(3), description="A collection of an agent.")
    collection_id = deep_find(made, "id")
    res = ctx.mcp.call("question_write", method="create", name="conformance mcp in collection", collection_id=collection_id,
                       query=mcp_query(ctx.mb, ORD, agg=["m:orders.revenue"], by=[STA]))
    card_id = deep_find(res, "id")
    if ctx.mb.api("GET", "/api/card/%d" % card_id).get("collection_id") != collection_id:
        raise Fail("the question is not in the collection")
    assert_rows(ctx.mcp.call("run_saved_question", id=card_id)["rows"], ch(STATUS_REV))
    ctx.mcp.call("collection_write", method="update", id=collection_id, archived=True)


@check("mcp.bookmark_content")
def _(ctx):
    """An agent bookmarks a question. Metabase lists it for this person, and the question gives the value of Cube."""
    card = ctx.mb.mbql_card("mcp bookmark", ORD, agg=["m:orders.revenue"], by=[STA])
    ctx.mcp.call("bookmark_content", type="question", id=card["id"], bookmarked=True)
    marks = ctx.mb.api("GET", "/api/bookmark")
    if not any(m.get("item_id") == card["id"] and m.get("type") == "card" for m in marks):
        raise Fail("the question is not in the bookmarks: %s" % str(marks)[:300])
    assert_rows(ctx.mcp.call("run_saved_question", id=card["id"])["rows"], ch(STATUS_REV))
    ctx.mcp.call("bookmark_content", type="question", id=card["id"], bookmarked=False)
    if any(m.get("item_id") == card["id"] and m.get("type") == "card" for m in ctx.mb.api("GET", "/api/bookmark")):
        raise Fail("the bookmark did not go away")


@check("mcp.document_write")
def _(ctx):
    """An agent writes a document with a card of a question. The card in the document gives the value of Cube."""
    card = ctx.mb.mbql_card("mcp document", ORD, agg=["m:orders.count"], by=[STA])
    body = "# Orders\n\nOrders by status.\n\n{%% card id=%d %%}\n" % card["id"]
    made = ctx.mcp.call("document_write", method="create", name="conformance mcp document", content_markdown=body, collection_id=ctx.mb.collection())
    doc_id = deep_find(made, "id")
    doc = ctx.mb.api("GET", "/api/document/%d" % doc_id)
    text = json.dumps(doc.get("document"))
    # Metabase can give the document its own copy of the card, so this runs the card that the document names.
    ids = [int(i) for i in re.findall(r'"type": "cardEmbed", "attrs": \{[^{}]*?"id": (\d+)', text)]
    if not ids:
        raise Fail("the document has no card: %s" % text[:600])
    assert_rows(ctx.mb.run_card(ids[0])["rows"], ch(STATUS_CNT))


@check("mcp.glossary")
def _(ctx):
    """An agent reads the term of the analysts, and the measure that the term names gives the value of Cube."""
    term = "conformance revenue %s" % secrets.token_hex(3)
    definition = "Sum of order amounts: the measure revenue of the table orders."
    made = ctx.mb.api("POST", "/api/glossary", {"term": term, "definition": definition})
    try:
        res = ctx.mcp.rpc("tools/call", {"name": "glossary", "arguments": {"term": term.upper()}})
        if definition not in json.dumps(res):
            raise Fail("the glossary has no definition: %s" % str(res)[:300])
        mcp_rows(ctx, ORD, REV, agg=["m:orders.revenue"])
    finally:
        ctx.mb.api("DELETE", "/api/glossary/%d" % made["id"], ok=(200, 204))


@check("mcp.learn")
def _(ctx):
    """The skill text teaches the query dialect. A query in that dialect on a Cube table gives the value of Cube."""
    catalog = ctx.mcp.call("learn")
    if "query" not in json.dumps(catalog).lower():
        raise Fail("the catalog has no topic on queries: %s" % str(catalog)[:300])
    topics = re.findall(r'[a-z][a-z_-]*query[a-z_-]*|query[a-z_-]*', json.dumps(catalog))
    if not topics:
        raise Fail("no topic name in the catalog: %s" % str(catalog)[:300])
    text = ""
    for topic in dict.fromkeys(topics):
        res = ctx.mcp.rpc("tools/call", {"name": "learn", "arguments": {"topic": topic}})
        if not res.get("isError"):
            text = json.dumps(res)
            break
    if "source-table" not in text and "aggregation" not in text:
        raise Fail("no topic teaches the dialect: %s" % text[:300])
    mcp_rows(ctx, ORD, STATUS_CNT, agg=["m:orders.count"], by=[STA])


def embed_query_rows(ctx, handle):
    """The query behind a handle of an MCP App, by the same route as its iframe, and the rows of that query."""
    headers = {"Mcp-Session-Id": ctx.mcp.with_ui().headers["Mcp-Session-Id"], "Cookie": "metabase.SESSION=" + ctx.mb.sid}
    status, text, _h = http("GET", MB + "/api/embed-mcp/queries/%s" % handle, headers=headers)
    if status != 200:
        raise Fail("the query of the handle: HTTP %s %s" % (status, text[:300]))
    query = json.loads(base64.b64decode(json.loads(text)["query"]))
    return ctx.mb.api("POST", "/api/dataset", query)["data"]["rows"]


@check("mcp.visualize_query")
def _(ctx):
    """visualize_query on a session of a client with MCP Apps. The query of the chart gives the value of Cube."""
    ui = ctx.mcp.with_ui()
    names = [t["name"] for t in ui.rpc("tools/list").get("tools", [])]
    if "visualize_query" not in names:
        raise Fail("tools/list has no visualize_query: %s" % names)
    res = ui.call("visualize_query", query=mcp_query(ctx.mb, ORD, agg=["m:orders.revenue"], by=[STA]))
    handle = deep_find(res, "query_handle") if isinstance(res, (dict, list)) else None
    if not handle:
        raise Fail("no query_handle: %s" % str(res)[:300])
    assert_rows(embed_query_rows(ctx, handle), ch(STATUS_REV))


@check("mcp.render_drill_through")
def _(ctx):
    """render_drill_through takes the handle of a query that the person drilled into. Its query gives the value of Cube."""
    ui = ctx.mcp.with_ui()
    data = ui.call("execute_query", query=mcp_query(ctx.mb, ORD, agg=["m:orders.count"], where=[["=", STA, "shipped"]]))
    res = ui.call("render_drill_through", query_handle=data["query_handle"])
    handle = deep_find(res, "query_handle") if isinstance(res, (dict, list)) else None
    if handle != data["query_handle"]:
        raise Fail("another handle: %s" % str(res)[:300])
    assert_rows(embed_query_rows(ctx, handle), ch(SHIPPED))


# ----------------------------------------------------------------- the browser


class Browser:
    """A small client of the Chrome DevTools Protocol, with the standard library only."""

    def __init__(self):
        status, text, _h = http("PUT", BROWSER_URL + "/json/new?about:blank")
        if status != 200:
            raise Fail("browser: HTTP %s %s" % (status, text[:200]))
        target = json.loads(text)
        self.target_id = target["id"]
        parsed = urllib.parse.urlparse(BROWSER_URL)
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout=60)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        path = "/devtools/page/" + self.target_id
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: %s:%s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, parsed.hostname, parsed.port, key)).encode())
        reply = b""
        while b"\r\n\r\n" not in reply:
            reply += self.sock.recv(4096)
        if b" 101 " not in reply.split(b"\r\n")[0]:
            raise Fail("browser websocket: %s" % reply[:200])
        self.buffer = reply.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    def _read(self, n):
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise Fail("the browser closed the connection")
            self.buffer += chunk
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def _frame(self):
        text = b""
        while True:
            first, second = self._read(2)
            size = second & 127
            if size == 126:
                size = struct.unpack(">H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._read(8))[0]
            text += self._read(size)
            if first & 0x80:
                return text

    def send(self, method, **params):
        self.next_id += 1
        payload = json.dumps({"id": self.next_id, "method": method, "params": params}).encode()
        mask = secrets.token_bytes(4)
        size = len(payload)
        head = bytes([0x81])
        if size < 126:
            head += bytes([0x80 | size])
        elif size < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", size)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", size)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        while True:
            message = json.loads(self._frame())
            if message.get("id") == self.next_id:
                if "error" in message:
                    raise Fail("browser %s: %s" % (method, message["error"]))
                return message.get("result", {})

    def js(self, expression):
        result = self.send("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in result:
            raise Fail("browser script: %s" % str(result["exceptionDetails"])[:300])
        return result["result"].get("value")

    def wait(self, expression, what, seconds=90):
        deadline = time.time() + seconds
        while True:
            value = self.js(expression)
            if value:
                return value
            if time.time() > deadline:
                raise Fail("browser: no %s in %d s. Page: %s" % (what, seconds, str(self.js("document.body.innerText"))[:400]))
            time.sleep(1)

    def click_at(self, x, y):
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left" if kind != "mouseMoved" else "none", clickCount=1 if kind != "mouseMoved" else 0)

    def close(self):
        self.sock.close()
        http("GET", BROWSER_URL + "/json/close/" + self.target_id)


@check("mb.other.drill_through_ui")
def _(ctx):
    """A person clicks a number in a table of a question and picks the drill-through. The rows that Metabase shows are the rows of ClickHouse."""
    card = ctx.mb.mbql_card("ui drill", ORD, agg=["m:orders.count"], by=[STA])
    browser = Browser()
    try:
        browser.send("Page.enable")
        browser.send("Network.enable")
        host = urllib.parse.urlparse(METABASE_INTERNAL).hostname
        browser.send("Network.setCookie", name="metabase.SESSION", value=ctx.mb.sid, domain=host, path="/", httpOnly=True)
        browser.send("Emulation.setDeviceMetricsOverride", width=1400, height=1000, deviceScaleFactor=1, mobile=False)
        browser.send("Page.navigate", url="%s/question/%d" % (METABASE_INTERNAL, card["id"]))
        browser.wait("document.querySelector('[data-testid=\"cell-data\"]') !== null", "cells of the table")
        find_cell = """(() => {
            const cells = [...document.querySelectorAll('[data-testid="cell-data"]')];
            const i = cells.findIndex(c => c.innerText.trim() === 'shipped');
            if (i < 0) return null;
            const r = cells[i + 1].getBoundingClientRect();
            return {x: r.x + r.width / 2, y: r.y + r.height / 2, text: cells[i + 1].innerText};
        })()"""
        browser.wait(find_cell, "the row of shipped")
        # The grid can draw again after the first cells, and a click then goes nowhere, so this clicks until the menu is there.
        click_drill = """(() => {
            const items = [...document.querySelectorAll('button, [role="button"], a')];
            const item = items.find(b => /^(See|View) (these|this)/i.test(b.innerText.trim()));
            if (!item) return false;
            item.click();
            return true;
        })()"""
        for _try in range(10):
            time.sleep(2)
            cell = browser.js(find_cell)
            browser.click_at(cell["x"], cell["y"])
            time.sleep(1)
            if browser.js(click_drill):
                break
        else:
            raise Fail("no drill-through in the menu. Page: %s" % str(browser.js("document.body.innerText"))[:400])
        truth = ch("SELECT count() FROM orders WHERE status = 'shipped'")[0][0]
        shown = browser.wait("""(() => {
            const t = document.body.innerText.match(/Showing (first )?([\\d,]+) rows?/);
            return t ? t[2].replace(/,/g, '') : null;
        })()""", "the number of rows of the drill-through")
        limit = 2000
        if int(shown) != min(int(truth), limit):
            raise Fail("the drill-through shows %s rows, truth %s" % (shown, truth))
        statuses = browser.js("""[...new Set([...document.querySelectorAll('[data-testid="cell-data"]')].map(c => c.innerText.trim()))]""")
        if "shipped" not in statuses or "completed" in statuses:
            raise Fail("the rows of the drill-through are not only shipped: %s" % statuses[:20])
    finally:
        browser.close()


# ----------------------------------------------------------------- metric catalog
# The model is dev/cube/model/metrics.yml. Each truth is hand SQL on ClickHouse. Each check runs the query builder and the MCP tool.

MT = "metric_types"
MC = "metric_customers"
MCM = "metric_customer_months"
ME = "metric_edges"
MS = "metric_sales"
MR = "metric_rollup_sales"
MB_ = "metric_balances"
MX = "metric_extended"
MV = "metric_view"
AMT = "toFloat64(amount)"
PAID_SQL = "status IN ('completed', 'shipped')"
MT_MONTH = "d:metric_types.created_at@month"
MT_DAY = "d:metric_types.created_at@day"
MT_CRT = "d:metric_types.created_at"


def by_builder_and_mcp(ctx, table, sql, ordered=False, tol=0.0, label="rows", **kw):
    """The same query through /api/dataset and the MCP tool execute_query. Both must equal the truth."""
    ensure_table(ctx, table)
    truth = sql if isinstance(sql, list) else ch(sql)
    assert_rows(ctx.mb.mbql(table, **kw)["rows"], truth, ordered=ordered, tol=tol, label=label + " in the query builder")
    route = {k: kw[k] for k in ("agg", "by", "where") if k in kw}
    assert_rows(ctx.mcp.call("execute_query", query=mcp_query(ctx.mb, table, **route))["rows"], truth, tol=tol, label=label + " through MCP")
    return truth


def M(check_id, table, sql, ordered=False, tol=0.0, post=None, **kw):
    def run(ctx):
        by_builder_and_mcp(ctx, table, sql, ordered=ordered, tol=tol, **kw)
        if post:
            post(ctx, ctx.mb.mbql(table, **kw))
    check(check_id)(run)


def expect_refusal(ctx, table, needle, **kw):
    """The query builder and MCP both stop with this text."""
    expect_clear_error(ctx, ctx.mb.inner(table, **kw), needle)
    route = {k: kw[k] for k in ("agg", "by", "where") if k in kw}
    res = ctx.mcp.rpc("tools/call", {"name": "execute_query", "arguments": {"query": mcp_query(ctx.mb, table, **route)}})
    text = (res.get("content") or [{}])[0].get("text", "")
    if needle not in text:
        raise Fail("MCP gave no refusal with %r: %s" % (needle, text[:300]))


def grain(label, by=(), keys=(), where=None, wsql=None):
    return {"label": label, "by": list(by), "keys": list(keys), "where": where, "wsql": wsql}


def grain_truth(expr, src, g):
    keys = g["keys"]
    return "SELECT %s FROM %s %s %s SETTINGS join_use_nulls = 1" % (
        ", ".join(keys + [expr]), src, "WHERE " + g["wsql"] if g["wsql"] else "",
        "GROUP BY " + ", ".join(str(i + 1) for i in range(len(keys))) if keys else "")


def run_grains(ctx, table, member, expr, src, grains, tol=0.0):
    for g in grains:
        kw = {"agg": [member]}
        if g["by"]:
            kw["by"] = g["by"]
        if g["where"]:
            kw["where"] = g["where"]
        by_builder_and_mcp(ctx, table, grain_truth(expr, src, g), tol=tol, label=g["label"], **kw)


FEB_FIRST_DAYS = [["between", MT_CRT, "2025-02-01", "2025-02-10"]]
MT_GRAINS = [
    grain("total"),
    grain("status", ["d:metric_types.status"], ["status"]),
    grain("size", ["d:metric_types.size"], [SIZE]),
    grain("paid", ["d:metric_types.is_paid"], [PAID_SQL]),
    grain("quantity", ["d:metric_types.quantity"], ["quantity"]),
    grain("month", [MT_MONTH], ["toStartOfMonth(created_at)"]),
    grain("quarter", ["d:metric_types.created_at@quarter"], ["toStartOfQuarter(created_at)"]),
    grain("status and month", ["d:metric_types.status", MT_MONTH], ["status", "toStartOfMonth(created_at)"]),
    grain("day", [MT_DAY], ["toDate(created_at)"], FEB_FIRST_DAYS, "toDate(created_at) BETWEEN '2025-02-01' AND '2025-02-10'"),
]


def G(check_id, member, expr, table=MT, src="orders", grains=None, tol=0.0, extra=None, post=None):
    """A metric at each grain, each with its own truth."""
    def run(ctx):
        run_grains(ctx, table, member, expr, src, MT_GRAINS if grains is None else grains, tol=tol)
        if extra:
            extra(ctx)
        if post:
            post(ctx, ctx.mb.mbql(table, agg=[member]))
    check(check_id)(run)


def scalar(sql):
    return float(ch(sql)[0][0])


# ----- each measure type

G("metric.type.count", "m:metric_types.m_count", "count()")
G("metric.type.count_distinct", "m:metric_types.m_users", "uniqExact(user_id)")
G("metric.type.count_distinct_approx", "m:metric_types.m_users_approx", "uniqExact(user_id)", tol=0.05)
G("metric.type.sum", "m:metric_types.m_revenue", "sum(amount)")
G("metric.type.avg", "m:metric_types.m_avg_amount", "avg(%s)" % AMT)
G("metric.type.min", "m:metric_types.m_min_amount", "min(amount)")
G("metric.type.max", "m:metric_types.m_max_amount", "max(amount)")
G("metric.type.number", "m:metric_types.m_ratio", "sum(%s) / nullIf(count(), 0)" % AMT)
G("metric.type.string", "m:metric_types.m_status_text", "min(status)")
G("metric.type.time", "m:metric_types.m_last_order_at", "max(created_at)")
G("metric.type.boolean", "m:metric_types.m_has_completed", "toBool(max(status = 'completed'))")
G("metric.type.number_agg", "m:metric_types.m_median_agg", "quantile(0.5)(%s)" % AMT, tol=1e-9)


@check("metric.type.rank")
def _(ctx):
    # reduce_by: [status] takes status out of the partition, so a status has a rank in its month.
    by_builder_and_mcp(ctx, MT, "SELECT status, rank() OVER (ORDER BY sum(amount) DESC) FROM orders GROUP BY status",
                       agg=["m:metric_types.m_rank"], by=["d:metric_types.status"], label="status")
    by_builder_and_mcp(ctx, MT, "SELECT status, toStartOfMonth(created_at) AS m, rank() OVER (PARTITION BY m ORDER BY sum(amount) DESC) FROM orders GROUP BY status, m",
                       agg=["m:metric_types.m_rank"], by=["d:metric_types.status", MT_MONTH], label="status and month")


# ----- each measure option

G("metric.opt.filter_one", "m:metric_types.m_count_completed", "countIf(status = 'completed')")
G("metric.opt.filter_several", "m:metric_types.m_count_large_completed", "countIf(status = 'completed' AND quantity >= 3)")
G("metric.opt.filter_or", "m:metric_types.m_count_completed_or_large", "countIf(status = 'completed' OR quantity >= 3)")
# SQL gives NULL for a sum with no row, so the truth has if(...) in place of sumIf, which gives 0.
G("metric.opt.filter_on_sum", "m:metric_types.m_paid_revenue", "sum(if(%s, amount, NULL))" % PAID_SQL)


def over_months(expr, months):
    """The truth of a window by month. m is the start of the month and n is the start of the next month."""
    return ("SELECT m, %s FROM orders o CROSS JOIN (SELECT toDateTime(x, 'UTC') AS m, toDateTime(addMonths(toDateTime(x, 'UTC'), 1), 'UTC') AS n "
            "FROM (SELECT arrayJoin(%s) AS x)) b GROUP BY m, n" % (expr, json.dumps(months).replace('"', "'")))


def over_days(expr, days):
    """The truth of a window by day. d is the start of the day, and e is its last second."""
    return ("SELECT d, %s FROM orders o CROSS JOIN (SELECT toDateTime(x, 'UTC') AS d, toDateTime(x, 'UTC') + INTERVAL 86399 SECOND AS e "
            "FROM (SELECT arrayJoin(%s) AS x)) b GROUP BY d, e" % (expr, json.dumps(days).replace('"', "'")))


Q2 = ["2025-04-01", "2025-05-01", "2025-06-01"]
Q2_WHERE = [[">=", MT_CRT, "2025-04-01"], ["<", MT_CRT, "2025-07-01"]]
MAR_DAYS = ["2025-03-30", "2025-03-31", "2025-04-01"]
MAR_DAYS_WHERE = [[">=", MT_CRT, "2025-03-30"], ["<", MT_CRT, "2025-04-02"]]


def window(check_id, member, month_expr, day_expr):
    """A rolling window by month and by day. Cube reads the rows in buckets, so a window of months holds whole months in the month query."""
    def run(ctx):
        by_builder_and_mcp(ctx, MT, over_months(month_expr, Q2), agg=[member], by=[MT_MONTH], where=Q2_WHERE, label="month")
        by_builder_and_mcp(ctx, MT, over_days(day_expr, MAR_DAYS), agg=[member], by=[MT_DAY], where=MAR_DAYS_WHERE, label="day")
    check(check_id)(run)


window("metric.opt.rolling_trailing", "m:metric_types.m_rev_trailing_3m",
       "sumIf(amount, created_at >= addMonths(m, -2) AND created_at < n)",
       "sumIf(amount, toStartOfDay(created_at) > addMonths(e, -3) AND created_at <= e)")
window("metric.opt.rolling_leading", "m:metric_types.m_rev_leading_1m",
       "sumIf(amount, created_at >= m AND created_at < n)",
       "sumIf(amount, toStartOfDay(created_at) >= d AND toStartOfDay(created_at) < addMonths(d, 1))")


@check("metric.opt.rolling_leading_offset_end")
def _(ctx):
    # Cube reads whole months for each bucket but the last, and it cuts the last month at the end of the window. So a month query stops.
    member = "m:metric_types.m_rev_leading_next"
    by_builder_and_mcp(ctx, MT, over_days("sumIf(amount, toStartOfDay(created_at) > e AND toStartOfDay(created_at) <= addMonths(e, 1))", MAR_DAYS), agg=[member], by=[MT_DAY], where=MAR_DAYS_WHERE, label="day")
    expect_refusal(ctx, MT, "after the end of a bucket", agg=[member], by=[MT_MONTH], where=Q2_WHERE)


window("metric.opt.rolling_offset_end", "m:metric_types.m_rev_offset_end",
       "sumIf(amount, created_at >= m AND created_at < n)",
       "sumIf(amount, toStartOfDay(created_at) > addMonths(e, -1) AND created_at <= e)")
window("metric.opt.rolling_offset_start", "m:metric_types.m_rev_offset_start",
       "sumIf(amount, created_at >= addMonths(m, -1) AND created_at < m)",
       "sumIf(amount, toStartOfDay(created_at) >= addMonths(d, -1) AND toStartOfDay(created_at) < d)")
window("metric.opt.rolling_to_date", "m:metric_types.m_rev_mtd",
       "sumIf(amount, created_at >= m AND created_at < n)",
       "sumIf(amount, created_at >= toStartOfMonth(d) AND created_at <= e)")
window("metric.opt.rolling_to_date_year", "m:metric_types.m_rev_ytd",
       "sumIf(amount, created_at >= toStartOfYear(m) AND created_at < n)",
       "sumIf(amount, created_at >= toStartOfYear(d) AND created_at <= e)")
window("metric.opt.rolling_unbounded", "m:metric_types.m_rev_running",
       "sumIf(amount, created_at < n)",
       "sumIf(amount, created_at <= e)")


@check("metric.opt.rolling_shorter_than_bucket_refused")
def _(ctx):
    expect_refusal(ctx, MT, "shorter than a quarter", agg=["m:metric_types.m_rev_mtd"], by=["d:metric_types.created_at@quarter"], where=[[">=", MT_CRT, "2025-01-01"], ["<", MT_CRT, "2025-07-01"]])


G("metric.opt.multi_stage", "m:metric_types.m_rev_grand_total", "any((SELECT sum(amount) FROM orders))",
  grains=[grain("total"), grain("status", ["d:metric_types.status"], ["status"]), grain("month", [MT_MONTH], ["toStartOfMonth(created_at)"])])


@check("metric.opt.group_by")
def _(ctx):
    rows = "SELECT o.status, o.m, t.r FROM (SELECT status, toStartOfMonth(created_at) AS m FROM orders GROUP BY status, m) o JOIN (SELECT status, sum(amount) AS r FROM orders GROUP BY status) t ON o.status = t.status"
    by_builder_and_mcp(ctx, MT, rows, agg=["m:metric_types.m_rev_group_by"], by=["d:metric_types.status", MT_MONTH], label="status and month")
    by_builder_and_mcp(ctx, MT, "SELECT status, sum(amount) FROM orders GROUP BY status", agg=["m:metric_types.m_rev_group_by"], by=["d:metric_types.status"], label="status")


@check("metric.opt.reduce_by")
def _(ctx):
    by_builder_and_mcp(ctx, MT, "SELECT status, (SELECT sum(amount) FROM orders) FROM orders GROUP BY status", agg=["m:metric_types.m_rev_reduce_by"], by=["d:metric_types.status"], label="status")
    rows = "SELECT o.status, o.m, t.r FROM (SELECT status, toStartOfMonth(created_at) AS m FROM orders GROUP BY status, m) o JOIN (SELECT toStartOfMonth(created_at) AS m, sum(amount) AS r FROM orders GROUP BY m) t ON o.m = t.m"
    by_builder_and_mcp(ctx, MT, rows, agg=["m:metric_types.m_rev_reduce_by"], by=["d:metric_types.status", MT_MONTH], label="status and month")


@check("metric.opt.add_group_by")
def _(ctx):
    by_builder_and_mcp(ctx, MT, "SELECT avg(r) FROM (SELECT status, sum(%s) AS r FROM orders GROUP BY status)" % AMT, agg=["m:metric_types.m_rev_add_group_by"], label="total")
    by_builder_and_mcp(ctx, MT, "SELECT m, avg(r) FROM (SELECT toStartOfMonth(created_at) AS m, status, sum(%s) AS r FROM orders GROUP BY m, status) GROUP BY m" % AMT,
                       agg=["m:metric_types.m_rev_add_group_by"], by=[MT_MONTH], label="month")


MONTHS_REV = "(SELECT toStartOfMonth(created_at) AS m, sum(%s) AS r FROM orders GROUP BY m)" % AMT
FEB_APR_WHERE = [["between", MT_CRT, "2025-02-01", "2025-04-30"]]
FEB_APR = "t.m BETWEEN '2025-02-01' AND '2025-04-01'"


def by_prior_month(value):
    return "SELECT t.m, %s FROM %s t LEFT JOIN %s p ON p.m = addMonths(t.m, -1) WHERE %s" % (value, MONTHS_REV, MONTHS_REV, FEB_APR)


M("metric.opt.time_shift_prior", MT, by_prior_month("p.r"), agg=["m:metric_types.m_rev_prior_month"], by=[MT_MONTH], where=FEB_APR_WHERE)
M("metric.opt.time_shift_calendar", "metric_calendar",
  "SELECT toStartOfMonth(toDate(created_at) + 28) AS m, sum(amount) FROM orders GROUP BY m HAVING m = '2025-03-01'",
  agg=["m:metric_calendar.m_rev_prior_calendar"], by=["d:metric_calendar.fiscal_calendar_date@month"],
  where=[["between", "d:metric_calendar.fiscal_calendar_date", "2025-03-01", "2025-03-31"]])


@check("metric.opt.time_shift_needs_a_range")
def _(ctx):
    expect_refusal(ctx, MT, "time shift", agg=["m:metric_types.m_rev_prior_month"])


REV_ALL = "SELECT sum(amount) FROM orders"
SHARE_ALL = "SELECT sum(if(%s, %s, NULL)) / sum(%s) FROM orders" % (PAID_SQL, AMT, AMT)


def fmt(check_id, member, truth, style, decimals=None, currency=None):
    M(check_id, MT, truth, agg=["m:metric_types.%s" % member], post=expect_format(style, currency, decimals))


fmt("metric.format.percent", "m_fmt_percent", SHARE_ALL, "percent", 2)
fmt("metric.format.percent_0", "m_fmt_percent_0", SHARE_ALL, "percent", 0)
fmt("metric.format.percent_2", "m_fmt_percent_2", SHARE_ALL, "percent", 2)
fmt("metric.format.currency", "m_fmt_currency", REV_ALL, "currency", 2)
fmt("metric.format.currency_0", "m_fmt_currency_0", REV_ALL, "currency", 0)
fmt("metric.format.currency_3", "m_fmt_currency_3", REV_ALL, "currency", 3)
fmt("metric.format.currency_code", "m_fmt_currency_eur", REV_ALL, "currency", 2, "EUR")
fmt("metric.format.number", "m_fmt_number", REV_ALL, "decimal", 2)
fmt("metric.format.number_0", "m_fmt_number_0", REV_ALL, "decimal", 0)
fmt("metric.format.number_3", "m_fmt_number_3", REV_ALL, "decimal", 3)
fmt("metric.format.id", "m_fmt_id", "SELECT max(id) FROM orders", "decimal", 0)
fmt("metric.format.d3", "m_fmt_d3", REV_ALL, "currency", 3)
fmt("metric.format.d3_percent", "m_fmt_d3_percent", SHARE_ALL, "percent", 1)
M("metric.format.abbr", MT, REV_ALL, agg=["m:metric_types.m_fmt_abbr"], post=expect_format_as_text(MT, "m_fmt_abbr", "decimal", 2, "Number format: .2~s."))
M("metric.format.abbr_1", MT, REV_ALL, agg=["m:metric_types.m_fmt_abbr_1"], post=expect_format_as_text(MT, "m_fmt_abbr_1", "decimal", 1, "Number format: .1~s."))
M("metric.format.accounting", MT, REV_ALL, agg=["m:metric_types.m_fmt_accounting"], post=expect_format_as_text(MT, "m_fmt_accounting", "decimal", 2, "Number format: (,.2~f."))
M("metric.format.accounting_0", MT, REV_ALL, agg=["m:metric_types.m_fmt_accounting_0"], post=expect_format_as_text(MT, "m_fmt_accounting_0", "decimal", 0, "Number format: (,.0f."))
M("metric.opt.drill_members", MT, "SELECT count() FROM orders", agg=["m:metric_types.m_count"], post=expect_description(MT, "Rows of shop.orders.", "m_count"))
M("metric.opt.public_false", MT, "SELECT count() FROM orders", agg=["m:metric_types.m_count"], post=expect_hidden(MT, "m_private_count"))
M("metric.opt.meta", MT, "SELECT count() FROM orders", agg=["m:metric_types.m_formatted_count"], post=expect_text(MT, "Count of orders with member meta."))


@check("metric.opt.drill_members_text")
def _(ctx):
    text = ctx.mb.entity(ctx.mb.measures, MT, "m_count").get("description") or ""
    if "To drill into this measure" not in text or "status" not in text:
        raise Fail("the description of m_count has no drill members: %r" % text)


# ----- business metrics

G("metric.business.revenue", "m:metric_types.m_revenue", "sum(amount)")
G("metric.business.average_order_value", "m:metric_types.b_aov_ratio", "sum(%s) / nullIf(count(), 0)" % AMT)

CUSTOMER_GRAINS = [
    grain("total"),
    grain("city", ["d:metric_customers.city"], ["u.city"]),
    grain("vip", ["d:metric_customers.is_vip"], ["u.is_vip"]),
]
CONVERSION_SRC = ("users u LEFT JOIN (SELECT user_id, count() AS n FROM orders WHERE status IN ('completed', 'shipped') "
                  "AND created_at < toDateTime('2025-02-01 00:00:00', 'UTC') GROUP BY user_id) o ON u.id = o.user_id")
G("metric.business.conversion", "m:metric_customers.conversion", "1.0 * countIf(o.n > 0) / nullIf(count(), 0)", table=MC, src=CONVERSION_SRC, grains=CUSTOMER_GRAINS)

MCM_MONTH = "d:metric_customer_months.month_at@month"
USERS_BY_MONTH = "(SELECT toStartOfMonth(created_at) AS m, groupUniqArray(user_id) AS u FROM orders GROUP BY m)"


@check("metric.business.retention")
def _(ctx):
    truth = "SELECT t.m, 1.0 * length(arrayIntersect(t.u, n.u)) / length(t.u) FROM %s t LEFT JOIN %s n ON n.m = addMonths(t.m, 1)" % (USERS_BY_MONTH, USERS_BY_MONTH)
    by_builder_and_mcp(ctx, MCM, truth, agg=["m:metric_customer_months.retention"], by=[MCM_MONTH])


@check("metric.business.churn")
def _(ctx):
    truth = "SELECT t.m, 1.0 - 1.0 * length(arrayIntersect(t.u, n.u)) / length(t.u) FROM %s t LEFT JOIN %s n ON n.m = addMonths(t.m, 1)" % (USERS_BY_MONTH, USERS_BY_MONTH)
    by_builder_and_mcp(ctx, MCM, truth, agg=["m:metric_customer_months.churn"], by=[MCM_MONTH])


@check("metric.business.repeat_rate")
def _(ctx):
    truth = "SELECT m, 1.0 * countIf(n >= 2) / count() FROM (SELECT toStartOfMonth(created_at) AS m, user_id, count() AS n FROM orders GROUP BY m, user_id) GROUP BY m"
    by_builder_and_mcp(ctx, MCM, truth, agg=["m:metric_customer_months.repeat_rate"], by=[MCM_MONTH])


SHARE_GRAINS = [
    grain("total"),
    grain("status", ["d:metric_types.status"], ["status"]),
    grain("month", [MT_MONTH], ["toStartOfMonth(created_at)"]),
    grain("status and month", ["d:metric_types.status", MT_MONTH], ["status", "toStartOfMonth(created_at)"]),
]
SHARE_EXPR = "sum(%s) / (SELECT sum(%s) FROM orders)" % (AMT, AMT)
G("metric.business.share_of_total", "m:metric_types.c_share_of_total", SHARE_EXPR, grains=SHARE_GRAINS)
M("metric.business.month_over_month", MT, by_prior_month("(t.r - p.r) / nullIf(p.r, 0)"), agg=["m:metric_types.b_mom"], by=[MT_MONTH], where=FEB_APR_WHERE)

MS_MONTH = "d:metric_sales.sold_at@month"
SALES_BY_MONTH = "(SELECT toStartOfMonth(sold_at) AS m, sum(toFloat64(amount)) AS r FROM metric_sales GROUP BY m)"
M("metric.business.year_over_year", MS,
  "SELECT t.m, (t.r - p.r) / nullIf(p.r, 0) FROM %s t LEFT JOIN %s p ON p.m = addYears(t.m, -1) WHERE t.m BETWEEN '2025-01-01' AND '2025-03-01'" % (SALES_BY_MONTH, SALES_BY_MONTH),
  agg=["m:metric_sales.yoy"], by=[MS_MONTH], where=[["between", "d:metric_sales.sold_at", "2025-01-01", "2025-03-31"]])
M("metric.business.running_total", MT, over_months("sumIf(amount, created_at < n)", Q2), agg=["m:metric_types.m_rev_running"], by=[MT_MONTH], where=Q2_WHERE)
M("metric.business.moving_average", MT, over_months("sumIf(%s, created_at >= addMonths(m, -2) AND created_at < n) / 3" % AMT, Q2), agg=["m:metric_types.b_moving_avg_3m"], by=[MT_MONTH], where=Q2_WHERE)
G("metric.business.median", "m:metric_types.b_median", "quantile(0.5)(%s)" % AMT, tol=1e-9)
G("metric.business.percentile", "m:metric_types.b_p90", "quantile(0.9)(%s)" % AMT, tol=1e-9)

EDGE_GRAINS = [
    grain("total"),
    grain("group", ["d:metric_edges.grp"], ["grp"]),
    grain("month", ["d:metric_edges.day@month"], ["toStartOfMonth(day)"]),
    grain("group and month", ["d:metric_edges.grp", "d:metric_edges.day@month"], ["grp", "toStartOfMonth(day)"]),
]


def E(check_id, member, expr):
    G(check_id, "m:metric_edges.%s" % member, expr, table=ME, src="metric_edges", grains=EDGE_GRAINS)


E("metric.business.ratio_zero_null_denominator", "ratio", "1.0 * sum(num) / nullIf(sum(den), 0)")
G("metric.business.weighted_average", "m:metric_types.b_weighted_amount", "sum(%s * quantity) / nullIf(sum(quantity), 0)" % AMT)

JOIN_USERS = "orders o JOIN users u ON o.user_id = u.id"
JOIN_GRAINS = [
    grain("total"),
    grain("status", ["d:metric_types.status"], ["o.status"]),
    grain("month", [MT_MONTH], ["toStartOfMonth(o.created_at)"]),
]
G("metric.business.distinct_over_join", "m:metric_types.b_distinct_cities", "uniqExact(u.city)", src=JOIN_USERS, grains=JOIN_GRAINS)
G("metric.business.distinct_key_over_join", "m:metric_types.b_distinct_vip_customers", "uniqExactIf(u.id, u.is_vip)", src=JOIN_USERS, grains=JOIN_GRAINS)

REGION_OF_STATE = REGION_SQL % "u.state"
VIEW_JOIN_GRAINS = [
    grain("region", ["d:metric_view.regions_region"], [REGION_OF_STATE]),
    grain("city", ["d:metric_view.metric_customers_city"], ["u.city"]),
    grain("status and region", ["d:metric_view.status", "d:metric_view.regions_region"], ["o.status", REGION_OF_STATE]),
]
G("metric.business.through_two_joins", "m:metric_view.m_revenue", "sum(o.amount)", table=MV, src=JOIN_USERS, grains=VIEW_JOIN_GRAINS)
PAYMENTS_OF_USER = ("users u LEFT JOIN orders o ON u.id = o.user_id LEFT JOIN (SELECT order_id, sum(paid) AS paid FROM (SELECT id AS order_id, toFloat64(amount) AS paid FROM orders "
                    "UNION ALL SELECT id, 1.0 FROM orders) GROUP BY order_id) p ON o.id = p.order_id")
G("metric.business.cube_measure_through_two_joins", "m:metric_customers.payments_total", "sum(p.paid)", table=MC, src=PAYMENTS_OF_USER, grains=CUSTOMER_GRAINS)


@check("metric.business.non_additive")
def _(ctx):
    run_grains(ctx, MT, "m:metric_types.m_users", "uniqExact(user_id)", "orders", MT_GRAINS)
    total = scalar("SELECT uniqExact(user_id) FROM orders")
    months = scalar("SELECT sum(c) FROM (SELECT uniqExact(user_id) AS c FROM orders GROUP BY toStartOfMonth(created_at))")
    if total >= months:
        raise Fail("the data cannot show this: %s customers, %s by month" % (total, months))


VIEW_GRAINS = [
    grain("total"),
    grain("status", ["d:metric_view.status"], ["o.status"]),
    grain("city", ["d:metric_view.metric_customers_city"], ["u.city"]),
    grain("month", ["d:metric_view.created_at@month"], ["toStartOfMonth(o.created_at)"]),
]
G("metric.business.on_a_view", "m:metric_view.revenue_per_order", "sum(toFloat64(o.amount)) / nullIf(count(), 0)", table=MV, src=JOIN_USERS, grains=VIEW_GRAINS)
G("metric.business.ratio_on_a_view", "m:metric_view.c_ratio_of_sums", "sum(toFloat64(o.amount)) / nullIf(sum(o.quantity), 0)", table=MV, src=JOIN_USERS, grains=VIEW_GRAINS)

EXT_GRAINS = [grain("total"), grain("status", ["d:metric_extended.status"], ["status"])]
G("metric.business.on_an_extended_cube", "m:metric_extended.per_order", "2.0 * sum(%s) / nullIf(count(), 0)" % AMT, table=MX, grains=EXT_GRAINS)
G("metric.business.inherited_ratio_reads_override", "m:metric_extended.per_unit", "2.0 * sum(%s) / nullIf(sum(quantity), 0)" % AMT, table=MX, grains=EXT_GRAINS)
M("metric.business.extended_base_hidden", MX, "SELECT sum(amount * 2) FROM orders", agg=["m:metric_extended.revenue"], post=expect_no_table("metric_base"))


# ----- metrics made from other metrics


def ratio_rollup_is_not_finer(ctx):
    """The data must separate a ratio of sums from an average or a sum of the finer ratios."""
    ratio = scalar("SELECT sum(%s) / sum(quantity) FROM orders" % AMT)
    avg_finer = scalar("SELECT avg(r) FROM (SELECT sum(%s) / sum(quantity) AS r FROM orders GROUP BY toDate(created_at))" % AMT)
    sum_finer = scalar("SELECT sum(r) FROM (SELECT sum(%s) / sum(quantity) AS r FROM orders GROUP BY toDate(created_at))" % AMT)
    got = ctx.mb.mbql(MT, agg=["m:metric_types.c_ratio_of_sums"])["rows"][0][0]
    for name, other in (("the average", avg_finer), ("the sum", sum_finer)):
        if abs(got - other) < 1e-6:
            raise Fail("the ratio %s equals %s of the finer ratios %s" % (got, name, other))
    assert_rows([[got]], [[ratio]])


G("metric.composite.ratio_of_sums", "m:metric_types.c_ratio_of_sums", "sum(%s) / nullIf(sum(quantity), 0)" % AMT, extra=ratio_rollup_is_not_finer)


def row_ratios_differ(ctx):
    a, b = (ctx.mb.mbql(MT, agg=[m])["rows"][0][0] for m in ("m:metric_types.c_sum_of_row_ratios", "m:metric_types.c_ratio_of_sums"))
    if abs(a - b) < 1e-6:
        raise Fail("the sum of row ratios %s equals the ratio of sums %s" % (a, b))


G("metric.composite.sum_of_row_ratios", "m:metric_types.c_sum_of_row_ratios", "sum(%s / nullIf(quantity, 0))" % AMT, extra=row_ratios_differ)
G("metric.composite.avg_of_ratio", "m:metric_types.c_avg_of_ratio", "avg(%s / nullIf(quantity, 0))" % AMT)
G("metric.composite.ratio_of_avgs", "m:metric_types.c_ratio_of_avgs", "avg(%s) / nullIf(avg(quantity), 0)" % AMT)

ORDER_PAYMENTS = ("(SELECT o.status AS status, o.quantity AS quantity, o.created_at AS created_at, o.amount AS amount, p.paid AS paid, p.n AS n FROM orders o LEFT JOIN "
                  "(SELECT order_id, sum(paid) AS paid, count() AS n FROM (SELECT id AS order_id, toFloat64(amount) AS paid FROM orders UNION ALL SELECT id, 1.0 FROM orders) GROUP BY order_id) p ON o.id = p.order_id)")


def fan_out_would_differ(ctx):
    plain = scalar("SELECT sum(toFloat64(o.amount)) FROM orders o JOIN (SELECT id AS order_id FROM orders UNION ALL SELECT id FROM orders) p ON o.id = p.order_id")
    if abs(plain - scalar("SELECT sum(%s) FROM orders" % AMT)) < 1:
        raise Fail("this join has no fan-out, so the check proves nothing")


G("metric.composite.cross_cube_sum_ratio", "m:metric_types.c_cross_paid_ratio", "sum(paid) / nullIf(sum(%s), 0)" % AMT, src=ORDER_PAYMENTS, extra=fan_out_would_differ)
G("metric.composite.cross_cube_count_ratio", "m:metric_types.c_cross_count_ratio", "1.0 * sum(n) / nullIf(count(), 0)", src=ORDER_PAYMENTS)
G("metric.composite.filtered_numerator", "m:metric_types.c_paid_share", "sum(if(%s, %s, NULL)) / nullIf(sum(%s), 0)" % (PAID_SQL, AMT, AMT))
G("metric.composite.distinct_over_count", "m:metric_types.c_customers_per_order", "1.0 * uniqExact(user_id) / nullIf(count(), 0)")
G("metric.composite.sum_over_distinct", "m:metric_types.c_revenue_per_customer", "sum(%s) / nullIf(uniqExact(user_id), 0)" % AMT)
G("metric.composite.value_over_total", "m:metric_types.c_share_of_total", SHARE_EXPR, grains=SHARE_GRAINS)


@check("metric.composite.value_over_prior")
def _(ctx):
    member = "m:metric_types.c_to_prior"
    by_builder_and_mcp(ctx, MT, by_prior_month("t.r / nullIf(p.r, 0)"), agg=[member], by=[MT_MONTH], where=FEB_APR_WHERE, label="month")
    by_status = "(SELECT status, toStartOfMonth(created_at) AS m, sum(%s) AS r FROM orders GROUP BY status, m)" % AMT
    by_builder_and_mcp(ctx, MT, "SELECT t.status, t.m, t.r / nullIf(p.r, 0) FROM %s t LEFT JOIN %s p ON p.status = t.status AND p.m = addMonths(t.m, -1) WHERE %s" % (by_status, by_status, FEB_APR),
                       agg=[member], by=["d:metric_types.status", MT_MONTH], where=FEB_APR_WHERE, label="status and month")
    # By a quarter, the shift moves each row by one month, so the prior value is March to May.
    by_builder_and_mcp(ctx, MT, "SELECT toDate('2025-04-01'), sumIf(%s, created_at >= '2025-04-01' AND created_at < '2025-07-01') / nullIf(sumIf(%s, created_at >= '2025-03-01' AND created_at < '2025-06-01'), 0) FROM orders" % (AMT, AMT),
                       agg=[member], by=["d:metric_types.created_at@quarter"], where=[["between", MT_CRT, "2025-04-01", "2025-06-30"]], label="quarter")
    by_builder_and_mcp(ctx, MT, "SELECT d, sumIf(%s, toDate(created_at) = d) / nullIf(sumIf(%s, toDate(created_at) = addMonths(d, -1)), 0) FROM orders CROSS JOIN (SELECT toDate('2025-03-01') + number AS d FROM numbers(3)) x GROUP BY d" % (AMT, AMT),
                       agg=[member], by=[MT_DAY], where=[["between", MT_CRT, "2025-03-01", "2025-03-03"]], label="day")


@check("metric.composite.rolling_over_plain")
def _(ctx):
    member = "m:metric_types.c_rolling_to_plain"
    ratio = "sumIf(%s, created_at >= addMonths(m, -2) AND created_at < n) / nullIf(sumIf(%s, created_at >= m AND created_at < n), 0)" % (AMT, AMT)
    by_builder_and_mcp(ctx, MT, over_months(ratio, Q2), agg=[member], by=[MT_MONTH], where=Q2_WHERE, label="month")
    by_status = ("SELECT status, m, %s FROM orders o CROSS JOIN (SELECT toDateTime(x, 'UTC') AS m, toDateTime(addMonths(toDateTime(x, 'UTC'), 1), 'UTC') AS n "
                 "FROM (SELECT arrayJoin(%s) AS x)) b GROUP BY status, m, n" % (ratio, json.dumps(Q2).replace('"', "'")))
    by_builder_and_mcp(ctx, MT, by_status, agg=[member], by=["d:metric_types.status", MT_MONTH], where=Q2_WHERE, label="status and month")
    by_builder_and_mcp(ctx, MT, over_days("sumIf(%s, toStartOfDay(created_at) > addMonths(e, -3) AND created_at <= e) / nullIf(sumIf(%s, created_at >= d AND created_at <= e), 0)" % (AMT, AMT), MAR_DAYS),
                       agg=[member], by=[MT_DAY], where=MAR_DAYS_WHERE, label="day")


G("metric.composite.difference_over_base", "m:metric_types.c_margin", "(sum(%s) - sum(quantity * 10)) / nullIf(sum(quantity * 10), 0)" % AMT)
G("metric.composite.product", "m:metric_types.c_product", "avg(quantity) * count()")
G("metric.composite.nested_ratio", "m:metric_types.c_nested_ratio", "sum(%s) / nullIf(sum(quantity), 0) / nullIf(count(), 0)" % AMT)

E("metric.edge.sum_of_numerator", "num", "sum(num)")
E("metric.edge.sum_of_denominator", "den", "sum(den)")
E("metric.edge.rows", "rows", "count()")
E("metric.edge.avg_ignores_null", "avg_num", "avg(num)")
E("metric.edge.min_negative", "min_den", "min(den)")
E("metric.edge.max_negative", "max_den", "max(den)")
E("metric.edge.sum_of_row_ratios", "sum_of_ratios", "sum(1.0 * num / nullIf(den, 0))")
E("metric.edge.avg_of_row_ratios", "avg_of_ratios", "avg(1.0 * num / nullIf(den, 0))")
E("metric.edge.change_over_base", "change", "1.0 * (sum(num) - sum(den)) / nullIf(sum(den), 0)")
E("metric.edge.filtered_sum", "positive_num", "sum(if(num > 0, num, NULL))")
E("metric.edge.filtered_share", "positive_share", "1.0 * sum(if(num > 0, num, NULL)) / nullIf(sum(num), 0)")


@check("metric.edge.ratio_with_no_guard")
def _(ctx):
    # A ratio with no NULLIF on a zero denominator has no number. SQL stops or gives NULL, so a number here is wrong.
    for route in ("builder", "mcp"):
        rows = (ctx.mb.mbql(ME, agg=["m:metric_edges.ratio_raw"], by=["d:metric_edges.grp"])["rows"] if route == "builder" else
                ctx.mcp.call("execute_query", query=mcp_query(ctx.mb, ME, agg=["m:metric_edges.ratio_raw"], by=["d:metric_edges.grp"]))["rows"])
        got = {r[0]: r[1] for r in rows}
        assert_rows([[got["plain"]], [got["neg_num"]]], ch("SELECT 1.0 * sum(num) / sum(den) FROM metric_edges WHERE grp IN ('plain', 'neg_num') GROUP BY grp"), label=route)
        for grp in ("zero_den", "cancels", "null_den", "null_num"):
            if isinstance(got[grp], (int, float)) and abs(got[grp]) < 1e300:
                raise Fail("%s: %s with no denominator gives the number %s" % (route, grp, got[grp]))


# ----- additive, semi-additive and non-additive metrics, with a rollup by day and status

MR_DAY = "d:metric_rollup_sales.created_at@day"
MR_MONTH = "d:metric_rollup_sales.created_at@month"
MR_HOUR = "d:metric_rollup_sales.created_at@hour"
MR_STATUS = "d:metric_rollup_sales.status"
MR_CRT = "d:metric_rollup_sales.created_at"
MB_CRT = "d:metric_balances.ts"
MR_FEB = [[">=", MR_CRT, "2025-02-01"], ["<", MR_CRT, "2025-03-01"]]
MR_FEB_DAY = [[">=", MR_CRT, "2025-02-03"], ["<", MR_CRT, "2025-02-04"]]
MR_Q1 = [[">=", MR_CRT, "2025-01-01"], ["<", MR_CRT, "2025-04-01"]]
FEB_SQL = "created_at >= '2025-02-01' AND created_at < '2025-03-01'"
FEB_DAY_SQL = "created_at >= '2025-02-03' AND created_at < '2025-02-04'"
Q1_SQL = "created_at >= '2025-01-01' AND created_at < '2025-04-01'"


def expect_used(ctx, table, needle, hit, **kw):
    """Asks Cube if the request of this query reads the rollup."""
    request = request_of(compile_query(ctx, ctx.mb.inner(table, **kw)))
    for _ in range(120):
        status, text, _h = http("POST", CUBE + "/cubejs-api/v1/load", {"query": request})
        res = json.loads(text)
        if res.get("error") == "Continue wait":
            time.sleep(2)
            continue
        if status != 200:
            raise Fail("Cube load: HTTP %s %s" % (status, text[:300]))
        used = json.dumps(res.get("usedPreAggregations") or {})
        if (needle in used) != hit:
            raise Fail("want %s of %s, used %s, request %s" % ("a hit" if hit else "a miss", needle, used[:200], json.dumps(request)[:300]))
        return
    raise Fail("Cube load: timeout")


def rollup_case(check_id, table, needle, hit, sql, **kw):
    def run(ctx):
        ensure_table(ctx, table)
        expect_used(ctx, table, needle, hit, **kw)
        by_builder_and_mcp(ctx, table, sql, **kw)
    check(check_id)(run)


def mr(member):
    return "m:metric_rollup_sales.%s" % member


NEEDLE = "by_status_day"
rollup_case("metric.rollup.additive_at_rollup_grain_hit", MR, NEEDLE, True, "SELECT status, toDate(created_at), sum(amount) FROM orders WHERE %s GROUP BY 1, 2" % FEB_DAY_SQL, agg=[mr("revenue")], by=[MR_STATUS, MR_DAY], where=MR_FEB_DAY)
rollup_case("metric.rollup.additive_coarser_hit", MR, NEEDLE, True, "SELECT toStartOfMonth(created_at), sum(amount) FROM orders WHERE %s GROUP BY 1" % Q1_SQL, agg=[mr("revenue")], by=[MR_MONTH], where=MR_Q1)
rollup_case("metric.rollup.additive_finer_miss", MR, NEEDLE, False, "SELECT toStartOfHour(created_at), sum(amount) FROM orders WHERE %s GROUP BY 1" % FEB_DAY_SQL, agg=[mr("revenue")], by=[MR_HOUR], where=MR_FEB_DAY)
rollup_case("metric.rollup.ratio_of_sums_coarser_hit", MR, NEEDLE, True, "SELECT toStartOfMonth(created_at), sum(%s) / nullIf(sum(quantity), 0) FROM orders WHERE %s GROUP BY 1" % (AMT, Q1_SQL), agg=[mr("per_unit")], by=[MR_MONTH], where=MR_Q1)
rollup_case("metric.rollup.ratio_of_sums_status_hit", MR, NEEDLE, True, "SELECT status, sum(%s) / nullIf(sum(quantity), 0) FROM orders GROUP BY 1" % AMT, agg=[mr("per_unit")], by=[MR_STATUS])
rollup_case("metric.rollup.ratio_of_sums_finer_miss", MR, NEEDLE, False, "SELECT toStartOfHour(created_at), sum(%s) / nullIf(sum(quantity), 0) FROM orders WHERE %s GROUP BY 1" % (AMT, FEB_DAY_SQL), agg=[mr("per_unit")], by=[MR_HOUR], where=MR_FEB_DAY)
rollup_case("metric.rollup.non_additive_at_rollup_grain_hit", MR, NEEDLE, True, "SELECT status, toDate(created_at), uniqExact(user_id) FROM orders WHERE %s GROUP BY 1, 2" % FEB_DAY_SQL, agg=[mr("customers")], by=[MR_STATUS, MR_DAY], where=MR_FEB_DAY)
rollup_case("metric.rollup.non_additive_coarser_miss", MR, NEEDLE, False, "SELECT toStartOfMonth(created_at), uniqExact(user_id) FROM orders WHERE %s GROUP BY 1" % Q1_SQL, agg=[mr("customers")], by=[MR_MONTH], where=MR_Q1)
rollup_case("metric.rollup.non_additive_finer_miss", MR, NEEDLE, False, "SELECT toStartOfHour(created_at), uniqExact(user_id) FROM orders WHERE %s GROUP BY 1" % FEB_DAY_SQL, agg=[mr("customers")], by=[MR_HOUR], where=MR_FEB_DAY)
rollup_case("metric.rollup.sum_over_distinct_coarser_miss", MR, NEEDLE, False, "SELECT toStartOfMonth(created_at), sum(%s) / nullIf(uniqExact(user_id), 0) FROM orders WHERE %s GROUP BY 1" % (AMT, Q1_SQL), agg=[mr("per_customer")], by=[MR_MONTH], where=MR_Q1)


def over_rollup_months(expr):
    return ("SELECT m, %s FROM orders o CROSS JOIN (SELECT toDateTime(x, 'UTC') AS m, toDateTime(addMonths(toDateTime(x, 'UTC'), 1), 'UTC') AS n "
            "FROM (SELECT arrayJoin(['2025-01-01', '2025-02-01', '2025-03-01']) AS x)) b GROUP BY m, n" % expr)


def over_rollup_days(expr):
    return ("SELECT d, %s FROM orders o CROSS JOIN (SELECT toDateTime(x, 'UTC') AS d, toDateTime(x, 'UTC') + INTERVAL 86399 SECOND AS e "
            "FROM (SELECT arrayJoin(['2025-02-28', '2025-03-01', '2025-03-31']) AS x)) b GROUP BY d, e" % expr)


MR_DAYS = [["=", MR_CRT, "2025-02-28"]]
MR_LAST_DAYS = [[">=", MR_CRT, "2025-02-28"], ["<", MR_CRT, "2025-04-01"]]
rollup_case("metric.rollup.month_window_by_month_hit", MR, NEEDLE, True, over_rollup_months("sumIf(amount, created_at >= m AND created_at < n)"), agg=[mr("revenue_1m")], by=[MR_MONTH], where=MR_Q1)
rollup_case("metric.rollup.to_date_by_month_hit", MR, NEEDLE, True, over_rollup_months("sumIf(amount, created_at >= m AND created_at < n)"), agg=[mr("revenue_mtd")], by=[MR_MONTH], where=MR_Q1)
rollup_case("metric.rollup.unbounded_by_month_hit", MR, NEEDLE, True, over_rollup_months("sumIf(amount, created_at < n)"), agg=[mr("revenue_running")], by=[MR_MONTH], where=MR_Q1)


@check("metric.rollup.day_window_by_day_hit")
def _(ctx):
    days = ["2025-03-30", "2025-03-31", "2025-04-01"]
    truth = ("SELECT d, %s FROM orders o CROSS JOIN (SELECT toDateTime(x, 'UTC') AS d, toDateTime(x, 'UTC') + INTERVAL 86399 SECOND AS e FROM (SELECT arrayJoin(%s) AS x)) b GROUP BY d, e"
             % ("sumIf(amount, toStartOfDay(created_at) > e - INTERVAL 30 DAY AND created_at <= e)", json.dumps(days).replace('"', "'")))
    kw = {"agg": [mr("revenue_30d")], "by": [MR_DAY], "where": [[">=", MR_CRT, "2025-03-30"], ["<", MR_CRT, "2025-04-02"]]}
    expect_used(ctx, MR, NEEDLE, True, **kw)
    by_builder_and_mcp(ctx, MR, truth, **kw)


@check("metric.rollup.day_window_by_month_refused")
def _(ctx):
    # Cube Store gives wrong windows here. A month has its own length, and Cube reads the window of another month.
    expect_refusal(ctx, MR, "rollup", agg=[mr("revenue_30d")], by=[MR_MONTH], where=MR_Q1)


def mbal(member):
    return "m:metric_balances.%s" % member


BAL_DAY = "d:metric_balances.ts@day"
BAL_MONTH = "d:metric_balances.ts@month"
BAL_QUARTER = "d:metric_balances.ts@quarter"
BAL_HOUR = "d:metric_balances.ts@hour"
BAL_REGION = "d:metric_balances.region"
BAL_MAR_DAYS = [[">=", MB_CRT, "2025-03-30"], ["<", MB_CRT, "2025-04-01"]]
BAL_MAR_DAYS_SQL = "toDate(ts) >= '2025-03-30' AND toDate(ts) < '2025-04-01'"
BAL_ROLLUP = "by_region_day"
BAL_Q1 = [[">=", MB_CRT, "2025-01-01"], ["<", MB_CRT, "2025-04-01"]]
BAL_DAYS = "(SELECT region, toDate(ts) AS d, sum(toFloat64(balance)) AS v FROM metric_balances GROUP BY region, d)"
BAL_DAY_TOTAL = "(SELECT toDate(ts) AS d, sum(toFloat64(balance)) AS v FROM metric_balances GROUP BY d)"
rollup_case("metric.semi_additive.closing_at_rollup_grain_hit", MB_, BAL_ROLLUP, True,
            "SELECT toDate(ts), sum(balance) FROM metric_balances WHERE %s GROUP BY 1" % BAL_MAR_DAYS_SQL, agg=[mbal("balance_closing")], by=[BAL_DAY], where=BAL_MAR_DAYS)
rollup_case("metric.semi_additive.closing_region_at_rollup_grain_hit", MB_, BAL_ROLLUP, True,
            "SELECT region, toDate(ts), sum(balance) FROM metric_balances WHERE %s GROUP BY 1, 2" % BAL_MAR_DAYS_SQL, agg=[mbal("balance_closing")], by=[BAL_REGION, BAL_DAY], where=BAL_MAR_DAYS)


@check("metric.semi_additive.closing_finer_miss")
def _(ctx):
    # Each account has its own hour. By hour the window of one day holds the latest balance of each account.
    where = [[">=", MB_CRT, "2025-03-30 00:00:00"], ["<", MB_CRT, "2025-03-30 04:00:00"]]
    kw = {"agg": [mbal("balance_closing")], "by": [BAL_HOUR], "where": where}
    expect_used(ctx, MB_, BAL_ROLLUP, False, **kw)
    truth = ("SELECT h, sum(b.balance) FROM (SELECT toDateTime('2025-03-30 00:00:00', 'UTC') + toIntervalHour(number) AS h FROM numbers(4)) AS t "
             "CROSS JOIN metric_balances b WHERE b.ts > h + INTERVAL 3599 SECOND - INTERVAL 1 DAY AND b.ts <= h + INTERVAL 3599 SECOND GROUP BY h")
    by_builder_and_mcp(ctx, MB_, truth, **kw)


@check("metric.semi_additive.closing_by_month_refused")
def _(ctx):
    expect_refusal(ctx, MB_, "shorter than a month", agg=[mbal("balance_closing")], by=[BAL_MONTH], where=BAL_Q1)


LAST_DAY_TRUTH = "SELECT toStartOfMonth(d) AS m, argMax(v, d) FROM %s WHERE d < '2025-04-01' GROUP BY m" % BAL_DAY_TOTAL


@check("metric.semi_additive.last_day_coarser_miss")
def _(ctx):
    kw = {"agg": [mbal("balance_last_day")], "by": [BAL_MONTH], "where": BAL_Q1}
    expect_used(ctx, MB_, BAL_ROLLUP, False, **kw)
    by_builder_and_mcp(ctx, MB_, LAST_DAY_TRUTH, label="month", **kw)
    by_builder_and_mcp(ctx, MB_, "SELECT toStartOfQuarter(d) AS q, argMax(v, d) FROM %s WHERE d < '2025-04-01' GROUP BY q" % BAL_DAY_TOTAL, agg=[mbal("balance_last_day")], by=[BAL_QUARTER], where=BAL_Q1, label="quarter")
    # It adds up over regions at one day, and it is not the sum of the months.
    by_builder_and_mcp(ctx, MB_, "SELECT region, toStartOfMonth(d) AS m, argMax(v, d) FROM %s WHERE d < '2025-04-01' GROUP BY region, m" % BAL_DAYS, agg=[mbal("balance_last_day")], by=[BAL_REGION, BAL_MONTH], where=BAL_Q1, label="region and month")


@check("metric.semi_additive.last_day_at_day_and_total")
def _(ctx):
    by_builder_and_mcp(ctx, MB_, "SELECT toDate(ts), sum(balance) FROM metric_balances WHERE %s GROUP BY 1" % BAL_MAR_DAYS_SQL, agg=[mbal("balance_last_day")], by=[BAL_DAY], where=BAL_MAR_DAYS, label="day")
    by_builder_and_mcp(ctx, MB_, "SELECT argMax(v, d) FROM %s" % BAL_DAY_TOTAL, agg=[mbal("balance_last_day")], label="total")


@check("metric.semi_additive.is_not_additive_over_time")
def _(ctx):
    closing = scalar("SELECT argMax(v, d) FROM %s" % BAL_DAY_TOTAL)
    got = ctx.mb.mbql(MB_, agg=[mbal("balance_last_day")])["rows"][0][0]
    total = ctx.mb.mbql(MB_, agg=[mbal("balance_total")])["rows"][0][0]
    assert_rows([[got]], [[closing]])
    if abs(got - total) < 1:
        raise Fail("the closing balance %s equals the sum of all days %s" % (got, total))


rollup_case("metric.semi_additive.daily_average_at_rollup_grain_hit", MB_, BAL_ROLLUP, True,
            "SELECT region, toDate(ts), sum(toFloat64(balance)) / nullIf(uniqExact(toDate(ts)), 0) FROM metric_balances WHERE %s GROUP BY 1, 2" % BAL_MAR_DAYS_SQL,
            agg=[mbal("balance_daily_average")], by=[BAL_REGION, BAL_DAY], where=BAL_MAR_DAYS)
rollup_case("metric.semi_additive.daily_average_coarser_miss", MB_, BAL_ROLLUP, False,
            "SELECT toStartOfMonth(ts), sum(toFloat64(balance)) / nullIf(uniqExact(toDate(ts)), 0) FROM metric_balances WHERE toDate(ts) < '2025-04-01' GROUP BY 1",
            agg=[mbal("balance_daily_average")], by=[BAL_MONTH], where=BAL_Q1)
rollup_case("metric.semi_additive.total_of_one_day_over_regions_hit", MB_, BAL_ROLLUP, True,
            "SELECT toDate(ts), sum(balance) FROM metric_balances WHERE %s GROUP BY 1" % BAL_MAR_DAYS_SQL, agg=[mbal("balance_total")], by=[BAL_DAY], where=BAL_MAR_DAYS)


# ----- a custom expression of two measures


def expression_or_refusal(ctx, table, truth, needle, tol=0.0, **kw):
    """The custom expression must give the number of the Cube metric, or stop with a clear message. The route must not change the answer."""
    ensure_table(ctx, table)
    query = ctx.mb.inner(table, **kw)
    res = ctx.mb.api("POST", "/api/dataset", {"database": ctx.mb.need_db(), "type": "query", "query": query}, ok=(200, 202, 400, 500))
    error = str(res.get("error") or "")
    route = {k: kw[k] for k in ("agg", "by", "where") if k in kw}
    mres = ctx.mcp.rpc("tools/call", {"name": "execute_query", "arguments": {"query": mcp_query(ctx.mb, table, **route)}})
    mtext = (mres.get("content") or [{}])[0].get("text", "")
    if error:
        if needle not in error:
            raise Fail("the query builder has no clear refusal: %s" % error[:400])
        if needle not in mtext:
            raise Fail("MCP has no clear refusal: %s" % mtext[:400])
        return "refused"
    if res.get("status") == "failed":
        raise Fail("the query builder failed: %s" % str(res)[:300])
    rows = ch(truth)
    assert_rows(res["data"]["rows"], rows, tol=tol, label="query builder")
    assert_rows(mcp_payload(mtext)["rows"], rows, tol=tol, label="MCP")
    return "number"


EXPRESSION_REFUSAL = "Cube cannot compute this expression"


def divide(check_id, table, a, b, expr, src, grains):
    def run(ctx):
        for g in grains:
            kw = {"agg": [["/", a, b]]}
            if g["by"]:
                kw["by"] = g["by"]
            if g["where"]:
                kw["where"] = g["where"]
            expression_or_refusal(ctx, table, grain_truth(expr, src, g), EXPRESSION_REFUSAL, **kw)
    check(check_id)(run)


divide("metric.expression.ratio_of_two_sums", MT, "m:metric_types.m_revenue", "m:metric_types.m_quantity_sum", "sum(%s) / nullIf(sum(quantity), 0)" % AMT, "orders", MT_GRAINS)
divide("metric.expression.sum_over_count", MT, "m:metric_types.m_revenue", "m:metric_types.m_count", "sum(%s) / nullIf(count(), 0)" % AMT, "orders", MT_GRAINS)
divide("metric.expression.zero_and_null_denominator", ME, "m:metric_edges.num", "m:metric_edges.den", "1.0 * sum(num) / nullIf(sum(den), 0)", "metric_edges", EDGE_GRAINS)
divide("metric.expression.distinct_over_count", MT, "m:metric_types.m_users", "m:metric_types.m_count", "1.0 * uniqExact(user_id) / nullIf(count(), 0)", "orders", MT_GRAINS)
divide("metric.expression.sum_over_distinct", MT, "m:metric_types.m_revenue", "m:metric_types.m_users", "sum(%s) / nullIf(uniqExact(user_id), 0)" % AMT, "orders", MT_GRAINS)
divide("metric.expression.filtered_over_plain", MT, "m:metric_types.m_paid_revenue", "m:metric_types.m_revenue", "sum(if(%s, %s, NULL)) / nullIf(sum(%s), 0)" % (PAID_SQL, AMT, AMT), "orders", MT_GRAINS)
divide("metric.expression.avg_over_avg", MT, "m:metric_types.m_avg_amount", "m:metric_types.m_avg_quantity", "avg(%s) / nullIf(avg(quantity), 0)" % AMT, "orders", MT_GRAINS)


@check("metric.composite.value_over_total_with_a_filter")
def _(ctx):
    """A filter applies to the value and to its total, as WHERE does for sum() OVER ()."""
    where = [["=", "d:metric_types.is_paid", True]]
    wsql = "WHERE %s" % PAID_SQL
    truth = "SELECT status, sum(%s) / (SELECT sum(%s) FROM orders %s) FROM orders %s GROUP BY status" % (AMT, AMT, wsql, wsql)
    by_builder_and_mcp(ctx, MT, truth, agg=["m:metric_types.c_share_of_total"], by=["d:metric_types.status"], where=where)


@check("metric.expression.same_as_the_cube_metric")
def _(ctx):
    for by in ([], ["d:metric_types.status"], [MT_MONTH]):
        kw = {"by": by} if by else {}
        metric = ctx.mb.mbql(MT, agg=["m:metric_types.c_ratio_of_sums"], **kw)["rows"]
        res = ctx.mb.api("POST", "/api/dataset", {"database": ctx.mb.need_db(), "type": "query",
                                                  "query": ctx.mb.inner(MT, agg=[["/", "m:metric_types.m_revenue", "m:metric_types.m_quantity_sum"]], **kw)}, ok=(200, 202, 400, 500))
        if res.get("error"):
            if EXPRESSION_REFUSAL not in str(res["error"]):
                raise Fail("no clear refusal: %s" % str(res["error"])[:300])
            continue
        assert_rows(res["data"]["rows"], metric, label="by %s" % by)


# ----- each dimension type that feeds a metric


def feed(check_id, table, member, expr, src, by, keys, where, wsql):
    """A metric by this dimension and with a filter on it."""
    def run(ctx):
        by_builder_and_mcp(ctx, table, grain_truth(expr, src, grain("by", by, keys)), agg=[member], by=by, label="group")
        by_builder_and_mcp(ctx, table, grain_truth(expr, src, grain("filter", wsql=wsql)), agg=[member], where=where, label="filter")
    check(check_id)(run)


RATIO = "sum(%s) / nullIf(sum(quantity), 0)" % AMT
CRATIO = "m:metric_types.c_ratio_of_sums"
feed("metric.dimension.string", MT, CRATIO, RATIO, "orders", ["d:metric_types.status"], ["status"], [["=", "d:metric_types.status", "shipped"]], "status = 'shipped'")
feed("metric.dimension.number", MT, CRATIO, RATIO, "orders", ["d:metric_types.quantity"], ["quantity"], [[">=", "d:metric_types.quantity", 3]], "quantity >= 3")
feed("metric.dimension.boolean", MT, CRATIO, RATIO, "orders", ["d:metric_types.is_paid"], [PAID_SQL], [["=", "d:metric_types.is_paid", True]], PAID_SQL)
feed("metric.dimension.time", MT, CRATIO, RATIO, "orders", [MT_MONTH], ["toStartOfMonth(created_at)"], [["between", MT_CRT, "2025-02-01", "2025-02-28"]], "toDate(created_at) BETWEEN '2025-02-01' AND '2025-02-28'")
feed("metric.dimension.case", MT, CRATIO, RATIO, "orders", ["d:metric_types.size"], [SIZE], [["=", "d:metric_types.size", "large"]], "quantity >= 3")
feed("metric.dimension.joined_string", MT, "m:metric_types.m_revenue", "sum(o.amount)", JOIN_USERS, ["d:metric_types>metric_customers.city"], ["u.city"], [["=", "d:metric_types>metric_customers.city", "Austin"]], "u.city = 'Austin'")

# A customer with no order has a count of 0: the count of the join reads no order for this customer.
USER_LIFETIME = "users u LEFT JOIN (SELECT user_id, count() AS m FROM orders GROUP BY user_id) c ON u.id = c.user_id"
LIFETIME = "ifNull(c.m, 0)"
feed("metric.dimension.sub_query", MC, "m:metric_customers.customers", "count()", USER_LIFETIME, ["d:metric_customers.lifetime_orders"], [LIFETIME], [[">=", "d:metric_customers.lifetime_orders", 25]], LIFETIME + " >= 25")
feed("metric.dimension.geo", MC, "m:metric_customers.customers", "count()", "users u", ["d:metric_customers.location__latitude", "d:metric_customers.location__longitude"], ["u.latitude", "u.longitude"],
     [["=", "d:metric_customers.location__latitude", 30.27]], "u.latitude = 30.27")
USER_REVENUE = "users u LEFT JOIN orders o ON u.id = o.user_id"
feed("metric.dimension.geo_with_a_joined_metric", MC, "m:metric_customers.revenue", "sum(o.amount)", USER_REVENUE, ["d:metric_customers.location__latitude", "d:metric_customers.location__longitude"], ["u.latitude", "u.longitude"],
     [["=", "d:metric_customers.location__latitude", 30.27]], "u.latitude = 30.27")
feed("metric.dimension.ratio_by_sub_query", MC, "m:metric_customers.conversion", "1.0 * countIf(p.n > 0) / nullIf(count(), 0)",
     USER_LIFETIME + " LEFT JOIN (SELECT user_id, count() AS n FROM orders WHERE status IN ('completed', 'shipped') "
     "AND created_at < toDateTime('2025-02-01 00:00:00', 'UTC') GROUP BY user_id) p ON u.id = p.user_id",
     ["d:metric_customers.lifetime_orders"], [LIFETIME], [[">=", "d:metric_customers.lifetime_orders", 25]], LIFETIME + " >= 25")


# ----- the whole catalog


METRIC_MODEL = os.path.join(REPO, "dev", "cube", "model", "metrics.yml")


@check("metric.model.stock_form")
def _(ctx):
    """No metric asks for a driver feature. The key date_only and the name of the driver are the only ways that a model can do this."""
    with open(METRIC_MODEL, encoding="utf-8") as f:
        text = f.read()
    for word in ("date_only", "metabase", "__cubeJoinField", "cube-clickhouse", "FILTER_PARAMS"):
        if word.lower() in text.lower():
            raise Fail("the model has %s" % word)


@check("metric.mcp.discovery")
def _(ctx):
    """An agent finds each public measure of the catalog through MCP, with its description. It does not find a private one."""
    status, text, _h = http("GET", CUBE + "/cubejs-api/v1/meta")
    cubes = json.loads(text)["cubes"]
    for name in (MT, MC, MCM, MS, ME, MR, MB_, MX, MV):
        table = ctx.mb.table(name)
        res = ctx.mcp.call("browse_data", action="get_fields", table_ids=[table["id"]])
        blob = json.dumps(res)
        cube = next(c for c in cubes if c["name"] == name)
        for member in cube["measures"]:
            title = member.get("shortTitle") or member["name"].split(".")[1]
            short = member["name"].split(".")[1]
            if not member.get("public", True) or short == "m_private_count":
                continue
            entity = ctx.mb.entity(ctx.mb.measures, name, short)
            if not entity:
                raise Fail("%s has no measure in Metabase" % member["name"])
            if entity["name"] not in blob and title not in blob:
                raise Fail("MCP get_fields of %s has no %s" % (name, member["name"]))
        if name == MT and "m_private_count" in blob:
            raise Fail("MCP shows a private measure")


# ----------------------------------------------------------------- the catalog of pre-aggregation forms
# Each check names the exact set of rollups. A hit on a wrong rollup and a read from ClickHouse both fail, because usedPreAggregations is exact.

PA_PAID = "status IN ('completed', 'shipped')"
PAT = "pa_types"
PAG = "pa_grain"
PAP = "pa_partitions"
PAO = "pa_options"
PAF = "pa_fresh"
NY = "America/New_York"
PA_CHECK_IDS = []


def pa_load(query, url=None, timeout=420):
    """The reply of /load. Cube answers `Continue wait` while it builds a rollup."""
    end = time.time() + timeout
    while time.time() < end:
        status, text, _h = http("POST", (url or CUBE) + "/cubejs-api/v1/load", {"query": query})
        res = json.loads(text)
        if res.get("error") == "Continue wait":
            time.sleep(2)
            continue
        if status != 200:
            raise Fail("Cube load: HTTP %s %s, request %s" % (status, text[:300], json.dumps(query)[:300]))
        return res
    raise Fail("Cube load: timeout, request %s" % json.dumps(query)[:300])


def pa_used(res):
    """The names of the rollups in the reply. Cube adds __usage_N when a query reads one rollup more than one time."""
    return sorted({re.sub(r"__usage_\d+$", "", k.split(".")[-1]) for k in (res.get("usedPreAggregations") or {})})


def pa_expect_used(res, want, query, external=True):
    got = pa_used(res)
    if got != sorted(want):
        raise Fail("expected rollups %s, Cube used %s, request %s" % (sorted(want), got, json.dumps(query)[:300]))
    if want and bool(res.get("external")) != external:
        raise Fail("expected external %s, Cube gave %s for %s" % (external, res.get("external"), got))
    if not want and res.get("external"):
        raise Fail("no rollup was expected, and Cube read Cube Store")


def pa_columns(query):
    cols = list(query.get("dimensions", []))
    cols += ["%s.%s" % (t["dimension"], t["granularity"]) for t in query.get("timeDimensions", []) if t.get("granularity")]
    return cols + list(query.get("measures", []))


def pa_value(v):
    if isinstance(v, str) and re.match(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$", v):
        return float(v)
    return v


def pa_rows(res, query):
    return [[pa_value(row.get(c)) for c in pa_columns(query)] for row in res["data"]]


def pa_query(measures, dims=(), gran=None, cube=None, date_range=None, filters=None, tz=None, segments=None):
    cube = cube or measures[0].split(".")[0]
    q = {"measures": list(measures), "dimensions": list(dims)}
    if gran or date_range:
        td = {"dimension": cube + ".created_at"}
        if gran:
            td["granularity"] = gran
        if date_range:
            td["dateRange"] = date_range
        q["timeDimensions"] = [td]
    if filters:
        q["filters"] = filters
    if tz:
        q["timezone"] = tz
    if segments:
        q["segments"] = segments
    return q


def PA(check_id, query, truth, used, external=True, tol=0.0, then=None):
    """Through the REST API: the exact rollups and the truth."""
    def run(ctx):
        res = pa_load(query)
        pa_expect_used(res, used, query, external)
        assert_rows(pa_rows(res, query), ch(truth), tol=tol)
        if then:
            then(ctx, res)
    PA_CHECK_IDS.append(check_id)
    check(check_id)(run)


def PM(check_id, table, truth, used, external=True, setting=None, tol=0.0, **kw):
    """Through Metabase: the request that the driver writes reads the exact rollups, and the rows of Metabase are the truth."""
    def run(ctx):
        ensure_table(ctx, table)
        restore = {k: ctx.mb.api("GET", "/api/setting/" + k) for k in (setting or {})}
        for k, v in (setting or {}).items():
            ctx.mb.api("PUT", "/api/setting/" + k, {"value": v})
        try:
            query = ctx.mb.inner(table, **kw)
            request = request_of(compile_query(ctx, query))
            res = pa_load(request)
            pa_expect_used(res, used, request, external)
            assert_rows(ctx.mb.raw(query)["rows"], ch(truth), tol=tol)
        finally:
            for k, v in restore.items():
                ctx.mb.api("PUT", "/api/setting/" + k, {"value": v if isinstance(v, str) and v else None})
    PA_CHECK_IDS.append(check_id)
    check(check_id)(run)


# ----- each measure type, and the rule of additive and non-additive measures

PA_ADDITIVE = {
    "count": "count()",
    "revenue": "sum(amount)",
    "min_amount": "min(amount)",
    "max_amount": "max(amount)",
    "paid_count": "countIf(%s)" % PA_PAID,
    "paid_revenue": "sum(if(%s, amount, NULL))" % PA_PAID,
    "per_order": "sum(toFloat64(amount)) / nullIf(count(), 0)",
}
PA_NON_ADDITIVE = {
    "buyers": "uniqExact(user_id)",
    "avg_amount": "avg(toFloat64(amount))",
    "median_amount": "quantile(0.5)(toFloat64(amount))",
    "first_status": "min(status)",
    "last_at": "max(created_at)",
    "any_completed": "max(status = 'completed')",
}
for name, expr in PA_ADDITIVE.items():
    m = ["%s.%s" % (PAT, name)]
    hit = ["pa_types_additive_day"]
    PA("preagg.additive.%s.same_grain_hit" % name, pa_query(m, ["pa_types.status"], "day"), "SELECT status, toDate(created_at), %s FROM orders GROUP BY 1, 2" % expr, hit)
    PA("preagg.additive.%s.coarser_time_hit" % name, pa_query(m, ["pa_types.status"], "month"), "SELECT status, toStartOfMonth(created_at), %s FROM orders GROUP BY 1, 2" % expr, hit)
    PA("preagg.additive.%s.coarser_dimension_hit" % name, pa_query(m, [], "day"), "SELECT toDate(created_at), %s FROM orders GROUP BY 1" % expr, hit)
    PA("preagg.additive.%s.total_hit" % name, pa_query(m), "SELECT %s FROM orders" % expr, hit)
    PA("preagg.additive.%s.finer_miss" % name, pa_query(m, [], "hour"), "SELECT toStartOfHour(created_at), %s FROM orders GROUP BY 1" % expr, [])
    PA("preagg.additive.%s.other_dimension_miss" % name, pa_query(m, ["pa_types.product_id"]), "SELECT product_id, %s FROM orders GROUP BY 1" % expr, [])
for name, expr in PA_NON_ADDITIVE.items():
    m = ["%s.%s" % (PAT, name)]
    PA("preagg.non_additive.%s.same_grain_hit" % name, pa_query(m, ["pa_types.status"], "day"), "SELECT status, toDate(created_at), %s FROM orders GROUP BY 1, 2" % expr, ["pa_types_non_additive_day"], tol=1e-9)
    PA("preagg.non_additive.%s.coarser_time_miss" % name, pa_query(m, ["pa_types.status"], "month"), "SELECT status, toStartOfMonth(created_at), %s FROM orders GROUP BY 1, 2" % expr, [], tol=1e-9)
    PA("preagg.non_additive.%s.coarser_dimension_miss" % name, pa_query(m, [], "day"), "SELECT toDate(created_at), %s FROM orders GROUP BY 1" % expr, [], tol=1e-9)
    PA("preagg.non_additive.%s.total_miss" % name, pa_query(m), "SELECT %s FROM orders" % expr, [], tol=1e-9)

PA("preagg.non_additive.ratio_with_a_distinct_leaf_miss", pa_query(["pa_types.per_buyer"], ["pa_types.status"], "day"),
   "SELECT status, toDate(created_at), sum(toFloat64(amount)) / nullIf(uniqExact(user_id), 0) FROM orders GROUP BY 1, 2", [])
PA("preagg.non_additive.mixed_with_additive_miss", pa_query(["pa_types.revenue", "pa_types.buyers"], ["pa_types.status"], "day"),
   "SELECT status, toDate(created_at), sum(amount), uniqExact(user_id) FROM orders GROUP BY 1, 2", [])


PA_APPROX_MODEL = """cubes:
  - name: pa_approx
    sql_table: shop.orders
    refresh_key: { every: 1 hour }
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: status, sql: status, type: string }
    measures:
      - { name: buyers_approx, type: count_distinct_approx, sql: user_id }
      - { name: orders, type: count }
    pre_aggregations:
      - name: approx
        measures: [buyers_approx]
        dimensions: [status]
        indexes:
          - { name: idx, columns: [status] }
"""


def pa_refresh_errors(since):
    p = subprocess.run(["docker", "compose", "-f", os.path.join(REPO, "dev", "docker-compose.yml"), "logs", "cube", "--since", since], capture_output=True, text=True, timeout=120)
    return p.stdout.count("Refresh Scheduler Error")


@check("preagg.type.count_distinct_approx_stops")
def _(ctx):
    """Cube has no sketch for ClickHouse. A rollup of this type stops each query on the measure, and each run of the refresh worker.
    So this check adds the rollup for a short time. The live model has none, because it stops the refresh of each other rollup too."""
    start = time.time()
    with open(PROBE_FILE, "w", encoding="utf-8") as f:
        f.write(PA_APPROX_MODEL)
    try:
        query = pa_query(["pa_approx.buyers_approx"], ["pa_approx.status"])
        text = ""
        for _ in range(60):
            status, text, _h = http("POST", CUBE + "/cubejs-api/v1/load", {"query": query})
            if "approximate distinct count is not supported" in text:
                break
            time.sleep(2)
        else:
            raise Fail("a rollup of count_distinct_approx now works, or Cube gives another error: %s. Give this entry its full status" % text[:200])
        other = pa_query(["pa_approx.orders"], ["pa_approx.status"])
        res = pa_load(other)
        pa_expect_used(res, [], other)
        assert_rows(pa_rows(res, other), ch("SELECT status, count() FROM orders GROUP BY 1"))
        for _ in range(45):
            if pa_refresh_errors("%ds" % (time.time() - start + 1)):
                return
            time.sleep(2)
        raise Fail("the refresh worker now goes on with this rollup. Give this entry its full status")
    finally:
        os.remove(PROBE_FILE)
        for _ in range(60):
            status, text, _h = http("GET", CUBE + "/cubejs-api/v1/meta")
            if status == 200 and "pa_approx" not in text:
                break
            time.sleep(2)


# ----- rolling windows and multi-stage measures read a rollup too

PA_DAYS = ["2025-03-30", "2025-03-31", "2025-04-01"]
PA_DAY_RANGE = ["2025-03-30", "2025-04-01"]
PA_MONTHS = ["2025-04-01", "2025-05-01", "2025-06-01"]
PA_MONTH_RANGE = ["2025-04-01", "2025-06-30"]
PA("preagg.rolling.trailing_same_grain_hit", pa_query(["pa_types.rev_3m"], [], "day", date_range=PA_DAY_RANGE),
   over_days("sumIf(amount, toStartOfDay(created_at) > addMonths(e, -3) AND created_at <= e)", PA_DAYS), ["pa_types_rolling_day"])
PA("preagg.rolling.to_date_same_grain_hit", pa_query(["pa_types.rev_mtd"], [], "day", date_range=PA_DAY_RANGE),
   over_days("sumIf(amount, created_at >= toStartOfMonth(d) AND created_at <= e)", PA_DAYS), ["pa_types_rolling_day"])
PA("preagg.rolling.running_same_grain_hit", pa_query(["pa_types.rev_running"], [], "day", date_range=PA_DAY_RANGE),
   over_days("sumIf(amount, created_at <= e)", PA_DAYS), ["pa_types_rolling_day"])
PA("preagg.rolling.trailing_coarser_time_hit", pa_query(["pa_types.rev_3m"], [], "month", date_range=PA_MONTH_RANGE),
   over_months("sumIf(amount, created_at >= addMonths(m, -2) AND created_at < n)", PA_MONTHS), ["pa_types_rolling_day"])
PA("preagg.rolling.to_date_coarser_time_hit", pa_query(["pa_types.rev_mtd"], [], "month", date_range=PA_MONTH_RANGE),
   over_months("sumIf(amount, created_at >= m AND created_at < n)", PA_MONTHS), ["pa_types_rolling_day"])
PA("preagg.rolling.finer_miss", pa_query(["pa_types.rev_mtd"], [], "hour", date_range=["2025-04-01", "2025-04-01"]),
   "SELECT h, sumIf(amount, created_at >= toDateTime('2025-04-01 00:00:00', 'UTC') AND created_at < h + INTERVAL 1 HOUR) FROM orders CROSS JOIN "
   "(SELECT toDateTime('2025-04-01 00:00:00', 'UTC') + INTERVAL number HOUR AS h FROM numbers(24)) GROUP BY h", [])
PA("preagg.multi_stage.grand_total_hit", pa_query(["pa_types.rev_total", "pa_types.revenue"], ["pa_types.status"]),
   "SELECT status, (SELECT sum(amount) FROM orders), sum(amount) FROM orders GROUP BY 1", ["pa_types_additive_day"])
PA("preagg.multi_stage.time_shift_hit", pa_query(["pa_types.rev_prior"], [], "month", date_range=["2025-03-01", "2025-04-30"]),
   "SELECT m, (SELECT sum(amount) FROM orders WHERE toStartOfMonth(created_at) = addMonths(m, -1)) FROM "
   "(SELECT arrayJoin([toDate('2025-03-01'), toDate('2025-04-01')]) AS m)", ["pa_types_additive_day"])

# ----- each shape of a query on one rollup

REV = ["pa_types.revenue"]
HIT = ["pa_types_additive_day"]
PA("preagg.shape.date_range_on_month_boundary_hit", pa_query(REV, [], None, date_range=["2025-03-01", "2025-03-31"]),
   "SELECT sum(amount) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-04-01'", HIT)
PA("preagg.shape.date_range_on_day_boundary_hit", pa_query(REV, [], "day", date_range=["2025-03-01", "2025-03-02"]),
   "SELECT toDate(created_at), sum(amount) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-03-03' GROUP BY 1", HIT)
PA("preagg.shape.date_range_inside_a_bucket_miss", pa_query(REV, [], None, date_range=["2025-03-01T06:00:00", "2025-03-02T06:00:00"]),
   "SELECT sum(amount) FROM orders WHERE created_at >= '2025-03-01 06:00:00' AND created_at <= '2025-03-02 06:00:00'", [])
PA("preagg.shape.month_bucket_with_a_day_range_hit", pa_query(REV, [], "month", date_range=["2025-03-10", "2025-04-20"]),
   "SELECT toStartOfMonth(created_at), sum(amount) FROM orders WHERE created_at >= '2025-03-10' AND created_at < '2025-04-21' GROUP BY 1", HIT)
PA("preagg.shape.filter_on_a_rollup_dimension_hit",
   pa_query(REV, [], "month", filters=[{"member": "pa_types.status", "operator": "equals", "values": ["shipped"]}]),
   "SELECT toStartOfMonth(created_at), sum(amount) FROM orders WHERE status = 'shipped' GROUP BY 1", HIT)
PA("preagg.shape.filter_on_another_dimension_miss",
   pa_query(REV, [], "month", filters=[{"member": "pa_types.product_id", "operator": "equals", "values": ["4"]}]),
   "SELECT toStartOfMonth(created_at), sum(amount) FROM orders WHERE product_id = 4 GROUP BY 1", [])
PA("preagg.shape.segment_in_the_rollup_hit", pa_query(["pa_types.peak_paid"], ["pa_types.status"], segments=["pa_types.paid"]),
   "SELECT status, max(amount) FROM orders WHERE %s GROUP BY 1" % PA_PAID, ["pa_types_paid_segment"])
PA("preagg.shape.segment_left_out_miss", pa_query(["pa_types.peak_paid"], ["pa_types.status"]), "SELECT status, max(amount) FROM orders GROUP BY 1", [])
PA("preagg.shape.segment_not_in_the_rollup_miss", pa_query(REV, ["pa_types.status"], segments=["pa_types.paid"]),
   "SELECT status, sum(amount) FROM orders WHERE %s GROUP BY 1" % PA_PAID, [])
PA("preagg.shape.view_hit", pa_query(["pa_view.revenue"], ["pa_view.status"], "month"), "SELECT status, toStartOfMonth(created_at), sum(amount) FROM orders GROUP BY 1, 2", HIT)
PA("preagg.shape.view_finer_miss", pa_query(["pa_view.revenue"], [], "hour"), "SELECT toStartOfHour(created_at), sum(amount) FROM orders GROUP BY 1", [])
PA("preagg.shape.view_other_dimension_miss", pa_query(["pa_view.revenue"], ["pa_view.product_id"]), "SELECT product_id, sum(amount) FROM orders GROUP BY 1", [])
PA("preagg.shape.view_non_additive_coarser_miss", pa_query(["pa_view.buyers"], [], "month"), "SELECT toStartOfMonth(created_at), uniqExact(user_id) FROM orders GROUP BY 1", [])
PA("preagg.shape.view_joined_dimension_hit", pa_query(["pa_view.count"], ["pa_view.pa_customers_state"]),
   "SELECT u.state, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1", ["pa_types_by_state_day"])
PA("preagg.shape.view_joined_dimension_miss", pa_query(["pa_view.count"], ["pa_view.pa_customers_city"]),
   "SELECT u.city, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1", [])
PA("preagg.shape.extended_cube_hit", pa_query(["pa_extended.revenue"], ["pa_extended.status"], "month"), "SELECT status, toStartOfMonth(created_at), sum(amount) FROM orders GROUP BY 1, 2", ["pa_extended_additive_day"])
PA("preagg.shape.extended_cube_miss", pa_query(["pa_extended.revenue"], ["pa_extended.product_id"]), "SELECT product_id, sum(amount) FROM orders GROUP BY 1", [])
PA("preagg.shape.join_dimension_in_the_rollup_hit", pa_query(["pa_types.count"], ["pa_customers.state"]),
   "SELECT u.state, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1", ["pa_types_by_state_day"])
PA("preagg.shape.join_dimension_in_the_rollup_coarser_hit", pa_query(["pa_types.count"], ["pa_customers.state"], "month", cube=PAT),
   "SELECT u.state, toStartOfMonth(o.created_at), count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1, 2", ["pa_types_by_state_day"])
PA("preagg.shape.join_dimension_not_in_the_rollup_miss", pa_query(["pa_types.count"], ["pa_customers.city"]),
   "SELECT u.city, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1", [])
PA("preagg.shape.join_with_fan_out_miss", pa_query(["pa_types.count", "pa_payments.total"]), "SELECT count(), sum(amount) FROM orders", [])

# ----- the time zone of a query. Cube builds one table for each time zone.

PA_NY_DAY = "toDate(toTimeZone(created_at, '%s'))" % NY
PA_NY_MONTH = "toStartOfMonth(toTimeZone(created_at, '%s'))" % NY
PA("preagg.time_zone.same_grain_hit", pa_query(REV, [], "day", date_range=["2025-03-01", "2025-03-03"], tz=NY),
   "SELECT %s AS d, sum(amount) FROM orders WHERE d BETWEEN '2025-03-01' AND '2025-03-03' GROUP BY 1" % PA_NY_DAY, HIT)
PA("preagg.time_zone.coarser_hit", pa_query(REV, [], "month", tz=NY), "SELECT %s, sum(amount) FROM orders GROUP BY 1" % PA_NY_MONTH, HIT)
PA("preagg.time_zone.boundary_of_a_local_day_hit", pa_query(REV, [], None, date_range=["2025-03-01", "2025-03-01"], tz=NY),
   "SELECT sum(amount) FROM orders WHERE %s = '2025-03-01'" % PA_NY_DAY, HIT)
PA("preagg.time_zone.inside_a_local_day_miss", pa_query(REV, [], None, date_range=["2025-03-01T06:00:00", "2025-03-01T18:00:00"], tz=NY),
   "SELECT sum(amount) FROM orders WHERE created_at >= toDateTime('2025-03-01 06:00:00', '%s') AND created_at <= toDateTime('2025-03-01 18:00:00', '%s')" % (NY, NY), [])
PA("preagg.time_zone.partitioned_hit", pa_query(["pa_partitions.rev_month"], [], "month", tz=NY), "SELECT %s, sum(amount) FROM orders GROUP BY 1" % PA_NY_MONTH, ["pa_partitions_part_month"])
PA("preagg.time_zone.finer_miss", pa_query(REV, [], "hour", date_range=["2025-03-01", "2025-03-01"], tz=NY),
   "SELECT toStartOfHour(toTimeZone(created_at, '%s')), sum(amount) FROM orders WHERE %s = '2025-03-01' GROUP BY 1" % (NY, PA_NY_DAY), [])

# ----- the same shapes through Metabase

PD = "d:pa_types.created_at"
PS = "d:pa_types.status"
PR = "m:pa_types.revenue"
ZONE = {"report-timezone": NY}
PM("preagg.metabase.same_grain_hit", PAT, "SELECT status, toDate(created_at), sum(amount) FROM orders GROUP BY 1, 2", HIT, agg=[PR], by=[PS, PD + "@day"])
PM("preagg.metabase.coarser_hit", PAT, "SELECT toStartOfMonth(created_at), sum(amount) FROM orders GROUP BY 1", HIT, agg=[PR], by=[PD + "@month"])
PM("preagg.metabase.finer_miss", PAT, "SELECT toStartOfHour(created_at), sum(amount) FROM orders GROUP BY 1", [], agg=[PR], by=[PD + "@hour"])
PM("preagg.metabase.other_dimension_miss", PAT, "SELECT product_id, sum(amount) FROM orders GROUP BY 1", [], agg=[PR], by=["d:pa_types.product_id"])
PM("preagg.metabase.non_additive_same_grain_hit", PAT, "SELECT status, toDate(created_at), uniqExact(user_id) FROM orders GROUP BY 1, 2", ["pa_types_non_additive_day"], agg=["m:pa_types.buyers"], by=[PS, PD + "@day"])
PM("preagg.metabase.non_additive_coarser_miss", PAT, "SELECT toStartOfMonth(created_at), uniqExact(user_id) FROM orders GROUP BY 1", [], agg=["m:pa_types.buyers"], by=[PD + "@month"])
PM("preagg.metabase.date_range_on_a_boundary_hit", PAT, "SELECT sum(amount) FROM orders WHERE toDate(created_at) BETWEEN '2025-03-05' AND '2025-03-09'", HIT, agg=[PR], where=[["between", PD, "2025-03-05", "2025-03-09"]])
PM("preagg.metabase.date_range_inside_a_bucket_miss", PAT, "SELECT sum(amount) FROM orders WHERE created_at >= '2025-03-05 06:00:00' AND created_at < '2025-03-09 06:00:00'", [],
   agg=[PR], where=[[">=", PD, "2025-03-05T06:00:00"], ["<", PD, "2025-03-09T06:00:00"]])
PM("preagg.metabase.filter_on_a_rollup_dimension_hit", PAT, "SELECT sum(amount) FROM orders WHERE status = 'shipped'", HIT, agg=[PR], where=[["=", PS, "shipped"]])
PM("preagg.metabase.filter_on_another_dimension_miss", PAT, "SELECT sum(amount) FROM orders WHERE product_id = 4", [], agg=[PR], where=[["=", "d:pa_types.product_id", 4]])
PM("preagg.metabase.view_hit", "pa_view", "SELECT toStartOfMonth(created_at), sum(amount) FROM orders GROUP BY 1", HIT, agg=["m:pa_view.revenue"], by=["d:pa_view.created_at@month"])
PM("preagg.metabase.view_miss", "pa_view", "SELECT product_id, sum(amount) FROM orders GROUP BY 1", [], agg=["m:pa_view.revenue"], by=["d:pa_view.product_id"])
PM("preagg.metabase.extended_cube_hit", "pa_extended", "SELECT toStartOfMonth(created_at), sum(amount) FROM orders GROUP BY 1", ["pa_extended_additive_day"], agg=["m:pa_extended.revenue"], by=["d:pa_extended.created_at@month"])
PM("preagg.metabase.join_through_a_view_hit", "pa_view", "SELECT u.state, count() FROM orders o JOIN users u ON o.user_id = u.id GROUP BY 1", ["pa_types_by_state_day"],
   agg=["m:pa_view.count"], by=["d:pa_view.pa_customers_state"])
# Metabase gives a bucket with its offset, so each truth here is the instant of the local start of the bucket, in UTC.
PA_NY_MONTH_START = "toTimeZone(toDateTime(%s, '%s'), 'UTC')" % (PA_NY_MONTH, NY)
PA_NY_DAY_START = "toTimeZone(toDateTime(%s, '%s'), 'UTC')" % (PA_NY_DAY, NY)
PA_NY_HOUR_START = "toTimeZone(toStartOfHour(toTimeZone(created_at, '%s')), 'UTC')" % NY
PM("preagg.metabase.time_zone_month_hit", PAT, "SELECT %s, sum(amount) FROM orders GROUP BY 1" % PA_NY_MONTH_START, HIT, setting=ZONE, agg=[PR], by=[PD + "@month"])
PM("preagg.metabase.time_zone_day_hit", PAT, "SELECT %s, sum(amount) FROM orders WHERE %s BETWEEN '2025-03-05' AND '2025-03-09' GROUP BY 1" % (PA_NY_DAY_START, PA_NY_DAY), HIT, setting=ZONE,
   agg=[PR], by=[PD + "@day"], where=[["between", PD, "2025-03-05", "2025-03-09"]])
PM("preagg.metabase.time_zone_range_without_a_bucket_hit", PAT, "SELECT sum(amount) FROM orders WHERE %s BETWEEN '2025-03-05' AND '2025-03-09'" % PA_NY_DAY, HIT, setting=ZONE,
   agg=[PR], where=[["between", PD, "2025-03-05", "2025-03-09"]])
PM("preagg.metabase.time_zone_range_next_to_a_filter_hit", PAT, "SELECT sum(amount) FROM orders WHERE status = 'shipped' AND %s BETWEEN '2025-03-05' AND '2025-03-09'" % PA_NY_DAY, HIT, setting=ZONE,
   agg=[PR], where=[["=", PS, "shipped"], ["between", PD, "2025-03-05", "2025-03-09"]])
# One bound is no date range. Cube reads it as UTC, so the driver writes it in UTC, and the rollup cannot read it.
PM("preagg.metabase.time_zone_one_bound_miss", PAT, "SELECT sum(amount) FROM orders WHERE created_at >= toDateTime('2025-03-05 00:00:00', '%s')" % NY, [], setting=ZONE,
   agg=[PR], where=[[">=", PD, "2025-03-05T00:00:00"]])
PM("preagg.metabase.time_zone_finer_miss", PAT, "SELECT %s, sum(amount) FROM orders WHERE %s = '2025-03-05' GROUP BY 1" % (PA_NY_HOUR_START, PA_NY_DAY), [], setting=ZONE,
   agg=[PR], by=[PD + "@hour"], where=[["=", PD, "2025-03-05"]])

# ----- each granularity. The coarse rollups come first in the model, so a rollup of a wrong grain fails here.

PA_GRAINS = {
    "year": "toStartOfYear(created_at)",
    "quarter": "toStartOfQuarter(created_at)",
    "month": "toStartOfMonth(created_at)",
    "week": "toStartOfWeek(created_at, 1)",
    "day": "toDate(created_at)",
    "hour": "toStartOfHour(created_at)",
    "minute": "toStartOfMinute(created_at)",
    "second": "created_at",
}
for name, expr in PA_GRAINS.items():
    PA("preagg.granularity.%s_hit" % name, pa_query(["pa_grain.revenue", "pa_grain.count"], [], name), "SELECT %s, sum(amount), count() FROM orders GROUP BY 1" % expr, ["pa_grain_by_%s" % name])
PA("preagg.granularity.week_rollup_week_hit", pa_query(["pa_week.revenue"], [], "week"), "SELECT toStartOfWeek(created_at, 1), sum(amount) FROM orders GROUP BY 1", ["pa_week_only_week"])
PA("preagg.granularity.week_rollup_month_miss", pa_query(["pa_week.revenue"], [], "month"), "SELECT toStartOfMonth(created_at), sum(amount) FROM orders GROUP BY 1", [])
PA("preagg.granularity.week_rollup_year_miss", pa_query(["pa_week.revenue"], [], "year"), "SELECT toStartOfYear(created_at), sum(amount) FROM orders GROUP BY 1", [])
PA("preagg.granularity.week_rollup_day_miss", pa_query(["pa_week.revenue"], [], "day"), "SELECT toDate(created_at), sum(amount) FROM orders GROUP BY 1", [])

# ----- each partition granularity and the build range

PA_DAY_IN_RANGE = "created_at >= '2025-01-01' AND created_at < '2025-02-01'"
PA("preagg.partition.hour_hit", pa_query(["pa_partitions.rev_hour"], [], "hour", date_range=["2025-01-01", "2025-01-02"]),
   "SELECT toStartOfHour(created_at), sum(amount) FROM orders WHERE created_at >= '2025-01-01' AND created_at < '2025-01-03' GROUP BY 1", ["pa_partitions_part_hour"])
PA("preagg.partition.day_hit", pa_query(["pa_partitions.rev_day"], [], "day", date_range=["2025-01-01", "2025-01-31"]),
   "SELECT toDate(created_at), sum(amount) FROM orders WHERE %s GROUP BY 1" % PA_DAY_IN_RANGE, ["pa_partitions_part_day"])
PA("preagg.partition.week_hit", pa_query(["pa_partitions.rev_week"], [], "week", date_range=["2025-03-03", "2025-03-30"]),
   "SELECT toStartOfWeek(created_at, 1), sum(amount) FROM orders WHERE created_at >= '2025-03-03' AND created_at < '2025-03-31' GROUP BY 1", ["pa_partitions_part_week"])
PA("preagg.partition.month_hit", pa_query(["pa_partitions.rev_month"], [], "month"), "SELECT toStartOfMonth(created_at), sum(amount) FROM orders GROUP BY 1", ["pa_partitions_part_month"])
PA("preagg.partition.quarter_hit", pa_query(["pa_partitions.rev_quarter"], [], "quarter"), "SELECT toStartOfQuarter(created_at), sum(amount) FROM orders GROUP BY 1", ["pa_partitions_part_quarter"])
PA("preagg.partition.year_hit", pa_query(["pa_partitions.rev_year"], [], "year"), "SELECT toStartOfYear(created_at), sum(amount) FROM orders GROUP BY 1", ["pa_partitions_part_year"])
PA("preagg.partition.range_over_two_partitions_hit", pa_query(["pa_partitions.rev_month"], [], "day", date_range=["2025-01-30", "2025-02-02"]),
   "SELECT toDate(created_at), sum(amount) FROM orders WHERE created_at >= '2025-01-30' AND created_at < '2025-02-03' GROUP BY 1", ["pa_partitions_part_month"])
PA("preagg.partition.range_inside_a_day_miss", pa_query(["pa_partitions.rev_month"], [], None, date_range=["2025-01-30T06:00:00", "2025-02-02T06:00:00"]),
   "SELECT sum(amount) FROM orders WHERE created_at >= '2025-01-30 06:00:00' AND created_at <= '2025-02-02 06:00:00'", [])
PA("preagg.build_range.inside_hit", pa_query(["pa_partitions.rev_range"], [], "month", date_range=["2025-01-01", "2025-03-31"]),
   "SELECT toStartOfMonth(created_at), sum(amount) FROM orders WHERE created_at < '2025-04-01' GROUP BY 1", ["pa_partitions_part_range"])


def pa_outside_the_range(query, truth, want):
    """The docs warn: "Cube will not return results outside of the defined build range." This shows the exact number, so a change of Cube fails this check."""
    def run(ctx):
        res = pa_load(query)
        pa_expect_used(res, ["pa_partitions_part_range"], query)
        got = pa_rows(res, query)
        assert_rows(got, ch(want), label="the rows of the build range")
        try:
            assert_rows(got, ch(truth))
        except Fail:
            return
        raise Fail("Cube now reads rows out of the build range: %s. Give this entry its full status" % got[:3])
    return run


check("preagg.build_range.whole_table_reads_only_the_range")(pa_outside_the_range(
    pa_query(["pa_partitions.rev_range"]), "SELECT sum(amount) FROM orders", "SELECT sum(amount) FROM orders WHERE created_at < '2025-04-01'"))
check("preagg.build_range.date_range_over_the_end_reads_only_the_range")(pa_outside_the_range(
    pa_query(["pa_partitions.rev_range"], [], "month", date_range=["2025-03-01", "2025-04-30"]),
    "SELECT toStartOfMonth(created_at), sum(amount) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-05-01' GROUP BY 1",
    "SELECT toStartOfMonth(created_at), sum(amount) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-04-01' GROUP BY 1"))

# ----- each option

OPT = "pa_options."
PA("preagg.option.strict_date_range_on_a_boundary_hit", pa_query([OPT + "rev_strict"], [], "day", date_range=["2025-03-01", "2025-03-02"]),
   "SELECT toDate(created_at), sum(amount) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-03-03' GROUP BY 1", ["pa_options_strict"])
PA("preagg.option.strict_date_range_inside_a_bucket_miss", pa_query([OPT + "rev_strict"], [], None, date_range=["2025-03-01T06:00:00", "2025-03-02T06:00:00"]),
   "SELECT sum(amount) FROM orders WHERE created_at >= '2025-03-01 06:00:00' AND created_at <= '2025-03-02 06:00:00'", [])
PA("preagg.option.non_strict_on_a_boundary_hit", pa_query([OPT + "rev_nonstrict"], [], "day", date_range=["2025-03-01", "2025-03-02"]),
   "SELECT toDate(created_at), sum(amount) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-03-03' GROUP BY 1", ["pa_options_nonstrict"])


@check("preagg.option.non_strict_inside_a_bucket_reads_whole_buckets")
def _(ctx):
    """allow_non_strict_date_range_match: true is the slight discrepancy of the docs. Cube reads only the buckets that start in the range."""
    query = pa_query([OPT + "rev_nonstrict"], [], None, date_range=["2025-03-01T06:00:00", "2025-03-02T06:00:00"])
    res = pa_load(query)
    pa_expect_used(res, ["pa_options_nonstrict"], query)
    got = pa_rows(res, query)
    assert_rows(got, ch("SELECT sum(amount) FROM orders WHERE toDate(created_at) = '2025-03-02'"), label="whole buckets")
    truth = ch("SELECT sum(amount) FROM orders WHERE created_at >= '2025-03-01 06:00:00' AND created_at <= '2025-03-02 06:00:00'")
    if float(got[0][0]) == float(truth[0][0]):
        raise Fail("the option now gives the truth. Give this entry its full status")


def pa_ch_statements(like):
    ch_write("SYSTEM FLUSH LOGS")
    return ch("SELECT count() FROM system.query_log WHERE type = 'QueryFinish' AND event_time > now() - INTERVAL 7 DAY AND query LIKE '%s'" % like)[0][0]


def pa_external_false(ctx, res):
    tables = ch("SELECT name FROM system.tables WHERE database = 'dev_pre_aggregations' AND name LIKE 'pa_options_internal%'")
    if not tables:
        raise Fail("no table of the rollup in ClickHouse")


PA("preagg.option.external_false_hit", pa_query([OPT + "rev_internal"], [OPT + "status"]), "SELECT status, sum(amount) FROM orders GROUP BY 1", ["pa_options_internal"], external=False, then=pa_external_false)
PA("preagg.option.external_false_coarser_hit", pa_query([OPT + "rev_internal"]), "SELECT sum(amount) FROM orders", ["pa_options_internal"], external=False)
PA("preagg.option.external_false_miss", pa_query([OPT + "rev_internal"], [OPT + "user_id"]), "SELECT user_id, sum(amount) FROM orders GROUP BY 1", [])


def pa_read_only(ctx, res):
    """A read-only rollup writes nothing to ClickHouse. Another rollup writes a temporary table only with a driver that has no stream."""
    if pa_ch_statements("CREATE TABLE%pa_options_readonly%"):
        raise Fail("a rollup with read_only made a table in ClickHouse")
    status, text, _h = http("GET", CUBE + "/cubejs-api/v1/sql?query=" + urllib.parse.quote(json.dumps(pa_query([OPT + "rev_readonly"], [OPT + "status"]))))
    meta = json.loads(text)["sql"]["preAggregations"][0]
    if meta.get("readOnly") is not True:
        raise Fail("Cube does not read the option: %s" % meta.get("readOnly"))


PA("preagg.option.read_only_hit", pa_query([OPT + "rev_readonly"], [OPT + "status"]), "SELECT status, sum(amount) FROM orders GROUP BY 1", ["pa_options_readonly"], then=pa_read_only)
PA("preagg.option.scheduled_refresh_false_hit", pa_query([OPT + "rev_manual"], [OPT + "status"]), "SELECT status, sum(amount) FROM orders GROUP BY 1", ["pa_options_manual"])


def pa_meta(member, dims):
    status, text, _h = http("GET", CUBE + "/cubejs-api/v1/sql?query=" + urllib.parse.quote(json.dumps({"measures": [member], "dimensions": dims})))
    if status != 200:
        raise Fail("Cube sql: HTTP %s %s" % (status, text[:200]))
    return json.loads(text)["sql"]["preAggregations"][0]


def pa_unique(ctx, res):
    meta = pa_meta(OPT + "rev_unique", [OPT + "status"])
    if meta.get("uniqueKeyColumns") != ["pa_options__status"]:
        raise Fail("Cube does not read unique_key_columns: %s" % meta.get("uniqueKeyColumns"))


def pa_index(member, kind, columns):
    def run(ctx, res):
        meta = pa_meta(OPT + member, [OPT + "status"])
        indexes = meta.get("createTableIndexes") or []
        got = [(i["type"], [c.strip("`") for c in i["columns"]]) for i in indexes]
        if got != [(kind, columns)]:
            raise Fail("Cube Store gets the index %s" % got)
    return run


PA("preagg.option.unique_key_hit", pa_query([OPT + "rev_unique"], [OPT + "status"]), "SELECT status, sum(amount) FROM orders GROUP BY 1", ["pa_options_unique"], then=pa_unique)
PA("preagg.option.index_regular_hit", pa_query([OPT + "rev_index"], [OPT + "status"], filters=[{"member": OPT + "user_id", "operator": "equals", "values": ["7"]}]),
   "SELECT status, sum(amount) FROM orders WHERE user_id = 7 GROUP BY 1", ["pa_options_regular_index"], then=pa_index("rev_index", "regular", ["pa_options__status", "pa_options__user_id"]))
PA("preagg.option.index_aggregate_hit", pa_query([OPT + "rev_agg_index"], [OPT + "status"]), "SELECT status, sum(amount) FROM orders GROUP BY 1", ["pa_options_aggregate_index"], then=pa_index("rev_agg_index", "aggregate", ["pa_options__status"]))
PA("preagg.option.index_on_a_null_column_hit", pa_query(["pa_nulls.rows"], ["pa_nulls.den"]), "SELECT den, count() FROM metric_edges GROUP BY 1", ["pa_nulls_by_den"])
PA("preagg.option.two_time_dimensions_hit", pa_query([OPT + "rev_two_times"], [], "day", date_range=["2025-03-01", "2025-03-02"]),
   "SELECT toDate(created_at), sum(amount) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-03-03' GROUP BY 1", ["pa_options_two_times"])


@check("preagg.option.no_index")
def _(ctx):
    """The dialect builds the smallest form of the docs. Stock Cube stops each query on it with a clear error."""
    query = pa_query(["pa_no_index.revenue"], ["pa_no_index.status"])
    if DIALECT:
        res = pa_load(query)
        pa_expect_used(res, ["pa_no_index_plain"], query)
        assert_rows(pa_rows(res, query), ch("SELECT status, sum(amount) FROM orders GROUP BY 1"))
        return
    status, text, _h = http("POST", CUBE + "/cubejs-api/v1/load", {"query": query})
    if status == 200 or "without indexes" not in text:
        raise Fail("stock Cube changed: %s" % text[:200])


# ----- original_sql, use_original_sql_pre_aggregations, rollup_join and rollup_lambda


@check("preagg.type.original_sql_in_clickhouse")
def _(ctx):
    query = pa_query(["pa_original.orders"], ["pa_original.status"])
    if not DIALECT:
        status, text, _h = http("POST", CUBE + "/cubejs-api/v1/load", {"query": query})
        if status == 200 or "without indexes" not in text:
            raise Fail("stock Cube changed: %s" % text[:200])
        return
    res = pa_load(query)
    pa_expect_used(res, ["pa_original_base"], query, external=False)
    assert_rows(pa_rows(res, query), ch("SELECT status, count() FROM orders GROUP BY 1"))


@check("preagg.type.original_sql_in_cube_store")
def _(ctx):
    query = pa_query(["pa_original_external.revenue"], ["pa_original_external.status"])
    if not DIALECT:
        status, text, _h = http("POST", CUBE + "/cubejs-api/v1/load", {"query": query})
        if status == 200 or "without indexes" not in text:
            raise Fail("stock Cube changed: %s" % text[:200])
        return
    res = pa_load(query)
    pa_expect_used(res, ["pa_original_external_base"], query)
    assert_rows(pa_rows(res, query), ch("SELECT status, sum(amount) FROM orders GROUP BY 1"))


def pa_rollup_over_original(query, truth):
    def run(ctx):
        if not DIALECT:
            status, text, _h = http("POST", CUBE + "/cubejs-api/v1/load", {"query": query})
            if status == 200 or "without indexes" not in text:
                raise Fail("stock Cube changed: %s" % text[:200])
            return
        res = pa_load(query)
        pa_expect_used(res, ["pa_original_base", "pa_original_main"], query)
        assert_rows(pa_rows(res, query), ch(truth))
    return run


check("preagg.option.use_original_sql_same_grain_hit")(pa_rollup_over_original(
    pa_query(["pa_original.revenue"], ["pa_original.status"], "day"), "SELECT status, toDate(created_at), sum(amount) FROM orders GROUP BY 1, 2"))
check("preagg.option.use_original_sql_coarser_hit")(pa_rollup_over_original(
    pa_query(["pa_original.revenue"], [], "month"), "SELECT toStartOfMonth(created_at), sum(amount) FROM orders GROUP BY 1"))

PJ = ["pa_join_customers_customers", "pa_join_sales_sales"]
JSQL = "orders o JOIN users u ON o.user_id = u.id"
PA("preagg.type.rollup_join_same_grain_hit", pa_query(["pa_join_sales.revenue"], ["pa_join_customers.state"], "day", cube="pa_join_sales", date_range=["2025-03-01", "2025-03-03"]),
   "SELECT u.state, toDate(o.created_at), sum(o.amount) FROM %s WHERE o.created_at >= '2025-03-01' AND o.created_at < '2025-03-04' GROUP BY 1, 2" % JSQL, PJ)
PA("preagg.type.rollup_join_coarser_hit", pa_query(["pa_join_sales.revenue"], ["pa_join_customers.state"], "month", cube="pa_join_sales"),
   "SELECT u.state, toStartOfMonth(o.created_at), sum(o.amount) FROM %s GROUP BY 1, 2" % JSQL, PJ)
PA("preagg.type.rollup_join_total_hit", pa_query(["pa_join_sales.revenue"], ["pa_join_customers.state"]), "SELECT u.state, sum(o.amount) FROM %s GROUP BY 1" % JSQL, PJ)
PA("preagg.type.rollup_join_filter_hit", pa_query(["pa_join_sales.revenue"], ["pa_join_customers.state"], filters=[{"member": "pa_join_sales.status", "operator": "equals", "values": ["shipped"]}]),
   "SELECT u.state, sum(o.amount) FROM %s WHERE o.status = 'shipped' GROUP BY 1" % JSQL, PJ)
PA("preagg.type.rollup_join_finer_miss", pa_query(["pa_join_sales.revenue"], ["pa_join_customers.state"], "hour", cube="pa_join_sales", date_range=["2025-03-01", "2025-03-01"]),
   "SELECT u.state, toStartOfHour(o.created_at), sum(o.amount) FROM %s WHERE toDate(o.created_at) = '2025-03-01' GROUP BY 1, 2" % JSQL, [])
PA("preagg.type.rollup_join_other_dimension_miss", pa_query(["pa_join_sales.revenue"], ["pa_join_customers.city"]), "SELECT u.city, sum(o.amount) FROM %s GROUP BY 1" % JSQL, [])
PA("preagg.type.rollup_join_other_measure_miss", pa_query(["pa_join_sales.orders"], ["pa_join_customers.state"]), "SELECT u.state, count() FROM %s GROUP BY 1" % JSQL, [])
PM("preagg.type.rollup_join_through_metabase", "pa_join_sales", "SELECT u.state, sum(o.amount) FROM %s GROUP BY 1" % JSQL, PJ,
   agg=["m:pa_join_sales.revenue"], by=["d:pa_join_sales>pa_join_customers.state"])

PL = "pa_lambda.revenue"
LAM = "SELECT toStartOfMonth(created_at), sum(amount) FROM orders"
PA("preagg.type.rollup_lambda_inside_the_batch_hit", pa_query([PL], [], "month", date_range=["2025-02-01", "2025-03-31"]), LAM + " WHERE created_at >= '2025-02-01' AND created_at < '2025-04-01' GROUP BY 1", ["pa_lambda_batch"])
PA("preagg.type.rollup_lambda_filter_hit", pa_query([PL], [], "month", date_range=["2025-02-01", "2025-03-31"], filters=[{"member": "pa_lambda.status", "operator": "equals", "values": ["shipped"]}]),
   LAM + " WHERE status = 'shipped' AND created_at >= '2025-02-01' AND created_at < '2025-04-01' GROUP BY 1", ["pa_lambda_batch"])


def pa_lambda_source(query, truth):
    """Over the end of the batch, Cube reads source rows too. A run gives the truth or the error of cube-js/cube#12285, never a wrong number."""
    def run(ctx):
        status, text, _h = http("POST", CUBE + "/cubejs-api/v1/load", {"query": query})
        for _ in range(120):
            if json.loads(text).get("error") != "Continue wait":
                break
            time.sleep(2)
            status, text, _h = http("POST", CUBE + "/cubejs-api/v1/load", {"query": query})
        if status != 200:
            if "Stream query failed" not in text:
                raise Fail("another error: %s" % text[:300])
            return
        res = json.loads(text)
        pa_expect_used(res, ["pa_lambda_batch"], query)
        assert_rows(pa_rows(res, query), ch(truth))
    return run


check("preagg.type.rollup_lambda_over_the_batch_with_source")(pa_lambda_source(pa_query([PL], [], "month"), LAM + " GROUP BY 1"))
check("preagg.type.rollup_lambda_after_the_batch_with_source")(pa_lambda_source(pa_query([PL], [], "month", date_range=["2025-08-01", "2025-09-30"]), LAM + " WHERE created_at >= '2025-08-01' AND created_at < '2025-10-01' GROUP BY 1"))
PA("preagg.type.rollup_lambda_of_two_rollups_hit", pa_query(["pa_lambda_rollups.revenue"], [], "month"), LAM + " GROUP BY 1", ["pa_lambda_rollups_first_half", "pa_lambda_rollups_second_half"])
PA("preagg.type.rollup_lambda_miss_on_a_finer_grain", pa_query([PL], [], "hour", date_range=["2025-02-01", "2025-02-01"]),
   "SELECT toStartOfHour(created_at), sum(amount) FROM orders WHERE toDate(created_at) = '2025-02-01' GROUP BY 1", [])

# ----- refresh_key: the rows change, and the old number of a rollup stays only for its documented time

PA_FRESH = {}
PA_DEADLINE = 200


def pa_fresh_query(member, kind, partitioned=False):
    q = {"measures": ["pa_fresh." + member], "filters": [{"member": "pa_fresh.kind", "operator": "equals", "values": [kind]}]}
    if partitioned:
        today = dt.datetime.now(dt.timezone.utc).date()
        q["timeDimensions"] = [{"dimension": "pa_fresh.ts", "dateRange": [(today - dt.timedelta(days=5)).isoformat(), today.isoformat()]}]
    return q


PA_ROLLUPS = {
    "amt_every": ("pa_fresh_fresh_every", False),
    "amt_sql": ("pa_fresh_fresh_sql", False),
    "amt_incremental": ("pa_fresh_fresh_incremental", True),
    "amt_last_partition": ("pa_fresh_fresh_last_partition", True),
    "amt_manual": ("pa_fresh_fresh_manual", False),
    "amt_long": ("pa_fresh_fresh_long", False),
}


def pa_fresh_value(member, kind):
    rollup, partitioned = PA_ROLLUPS[member]
    query = pa_fresh_query(member, kind, partitioned)
    res = pa_load(query)
    pa_expect_used(res, [rollup], query)
    v = res["data"][0].get("pa_fresh." + member) if res["data"] else None
    return float(v) if v is not None else 0.0


def pa_fresh_scenario():
    """Writes rows once and follows each rollup. Each check reads the part that it needs, so the wait of the suite stays one wait."""
    if PA_FRESH:
        return PA_FRESH
    ch_write("TRUNCATE TABLE pa_mutable")
    kind = "run%d" % int(time.time())
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None)

    def write(row_id, when, amount):
        ch_write("INSERT INTO pa_mutable VALUES (%d, '%s', '%s', %s, %d)" % (row_id, kind, when, amount, int(time.time() * 1000)))

    def wait_for(member, want):
        start = time.time()
        while time.time() - start < PA_DEADLINE:
            if pa_fresh_value(member, kind) == want:
                return round(time.time() - start, 1)
            time.sleep(3)
        return None

    # Cube builds each rollup now, so a row that comes later is a change of the source.
    for member in PA_ROLLUPS:
        pa_fresh_value(member, kind)
    today = now.strftime("%Y-%m-%d 00:05:00")
    yesterday = (now - dt.timedelta(days=1)).strftime("%Y-%m-%d 00:05:00")
    old = (now - dt.timedelta(days=4)).strftime("%Y-%m-%d 00:05:00")
    write(1, today, 10)
    # This row is in no rollup yet, so a read now is a stale read of each rollup.
    PA_FRESH["first_read"] = {m: pa_fresh_value(m, kind) for m in PA_ROLLUPS}
    # A row of the last partition, a row inside the update window, and a row out of the window.
    PA_FRESH["first"] = {m: wait_for(m, 10.0) for m in ("amt_every", "amt_sql", "amt_incremental", "amt_last_partition")}
    write(2, yesterday, 5)
    write(3, old, 100)
    PA_FRESH["all_rows"] = {"amt_every": wait_for("amt_every", 115.0), "amt_sql": wait_for("amt_sql", 115.0)}
    PA_FRESH["window"] = wait_for("amt_incremental", 15.0)
    # Now the worker had time for each rollup. These stay as the docs say.
    time.sleep(35)
    PA_FRESH["values"] = {m: pa_fresh_value(m, kind) for m in PA_ROLLUPS}
    PA_FRESH["truth"] = float(ch("SELECT sum(amount) FROM pa_mutable WHERE kind = '%s'" % kind)[0][0])
    PA_FRESH["kind"] = kind
    PA_FRESH["manual_wait"] = wait_for("amt_manual", 115.0)
    print("refresh: %s" % json.dumps(PA_FRESH, sort_keys=True))
    return PA_FRESH


def pa_fresh_stale_read(member):
    state = pa_fresh_scenario()
    if state["first_read"][member] != 0.0:
        raise Fail("%s gave the new row at once: %s. This read is not from the rollup" % (member, state["first_read"]))


@check("preagg.refresh.every_old_number_ends_in_time")
def _(ctx):
    state = pa_fresh_scenario()
    pa_fresh_stale_read("amt_every")
    if state["first"]["amt_every"] is None or state["all_rows"]["amt_every"] is None:
        raise Fail("an old rollup stays past its refresh_key of 30 seconds: %s" % state)
    if state["values"]["amt_every"] != state["truth"]:
        raise Fail("every: %s, truth %s" % (state["values"]["amt_every"], state["truth"]))


@check("preagg.refresh.sql_key_old_number_ends_in_time")
def _(ctx):
    state = pa_fresh_scenario()
    if state["first"]["amt_sql"] is None or state["all_rows"]["amt_sql"] is None:
        raise Fail("a change of the SQL key does not refresh the rollup: %s" % state)
    if state["values"]["amt_sql"] != state["truth"]:
        raise Fail("sql: %s, truth %s" % (state["values"]["amt_sql"], state["truth"]))


@check("preagg.refresh.incremental_refreshes_the_update_window")
def _(ctx):
    state = pa_fresh_scenario()
    if state["first"]["amt_incremental"] is None or state["window"] is None:
        raise Fail("a change inside the update window does not refresh the rollup: %s" % state)


@check("preagg.refresh.incremental_keeps_a_partition_out_of_the_window")
def _(ctx):
    """The docs: partitions before the window will not be refreshed. Row 3 is 4 days old, and the window is 2 days."""
    state = pa_fresh_scenario()
    if state["values"]["amt_incremental"] != 15.0:
        raise Fail("incremental: %s, and the docs give the rows of the window only, 15" % state["values"]["amt_incremental"])
    if state["truth"] != 115.0:
        raise Fail("truth: %s" % state["truth"])


@check("preagg.refresh.no_update_window_refreshes_only_the_last_partition")
def _(ctx):
    """The docs: with no update_window, only the last partition refreshes. Row 1 is today, and row 2 is yesterday."""
    state = pa_fresh_scenario()
    if state["first"]["amt_last_partition"] is None:
        raise Fail("a change of the last partition does not refresh the rollup: %s" % state)
    if state["values"]["amt_last_partition"] != 10.0:
        raise Fail("last partition: %s, and the docs give 10" % state["values"]["amt_last_partition"])


@check("preagg.refresh.long_key_keeps_the_old_number_inside_its_time")
def _(ctx):
    """A key of one day. The read after the write is the old number. It is a hit, and a hit of the old number proves that the read is from the rollup."""
    state = pa_fresh_scenario()
    if state["values"]["amt_long"] != 0.0:
        raise Fail("a key of one day gave new rows at once: %s" % state["values"])


@check("preagg.refresh.scheduled_refresh_false")
def _(ctx):
    """scheduled_refresh: false. The record of this check tells what a query does."""
    state = pa_fresh_scenario()
    if state["manual_wait"] is None and state["values"]["amt_manual"] == state["truth"]:
        raise Fail("inconsistent read: %s" % state)
    if state["manual_wait"] is None:
        raise Fail("a query does not refresh a rollup with scheduled_refresh false: %s" % state["values"])


@check("preagg.refresh.through_metabase")
def _(ctx):
    """The driver reads the same old number from the rollup, so the SQL API reads Cube Store. A read from ClickHouse gives the truth here."""
    state = pa_fresh_scenario()
    ensure_table(ctx, PAF)
    kind = state["kind"]
    where = [["=", "d:pa_fresh.kind", kind]]
    long_rows = ctx.mb.mbql(PAF, agg=["m:pa_fresh.amt_long"], where=where)["rows"]
    if long_rows and long_rows[0][0] not in (None, 0):
        raise Fail("Metabase read new rows through a key of one day: %s" % long_rows)
    every = ctx.mb.mbql(PAF, agg=["m:pa_fresh.amt_every"], where=where)["rows"]
    assert_rows(every, [[state["truth"]]], label="key of 30 seconds")
    plain = ctx.mb.mbql(PAF, agg=["m:pa_fresh.amt_plain"], where=where)["rows"]
    assert_rows(plain, [[state["truth"]]], label="no rollup")


# ----- latency on ten million rows


def pa_latency(query):
    """Milliseconds of a query that Cube has not cached. A new date range each time makes a new key."""
    times = []
    used = None
    for i in range(5):
        q = json.loads(json.dumps(query))
        q["timeDimensions"][0]["dateRange"] = ["2024-01-01", "2025-%02d-28" % (i + 3)]
        start = time.time()
        res = pa_load(q)
        times.append((time.time() - start) * 1000)
        used = pa_used(res)
    return sorted(times)[len(times) // 2], used


PA_LATENCY_HIT = {"measures": ["big_orders.revenue"], "dimensions": ["big_orders.status"], "timeDimensions": [{"dimension": "big_orders.created_at", "granularity": "month"}]}
PA_LATENCY_MISS = {"measures": ["big_orders.revenue"], "dimensions": ["big_orders.user_id"], "timeDimensions": [{"dimension": "big_orders.created_at", "granularity": "year"}], "limit": 20}


@check("preagg.latency.big_orders_hit_is_faster_than_a_miss")
def _(ctx):
    hit_ms, hit_used = pa_latency(PA_LATENCY_HIT)
    miss_ms, miss_used = pa_latency(PA_LATENCY_MISS)
    print("latency on big_orders: hit %.0f ms, miss %.0f ms" % (hit_ms, miss_ms))
    if hit_used != ["big_orders_daily"]:
        raise Fail("the hit used %s" % hit_used)
    if miss_used:
        raise Fail("the miss used %s" % miss_used)
    if hit_ms >= miss_ms:
        raise Fail("the hit took %.0f ms and the miss took %.0f ms" % (hit_ms, miss_ms))
    query = dict(PA_LATENCY_HIT, timeDimensions=[{"dimension": "big_orders.created_at", "granularity": "month"}])
    assert_rows(pa_rows(pa_load(query), query), ch("SELECT status, toStartOfMonth(created_at), sum(amount) FROM big_orders GROUP BY 1, 2"))


@check("preagg.model.stock_form")
def _(ctx):
    """Each form above has a check, and the check names a rollup that the model holds."""
    status, text, _h = http("GET", CUBE + "/cubejs-api/v1/meta")
    names = {c["name"] for c in json.loads(text)["cubes"]}
    for cube in ("pa_types", "pa_grain", "pa_week", "pa_partitions", "pa_options", "pa_fresh", "pa_join_sales", "pa_lambda", "pa_original", "pa_view", "pa_extended"):
        if cube not in names:
            raise Fail("no cube %s" % cube)


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
    # The security checks add databases to Metabase, so they run last. The other checks must not find those objects.
    for f in sorted(features, key=lambda x: x["id"].startswith("security.") or str(x.get("check")).startswith("security.")):
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
        except Blocked as e:
            final[fid], reasons[fid] = "blocked", str(e)
        except Fail as e:
            final[fid], reasons[fid] = "fail", str(e)
        except Exception as e:  # a reply of an unexpected shape must not stop the run
            final[fid], reasons[fid] = "fail", "unexpected %s: %s" % (type(e).__name__, str(e)[:300])
            if args.verbose:
                traceback.print_exc()
        else:
            # `cap` is the best status of an entry whose check cannot prove the whole feature. The reason tells why.
            # `cap_stock` and `reason_stock` apply only on stock Cube: the dialect closes that limit.
            final[fid] = f.get("cap", "pass") if DIALECT else f.get("cap_stock", f.get("cap", "pass"))
            if final[fid] != "pass":
                reasons[fid] = f.get("reason_stock", f.get("reason")) if not DIALECT else f.get("reason")
        if final[fid] == "fail":
            print("fail     %s: %s" % (fid, reasons[fid]))
        elif final[fid] == "pass":
            print("pass     %s" % fid)
        else:
            print("%-8s %s: %s" % (final[fid], fid, reasons[fid]))

    if not setup_error:
        if getattr(ctx, "secure", None):
            ctx.secure.cleanup()
        mb.cleanup()
    if not args.no_write:
        write_statuses(final)

    passed = sum(1 for st in final.values() if st == "pass")
    line = "coverage: %d/%d pass on %s" % (passed, len(final), VARIANT_NAME)
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
