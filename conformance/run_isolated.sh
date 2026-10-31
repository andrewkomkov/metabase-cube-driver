#!/bin/sh
# Runs the suite on the service metabase-conformance, from a new volume. It reads no secret store, so it needs no person.
# The service metabase and its volume do not change. See conformance/README.md.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
plugins="$root/dev/plugins-conformance"

compose() {
  docker compose -f "$root/dev/docker-compose.yml" -f "$root/dev/docker-compose.conformance.yml" "$@"
}

sh "$root/scripts/package.sh"
mkdir -p "$plugins"
rm -f "$plugins/cube.metabase-driver.jar"
cp "$root/dev/plugins/cube.metabase-driver.jar" "$plugins/"
# The Metabase container runs as uid 2000. It ignores the plugins directory if it cannot write to it.
chmod 777 "$plugins"

# One run for each kind of Cube. VARIANT=off is stock Cube, on is Cube with cube-clickhouse. Each run starts from a new volume.
run_variant() {
  variant=$1
  shift
  export CUBE_CLICKHOUSE_FIXES="$variant"
  compose up -d --wait clickhouse cube cube-secure cube-legacy mail browser
  # A container that restarts counts as started, so this asks Cube.
  for url in http://127.0.0.1:4400 http://127.0.0.1:4401 http://127.0.0.1:4402; do
    tries=0
    until curl -fsS -o /dev/null "$url/readyz"; do
      tries=$((tries + 1))
      if [ "$tries" -gt 60 ]; then
        echo "error: $url did not start" >&2
        return 2
      fi
      sleep 2
    done
  done
  compose exec -T clickhouse clickhouse-client -u shop --password shop-dev-only --multiquery < "$root/dev/clickhouse/seed-extra.sql"
  compose rm -sfv metabase-conformance
  docker volume rm -f metabase-cube-driver_metabase-conformance-data
  compose up -d metabase-conformance
  METABASE_URL=http://127.0.0.1:3301 \
    METABASE_INTERNAL_URL=http://metabase-conformance:3000 \
    METABASE_CONTAINER=metabase-cube-driver-metabase-conformance-1 \
    METABASE_ADMIN_PASSWORD=conformance-dev-only-1 \
    python3 -u "$root/conformance/run.py" "$@"
}

status=0
for variant in ${VARIANT:-off on}; do
  run_variant "$variant" "$@" || status=$?
done

compose stop metabase-conformance
# The demo stack uses the dialect, so a run on stock Cube must not stay.
export CUBE_CLICKHOUSE_FIXES=on
compose up -d clickhouse cube cube-secure
exit "$status"
