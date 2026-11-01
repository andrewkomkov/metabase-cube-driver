#!/bin/sh
# One step after the other, because the report time zone is global.
cd "$HOME/mcd-review"
export REVIEW_ADMIN=gate@example.com REVIEW_PASSWORD=gate-throwaway-1
disk() { avail=$(df -BG --output=avail / | tail -1 | tr -dc 0-9); echo "disk $avail GB $1" >> out/chain.log; [ "$avail" -ge 15 ] || { echo "stop: disk" >> out/chain.log; exit 3; }; }
disk start
python3 -u run_gate.py --db Cube --out out/dialect2.jsonl --only "$(cat out/rerun.txt)" > out/dialect2.log 2>&1; disk dialect2
REVIEW_POSTGRES=postgres python3 -u pg_truths.py > out/pg-truths.txt 2>&1; disk pg
python3 -u agent_flow.py > out/agent.txt 2>&1; disk agent
python3 -u run_gate.py --db "Cube stock" --out out/stock.jsonl > out/stock.log 2>&1; disk stock
python3 -u run_gate.py --db Cube --channel mcp --every 3 --out out/mcp-dialect.jsonl > out/mcp-dialect.log 2>&1; disk mcp1
python3 -u run_gate.py --db "Cube stock" --channel mcp --every 6 --out out/mcp-stock.jsonl > out/mcp-stock.log 2>&1; disk mcp2
python3 -u run_gate.py --db "Cube nodev" --only '^(x|f|j|v|n|sql|p|e)/' --out out/nodev.jsonl > out/nodev.log 2>&1; disk nodev
python3 -u refusals.py --seed gate-1101 --count 1200 --out out/refusals-dialect.jsonl > out/refusals-dialect.log 2>&1; disk refusals
touch out/chain.done
