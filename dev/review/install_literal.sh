#!/bin/sh
# The install of README and docs/install.md, word for word. Only the URL differs, because no release exists: the jar comes from a local copy of the artifact of the dry run.
# Case 1 follows the docs. Case 2 leaves out chmod 777, to test what the docs say about a directory that Metabase cannot write to.
set -u
cd "$HOME/mcd-review/repo/dev"
export COMPOSE_PROJECT_NAME=mcd-review COMPOSE_FILE=docker-compose.yml:compose.review.yml COMPOSE_PROFILES=install
export REVIEW_PLUGINS="$HOME/mcd-review/plugins-literal"

echo "== the URL of the docs today"
curl -fL -o /tmp/from-docs.jar https://github.com/andrewkomkov/metabase-cube-driver/releases/latest/download/cube.metabase-driver.jar
echo "curl exit code $?"

python3 -m http.server --directory /tmp 18099 --bind 127.0.0.1 >/dev/null 2>&1 &
srv=$!
sleep 1

wait_health() {
  for _ in $(seq 1 100); do
    curl -fsS http://127.0.0.1:13302/api/health >/dev/null 2>&1 && return 0
    sleep 3
  done
  return 1
}

echo "== case 1: the docs"
MB_PLUGINS_DIR="$REVIEW_PLUGINS"
rm -rf "$MB_PLUGINS_DIR"
mkdir -p "$MB_PLUGINS_DIR"
curl -fL -o "$MB_PLUGINS_DIR/cube.metabase-driver.jar" http://127.0.0.1:18099/release-dry-run.jar
chmod 644 "$MB_PLUGINS_DIR/cube.metabase-driver.jar"
chmod 777 "$MB_PLUGINS_DIR"
docker compose up -d metabase-install 2>&1 | tail -1
wait_health && echo healthy
docker logs mcd-review-metabase-install-1 2>&1 | grep -E "Registered driver :cube|cannot use the plugins" | cut -c1-140
curl -s http://127.0.0.1:13302/api/session/properties | python3 -c "import json,sys; d=json.load(sys.stdin); print('has-user-setup', d.get('has-user-setup'), 'cube in engines', 'cube' in d.get('engines', {}))"
python3 "$HOME/mcd-review/install_connect.py"
docker compose rm -sfv metabase-install 2>&1 | tail -1

echo "== case 3: the same jar with the manifest of PR 1, version 0.1.0"
rm -rf "$MB_PLUGINS_DIR"
mkdir -p "$MB_PLUGINS_DIR"
python3 - "$HOME/mcd-review/pr1-manifest.yaml" /tmp/release-dry-run.jar "$MB_PLUGINS_DIR/cube.metabase-driver.jar" <<'PY2'
import sys, zipfile
manifest, src, dst = sys.argv[1:]
with zipfile.ZipFile(src) as a, zipfile.ZipFile(dst, "w") as b:
    for item in a.infolist():
        b.writestr(item, open(manifest, "rb").read() if item.filename == "metabase-plugin.yaml" else a.read(item.filename))
PY2
chmod 644 "$MB_PLUGINS_DIR/cube.metabase-driver.jar"
chmod 777 "$MB_PLUGINS_DIR"
docker compose up -d metabase-install 2>&1 | tail -1
wait_health && echo healthy
docker logs mcd-review-metabase-install-1 2>&1 | grep -E "Registered driver :cube" | cut -c1-140
docker compose rm -sfv metabase-install 2>&1 | tail -1

echo "== case 2: no chmod 777"
rm -rf "$MB_PLUGINS_DIR"
mkdir -p "$MB_PLUGINS_DIR"
cp /tmp/release-dry-run.jar "$MB_PLUGINS_DIR/cube.metabase-driver.jar"
chmod 644 "$MB_PLUGINS_DIR/cube.metabase-driver.jar"
chmod 755 "$MB_PLUGINS_DIR"
docker compose up -d metabase-install 2>&1 | tail -1
wait_health && echo healthy
docker logs mcd-review-metabase-install-1 2>&1 | grep -E "Registered driver :cube|cannot use the plugins|plugins directory|plugins" | cut -c1-220 | head -8
docker compose rm -sfv metabase-install 2>&1 | tail -1
rm -rf "$MB_PLUGINS_DIR" 2>/dev/null
kill "$srv"
