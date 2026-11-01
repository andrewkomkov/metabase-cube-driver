#!/bin/sh
# docs/install.md word for word, with the URL of a local copy of the jar of the dry run, because no release exists.
set -u
cd "$HOME/mcd-review/repo/dev"
export COMPOSE_PROJECT_NAME=mcd-review COMPOSE_FILE=docker-compose.yml:compose.review.yml:compose.gate.yml COMPOSE_PROFILES=install
export REVIEW_PLUGINS="$HOME/mcd-review/plugins-literal"
python3 -m http.server --directory "$HOME/mcd-review" 18099 --bind 127.0.0.1 >/dev/null 2>&1 &
srv=$!
health() { for _ in $(seq 1 60); do curl -fsS -m 3 http://127.0.0.1:13302/api/health >/dev/null 2>&1 && return 0; sleep 3; done; return 1; }
echo "== the URL of the docs today"
curl -sS -o /dev/null -L -w "%{http_code}\n" https://github.com/andrewkomkov/metabase-cube-driver/releases/latest/download/cube.metabase-driver.jar
echo "== case 1: the docs"
MB_PLUGINS_DIR="$REVIEW_PLUGINS"
rm -rf "$MB_PLUGINS_DIR" 2>/dev/null
mkdir -p "$MB_PLUGINS_DIR"
curl -fL -sS -o "$MB_PLUGINS_DIR/cube.metabase-driver.jar" http://127.0.0.1:18099/release-dry-run.jar
chmod 644 "$MB_PLUGINS_DIR/cube.metabase-driver.jar"
chmod 777 "$MB_PLUGINS_DIR"
docker compose up -d metabase-install 2>&1 | tail -1
health && echo healthy
docker logs mcd-review-metabase-install-1 2>&1 | grep -E "Registered driver :cube|cannot use the plugins" | cut -c1-140
REVIEW_MB=http://127.0.0.1:13302 REVIEW_ADMIN=install@example.com REVIEW_PASSWORD=install-throwaway-1 python3 - <<'PY'
import time
import lib
mb = lib.Metabase().login()
d = mb.api("POST", "/api/database", {"engine": "cube", "name": "Cube", "details": {"host": "cube", "port": 15432, "dbname": "cube", "user": "cube", "password": "cube-dev-only", "cube-rest-url": "http://cube:4000", "cube-api-secret-value": "cube-dev-only-secret-0123456789abcdef"}})
for _ in range(60):
    if mb.api("GET", "/api/database/%d" % d["id"]).get("initial_sync_status") == "complete":
        break
    time.sleep(3)
tables = {t["name"]: t for t in mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % d["id"])["tables"]}
print("tables", len(tables))
for _ in range(20):
    ms = [m for m in mb.api("GET", "/api/measure") if m.get("table_id") == tables["orders"]["id"] and m["name"] == "Revenue"]
    if ms:
        break
    time.sleep(3)
print("revenue", mb.api("POST", "/api/dataset", {"database": d["id"], "type": "query", "query": {"source-table": tables["orders"]["id"], "aggregation": [["measure", ms[0]["id"]]]}})["data"]["rows"], "truth", lib.ch("SELECT sum(amount) FROM orders"))
PY
docker compose rm -sfv metabase-install 2>&1 | tail -1
echo "== case 2: no chmod 777"
sudo -n true 2>/dev/null; docker run --rm -v "$HOME/mcd-review:/w" alpine:3 rm -rf /w/plugins-literal 2>/dev/null || rm -rf "$MB_PLUGINS_DIR" 2>/dev/null
mkdir -p "$MB_PLUGINS_DIR"
cp "$HOME/mcd-review/release-dry-run.jar" "$MB_PLUGINS_DIR/cube.metabase-driver.jar"
chmod 644 "$MB_PLUGINS_DIR/cube.metabase-driver.jar"
chmod 755 "$MB_PLUGINS_DIR"
docker compose up -d metabase-install 2>&1 | tail -1
health && echo healthy
docker logs mcd-review-metabase-install-1 2>&1 | grep -E "Registered driver :cube|cannot use the plugins|lugins directory" | cut -c1-200 | head -4
docker compose rm -sfv metabase-install 2>&1 | tail -1
kill "$srv"
