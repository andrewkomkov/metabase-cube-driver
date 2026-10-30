#!/usr/bin/env python3
"""Proves that a running Metabase loads the plugin jar.

The manifest alone puts the driver in the list of engines, because the driver is lazy. The namespaces
load on first use, so this check also validates a real connection to the demo Cube. The check finishes
the Metabase setup. A test that needs a fresh setup must use a fresh Metabase.
"""
import json
import secrets
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:3300"
DEADLINE_SECONDS = 300
# Addresses from the Metabase container. These are the throwaway demo values from dev/docker-compose.yml.
CUBE_DETAILS = {
    "host": "cube",
    "port": 15432,
    "dbname": "cube",
    "user": "cube",
    "password": "cube-dev-only",
    "cube-rest-url": "http://cube:4000",
    "cube-api-secret": "cube-dev-only-secret-0123456789abcdef",
    "ssl": False,
}


def call(method, path, body=None, session=None):
    headers = {"Content-Type": "application/json"}
    if session:
        headers["X-Metabase-Session"] = session
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")


def wait_for_health():
    deadline = time.time() + DEADLINE_SECONDS
    while time.time() < deadline:
        try:
            status, body = call("GET", "/api/health")
            if status == 200 and isinstance(body, dict) and body.get("status") == "ok":
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError):
            pass
        time.sleep(3)
    sys.exit("Metabase did not become healthy")


def main():
    wait_for_health()
    _, properties = call("GET", "/api/session/properties")

    status, setup = call("POST", "/api/setup", {
        "token": properties["setup-token"],
        "user": {
            "email": "ci@example.com",
            "password": secrets.token_urlsafe(24),
            "first_name": "CI",
            "last_name": "Load check",
        },
        "prefs": {"site_name": "load check"},
    })
    if status != 200:
        sys.exit(f"setup failed with {status}: {setup}")

    # Metabase 0.65 removed the engines from the session properties.
    status, engines = call("GET", "/api/setting/engines", session=setup["id"])
    if status != 200 or "cube" not in engines:
        sys.exit(f"the plugin did not register: {status}: {engines}")
    print("registered: engine cube is in the list of engines")

    status, result = call(
        "POST",
        "/api/database/validate",
        {"details": {"engine": "cube", "details": CUBE_DETAILS}},
        session=setup["id"],
    )
    if status != 200 or result.get("valid") is not True:
        sys.exit(f"the driver did not load or connect: {status}: {result}")
    print("loaded: the driver initialized and connected to Cube")


if __name__ == "__main__":
    main()
