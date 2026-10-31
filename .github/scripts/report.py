#!/usr/bin/env python3
"""Turns conformance output into summaries, diffs, badges and tables.

Each number comes from a result.json that conformance/run.py wrote, or from the outcome of a job.
If a result does not exist, the output says "no data". This script does not estimate numbers.
"""
import json
import os
import sys
from pathlib import Path

USAGE = "usage: report.py summary RESULT | compare BASE_DIR CANDIDATE_DIR | status CELLS_DIR OUT_DIR [--compat] | notes CELL_DIR"
STATUSES = ("pass", "partial", "blocked", "unsupported", "fail")
NO_DATA = "no data"


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def valid_result(result):
    return (
        isinstance(result, dict)
        and isinstance(result.get("counts"), dict)
        and isinstance(result["counts"].get("pass"), int)
        and isinstance(result["counts"].get("total"), int)
        and isinstance(result.get("features"), list)
        and isinstance(result.get("coverage"), str)
    )


def read_result(directory):
    result = load_json(Path(directory) / "result.json")
    return result if valid_result(result) else None


def output(**values):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for key, value in values.items():
                f.write(f"{key}={value}\n")


def counts_line(result):
    counts = result["counts"]
    parts = [f"{counts[s]} {s}" for s in STATUSES if isinstance(counts.get(s), int)]
    return ", ".join(parts) + f" of {counts['total']}"


def one_line(text):
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def summary(path):
    result = load_json(path)
    if not valid_result(result):
        print(f"**Conformance:** {NO_DATA}. `{path}` does not exist or does not follow the contract.")
        output(valid="false")
        return
    print(f"**Conformance:** {result['coverage']}\n\n{counts_line(result)}\n")
    failed = [f for f in result["features"] if f.get("status") == "fail"]
    if failed:
        print("| Failed feature | Reason |\n| --- | --- |")
        for feature in failed:
            print(f"| `{feature.get('id')}` | {one_line(feature.get('reason'))} |")
    output(valid="true", passed=result["counts"]["pass"], total=result["counts"]["total"])


def compare(base_dir, candidate_dir):
    base, candidate = read_result(base_dir), read_result(candidate_dir)
    cell = load_json(Path(candidate_dir) / "cell.json") or {}
    if base is None:
        print(f"Baseline result: {NO_DATA}. The pass counts cannot be compared.")
        output(decision="no-data")
        return
    if candidate is None:
        print(f"Candidate result: {NO_DATA}. Baseline had {base['counts']['pass']} pass.")
        print(f"\nCandidate conformance outcome: `{cell.get('conformance', 'unknown')}`.")
        output(decision="fell", base_pass=base["counts"]["pass"], candidate_pass=NO_DATA)
        return
    base_pass, candidate_pass = base["counts"]["pass"], candidate["counts"]["pass"]
    print(f"| | pass | total |\n| --- | --- | --- |\n| baseline | {base_pass} | {base['counts']['total']} |")
    print(f"| candidate | {candidate_pass} | {candidate['counts']['total']} |\n")
    before = {f.get("id"): f.get("status") for f in base["features"]}
    after = {f.get("id"): f for f in candidate["features"]}
    rows = []
    for feature in sorted(set(before) | set(after), key=str):
        old, new = before.get(feature), (after.get(feature) or {}).get("status")
        if old != new:
            reason = one_line((after.get(feature) or {}).get("reason"))
            rows.append(f"| `{feature}` | {old or 'absent'} | {new or 'absent'} | {reason} |")
    if rows:
        print("| Feature | Baseline | Candidate | Reason |\n| --- | --- | --- | --- |")
        print("\n".join(rows))
    else:
        print("All features keep their status.")
    if candidate["counts"]["total"] == 0:
        decision = "no-data"
    elif candidate_pass < base_pass:
        decision = "fell"
    else:
        decision = "ok"
    output(decision=decision, base_pass=base_pass, candidate_pass=candidate_pass)


def read_cells(cells_dir):
    cells = []
    for path in sorted(Path(cells_dir).rglob("cell.json")):
        cell = load_json(path)
        if cell:
            cell["result"] = read_result(path.parent)
            cells.append(cell)
    return cells


def cell_ok(cell):
    outcomes = (cell.get("load"), cell.get("conformance"))
    return all(o in ("success", "skipped") for o in outcomes) and "success" in outcomes


