#!/bin/sh
# `make grid`: the same stack as `make conformance`, with stock Cube and Postgres next to Cube with the dialect. See dev/review/README.md.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
plugins="$root/dev/plugins-conformance"
compose() {
  docker compose -f "$root/dev/docker-compose.yml" -f "$root/dev/docker-compose.conformance.yml" -f "$root/dev/docker-compose.grid.yml" "$@"
}
if [ "${GRID_KEEP:-}" = "" ]; then
  sh "$root/scripts/package.sh"
  mkdir -p "$plugins"
  rm -f "$plugins/cube.metabase-driver.jar"
  cp "$root/dev/plugins/cube.metabase-driver.jar" "$plugins/"
  chmod 777 "$plugins"
fi
export CUBE_CLICKHOUSE_FIXES=on
compose up -d --wait clickhouse cube cube-stock postgres
for url in http://127.0.0.1:4400 http://127.0.0.1:4403; do
  tries=0
  until curl -fsS -o /dev/null "$url/readyz"; do
    tries=$((tries + 1))
    [ "$tries" -le 60 ] || { echo "error: $url did not start" >&2; exit 2; }
    sleep 2
  done
done
compose exec -T clickhouse clickhouse-client -u shop --password shop-dev-only --multiquery < "$root/dev/clickhouse/seed-extra.sql"
ch_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(compose ps -q clickhouse)")
# A copy of the demo data on Postgres. It stays until `make down`.
if [ "$(compose exec -T postgres psql -U shop -d shop -Atc "SELECT to_regclass('public.orders') IS NOT NULL")" != "t" ]; then
  compose exec -T postgres psql -q -U shop -d shop -c "CREATE TABLE users (id int primary key, name text, email text, city text, state text, latitude float8, longitude float8, is_vip boolean, created_at timestamp); CREATE TABLE products (id int primary key, name text, category text, price numeric(10,2)); CREATE TABLE orders (id int primary key, user_id int references users(id), product_id int references products(id), status text, quantity int, amount numeric(12,2), created_at timestamp);"
  for t in users products orders; do
    curl -fsS -u shop:shop-dev-only "http://$ch_ip:8123/?database=shop" -d "SELECT * FROM $t FORMAT CSV" | compose exec -T postgres psql -q -U shop -d shop -c "COPY $t FROM STDIN WITH CSV"
  done
fi
if [ "${GRID_KEEP:-}" = "" ]; then
  compose rm -sfv metabase-conformance
  docker volume rm -f metabase-cube-driver_metabase-conformance-data
fi
compose up -d metabase-conformance
tries=0
until curl -fsS -o /dev/null http://127.0.0.1:3301/api/health; do
  tries=$((tries + 1))
  [ "$tries" -le 120 ] || { echo "error: Metabase did not start" >&2; exit 2; }
  sleep 3
done
status=0
REVIEW_MB=http://127.0.0.1:3301 REVIEW_CH="http://$ch_ip:8123" REVIEW_ADMIN=admin@example.com REVIEW_PASSWORD=conformance-dev-only-1 REVIEW_POSTGRES=postgres \
  python3 -u "$root/dev/review/grid.py" "$@" || status=$?
[ "${GRID_KEEP:-}" != "" ] || compose stop metabase-conformance cube-stock postgres
exit "$status"
