#!/bin/sh
# Case 1 of install_literal.sh again, with a wait for the measures.
set -u
cd "$HOME/mcd-review/repo/dev"
export COMPOSE_PROJECT_NAME=mcd-review COMPOSE_FILE=docker-compose.yml:compose.review.yml COMPOSE_PROFILES=install
export REVIEW_PLUGINS="$HOME/mcd-review/plugins-literal"
rm -rf "$REVIEW_PLUGINS"
mkdir -p "$REVIEW_PLUGINS"
cp /tmp/release-dry-run.jar "$REVIEW_PLUGINS/cube.metabase-driver.jar"
chmod 644 "$REVIEW_PLUGINS/cube.metabase-driver.jar"
chmod 777 "$REVIEW_PLUGINS"
docker compose up -d metabase-install 2>&1 | tail -1
for _ in $(seq 1 100); do curl -fsS http://127.0.0.1:13302/api/health >/dev/null 2>&1 && break; sleep 3; done
python3 "$HOME/mcd-review/install_connect.py"
docker compose rm -sfv metabase-install 2>&1 | tail -1
