#!/usr/bin/env python3
"""Downloads the binaries in .github/tools/tools.json into a directory and checks their SHA-256."""
import hashlib
import json
import os
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools" / "tools.json"


def asset_url(tool):
    version = tool["tag"].lstrip("v")
    asset = tool["asset"].format(version=version)
    return f"https://github.com/{tool['repo']}/releases/download/{tool['tag']}/{asset}"


def install(name, tool, dest):
    url = asset_url(tool)
    with urllib.request.urlopen(url) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != tool["sha256"]:
        sys.exit(f"{name}: expected sha256 {tool['sha256']}, got {digest} from {url}")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "archive"
        archive.write_bytes(data)
        if url.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extract(name, dest)
        else:
            with tarfile.open(archive) as t:
                t.extract(name, dest, filter="data")
    os.chmod(dest / name, 0o755)
    print(f"installed {name} {tool['tag']}")


def main():
    dest = Path(sys.argv[1])
    dest.mkdir(parents=True, exist_ok=True)
    for name, tool in json.loads(TOOLS.read_text()).items():
        install(name, tool, dest)


if __name__ == "__main__":
    main()
