#!/bin/sh
# Runs the demo stack on the iMac with Apple container. The services, ports, credentials and mounts are those of
# dev/docker-compose.yml and dev/docker-compose.conformance.yml: change the two together. See dev/container/README.md.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
dev="$root/dev"
net=mcd
prefix=mcd-
volumes=${STACK_VOLUMES:-/Volumes/SanDisk2Tb/builds/metabase-cube-driver/volumes}
fixes=${CUBE_CLICKHOUSE_FIXES:-on}
img_clickhouse=clickhouse/clickhouse-server:${CLICKHOUSE_VERSION:-26.10.3-alpine}
img_cube=cubejs/cube:${CUBE_VERSION:-v1.8.20}
img_metabase=metabase/metabase:${METABASE_VERSION:-v0.65.3.6}
img_mail=axllent/mailpit:v1.32.0
img_browser=chromedp/headless-shell:157.0.8103.27
all="clickhouse cube cube-secure cube-legacy mail browser metabase metabase-conformance"

die() {
  echo "error: $*" >&2
  exit 2
}

# Prints "name state config-label ip" for each container of the stack.
table() {
  container ls -a --format json | python3 -c '
import json, sys
for c in json.load(sys.stdin):
    if not c["id"].startswith(sys.argv[1]):
        continue
    nets = c["status"].get("networks") or [{}]
    ip = nets[0].get("ipv4Address", "-").split("/")[0]
    print(c["id"], c["status"]["state"], c["configuration"]["labels"].get("mcd.config", "-"), ip)
' "$prefix"
}

field() {
  table | awk -v n="$prefix$1" -v f="$2" '$1 == n { print $f }'
}

# Apple container gives no name lookup between containers, so each container gets the addresses of the others in /etc/hosts.
sync_hosts() {
  hosts=$(table | awk -v p="$prefix" '$2 == "running" { print $4, substr($1, length(p) + 1), "# mcd" }')
  for name in $(table | awk '$2 == "running" { print $1 }'); do
    printf '%s\n' "$hosts" | container exec -i -u 0 "$name" sh -c \
      '{ grep -v "# mcd$" /etc/hosts; cat; } > /tmp/hosts.mcd && cat /tmp/hosts.mcd > /etc/hosts' ||
      echo "warning: no hosts update in $name" >&2
  done
}

# A named volume is an ext4 image in the application directory of Apple container, on the internal disk.
# A symbolic link moves the image to the external drive. A bind mount is not possible: ClickHouse cannot chown on virtiofs.
volume() {
  container volume ls -q | grep -qx "$1" && return 0
  [ -d "$(dirname "$volumes")" ] || die "the external drive is not mounted: $volumes"
  app=$(container system status | awk '$1 == "paths.appRoot" { sub(/^[^ ]+ +/, ""); print }')
  container volume create -s "$2" "$1" >/dev/null
  mkdir -p "$volumes"
  rm -rf "${volumes:?}/$1"
  mv "${app%/}/volumes/$1" "$volumes/$1"
  ln -s "$volumes/$1" "${app%/}/volumes/$1"
}

drop_volume() {
  container volume rm "$1" >/dev/null 2>&1 || true
  rm -rf "${volumes:?}/$1"
}

need_image() {
  container image ls | awk '{ print $1 ":" $2 }' | grep -qx "$1" && return 0
  free=$(df -g /System/Volumes/Data | awk 'NR == 2 { print $4 }')
  [ "$free" -ge 10 ] || die "only $free GB free on the internal disk, and the images are there"
  container image pull --progress none "$1"
}

# Starts one container. A change of an argument makes a new container, the same as docker compose.
launch() {
  name=$prefix$1
  image=$2
  shift 2
  hash=$(printf '%s\n' "$image" "$@" | cksum | cut -d' ' -f1)
  state=$(field "${name#"$prefix"}" 2)
  if [ "$state" != "" ] && [ "$(field "${name#"$prefix"}" 3)" != "$hash" ]; then
    container rm -f "$name" >/dev/null
    state=
  fi
  case $state in
    running) return 0 ;;
    stopped) container start "$name" >/dev/null ;;
    *)
      need_image "$image"
      container run -d --progress none --name "$name" --network "$net" -l "mcd.config=$hash" "$@" "$image" >/dev/null
      ;;
  esac
  sync_hosts
}

