#!/bin/sh
# Runs the suite on the service metabase-conformance, from a new volume. It reads no secret store, so it needs no person.
# The service metabase and its volume do not change. See conformance/README.md.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
plugins="$root/dev/plugins-conformance"

# STACK=compose is docker compose on nettop. STACK=container is Apple container on the iMac. See dev/container/README.md.
STACK=${STACK:-compose}
export STACK
stack="$root/dev/container/stack.sh"
services="clickhouse cube cube-secure cube-legacy mail browser"

compose() {
  docker compose -f "$root/dev/docker-compose.yml" -f "$root/dev/docker-compose.conformance.yml" "$@"
}

start_services() {
  if [ "$STACK" = container ]; then
    # The two Metabase services together do not fit in the memory of the iMac. The data of the service metabase stays.
    sh "$stack" stop metabase
    # shellcheck disable=SC2086
    sh "$stack" up $services
  else
    # shellcheck disable=SC2086
    compose up -d --wait $services
  fi
}

seed_extra() {
  if [ "$STACK" = container ]; then
    container exec -i mcd-clickhouse clickhouse-client -u shop --password shop-dev-only --multiquery < "$root/dev/clickhouse/seed-extra.sql"
  else
    compose exec -T clickhouse clickhouse-client -u shop --password shop-dev-only --multiquery < "$root/dev/clickhouse/seed-extra.sql"
  fi
}

new_metabase() {
  if [ "$STACK" = container ]; then
    sh "$stack" reset metabase-conformance
    sh "$stack" up metabase-conformance
  else
    compose rm -sfv metabase-conformance
    docker volume rm -f metabase-cube-driver_metabase-conformance-data
    compose up -d metabase-conformance
  fi
}

case $STACK in
  compose) metabase_container=metabase-cube-driver-metabase-conformance-1 ;;
  container)
    metabase_container=mcd-metabase-conformance
    CUBE_MODEL_DIR=$(sh "$stack" model-dir)
    export CUBE_MODEL_DIR
    ;;
  *) echo "error: STACK must be compose or container" >&2; exit 2 ;;
esac

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
  start_services
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
  seed_extra
  new_metabase
  result=0
  if [ "$STACK" = container ]; then
    # A published port of Apple container cuts the end of a large answer of Cube: 10 of 150 reads of 555 kB were short.
    # So the runner reads Cube at the address of the container, where no read was short. Metabase stays on its
    # published port: its answers were complete, and its OAuth resource must be equal to MB_SITE_URL.
    CUBE_REST_URL=http://$(sh "$stack" address cube):4000
    CUBE_LEGACY_REST_URL=http://$(sh "$stack" address cube-legacy):4000
    export CUBE_REST_URL CUBE_LEGACY_REST_URL
  fi
  METABASE_URL=http://127.0.0.1:3301 \
    METABASE_INTERNAL_URL=http://metabase-conformance:3000 \
    METABASE_CONTAINER="$metabase_container" \
    METABASE_ADMIN_PASSWORD=conformance-dev-only-1 \
    python3 -u "$root/conformance/run.py" "$@" || result=$?
  # Apple container has no restart policy, so a service that stopped in the run made each later check fail.
  if [ "$STACK" = container ]; then
    # shellcheck disable=SC2086
    sh "$stack" check $services metabase-conformance || result=2
  fi
  return "$result"
}

status=0
for variant in ${VARIANT:-off on}; do
  run_variant "$variant" "$@" || status=$?
done

# The demo stack uses the dialect, so a run on stock Cube must not stay.
export CUBE_CLICKHOUSE_FIXES=on
if [ "$STACK" = container ]; then
  sh "$stack" stop metabase-conformance
  sh "$stack" up clickhouse cube cube-secure
else
  compose stop metabase-conformance
  compose up -d clickhouse cube cube-secure
fi
exit "$status"
