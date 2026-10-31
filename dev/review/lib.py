"""Client for the last review: Metabase REST, the MCP server and the truth from ClickHouse. It uses only the standard library."""
import base64
import decimal
import hashlib
import html.parser
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

MB = os.environ.get("REVIEW_MB", "http://127.0.0.1:13300")
CH = os.environ.get("REVIEW_CH", "http://127.0.0.1:18123")
ADMIN = os.environ.get("REVIEW_ADMIN", "review@example.com")
PASSWORD = os.environ.get("REVIEW_PASSWORD", "review-dev-only-1")
REDIRECT = "http://127.0.0.1:9/callback"
SCOPES = "mcp"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def http(method, url, body=None, headers=None, form=False, timeout=240):
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
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, r.read().decode(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers


def ch(sql, url=None):
    """The truth. Direct SQL on ClickHouse, each value in JSON as text, so no value passes through a double."""
    q = urllib.parse.urlencode({"database": "shop", "default_format": "JSONCompact", "output_format_json_quote_64bit_integers": 1,
                                "output_format_json_quote_decimals": 1, "session_timezone": "UTC"})
    status, text, _ = _ch_post(sql, q, url)
    if status != 200:
        raise RuntimeError("clickhouse: %s: %s" % (status, text[:400]))
    return json.loads(text)["data"]


def _ch_post(sql, q, url):
    req = urllib.request.Request((url or CH) + "/?" + q, data=sql.encode(), method="POST",
                                 headers={"X-ClickHouse-User": "shop", "X-ClickHouse-Key": "shop-dev-only"})
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            return r.status, r.read().decode(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers


def norm(v):
    """One form for each value: a number as a Decimal, a time as yyyy-mm-dd hh:mm:ss[.fff] with no zone mark, others as text."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return decimal.Decimal(repr(v))
    s = str(v)
    m = re.match(r"^(\d{4}-\d\d-\d\d)(?:[T ](\d\d:\d\d:\d\d)(\.\d+)?)?(Z|[+-]\d\d:\d\d)?$", s)
    if m:
        frac = (m.group(3) or "").rstrip("0").rstrip(".")
        return "%s %s%s" % (m.group(1), m.group(2) or "00:00:00", frac)
    if re.match(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$", s):
        return decimal.Decimal(s)
    if s in ("true", "false"):
        return s
    return s


def same(a, b, tol=1e-9):
    a, b = norm(a), norm(b)
    if a is None or b is None:
        return a is b
    if isinstance(a, decimal.Decimal) and isinstance(b, decimal.Decimal):
        if a == b:
            return True
        scale = max(abs(a), abs(b), decimal.Decimal(1))
        return abs(a - b) <= scale * decimal.Decimal(str(tol))
    return str(a) == str(b)


def key(row):
    return [("" if v is None else str(norm(v))) for v in row]


def compare(got, want, ordered=False, tol=1e-9):
    """None if the rows are equal. Otherwise a short text with the first difference."""
    if not ordered:
        got, want = sorted(got, key=key), sorted(want, key=key)
    if len(got) != len(want):
        return "rows: got %d, truth %d. First got %s, first truth %s" % (len(got), len(want), got[:2], want[:2])
    for i, (a, b) in enumerate(zip(got, want)):
        if len(a) != len(b) or not all(same(x, y, tol) for x, y in zip(a, b)):
            return "row %d: got %s, truth %s" % (i, a, b)
    return None


class Metabase:
    def __init__(self, url=MB, password=PASSWORD, email=ADMIN):
        self.url, self.password, self.email = url, password, email
        self.sid = None

    def api(self, method, path, body=None, ok=(200, 201, 202, 204)):
        status, text, _ = http(method, self.url + path, body, {"X-Metabase-Session": self.sid} if self.sid else {})
        data = json.loads(text) if text.strip()[:1] in ("{", "[") else text
        if status not in ok:
            raise RuntimeError("%s %s: HTTP %s: %s" % (method, path, status, text[:600]))
        return data

    def login(self):
        props = self.api("GET", "/api/session/properties")
        if not props.get("has-user-setup"):
            self.api("POST", "/api/setup", {"token": props["setup-token"],
                                            "user": {"email": self.email, "first_name": "Review", "last_name": "Admin", "password": self.password},
                                            "prefs": {"site_name": "Review", "allow_tracking": False}})
        self.sid = self.api("POST", "/api/session", {"username": self.email, "password": self.password})["id"]
        return self

    def run(self, query):
        """Returns (rows, cols, error). A query with a failed status is an error with text, as a person sees it."""
        res = self.api("POST", "/api/dataset", query, ok=(200, 202, 400, 403, 500))
        if isinstance(res, str):
            return None, None, res
        if res.get("error") or res.get("status") == "failed":
            return None, None, str(res.get("error") or res.get("error_type") or res.get("message"))
        return res["data"]["rows"], res["data"]["cols"], None


class Consent(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.action, self.hidden, self.scopes = None, {}, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("id") == "consent-form":
            self.action = a.get("action")
        if tag == "input" and a.get("type") == "hidden" and a.get("name"):
            self.hidden[a["name"]] = a.get("value") or ""
        if tag == "input" and a.get("name") == "granted_scope":
            self.scopes.append(a.get("value"))


class Mcp:
    """The MCP server of Metabase through its OAuth flow, as a client signs in."""

    def __init__(self, mb):
        self.mb, self.next_id = mb, 1
        self.path = "/api/metabase-mcp"
        self._connect()

    def _connect(self):
        mb, url = self.mb, self.mb.url
        meta_status, meta, _ = http("GET", url + "/.well-known/oauth-authorization-server")
        scope = " ".join(json.loads(meta).get("scopes_supported") or []) if meta_status == 200 else SCOPES
        status, text, _ = http("POST", url + "/oauth/register", {"client_name": "review", "redirect_uris": [REDIRECT],
                                                                 "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                                                                 "token_endpoint_auth_method": "none", "scope": scope})
        client = json.loads(text)["client_id"]
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        resource = url + self.path
        session = "metabase.SESSION=" + mb.sid
        q = urllib.parse.urlencode({"client_id": client, "response_type": "code", "redirect_uri": REDIRECT, "scope": scope, "state": "s",
                                    "code_challenge": challenge, "code_challenge_method": "S256", "resource": resource})
        status, text, headers = http("GET", url + "/oauth/authorize?" + q, headers={"Cookie": session})
        form = Consent()
        form.feed(text)
        cookies = [c.split(";")[0] for c in headers.get_all("Set-Cookie") or []]
        status, text, headers = http("POST", form.action, dict(form.hidden, approved="true", granted_scope=form.scopes),
                                     {"Cookie": "; ".join([session] + cookies)}, form=True)
        code = urllib.parse.parse_qs(urllib.parse.urlparse(headers.get("Location") or "").query)["code"][0]
        status, text, _ = http("POST", url + "/oauth/token", {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                                                              "client_id": client, "code_verifier": verifier, "resource": resource}, form=True)
        self.headers = {"Authorization": "Bearer " + json.loads(text)["access_token"], "Accept": "application/json, text/event-stream"}
        status, text, resp = http("POST", url + self.path, {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                                            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                                                       "clientInfo": {"name": "review", "version": "1"}}}, self.headers)
        self.headers["Mcp-Session-Id"] = resp["Mcp-Session-Id"]
        http("POST", url + self.path, {"jsonrpc": "2.0", "method": "notifications/initialized"}, self.headers)

    def rpc(self, method, params=None):
        self.next_id += 1
        status, text, _ = http("POST", self.mb.url + self.path, {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}, self.headers)
        if status != 200:
            raise RuntimeError("%s: HTTP %s %s" % (method, status, text[:300]))
        for line in text.splitlines():
            if line.startswith("data:"):
                msg = json.loads(line[5:])
                break
        else:
            msg = json.loads(text)
        if "error" in msg:
            raise RuntimeError("%s: %s" % (method, json.dumps(msg["error"])[:600]))
        return msg["result"]

    def call(self, tool, **args):
        """Returns (payload, error text). A tool error is a result, not an exception, because the review compares it."""
        res = self.rpc("tools/call", {"name": tool, "arguments": args})
        text = (res.get("content") or [{}])[0].get("text", "")
        if res.get("isError"):
            return None, text
        block = re.search(r'<data boundary="[^"]*">\s*', text)
        try:
            return json.JSONDecoder().raw_decode(text[block.end():] if block else text)[0], None
        except ValueError:
            return text, None
