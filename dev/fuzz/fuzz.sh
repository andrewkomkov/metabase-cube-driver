#!/bin/sh
# `make fuzz SEED=n COUNT=n`: starts the small stack, then runs the generator. See dev/fuzz/README.md.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
project=${FUZZ_PROJECT:-mcd-fuzz}
compose() {
  docker compose -p "$project" -f "$root/dev/docker-compose.yml" -f "$root/dev/fuzz/docker-compose.fuzz.yml" "$@"
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
compose up -d --wait clickhouse cube
wait_for http://127.0.0.1:4400/readyz
compose exec -T clickhouse clickhouse-client -u shop --password shop-dev-only --multiquery < "$root/dev/clickhouse/seed-extra.sql"
compose up -d metabase
wait_for http://127.0.0.1:3300/api/health
REVIEW_MB=http://127.0.0.1:3300 REVIEW_CH=http://127.0.0.1:18123 REVIEW_ADMIN=admin@example.com REVIEW_PASSWORD=fuzz-dev-only-1 \
  exec python3 -u "$root/dev/fuzz/fuzz.py" ${SEED:+--seed "$SEED"} ${COUNT:+--count "$COUNT"} "$@"
