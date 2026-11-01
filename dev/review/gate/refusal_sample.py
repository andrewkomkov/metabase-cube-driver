"""Groups the refused queries of refusals.py by message, and prints each group with one query in short form, so that a person can judge it."""
import collections
import json
import re
import sys


def short(sp):
    def ag(a):
        return a.get("m") or a.get("f") or a.get("op") or "?"
    parts = ["%s agg=%s by=%s where=%s zone=%s week=%s" % (sp["source"], [ag(a) for a in sp.get("agg", [])], [(b.get("c") or b.get("i"), b.get("unit") or b.get("bin")) for b in sp.get("by", [])],
                                                          [(f.get("op"), f.get("c"), f.get("v")) for f in sp.get("where", [])], sp.get("zone"), sp.get("week"))]
    if sp.get("join"):
        parts.append("join=%s" % sp["join"])
    if sp.get("stage"):
        st = sp["stage"]
        parts.append("stage via=%s agg=%s by=%s where=%s" % (st.get("via"), [ag(a) for a in st.get("agg", [])], st.get("by"), st.get("where")))
    return " ".join(parts)[:330]


rows = [json.loads(l) for l in open(sys.argv[1])]
print("queries", len(rows), collections.Counter(r["verdict"] for r in rows))
ref = [r for r in rows if r["verdict"] == "refused"]
print("refused", len(ref), "expected by the generator", sum(1 for r in ref if r.get("expected")))
groups = collections.defaultdict(list)
for r in ref:
    key = re.sub(r"\b[a-z_0-9]+ (is|counts)\b", "X \\1", (r["error"] or "").split(" Cube said:")[0])
    key = re.sub(r"'[^']*'|\d[\d.E]*", "#", key)[:95]
    groups[key].append(r)
for key, g in sorted(groups.items(), key=lambda x: -len(x[1])):
    print("\n[%d, expected %d] %s" % (len(g), sum(1 for x in g if x.get("expected")), key))
    for x in g[:int(sys.argv[2]) if len(sys.argv) > 2 else 2]:
        print("   #%d %s" % (x["index"], short(x["spec"])))
for r in rows:
    if r["verdict"] not in ("pass", "refused", "skip", "stock_known", "stock_no_rows"):
        print("FAIL", r["index"], r["verdict"], str(r.get("diff") or r.get("error"))[:250], short(r["spec"]))
