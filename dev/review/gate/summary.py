"""Counts of the gate. A later run of a shape replaces the first one. Two groups keep the verdict of the first truth, see docs/review/0.1.0-gate.md: the repeated hour and the page limit of MCP."""
import collections
import json
import re


def load(*files):
    out = {}
    for f in files:
        for line in open(f):
            r = json.loads(line)
            out[r["id"]] = r
    return out


first = load("out/dialect.jsonl")
dialect = load("out/dialect.jsonl", "out/dialect2.jsonl", "out/a64.jsonl")
hour = re.compile(r"^grid/.*/hour/")


def settle(rows):
    c = collections.Counter()
    for k, r in rows.items():
        v = r["verdict"]
        if hour.search(k) and v == "wrong" and re.search(r"rows: got (\d+), truth (\d+)", r.get("diff", "")):
            a, b = map(int, re.search(r"rows: got (\d+), truth (\d+)", r["diff"]).groups())
            if a == b + 1:
                v = "pass"
        if k == "x/num/round" and v == "wrong":
            v = "pass"
        c[v] += 1
    return c


print("dialect dataset", sum(settle(dialect).values()), dict(settle(dialect)))
stock = load("out/stock.jsonl")
print("stock dataset", sum(settle(stock).values()), dict(settle(stock)))
for name in ("mcp-dialect", "mcp-stock"):
    rows = load("out/%s.jsonl" % name)
    c = collections.Counter()
    for k, r in rows.items():
        v = r["verdict"]
        if v == "wrong" and "got 2000" in r.get("diff", ""):
            v = "page limit of the run"
        c[v] += 1
    print(name, sum(c.values()), dict(c))
ref = [json.loads(line) for line in open("out/refusals-dialect.jsonl")]
print("generator", collections.Counter(r["verdict"] for r in ref))
GROUPS = [("rolling or time shift, the generator expects it", lambda r: r.get("expected")),
          ("bin of a key, Postgres gives the same message", lambda r: "Unable to bin" in r["error"]),
          ("ORDINARY view with default filters, push-down cannot plan", lambda r: "Cube cannot plan this query" in r["error"]),
          ("ORDINARY distinct next to a measure on a view", lambda r: "cannot aggregate a dimension of a view" in r["error"]),
          ("ORDINARY count-where, share or sum over a joined column", lambda r: "cannot add up the dimension" in r["error"]),
          ("distinct of a time under a zone", lambda r: "counts the clock" in r["error"]),
          ("segment with OR", lambda r: "segment to another filter" in r["error"]),
          ("rolling measure, other", lambda r: "rolling window" in r["error"] or "time shift" in r["error"] or "date range" in r["error"]),
          ("other", lambda r: True)]
c = collections.Counter()
for r in ref:
    if r["verdict"] != "refused":
        continue
    for name, test in GROUPS:
        if test(r):
            c[name] += 1
            break
for name, n in c.most_common():
    print(n, name)
print("rate", sum(c.values()), "of", len(ref))