wait_for() {
  tries=0
  until "$@" >/dev/null 2>&1; do
    tries=$((tries + 1))
    [ "$tries" -le 90 ] || die "no answer: $*"
    sleep 2
  done
}

clickhouse_ready() {
  # The address of the container: the server that loads the seed listens on the loopback interface only, and it stops after the seed.
  container exec ${prefix}clickhouse sh -c \
    "clickhouse-client -h \$(hostname -i) -u shop --password shop-dev-only -q 'SELECT count() FROM shop.orders' | grep -q 5000"
}

up_clickhouse() {
  volume ${prefix}clickhouse-data 40G
  launch clickhouse "$img_clickhouse" -m 2g -c 4 --ulimit nofile=262144:262144 \
    -e CLICKHOUSE_DB=shop -e CLICKHOUSE_USER=shop -e CLICKHOUSE_PASSWORD=shop-dev-only -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
    -v "$dev/clickhouse/seed.sql:/docker-entrypoint-initdb.d/seed.sql:ro" \
    -v "$dev/clickhouse/seed-extra.sql:/docker-entrypoint-initdb.d/seed-extra.sql:ro" \
    -v ${prefix}clickhouse-data:/var/lib/clickhouse
  wait_for clickhouse_ready
}

# The settings that the three Cube services share. See dev/docker-compose.yml for the reason of each.
cube_common() {
  printf '%s\n' -e CUBEJS_DB_TYPE=clickhouse -e CUBEJS_DB_HOST=clickhouse -e CUBEJS_DB_PORT=8123 -e CUBEJS_DB_NAME=shop \
    -e CUBEJS_DB_USER=shop -e CUBEJS_DB_PASS=shop-dev-only -e CUBEJS_PG_SQL_PORT=15432 -e CUBEJS_TELEMETRY=false \
    -e CUBEJS_PRE_AGGREGATIONS_ALLOW_NON_STRICT_DATE_RANGE_MATCH=false -e CUBEJS_DB_QUERY_LIMIT=2000000 \
    -e "CUBE_CLICKHOUSE_FIXES=$fixes" -v "$root/cube-clickhouse:/cube/conf/cube-clickhouse:ro"
}

second_source() {
  printf '%s\n' -e CUBEJS_DATASOURCES=default,second -e CUBEJS_DS_SECOND_DB_TYPE=clickhouse -e CUBEJS_DS_SECOND_DB_HOST=clickhouse \
    -e CUBEJS_DS_SECOND_DB_PORT=8123 -e CUBEJS_DS_SECOND_DB_NAME=shop_second -e CUBEJS_DS_SECOND_DB_USER=shop \
    -e CUBEJS_DS_SECOND_DB_PASS=shop-dev-only -e CUBEJS_SQL_USER=cube -e CUBEJS_SQL_PASSWORD=cube-dev-only \
    -v "$dev/cube/model:/cube/conf/model:ro" -v "$dev/cube/cube.js:/cube/conf/cube.js:ro"
}

up_cube() {
  # shellcheck disable=SC2046
  launch cube "$img_cube" -m 2g -c 4 -p 127.0.0.1:4400:4000 -p 127.0.0.1:15433:15432 $(cube_common) $(second_source) \
    -e CUBEJS_DEV_MODE=true -e CUBEJS_API_SECRET=cube-dev-only-secret-0123456789abcdef \
    -e CUBEJS_DEFAULT_API_SCOPES=meta,data,graphql,sql,jobs -e CUBESQL_QUERY_CACHE_SIZE=50 -e CUBEJS_COMPILER_CACHE_SIZE=4
  wait_for curl -fsS -o /dev/null http://127.0.0.1:4400/readyz
}

up_cube_secure() {
  # shellcheck disable=SC2046
  launch cube-secure "$img_cube" -m 1g -c 2 -p 127.0.0.1:4401:4000 -p 127.0.0.1:15434:15432 $(cube_common) \
    -e CUBEJS_DEV_MODE=false -e CUBEJS_CACHE_AND_QUEUE_DRIVER=memory -e CUBEJS_API_SECRET=cube-secure-dev-only-secret-0123456789 \
    -e CUBEJS_SQL_PASSWORD=cube-secure-dev-only \
    -v "$dev/cube-secure/cube.js:/cube/conf/cube.js:ro" -v "$dev/cube-secure/model:/cube/conf/model:ro"
  wait_for curl -fsS -o /dev/null http://127.0.0.1:4401/readyz
}

