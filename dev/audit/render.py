#!/usr/bin/env python3
"""Writes docs/cube-audit.md from cases/, results/ and answers/. It adds no result of its own."""
import glob
import importlib.util
import json
import os
import re
from collections import Counter, OrderedDict
from decimal import Decimal, InvalidOperation

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "..", "docs", "cube-audit.md")
ORDER = ["B1", "B2", "B4", "M1", "M2", "m3", "m4", "m5", "unbounded", "geo", "gran", "big", "propagate", "datetime64", "interval", "extract", "N1", "N2"]
VERSION = "v1.8.15"
VERDICTS = ["confirmed", "confirmed-but-narrow", "intended", "misuse", "already-fixed", "not-reproduced"]


def load_spec(defect):
    sp = importlib.util.spec_from_file_location("cases_" + defect.replace("-", "_"), os.path.join(HERE, "cases", defect + ".py"))
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod


def norm(v):
    if v is None:
        return "NULL"
    s = str(v)
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(\.\d+)?(Z|\+00(:00)?)?", s)
    if m:
        frac = (m.group(3) or "").rstrip("0")
        return m.group(1) + " " + m.group(2) + ("" if frac == "." else frac)
    try:
        d = Decimal(s)
        if d.is_finite():
            return format(d.normalize(), "f")
    except InvalidOperation:
        pass
    return s


def show_rows(rows):
    if rows is None:
        return "none"
    if not rows:
        return "no rows"
    return "; ".join(", ".join(norm(c) for c in r) for r in rows)


def scrub(text):
    text = re.sub(r"query id: [0-9a-f-]+", "query id: <id>", text)
    text = re.sub(r"__table\d+", "__tableN", text)
    return " ".join(text.split())


def cell(text, limit=None):
    text = str(text)
    if limit and len(text) > limit:
        text = text[:limit] + " ..."
    return text.replace("|", "\\|").replace("\n", " ")


def code(text, limit=None):
    text = " ".join(str(text).split())
    if limit and len(text) > limit:
        text = text[:limit] + " ..."
    return "`" + text.replace("|", "\\|").replace("`", "'") + "`"


def input_of(case):
    prefix = ""
    if case.get("setup"):
        prefix = "; ".join(case["setup"]) + "; "
    if "sql" in case:
        return "SQL API: " + code(prefix + case["sql"])
    if "wire" in case:
        w = case["wire"]
        oids = w.get("oids")
        kind = "OID " + ",".join(str(o) for o in oids) if oids else "no OID"
        return "SQL API, Parse and Bind, %s: %s, params %s" % (kind, code(prefix + w["sql"]), code(json.dumps(w.get("params", []))))
    if "rest" in case:
        return "REST: " + code(json.dumps(case["rest"]))
    if "direct" in case:
        d = case["direct"]
        return "Database only: " + code(d if isinstance(d, str) else d.get("ch", ""))
    if "meta" in case:
        return "REST: `GET /v1/meta`, geo members of " + code(case["meta"])
    return ""


def expected_of(case, db=None):
    if case.get("expected_error") and not isinstance(case["expected_error"], dict):
        return "error: " + code(case["expected_error"])
    parts = []
    exp = case.get("expected")
    if isinstance(case.get("expected_error"), dict):
        for k, v in case["expected_error"].items():
            parts.append("%s: error %s" % (k, code(v)))
    if isinstance(exp, dict):
        if all(show_rows(v) == show_rows(list(exp.values())[0]) for v in exp.values()):
            parts.append(code(show_rows(list(exp.values())[0])))
        else:
            for k, v in exp.items():
                parts.append("%s: %s" % (k, code(show_rows(v))))
    elif exp is not None:
        parts.append(code(show_rows(exp)))
    return ", ".join(parts)


def actual_of(r):
    if r.get("error"):
        return "error: " + code(scrub(r["error"]), 170)
    return code(show_rows(r.get("rows")), 170)


def split_cfg(cfg):
    base, _, suffix = cfg.partition("_")
    db, tess = base.split("-")
    return db, tess, suffix.replace("ch-", "ClickHouse ")


def label_db(db, suffix):
    name = {"ch": "ClickHouse", "pg": "Postgres"}[db]
    if suffix == "nopush":
        return name + ", `CUBESQL_SQL_PUSH_DOWN=false`"
    if suffix.startswith("ClickHouse "):
        return suffix
    return name


def shape_from_plan(r):
    """The shape from the stored EXPLAIN text, with the rule of audit.py."""
    plan = r.get("plan")
    if not plan:
        return r.get("shape")
    if "CubeScanWrapper" in plan or "CubeScanWrappedSql" in plan or "WrappedSelect" in plan:
        return "pushdown"
    logical = plan.split("physical_plan")[0].replace("logical_plan", "", 1).strip()
    return "regular" if logical.startswith("CubeScan") else "post-processing"


def load_results(defect):
    """Returns {version: {config: {case id: result}}}."""
    res = {}
    for path in sorted(glob.glob(os.path.join(HERE, "results", defect, "*.json"))):
        version, cfg = os.path.basename(path)[:-5].split("--")
        res.setdefault(version, {})[cfg] = {r["id"]: r for r in json.load(open(path))}
    return res


