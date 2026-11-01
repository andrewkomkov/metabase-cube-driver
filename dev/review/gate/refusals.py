"""Runs the generator for one seed and keeps each refused query with its message, so that a person can read a sample. dev/fuzz/fuzz.py counts refusals, and it does not keep them."""
import argparse
import json
import random
import re
import sys

sys.path.insert(0, "fuzz")
import fuzz  # noqa: E402
import gen  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--seed", required=True)
ap.add_argument("--count", type=int, default=1200)
ap.add_argument("--db", default="Cube")
ap.add_argument("--out", required=True)
a = ap.parse_args()
mb, ctx = fuzz.connect()
if a.db != "Cube":
    ctx = fuzz.run_grid.Ctx(mb, a.db)
runner = fuzz.Runner(mb, ctx, stock=a.db != "Cube")
counts = {}
with open(a.out, "w") as f:
    i = 0
    while i < a.count:
        end = min((i // gen.BLOCK + 1) * gen.BLOCK, a.count)
        block = [(n, gen.generate(a.seed, n)) for n in range(i, end)]
        runner.apply(block[0][1]["zone"], block[0][1]["week"])
        for n, spec in block:
            r = fuzz._safe(runner, spec)
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
            f.write(json.dumps({"index": n, "spec": spec, "verdict": r["verdict"], "error": r.get("error"), "expected": r.get("expected"), "diff": r.get("diff")}, default=str) + "\n")
        f.flush()
        i = end
        print(i, counts, flush=True)
runner.reset()
print("total", counts)