def badge(label, message, color):
    return {"schemaVersion": 1, "label": label, "message": message, "color": color}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def coverage_files(cells, out):
    # The badge shows the demo stack, which has the ClickHouse dialect. A cell of stock Cube has its own row in the compatibility table.
    cell = next((c for c in cells if c.get("required") and c.get("clickhouse_fixes") != "off" and c.get("conformance") != "skipped"), None)
    result = cell and cell["result"]
    if not result:
        write_json(out / "badges" / "coverage.json", badge("coverage", NO_DATA, "lightgrey"))
        (out / "coverage.md").write_text("# Coverage\n\nThe last run did not produce a result.\n")
        return
    color = "brightgreen" if cell_ok(cell) else "red"
    message = result["coverage"].removeprefix("coverage: ")
    write_json(out / "badges" / "coverage.json", badge("coverage", message, color))
    write_json(out / "conformance.json", result)
    lines = [
        "# Coverage",
        "",
        "CI generates this page from `conformance/run.py`. Do not edit it.",
        "",
        f"**{result['coverage']}** ({counts_line(result)})",
        "",
        f"Metabase `{cell.get('metabase')}`, Cube `{cell.get('cube')}`, ClickHouse `{cell.get('clickhouse')}`, "
        f"commit `{str(cell.get('sha'))[:12]}`, [run]({cell.get('run_url')}), {cell.get('date')}.",
        "",
        "| Feature | Status | Reason |",
        "| --- | --- | --- |",
    ]
    for feature in result["features"]:
        lines.append(f"| `{feature.get('id')}` | {feature.get('status')} | {one_line(feature.get('reason'))} |")
    (out / "coverage.md").write_text("\n".join(lines) + "\n")


def compat_row(cell):
    return {
        "metabase": cell.get("metabase"),
        "cube": cell.get("cube"),
        "clickhouse": cell.get("clickhouse"),
        "required": bool(cell.get("required")),
        "clickhouse_fixes": cell.get("clickhouse_fixes"),
        "load": cell.get("load"),
        "conformance": cell.get("conformance"),
        "coverage": cell["result"]["coverage"] if cell["result"] else None,
        "ok": cell_ok(cell),
        "sha": cell.get("sha"),
        "run_url": cell.get("run_url"),
        "date": cell.get("date"),
    }


def compat_files(cells, out):
    rows = [compat_row(c) for c in cells]
    rows.sort(key=lambda r: (not r["required"], str(r["metabase"]), str(r["cube"])))
    write_json(out / "compatibility.json", rows)
    total, good = len(rows), sum(r["ok"] for r in rows)
    pinned = next((r for r in rows if r["required"]), None)
    if not rows:
        message, color = NO_DATA, "lightgrey"
    else:
        message = f"{good} of {total} version pairs pass"
        color = "brightgreen" if good == total else ("yellow" if pinned and pinned["ok"] else "red")
    write_json(out / "badges" / "compatibility.json", badge("compatibility", message, color))
    lines = [
        "# Compatibility",
        "",
        "CI generates this table from the version matrix. Do not edit it. Only the pinned pair is required.",
        "The other pairs report only.",
        "",
        "| Metabase | Cube | ClickHouse | cube-clickhouse | Pair | Jar loads | Conformance | Coverage | Run |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| `{r['metabase']}` | `{r['cube']}` | `{r['clickhouse']}` | {r.get('clickhouse_fixes') or 'on'} | {'pinned' if r['required'] else 'report only'} "
            f"| {r['load']} | {r['conformance']} | {r['coverage'] or NO_DATA} | [run]({r['run_url']}) |"
        )
    if rows:
        lines += ["", f"Commit `{str(rows[0]['sha'])[:12]}`, {rows[0]['date']}."]
    (out / "compatibility.md").write_text("\n".join(lines) + "\n")


def status(cells_dir, out_dir, compat):
    out = Path(out_dir)
    (out / "badges").mkdir(parents=True, exist_ok=True)
    cells = read_cells(cells_dir)
    coverage_files(cells, out)
    if compat:
        compat_files(cells, out)


def notes(cell_dir):
    result = read_result(cell_dir)
    cell = load_json(Path(cell_dir) / "cell.json") or {}
    if not result:
        sys.exit("no conformance result: the release notes need one")
    print("\n## Coverage\n")
    print(f"**{result['coverage']}** ({counts_line(result)})\n")
    print(f"From `conformance/run.py` on Metabase `{cell.get('metabase')}`, Cube `{cell.get('cube')}`, "
          f"ClickHouse `{cell.get('clickhouse')}`: [run]({cell.get('run_url')}).")


def main():
    args = sys.argv[1:]
    if args[:1] == ["summary"]:
        summary(args[1])
    elif args[:1] == ["compare"]:
        compare(args[1], args[2])
    elif args[:1] == ["status"]:
        status(args[1], args[2], "--compat" in args)
    elif args[:1] == ["notes"]:
        notes(args[1])
    else:
        sys.exit(USAGE)


if __name__ == "__main__":
    main()
