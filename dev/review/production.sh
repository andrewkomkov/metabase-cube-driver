#!/bin/sh
# `make production`: runs a sample of the review shapes on Cube with dev mode off and Cube Store, next to the demo Cube. See dev/review/README.md.
# A separate project, so the demo stack and its volumes stay as they are. It uses the ports of the demo stack, so stop that stack first on a host where it runs.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
project=${PRODUCTION_PROJECT:-mcd-production}
compose() {
  docker compose -p "$project" -f "$root/dev/docker-compose.yml" -f "$root/dev/docker-compose.production.yml" "$@"
}
wait_for() {
  tries=0
  until curl -fsS -m 5 -o /dev/null "$1"; do
    tries=$((tries + 1))
    if [ "$tries" -gt 100 ]; then
      echo "error: $1 did not start" >&2
      exit 2
    fi
    sleep 3
  done
}
sh "$root/scripts/package.sh"
compose up -d --wait clickhouse cube cubestore cube-prod
wait_for http://127.0.0.1:4400/readyz
wait_for http://127.0.0.1:4405/readyz
compose exec -T clickhouse clickhouse-client -u shop --password shop-dev-only --multiquery < "$root/dev/clickhouse/seed-extra.sql"
compose up -d metabase
wait_for http://127.0.0.1:3300/api/health
status=0
REVIEW_MB=http://127.0.0.1:3300 REVIEW_CH=http://127.0.0.1:18123 REVIEW_ADMIN=admin@example.com REVIEW_PASSWORD=production-dev-only-1 \
  PYTHONPATH="$root/dev/review:$root/dev/review/gate:$root/dev/fuzz" python3 -u "$root/dev/review/production.py" "$@" || status=$?
if [ -z "${PRODUCTION_KEEP:-}" ]; then
  compose down -v
fi
exit "$status"
