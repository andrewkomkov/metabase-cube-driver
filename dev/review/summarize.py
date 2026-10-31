"""Reads the results of run_grid.py and groups each result that is not a pass. Finding names are the ones in docs/review/0.1.0-final.md."""
import collections
import glob
import json
import re
import sys

KNOWN = [
    (r"^(core/.*/first_second|follow/eq/)", "B1 equality on a time at the start of a bucket"),
    (r"^(edge/like_[34]|follow/case(_group|_native)?/)", "B2 case of a text filter"),
    (r"^(native/rolling_day_trunc_in|follow/rolling_native/)", "B3 native rolling window with a function in the filter"),
    (r"^time/revenue_mtd/quarter", "M1 month to date by a quarter"),
    (r"^core/.*/week_of_year/", "M2 week of year differs from Metabase on Postgres"),
    (r"^edge/berlin_not_gt", "known: stock NOT (x > t) on a DateTime64"),
    (r"^edge/raw_ts64", "known: stock raw DateTime64"),
    (r"^native/count_star_empty", "known: native COUNT(*) on no rows"),
    (r"^native/switch_star", "known: raw select of a switch"),
    (r"^native/sum_price_join", "known: native sum of a joined dimension"),
    (r"^edge/raw_dec_big", "known: long decimal"),
]
for path in sys.argv[1:] or sorted(glob.glob("out/*.jsonl")):
    rs = [json.loads(line) for line in open(path)]
    print("==", path, len(rs), dict(collections.Counter(r["verdict"] for r in rs)))
    groups = collections.defaultdict(list)
    for r in rs:
        if r["verdict"] in ("pass", "refused_as_expected", "info", "skip"):
            continue
        name = next((n for pat, n in KNOWN if re.search(pat, r["id"])), None)
        if r["verdict"] == "error" and not name:
            name = "refused or error: " + re.sub(r"[`'\"].*", "", r.get("error", ""))[:90]
        groups[name or "UNEXPLAINED"].append(r)
    for name, items in sorted(groups.items(), key=lambda kv: kv[0]):
        print("  %-4d %s" % (len(items), name))
        if name == "UNEXPLAINED" or name.startswith("refused or error"):
            for r in items[:12]:
                print("        ", r["id"], r["verdict"], str(r.get("diff") or r.get("error") or r.get("got"))[:210])
