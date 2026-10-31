#!/usr/bin/env python3
"""Reads and changes the pins, and finds new versions on Docker Hub.

The defaults in dev/docker-compose.yml are the only source of the pins. Run with no arguments for usage.
"""
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

from install_tools import TOOLS, asset_url

USAGE = "usage: pins.py read | resolve | matrix | updates | set NAME VERSION | set-tool NAME TAG"
ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "dev" / "docker-compose.yml"
DOCS = ["AGENTS.md", "README.md", "CONTRIBUTING.md", "docs", ".claude"]
IMAGES = {
    "metabase": "metabase/metabase",
    "cube": "cubejs/cube",
    "clickhouse": "clickhouse/clickhouse-server",
}
STABLE = {
    "metabase": re.compile(r"^v0\.\d+\.\d+(\.\d+)?$"),
    "cube": re.compile(r"^v\d+\.\d+\.\d+$"),
    "clickhouse": re.compile(r"^\d+\.\d+\.\d+-alpine$"),
}
BETA = re.compile(r"^v0\.\d+\.\d+(\.\d+)?-beta$")


def version_key(tag):
    return tuple(int(part) for part in re.findall(r"\d+", tag))


def read_pins():
    found = re.findall(r"\$\{(METABASE|CUBE|CLICKHOUSE)_VERSION:-([^}]+)\}", COMPOSE.read_text())
    pins = {name.lower(): version for name, version in found}
    absent = set(IMAGES) - set(pins)
    if absent:
        sys.exit(f"no pin for {sorted(absent)} in {COMPOSE}")
    return pins


def fetch_json(url, headers=None):
    request = urllib.request.Request(url, headers={"User-Agent": "metabase-cube-driver-ci", **(headers or {})})
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def hub_tags(image):
    auth = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{image}:pull"
    token = fetch_json(auth)["token"]
    url = f"https://registry-1.docker.io/v2/{image}/tags/list"
    return fetch_json(url, {"Authorization": f"Bearer {token}"})["tags"]


def newest(tags, pattern):
    matching = [tag for tag in tags if pattern.match(tag)]
    return max(matching, key=version_key) if matching else None


def previous_major(tags, pin):
    """Returns the newest stable Metabase of the major version below the pin."""
    major = version_key(pin)[1]
    older = [tag for tag in tags if STABLE["metabase"].match(tag) and version_key(tag)[1] < major]
    return max(older, key=version_key) if older else None


def resolve():
    pins = read_pins()
    tags = {name: hub_tags(image) for name, image in IMAGES.items()}
    return {
        "metabase": {
            "pinned": pins["metabase"],
            "newest": newest(tags["metabase"], STABLE["metabase"]),
            "previous": previous_major(tags["metabase"], pins["metabase"]),
            "beta": newest(tags["metabase"], BETA),
        },
        "cube": {"pinned": pins["cube"], "newest": newest(tags["cube"], STABLE["cube"])},
        "clickhouse": {
            "pinned": pins["clickhouse"],
            "newest": newest(tags["clickhouse"], STABLE["clickhouse"]),
        },
    }


def matrix(resolved):
    """Metabase (pinned, previous stable, newest beta) by Cube (pinned, newest) on the pinned ClickHouse.

    Each pair runs with cube-clickhouse and on stock Cube, so that a Cube release that ships a fix shows here.
    """
    mb, cube = resolved["metabase"], resolved["cube"]
    metabases = list(dict.fromkeys(v for v in (mb["pinned"], mb["previous"], mb["beta"]) if v))
    cubes = list(dict.fromkeys(v for v in (cube["pinned"], cube["newest"]) if v))
    cells = []
    for m in metabases:
        for c in cubes:
            required = m == mb["pinned"] and c == cube["pinned"]
            slug = re.sub(r"[^A-Za-z0-9.-]", "-", f"mb-{m}-cube-{c}")
            for fixes, suffix in (("on", ""), ("off", "-stock")):
                cells.append({
                    "id": slug + suffix,
                    "metabase": m,
                    "cube": c,
                    "clickhouse": resolved["clickhouse"]["pinned"],
                    "fixes": fixes,
                    "required": required,
                })
    return {"include": cells}


def updates(resolved):
    out = []
    for name in IMAGES:
        pin, new = resolved[name]["pinned"], resolved[name]["newest"]
        if new and version_key(new) > version_key(pin):
            out.append({"kind": "pin", "name": name, "from": pin, "to": new})
    tools = json.loads(TOOLS.read_text())
    for name, tool in tools.items():
        tag = fetch_json(f"https://api.github.com/repos/{tool['repo']}/releases/latest")["tag_name"]
        if version_key(tag) > version_key(tool["tag"]):
            out.append({"kind": "tool", "name": name, "from": tool["tag"], "to": tag})
    return out


def doc_files():
    for entry in DOCS:
        path = ROOT / entry
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from (p for p in path.rglob("*.md") if p.is_file())


def set_pin(name, new):
    if name not in IMAGES:
        sys.exit(f"unknown pin {name}")
    if not STABLE[name].match(new):
        sys.exit(f"{new} is not a stable version of {name}")
    old = read_pins()[name]
    compose = COMPOSE.read_text()
    COMPOSE.write_text(compose.replace(f"{name.upper()}_VERSION:-{old}", f"{name.upper()}_VERSION:-{new}"))
    mention = re.compile(re.escape(old) + r"(?!\.?\d)")
    for path in doc_files():
        text = path.read_text()
        changed = mention.sub(new, text)
        if changed != text:
            path.write_text(changed)


def set_tool(name, tag):
    tools = json.loads(TOOLS.read_text())
    tool = tools[name]
    tool["tag"] = tag
    with urllib.request.urlopen(asset_url(tool)) as response:
        tool["sha256"] = hashlib.sha256(response.read()).hexdigest()
    TOOLS.write_text(json.dumps(tools, indent=2) + "\n")


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "read":
        print(json.dumps(read_pins()))
    elif command == "resolve":
        print(json.dumps(resolve(), indent=2))
    elif command == "matrix":
        print(json.dumps(matrix(resolve())))
    elif command == "updates":
        print(json.dumps(updates(resolve())))
    elif command == "set":
        set_pin(sys.argv[2], sys.argv[3])
    elif command == "set-tool":
        set_tool(sys.argv[2], sys.argv[3])
    else:
        sys.exit(USAGE)


if __name__ == "__main__":
    main()