up_cube_legacy() {
  # shellcheck disable=SC2046
  launch cube-legacy "$img_cube" -m 2g -c 2 -p 127.0.0.1:4402:4000 -p 127.0.0.1:15436:15432 $(cube_common) $(second_source) \
    -e CUBEJS_DEV_MODE=true -e CUBEJS_CACHE_AND_QUEUE_DRIVER=memory -e CUBEJS_API_SECRET=cube-legacy-dev-only-secret-0123456789 \
    -e CUBEJS_TESSERACT_SQL_PLANNER=false -e CUBEJS_PRE_AGGREGATIONS_SCHEMA=dev_pre_aggregations_legacy
  wait_for curl -fsS -o /dev/null http://127.0.0.1:4402/readyz
}

# $1 is the service, $2 the host port, $3 the plugins directory.
metabase() {
  volume "$prefix$1-data" 10G
  mkdir -p "$3"
  chmod 777 "$3"
  launch "$1" "$img_metabase" -m 3g -c 4 -p "127.0.0.1:$2:3000" \
    -e MB_DB_FILE=/data/metabase.db -e MB_PLUGINS_DIR=/plugins -e "MB_SITE_URL=http://127.0.0.1:$2" \
    -e MB_ANON_TRACKING_ENABLED=false -e MB_CHECK_FOR_UPDATES=false -e MB_EMAIL_SMTP_HOST=mail -e MB_EMAIL_SMTP_PORT=1025 \
    -e MB_EMAIL_SMTP_SECURITY=none -e MB_EMAIL_FROM_ADDRESS=metabase@example.com -e JAVA_OPTS=-Xmx2g \
    -v "$prefix$1-data:/data" -v "$3:/plugins"
}

up_one() {
  case $1 in
    clickhouse) up_clickhouse ;;
    cube) up_cube ;;
    cube-secure) up_cube_secure ;;
    cube-legacy) up_cube_legacy ;;
    mail) launch mail "$img_mail" -m 256m -c 1 -p 127.0.0.1:8025:8025 ;;
    browser) launch browser "$img_browser" -m 1g -c 2 --shm-size 256m -p 127.0.0.1:9222:9222 ;;
    metabase) metabase metabase 3300 "$dev/plugins" ;;
    metabase-conformance) metabase metabase-conformance 3301 "$dev/plugins-conformance" ;;
    *) die "unknown service: $1" ;;
  esac
}

command -v container >/dev/null || die "Apple container is not installed"
cmd=${1:-}
[ "$#" -eq 0 ] || shift
case $cmd in
  up)
    container network ls -q | grep -qx "$net" || container network create "$net" >/dev/null
    [ "$#" -ne 0 ] || set -- clickhouse cube cube-secure mail metabase
    for service in "$@"; do
      up_one "$service"
    done
    ;;
  stop)
    # shellcheck disable=SC2086
    [ "$#" -ne 0 ] || set -- $all
    for service in "$@"; do
      [ "$(field "$service" 2)" != running ] || container stop "$prefix$service" >/dev/null
    done
    ;;
  # Removes the containers and their volumes. With no service, it removes the whole stack and its network.
  reset)
    whole=$#
    # shellcheck disable=SC2086
    [ "$#" -ne 0 ] || set -- $all
    for service in "$@"; do
      [ "$(field "$service" 2)" = "" ] || container rm -f "$prefix$service" >/dev/null
      drop_volume "$prefix$service-data"
    done
    [ "$whole" -ne 0 ] || container network rm "$net" >/dev/null 2>&1 || true
    ;;
  hosts) sync_hosts ;;
  status) table | awk '{ print $1, $2, $4 }' ;;
  # Fails if a service is not in the state running: Apple container has no restart policy.
  check)
    for service in "$@"; do
      [ "$(field "$service" 2)" = running ] || die "the service $service is not in the state running"
    done
    ;;
  *) die "usage: stack.sh up|stop|reset|check|hosts|status [service...]" ;;
esac
