#!/bin/sh
# `make fuzz-native SEED=n COUNT=n`: starts ClickHouse, three Cubes and one Metabase, then runs the generator of the allow-list of native SQL on each path. See dev/fuzz/README.md.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
project=${FUZZ_PROJECT:-mcd-native}
base=${NATIVE_PORT_BASE:-2}
export NATIVE_PORT_BASE="$base"
seed=${SEED:-$(date +%s)}
count=${COUNT:-1000}
compose() {
  docker compose -p "$project" -f "$root/dev/docker-compose.yml" -f "$root/dev/docker-compose.production.yml" -f "$root/dev/docker-compose.grid.yml" -f "$root/dev/fuzz/docker-compose.native.yml" "$@"
}
wait_for() {
  tries=0
  until curl -fsS -m 5 -o /dev/null "$1"; do
    tries=$((tries + 1))
    if [ "$tries" -gt 120 ]; then
      echo "error: $1 did not start" >&2
      exit 2
    fi
    sleep 3
  done
}
sh "$root/scripts/package.sh"
compose up -d --wait clickhouse cube cube-stock cubestore cube-prod
wait_for "http://127.0.0.1:${base}4400/readyz"
wait_for "http://127.0.0.1:${base}4403/readyz"
wait_for "http://127.0.0.1:${base}4405/readyz"
compose exec -T clickhouse clickhouse-client -u shop --password shop-dev-only --multiquery < "$root/dev/clickhouse/seed-extra.sql"
compose up -d --no-deps metabase
wait_for "http://127.0.0.1:${base}3300/api/health"
export REVIEW_MB="http://127.0.0.1:${base}3300" REVIEW_CH="http://127.0.0.1:${base}8123" REVIEW_ADMIN=admin@example.com REVIEW_PASSWORD=fuzz-dev-only-1
python3 "$root/dev/fuzz/native_paths.py" connect "http://127.0.0.1:${base}4400" > "$root/dev/fuzz/meta.json"
# The check with no Cube runs in the image of Metabase, which has Clojure. The grammar reads the data model of the demo.
# `config --images` lists the image of each service in no fixed order, so read the one of metabase.
image=$(compose config --format json | python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["metabase"]["image"])')
export NATIVE_ADMIT="docker run --rm -i -v $root/src:/s:ro -v $root/dev/fuzz:/f:ro -v /tmp:/tmp --entrypoint java $image -cp /s:/app/metabase.jar clojure.main /f/admit.clj /f/meta.json {queries} {variant} {zone}"
status=0
python3 -u "$root/dev/fuzz/fuzz_native.py" --seed "$seed" --count "$count" --db Cube --path dialect "$@" || status=1
python3 -u "$root/dev/fuzz/fuzz_native.py" --seed "$seed" --count "$count" --db Stock --path stock --stock --no-offline "$@" || status=1
python3 -u "$root/dev/fuzz/fuzz_native.py" --seed "$seed" --count "$count" --db Prod --path production --no-offline "$@" || status=1
# A mutation has its seed in a comment. The driver refuses it before Cube, so no log of Cube has this text.
for service in cube cube-stock cube-prod; do
  if compose logs "$service" 2>&1 | grep -q "mut-$seed"; then
    echo "error: a mutation reached $service" >&2
    status=1
  fi
done
python3 "$root/dev/fuzz/native_paths.py" summary "$root/dev/fuzz/out" "$seed"
if [ -z "${NATIVE_KEEP:-}" ]; then
  compose down -v
fi
exit "$status"