def case_table(spec, results):
    lines = ["| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    counts = Counter()
    for case in spec.CASES:
        groups = OrderedDict()
        for cfg, rows in results.items():
            r = rows.get(case["id"])
            if not r:
                continue
            db, tess, suffix = split_cfg(cfg)
            used = r.get("used")
            shape = shape_from_plan(r) or "no EXPLAIN"
            if shape == "rest" and used is not None:
                shape = "REST, rollup hit" if used else "REST, no rollup"
            key = (actual_of(r), shape, r.get("verdict"))
            groups.setdefault(key, []).append((db, tess, suffix))
            counts[r.get("verdict")] += 1
        first = True
        for (actual, shape, verdict), members in groups.items():
            dbs = OrderedDict()
            for db, tess, suffix in members:
                dbs.setdefault(label_db(db, suffix), []).append(tess)
            planners = set(tuple(v) for v in dbs.values())
            if len(planners) == 1:
                planner = ", ".join(list(planners)[0])
                database = ", ".join(dbs)
            else:
                planner = "see database"
                database = ", ".join("%s (%s)" % (k, ", ".join(v)) for k, v in dbs.items())
            if first:
                why = cell(case.get("why", ""))
                truth = ""
                for cfg, rows in results.items():
                    r = rows.get(case["id"])
                    if r and r.get("truth"):
                        truth = " Direct SQL: " + code(r["truth"].replace("\t", ", ").replace("\n", "; "), 120) + "."
                        break
                lines.append("| %s | %s | %s. %s%s | %s | %s | %s | %s | %s |" % (case["id"], input_of(case), expected_of(case), why, truth, actual, shape, planner, database, verdict))
                first = False
            else:
                lines.append("| | | | %s | %s | %s | %s | %s |" % (actual, shape, planner, database, verdict))
    return "\n".join(lines), counts


def tag_table(spec, all_results):
    rows = ["| Cube tag | Config | Case | Actual | Result |", "| --- | --- | --- | --- | --- |"]
    tags = [v for v in all_results if v != VERSION]
    key = lambda t: [int(x) for x in re.findall(r"\d+", t)]
    found = False
    for tag in sorted(tags, key=key):
        for cfg, res in sorted(all_results[tag].items()):
            for case_id, r in res.items():
                found = True
                rows.append("| %s | %s | %s | %s | %s |" % (tag, cfg, case_id, actual_of(r), r.get("verdict")))
    return "\n".join(rows) if found else None


def read_answer(defect):
    text = open(os.path.join(HERE, "answers", defect + ".md")).read()
    head, _, body = text.partition("\n---\n")
    meta = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body.strip()


def anchor(defect):
    return defect.lower()


def main():
    intro = open(os.path.join(HERE, "answers", "_intro.md")).read().strip()
    answers, tables = {}, {}
    case_total = 0
    for d in ORDER:
        spec = load_spec(d)
        res = load_results(d)
        meta, body = read_answer(d)
        answers[d] = (meta, body)
        table, counts = case_table(spec, res.get(VERSION, {}))
        tables[d] = (table, tag_table(spec, res), len(spec.CASES), counts)
        case_total += len(spec.CASES)
    verdict_count = Counter(a[0]["verdict"] for a in answers.values())
    out = [intro.replace("{{CASES}}", str(case_total)), "", "## Result", ""]
    out += ["| Verdict | Defects |", "| --- | --- |"]
    for v in VERDICTS:
        out.append("| `%s` | %d |" % (v, verdict_count.get(v, 0)))
    out += ["", "### Defects by real user impact", "", "| Rank | Defect | Verdict | Result for a user | Impact |", "| --- | --- | --- | --- | --- |"]
    ranked = sorted(ORDER, key=lambda d: int(answers[d][0].get("rank", 99)))
    for d in ranked:
        m = answers[d][0]
        out.append("| %s | [%s](#%s) | `%s` | %s | %s |" % (m.get("rank", ""), d, anchor(d), m["verdict"], m["severity"], m["impact"]))
    out += ["", "### Verdicts that differ from docs/cube-findings.md", "", "`docs/cube-findings.md` has `cube-bug` or `cube-clickhouse-bug` for all 18. Each verdict other than `confirmed` differs in strength. The table gives the reason.", "", "| Defect | Findings | Audit | Reason |", "| --- | --- | --- | --- |"]
    for d in ORDER:
        m = answers[d][0]
        if m["verdict"] != "confirmed" or m.get("differs"):
            out.append("| [%s](#%s) | `%s` | `%s` | %s |" % (d, anchor(d), m["findings"], m["verdict"], m.get("differs", "")))
    out += ["", "## Defects", ""]
    for d in ORDER:
        meta, body = answers[d]
        table, tags, n, counts = tables[d]
        out += ["### %s" % d, "", "**%s**" % meta["title"], "", "- **Verdict:** `%s`" % meta["verdict"], "- **Result for a user:** %s" % meta["severity"], "- **Impact:** %s" % meta["impact"], "- **Findings verdict:** `%s`" % meta["findings"], "- **Run:** `python3 audit.py %s`. %d cases, %d runs: %d match the expected value, %d differ." % (d, n, sum(counts.values()), counts.get("match", 0), counts.get("diff", 0)), "", "#### Cases", "", table, ""]
        if tags:
            out += ["#### Older Cube tags", "", tags, ""]
        out += ["#### Refutation", "", body, ""]
    with open(OUT, "w") as f:
        f.write("\n".join(out))
    print("wrote %s: %d defects, %d cases" % (os.path.relpath(OUT), len(ORDER), case_total))
    print(dict(verdict_count))


main()
