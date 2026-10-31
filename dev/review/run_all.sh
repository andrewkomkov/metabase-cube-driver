#!/bin/sh
# Runs the last review from zero in compose project mcd-review, on nettop. Copy dev/review to ~/mcd-review first, and the jar of the dry run to /tmp/release-dry-run.jar.
# Results go to ~/mcd-review/out. This script removes nothing. The removal is one command at the end of docs/review/0.1.0-final.md.
set -u
base="$HOME/mcd-review"
mkdir -p "$base/out"
cd "$base"
rm -rf repo
git clone -q https://github.com/andrewkomkov/metabase-cube-driver repo
git -C repo log --oneline | head -1 > out/commit.txt
cp compose.review.yml repo/dev/compose.review.yml
cp zz_review.yml repo/dev/cube/model/zz_review.yml
export COMPOSE_PROJECT_NAME=mcd-review COMPOSE_FILE=docker-compose.yml:compose.review.yml COMPOSE_PROFILES=plain
(cd repo && make up) > out/make-up.log 2>&1
(cd repo/dev && docker compose up -d cube-stock cube-legacy postgres metabase-plain) >> out/make-up.log 2>&1
for url in http://127.0.0.1:13300/api/health http://127.0.0.1:13301/api/health http://127.0.0.1:14403/readyz http://127.0.0.1:14400/readyz; do
  for _ in $(seq 1 100); do
    curl -fsS "$url" >/dev/null 2>&1 && break
    sleep 3
  done
done
docker logs mcd-review-metabase-1 2>&1 | grep -E "Registered driver :cube" | cut -c1-120 >> out/make-up.log
(cd repo/dev && docker compose exec -T postgres psql -U shop -d shop -c "CREATE TABLE users (id int primary key, name text, email text, city text, state text, latitude float8, longitude float8, is_vip boolean, created_at timestamp); CREATE TABLE products (id int primary key, name text, category text, price numeric(10,2)); CREATE TABLE orders (id int primary key, user_id int references users(id), product_id int references products(id), status text, quantity int, amount numeric(12,2), created_at timestamp);") >> out/make-up.log 2>&1
for t in users products orders; do
  curl -s -u shop:shop-dev-only "http://127.0.0.1:18123/?database=shop" -d "SELECT * FROM $t FORMAT CSV" | (cd repo/dev && docker compose exec -T postgres psql -U shop -d shop -c "COPY $t FROM STDIN WITH CSV") >> out/make-up.log 2>&1
done
python3 -u connect.py > out/connect.log 2>&1
# The compare runs first, before a change to one of the two Postgres databases.
python3 -u plugin_effect.py > out/plugin.log 2>&1
python3 -u run_grid.py --db Cube --channel dataset --out out/dataset-dialect.jsonl > out/grid.log 2>&1
python3 -u run_grid.py --db "Cube stock" --channel dataset --out out/dataset-stock.jsonl >> out/grid.log 2>&1
python3 -u run_grid.py --db Cube --channel mcp --every 4 --out out/mcp-dialect.jsonl >> out/grid.log 2>&1
python3 -u run_grid.py --db "Cube stock" --channel mcp --every 8 --out out/mcp-stock.jsonl >> out/grid.log 2>&1
python3 -u settings_grid.py out/settings.jsonl > out/settings.log 2>&1
python3 -u tz_probe.py > out/tz-probe.log 2>&1
python3 -u agent_dashboard.py > out/agent.log 2>&1
python3 -u known_wrong.py > out/known.log 2>&1
(cd repo/dev && CUBE_CLICKHOUSE_FIXES=off docker compose up -d cube-legacy) >> out/make-up.log 2>&1
sleep 15
python3 -u known_legacy.py > out/known-legacy.log 2>&1
(cd repo/dev && docker compose up -d cube-legacy) >> out/make-up.log 2>&1
python3 -u evidence.py > out/evidence.log 2>&1
python3 -u security_review.py > out/security.log 2>&1
python3 -u secret_logs.py > out/secret-logs.log 2>&1
sh install_literal.sh > out/install.log 2>&1
sh install_case1.sh > out/install-case1.log 2>&1
python3 summarize.py out/dataset-dialect.jsonl out/dataset-stock.jsonl out/mcp-dialect.jsonl out/mcp-stock.jsonl > out/summary.txt 2>&1
python3 - <<'PY'
import glob, json
for p in sorted(glob.glob("out/*.jsonl")):
    with open(p.replace(".jsonl", ".notpass.txt"), "w") as f:
        for line in open(p):
            r = json.loads(line)
            if r["verdict"] != "pass":
                r.pop("query", None)
                f.write(json.dumps(r, default=str) + "\n")
PY
echo finished > out/finished.txt
