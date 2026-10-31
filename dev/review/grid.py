"""`make grid`: runs the shapes of shapes.py with their truth, on Cube with the dialect and on stock Cube, through /api/dataset, under a report time
zone and through MCP. It prints the counts. A wrong number or a missing refusal must be on the list in known_limits.py, and each entry of that list must
still occur, or the run fails. See dev/review/README.md."""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import known_limits as K  # noqa: E402

DBS = {"dialect": "Cube", "stock": "Cube stock"}


def verdicts(path):
    with open(path) as f:
        return [json.loads(line) for line in f]


def run(args):
    print("+", " ".join(args), flush=True)
    return subprocess.run([sys.executable, "-u"] + args, cwd=HERE).returncode


def judge(results, variant, seen):
    """(counts, unexpected)."""
    counts = {"pass": 0, "wrong": 0, "no refusal": 0, "refused": 0, "known": 0, "other": 0}
    unexpected = []
    for r in results:
        verdict = r["verdict"]
        if verdict == "pass":
            counts["pass"] += 1
        elif verdict in ("wrong", "no_refusal"):
            limit = K.limit_of(r["id"], variant)
            counts["wrong" if verdict == "wrong" else "no refusal"] += 1
            if limit:
                counts["known"] += 1
                seen.add(limit["id"])
            else:
                unexpected.append(r)
        elif verdict in ("refused_as_expected", "error"):
            counts["refused"] += 1
            # A refusal is a clear message from the driver. A raw error of Cube is not.
            if verdict == "error" and not K.clear_error(r.get("error", "")):
                unexpected.append(r)
        elif verdict in ("harness", "truth_error"):
            unexpected.append(r)
            counts["other"] += 1
        else:
            counts["other"] += 1
    return counts, unexpected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--only", help="run only the shapes whose id matches")
    ap.add_argument("--variant", choices=sorted(DBS), help="run only one variant")
    ap.add_argument("--part", action="append", choices=["dataset", "zones", "mcp", "mcp-zones"], help="run only this part")
    ap.add_argument("--no-stale", action="store_true", help="do not fail on a limit that did not occur, for a run of a part of the grid")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    parts = a.part or ["dataset", "zones", "mcp", "mcp-zones"]
    variants = [a.variant] if a.variant else ["dialect", "stock"]
    if run(["connect.py"]) != 0:
        return 2
    lines, failures, seen = [], [], {v: set() for v in variants}
    for part in parts:
        for variant in variants:
            out = os.path.join(a.out, "%s-%s.jsonl" % (part, variant))
            if part == "dataset":
                cmd = ["run_grid.py", "--db", DBS[variant], "--channel", "dataset", "--out", out]
            elif part == "mcp":
                cmd = ["run_grid.py", "--db", DBS[variant], "--channel", "mcp", "--every", "4", "--out", out]
            else:
                cmd = ["settings_grid.py", "--db", DBS[variant], "--channel", "mcp" if part == "mcp-zones" else "dataset", "--out", out]
            if a.only:
                cmd += ["--only", a.only]
            if run(cmd) != 0:
                failures.append({"id": "%s %s" % (part, variant), "verdict": "the runner failed"})
                continue
            counts, unexpected = judge(verdicts(out), variant, seen[variant])
            lines.append("grid %-9s %-7s: %d shapes, %d pass, %d wrong, %d no refusal, %d refused, %d on the list of known limits, %d other, %d not expected" % (
                part, variant, sum(counts[k] for k in ("pass", "wrong", "no refusal", "refused", "other")), counts["pass"], counts["wrong"], counts["no refusal"],
                counts["refused"], counts["known"], counts["other"], len(unexpected)))
            failures += [dict(r, part=part, variant=variant) for r in unexpected]
    for r in failures:
        print("NOT EXPECTED", r.get("part"), r.get("variant"), r["verdict"], r["id"], str(r.get("diff") or r.get("error") or r.get("got"))[:300])
    if not a.only and not a.part and not a.no_stale:
        for variant in variants:
            for limit in K.LIMITS:
                if variant in limit["variants"] and limit["id"] not in seen[variant]:
                    print("STALE limit %s on %s: no shape shows it, so remove it from the list" % (limit["id"], variant))
                    failures.append({"id": limit["id"], "verdict": "stale"})
    print()
    for line in lines:
        print(line)
    print("grid result: %d not expected" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
