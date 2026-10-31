"""The random query generator. generate(seed, index) gives one spec, a plain JSON value that oracle.py reads to write the truth and mbql.py reads to write the query.
The same seed and index give the same spec on each machine. See README.md for the form of a spec."""
import json
import random

import catalog as C

BLOCK = 40  # The report time zone and the first day of the week are global settings, so one pair holds for a block of queries.


def settings(seed, index):
    rng = random.Random("%s/block/%d" % (seed, index // BLOCK))
    return rng.choice(C.ZONES), rng.choice(C.WEEK_DAYS)


def _weighted(rng, items):
    keys = list(items)
    return rng.choices(keys, weights=[items[k] for k in keys])[0]


def _maybe(rng, p):
    return rng.random() < p


def _plain_measures(src):
    return [k for k, m in src["measures"].items() if not (m.get("window") or m.get("shift"))]


def _number_measures(src):
    return [k for k in _plain_measures(src) if src["measures"][k]["kind"] in ("int", "num")]


def _values(col_name):
    return "typed" if col_name.startswith(("ts_", "ts64", "day", "n_ts")) else "common"


def _time_literal(rng, col_name):
    return rng.choice(C.DATE_VALUES[_values(col_name)] if _maybe(rng, 0.5) else C.TIME_VALUES[_values(col_name)])


def _date_literal(rng, col_name):
    return rng.choice(C.DATE_VALUES[_values(col_name)])


def gen_leaf(rng, ref, col_name, kind, vals):
    """One filter on one column. `ref` is {"c": name} or {"i": position in the stage before}."""
    if kind == "str":
        op = _weighted(rng, {"=": 5, "!=": 3, "contains": 2, "starts-with": 1, "ends-with": 1, "does-not-contain": 2, "is-null": 1, "not-null": 1, "is-empty": 1, "not-empty": 1})
        if op in ("is-null", "not-null", "is-empty", "not-empty"):
            return dict(ref, op=op)
        pool = vals or ["completed", "x"]
        if op in ("=", "!="):
            return dict(ref, op=op, v=rng.sample(pool, min(len(pool), rng.choice([1, 1, 1, 2, 3]))))
        text = rng.choice([v for v in pool if v not in ("%", "_")] or ["x"])
        if len(text) > 3 and _maybe(rng, 0.5):
            start = rng.randrange(len(text) - 2)
            text = text[start:start + 3]
        out = dict(ref, op=op, v=[text])
        if _maybe(rng, 0.25):
            out["ci"] = True
        return out
    if kind in ("int", "num"):
        pool = vals or [0, 1, 100]
        op = _weighted(rng, {"=": 3, "!=": 2, ">": 2, ">=": 2, "<": 2, "<=": 2, "between": 2, "is-null": 1, "not-null": 1})
        if op in ("is-null", "not-null"):
            return dict(ref, op=op)
        if op == "between":
            return dict(ref, op=op, v=[rng.choice(pool), rng.choice(pool)])
        if op in ("=", "!="):
            return dict(ref, op=op, v=rng.sample(pool, min(len(pool), rng.choice([1, 1, 2]))))
        return dict(ref, op=op, v=[rng.choice(pool)])
    if kind == "bool":
        op = _weighted(rng, {"=": 4, "!=": 1, "is-null": 1, "not-null": 1})
        if op in ("is-null", "not-null"):
            return dict(ref, op=op)
        return dict(ref, op=op, v=[rng.choice(vals or [True, False])])
    # A time or a stored date.
    shape = _weighted(rng, {"literal": 6, "unit": 3, "part": 2, "relative": 1, "null": 1})
    if shape == "null":
        return dict(ref, op=rng.choice(["is-null", "not-null"]))
    if shape == "relative":
        return dict(ref, op="time-interval", n=rng.choice([-7, -30, -400, -500, -14, -2]), unit=rng.choice(["day", "week", "month", "quarter", "year"]))
    if shape == "part":
        unit = rng.choice(["hour-of-day", "day-of-week", "month-of-year", "quarter-of-year"] if kind == "time" else ["day-of-week", "month-of-year", "quarter-of-year"])
        # Metabase core loses the number of `x != 3` on a part of a date before a driver gets the query, also on Postgres. See README.md.
        return dict(ref, op="=", unit=unit, v=[rng.choice(C.PART_VALUES[unit])])
    if shape == "unit":
        unit = rng.choice(["day", "week", "month", "quarter", "year"])
        op = rng.choice(["=", "!=", ">", ">=", "<", "<=", "between"])
        n = 2 if op == "between" else 1
        return dict(ref, op=op, unit=unit, v=[_date_literal(rng, col_name) for _ in range(n)])
    op = rng.choice(["=", "!=", ">", ">=", "<", "<=", "between"])
    pick = _date_literal if kind == "date" else _time_literal
    first = pick(rng, col_name)
    if op == "between":
        other = pick(rng, col_name)
        while ("T" in other) != ("T" in first):
            other = pick(rng, col_name)
        return dict(ref, op=op, v=sorted([first, other]) if _maybe(rng, 0.9) else [first, other])
    return dict(ref, op=op, v=[first])


def gen_filter(rng, src, depth=0):
    if src.get("segments") and _maybe(rng, 0.12):
        return {"op": "segment", "s": rng.choice(sorted(src["segments"]))}
    if depth < 2 and _maybe(rng, 0.18):
        kind = rng.choice(["and", "or", "or", "not"])
        if kind == "not":
            return {"op": "not", "a": [gen_filter(rng, src, depth + 1)]}
        return {"op": kind, "a": [gen_filter(rng, src, depth + 1) for _ in range(rng.choice([2, 2, 3]))]}
    name = rng.choice(sorted(src["cols"]))
    col = src["cols"][name]
    return gen_leaf(rng, {"c": name}, name, col["kind"], col["vals"])


def gen_by(rng, src, name):
    col = src["cols"][name]
    if col["kind"] in ("time", "date"):
        small = not src.get("big")
        if _maybe(rng, 0.1):
            return {"c": name}  # With no unit Metabase groups by day.
        units = (["minute", "hour"] if small and col["kind"] == "time" else []) + ["day", "week", "month", "month", "quarter", "year"]
        parts = [u for u in C.PART_UNITS if (small or u != "minute-of-hour") and (col["kind"] == "time" or u not in ("minute-of-hour", "hour-of-day"))]
        return {"c": name, "unit": rng.choice(units if _maybe(rng, 0.7) else parts)}
    if col["kind"] in ("int", "num") and _maybe(rng, 0.2):
        return {"c": name, "bin": rng.choice([1, 2, 5, 10, 25, 100])}
    return {"c": name}


def gen_breakouts(rng, src, most=3):
    names = [n for n, c in src["cols"].items() if c["group"]]
    n = rng.choice([0, 1, 1, 1, 2, 2, 3])
    # Metabase stops at 10000 rows, so a table of ten million rows gets no third breakout.
    most = min(most, 2) if src.get("big") else most
    return [gen_by(rng, src, name) for name in rng.sample(names, min(n, most, len(names)))]


def gen_agg(rng, src):
    kind = _weighted(rng, {"measure": 12, "count": 2, "column": 5, "where": 1, "ratio": 1})
    if kind == "measure":
        return {"m": rng.choice(_plain_measures(src))}
    if kind == "count":
        return {"f": "count"}
    if kind == "where":
        f = rng.choice(["count-where", "sum-where", "share"])
        out = {"f": f, "p": gen_filter(rng, src, 2)}
        if f == "sum-where":
            numeric = [n for n, c in src["cols"].items() if c["kind"] in ("int", "num")]
            if not numeric:
                return {"f": "count"}
            out["c"] = rng.choice(numeric)
        return out
    if kind == "ratio":
        pick = _number_measures(src)
        return {"op": "/", "a": {"m": rng.choice(pick)}, "b": {"m": rng.choice(pick)}}
    name = rng.choice(sorted(src["cols"]))
    k = src["cols"][name]["kind"]
    if k in ("int", "num"):
        return {"f": rng.choice(["sum", "avg", "min", "max", "distinct", "stddev", "var"]), "c": name}
    if k in ("time", "date"):
        return {"f": rng.choice(["min", "max", "distinct"]), "c": name}
    return {"f": "distinct", "c": name}


def gen_aggregated(rng, src):
    spec = {"agg": [gen_agg(rng, src) for _ in range(rng.choice([1, 1, 1, 2, 2, 3]))], "by": gen_breakouts(rng, src), "where": [gen_filter(rng, src) for _ in range(rng.choice([0, 0, 1, 1, 2, 3]))]}
    # An order and a limit need a total order: an additive first aggregation and each breakout. A column that can be NULL has no order that all databases share.
    first = spec["agg"][0]
    safe = first.get("f") == "count" or ("m" in first and src["measures"][first["m"]]["kind"] == "int" and not src["measures"][first["m"]].get("tol"))
    if safe and spec["by"] and not any(src["cols"][b["c"]]["nullable"] for b in spec["by"]) and _maybe(rng, 0.2):
        spec["order"] = [{"agg": 0, "dir": rng.choice(["asc", "desc"])}] + [{"by": j, "dir": rng.choice(["asc", "desc"])} for j in range(len(spec["by"]))]
        spec["limit"] = rng.choice([1, 2, 3, 5, 10])
    return spec


def gen_raw(rng, src):
    names = [n for n in src["cols"] if n != "id"]
    fields = ["id"] + rng.sample(names, min(len(names), rng.choice([1, 2, 3, 5])))
    return {"fields": fields, "where": [gen_filter(rng, src) for _ in range(rng.choice([0, 1, 1, 2]))], "order": [{"field": 0, "dir": "asc"}], "limit": rng.choice([1, 10, 50, 500])}


def gen_join(rng, name, src):
    to = rng.choice(sorted(src["joins"]))
    spec = gen_aggregated(rng, src)
    spec["join"] = {"to": to, "strategy": rng.choice(["left-join", "inner-join"])}
    cols = src["joins"][to]["cols"]
    picked = rng.sample(sorted(cols), min(2, len(cols)))
    spec["by"] = spec["by"][:1] + [{"c": n, "j": 1} for n in picked[:rng.choice([1, 2])]]
    spec.pop("order", None)
    spec.pop("limit", None)
    if _maybe(rng, 0.4):
        col = cols[picked[-1]]
        spec["where"] = spec["where"] + [gen_leaf(rng, {"c": picked[-1], "j": 1}, picked[-1], col["kind"], col["vals"])]
    return spec


def _aligned_range(rng, unit):
    if unit == "day":
        start = rng.choice(["2025-03-01", "2025-02-27", "2025-06-10"])
        return start, {"2025-03-01": "2025-03-08", "2025-02-27": "2025-03-03", "2025-06-10": "2025-06-12"}[start]
    if unit == "month":
        return rng.choice([("2025-02-01", "2025-07-01"), ("2025-01-01", "2025-03-01"), ("2025-11-01", "2026-01-01"), ("2025-04-01", "2025-05-01")])
    if unit == "quarter":
        return rng.choice([("2025-01-01", "2025-07-01"), ("2025-04-01", "2025-10-01")])
    return ("2025-01-01", "2026-01-01")


def gen_time_measure(rng, src, key, units):
    """A rolling window or a time shift: most queries have the form that Cube can answer, and the others must stop."""
    name = rng.choice([k for k, m in src["measures"].items() if m.get(key)])
    time_col = "created_at"
    unit = rng.choice(units)
    a, b = _aligned_range(rng, unit)
    where = [{"c": time_col, "op": ">=", "v": [a]}, {"c": time_col, "op": "<", "v": [b]}]
    by = [{"c": time_col, "unit": unit}]
    variant = _weighted(rng, {"whole": 8, "no bucket": 1, "one bound": 1, "inside": 1})
    if variant == "no bucket":
        by = []
    elif variant == "one bound":
        where = where[:1]
    elif variant == "inside" and unit != "day":
        where[0] = {"c": time_col, "op": ">=", "v": [a[:8] + "15"]}
    if _maybe(rng, 0.3):
        where.append(rng.choice([{"c": "status", "op": "=", "v": ["completed"]}, {"c": "status", "op": "=", "v": ["no such"]}]))
    return {"agg": [{"m": name}], "by": by, "where": where}


def gen_stage(rng, src, name):
    via = rng.choice(["inline", "question", "model"])
    raw = "id" in src["cols"] and _maybe(rng, 0.45)
    if raw:
        return gen_stage_raw(rng, src, via)
    return gen_stage_aggregated(rng, src, via)


def gen_stage_aggregated(rng, src, via):
    inner = {"agg": [gen_agg_simple(rng, src) for _ in range(rng.choice([1, 1, 2]))], "by": [], "where": [gen_filter(rng, src) for _ in range(rng.choice([0, 0, 1]))]}
    inner["by"] = gen_breakouts(rng, src, 2) or [gen_by(rng, src, rng.choice([n for n, c in src["cols"].items() if c["group"]]))]
    n_by, n_agg = len(inner["by"]), len(inner["agg"])
    kinds = stage_kinds(src, inner)
    outer = {"where": [], "by": [], "agg": []}
    agg_at = rng.randrange(n_by, n_by + n_agg)
    if _maybe(rng, 0.6):
        at = rng.randrange(n_by + n_agg)
        outer["where"].append(gen_leaf(rng, {"i": at}, "", kinds[at], stage_values(kinds[at])))
    choice = _weighted(rng, {"rows": 2, "agg": 4, "coarsen": 3, "top": 1})
    if choice == "agg" or choice == "coarsen":
        fn = rng.choice(["sum", "avg", "min", "max", "distinct"]) if kinds[agg_at] in ("int", "num") else rng.choice(["min", "max", "distinct"])
        outer["agg"] = [{"f": fn, "i": agg_at}] if _maybe(rng, 0.85) else [{"f": "count"}]
    if choice == "coarsen":
        times = [j for j in range(n_by) if kinds[j] in ("time", "date")]
        if times:
            outer["by"] = [{"i": rng.choice(times), "unit": rng.choice(["month", "quarter", "year"])}]
        else:
            outer["by"] = [{"i": rng.randrange(n_by)}]
    if choice == "top" and all(not _nullable(src, b) for b in inner["by"]):
        outer["order"] = [{"i": j, "dir": "asc"} for j in range(n_by)]
        outer["limit"] = rng.choice([1, 3])
    inner.update(stage={"via": via, **outer})
    return inner


def _nullable(src, b):
    return src["cols"][b["c"]]["nullable"]


def stage_kinds(src, inner):
    out = []
    for b in inner.get("by", []):
        col = src["cols"][b["c"]]
        unit = b.get("unit")
        out.append("int" if unit in C.PART_UNITS or b.get("bin") else col["kind"])
    for a in inner.get("agg", []):
        out.append(agg_kind(src, a))
    return out


def agg_kind(src, a):
    if "m" in a:
        return src["measures"][a["m"]]["kind"]
    if a.get("f") in ("min", "max") and "c" in a:
        return src["cols"][a["c"]]["kind"]
    return "num"


def stage_values(kind):
    return {"int": [0, 1, 5, 50, 100, 1000], "num": [0, 1, 50, 100, 1000.5], "str": ["completed", "large", "Austin", "alpha", ""], "bool": [True, False]}.get(kind, [])


def gen_agg_simple(rng, src):
    kind = _weighted(rng, {"measure": 8, "count": 2, "column": 3})
    if kind == "measure":
        names = [k for k in _plain_measures(src) if not src["measures"][k].get("total")]
        return {"m": rng.choice(names)}
    if kind == "count":
        return {"f": "count"}
    numeric = [n for n, c in src["cols"].items() if c["kind"] in ("int", "num")]
    if not numeric:
        return {"f": "count"}
    return {"f": rng.choice(["sum", "min", "max", "avg"]), "c": rng.choice(numeric)}


def gen_stage_raw(rng, src, via):
    names = [n for n, c in src["cols"].items() if n != "id" and not c.get("via")]
    fields = ["id"] + rng.sample(names, min(len(names), rng.choice([2, 3, 4])))
    additive = [k for k, m in src["measures"].items() if m.get("row")]
    measures = rng.sample(additive, min(len(additive), rng.choice([0, 1, 2])))
    inner = {"fields": fields, "measure_fields": measures, "where": [gen_filter(rng, src) for _ in range(rng.choice([0, 0, 1]))]}
    n = len(fields)
    kinds = [src["cols"][f]["kind"] for f in fields] + ["num"] * len(measures)
    outer = {"where": [], "by": [], "agg": []}
    if _maybe(rng, 0.6):
        at = rng.randrange(1, n)
        col = src["cols"][fields[at]]
        outer["where"].append(gen_leaf(rng, {"i": at}, fields[at], kinds[at], col["vals"]))
    # On a count column, the sum of no rows is 0 as a measure and NULL as a sum, and a filter on the column picks the sum.
    amounts = [n + j for j, m in enumerate(measures) if src["measures"][m]["row"] != "1"]
    if amounts and _maybe(rng, 0.1):
        outer["where"].append({"i": rng.choice(amounts), "op": rng.choice([">", ">=", "<"]), "v": [rng.choice([0, 1, 50, 150])]})
    choice = _weighted(rng, {"agg": 6, "rows": 1})
    if choice == "rows":
        outer["fields"] = list(range(n))
        outer["order"] = [{"i": 0, "dir": "asc"}]
        outer["limit"] = rng.choice([1, 5, 50])
    else:
        n_agg = rng.choice([1, 1, 2])
        for _ in range(n_agg):
            at = rng.randrange(n + len(measures)) if measures or _maybe(rng, 0.5) else rng.randrange(n)
            k = kinds[at]
            if at >= n:
                outer["agg"].append({"f": "sum", "i": at})
            elif k in ("int", "num"):
                outer["agg"].append({"f": rng.choice(["sum", "avg", "min", "max", "distinct"]), "i": at})
            elif k in ("time", "date"):
                outer["agg"].append({"f": rng.choice(["min", "max", "distinct"]), "i": at})
            elif _maybe(rng, 0.5):
                outer["agg"].append({"f": "distinct", "i": at})
            else:
                outer["agg"].append({"f": "count"})
        if _maybe(rng, 0.6):
            at = rng.randrange(1, n)
            if kinds[at] in ("time", "date"):
                outer["by"] = [{"i": at, "unit": rng.choice(["day", "month", "quarter", "year"])}]
            else:
                outer["by"] = [{"i": at}]
    inner.update(stage={"via": via, **outer})
    return inner


def avoid_clash(spec):
    """A measure with an unqualified column in its SQL, such as min(created_at), is ambiguous next to a join with that column. This is the text of the demo model."""
    src = C.SOURCES[spec["source"]]
    joined = '"users.' in json.dumps(spec) or (spec.get("join") or {}).get("to") == "users"
    for a in spec.get("agg", []):
        if joined and src["measures"].get(a.get("m"), {}).get("kind") == "time":
            a["m"] = "count"
    return spec


def generate(seed, index):
    rng = random.Random("%s/%d" % (seed, index))
    zone, week = settings(seed, index)
    names = list(C.SOURCES)
    name = rng.choices(names, weights=[C.SOURCES[n]["weight"] for n in names])[0]
    src = C.SOURCES[name]
    shape = _weighted(rng, {"aggregated": 54, "raw": 7, "stage": 20, "join": 6, "window": 10, "shift": 3})
    if shape == "raw" and "id" in src["cols"]:
        spec = gen_raw(rng, src)
    elif shape == "stage" and not src.get("big"):
        spec = gen_stage(rng, src, name)
    elif shape == "join" and src.get("joins"):
        spec = gen_join(rng, name, src)
    elif shape == "window" and any(m.get("window") for m in src["measures"].values()):
        spec = gen_time_measure(rng, src, "window", ["day", "month", "quarter", "year"])
    elif shape == "shift" and any(m.get("shift") for m in src["measures"].values()):
        spec = gen_time_measure(rng, src, "shift", ["month"])
    else:
        spec = gen_aggregated(rng, src)
    return avoid_clash(dict(spec, source=name, zone=zone, week=week))


def tags(spec):
    """The features that a spec uses. The report counts them, so that a run shows what it read."""
    out = ["source:" + spec["source"], "zone:" + str(spec.get("zone")), "week:" + str(spec.get("week"))]
    text = json.dumps(spec)
    if spec.get("stage"):
        out += ["stage", "stage:" + spec["stage"]["via"]]
    if spec.get("join"):
        out += ["join:" + spec["join"]["strategy"]]
    if spec.get("fields") and not spec.get("stage"):
        out.append("raw rows")
    if spec.get("limit"):
        out.append("limit")
    src = C.SOURCES[spec["source"]]
    for a in spec.get("agg", []):
        m = src["measures"].get(a.get("m")) if "m" in a else None
        if m is not None:
            out.append("window" if m.get("window") else "shift" if m.get("shift") else "measure")
        else:
            out.append("agg:" + str(a.get("f") or a.get("op")))
    for b in spec.get("by", []):
        out.append("bucket:" + str(b["unit"]) if b.get("unit") else "bin" if b.get("bin") else "breakout")
    for word, tag in (('"via": "model"', "model"), ('"op": "segment"', "filter:segment"), ('"op": "or"', "filter:or"), ('"op": "not"', "filter:not"), ('"op": "time-interval"', "filter:relative"),
                      ('"ci": true', "filter:ignore case"), ('"op": "contains"', "filter:text"), ('"op": "between"', "filter:between"), ('"op": "is-null"', "filter:null")):
        if word in text:
            out.append(tag)
    if src.get("always") or spec["source"] in ("sales", "paid_sales"):
        out.append("view")
    return out
