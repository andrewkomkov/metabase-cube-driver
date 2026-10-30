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

compose up -d --wait clickhouse cube cube-secure
compose rm -sfv metabase-conformance
docker volume rm -f metabase-cube-driver_metabase-conformance-data
compose up -d metabase-conformance

status=0
METABASE_URL=http://127.0.0.1:3301 \
  METABASE_CONTAINER=metabase-cube-driver-metabase-conformance-1 \
  METABASE_ADMIN_PASSWORD=conformance-dev-only-1 \
  python3 "$root/conformance/run.py" "$@" || status=$?

compose stop metabase-conformance
exit "$status"
