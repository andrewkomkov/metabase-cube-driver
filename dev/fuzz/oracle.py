"""The independent oracle: truth(spec) writes one direct ClickHouse query from a spec. It reads no driver code and no Cube output. README.md has its rules."""
import re

import catalog as C


def q(v):
    return "'%s'" % str(v).replace("\\", "\\\\").replace("'", "\\'")


def lit(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return q(v)


class Refuse(Exception):
    """The only right answer to this query is a refusal."""


class Unknown(Exception):
    """This form has no defined truth."""


class Query:
    def __init__(self, spec):
        self.spec = spec
        self.src = C.SOURCES[spec["source"]]
        self.zone = spec.get("zone") or "UTC"
        self.week = C.DAY_NUMBER[spec.get("week") or "sunday"]
        self.tol = 1e-9
        self.join = spec.get("join")
        self.inner_kinds = None
        self.in_aggregation = False

    # ------------------------------------------------------------------ columns

    def column(self, ref):
        if "i" in ref:
            kind = self.inner_kinds[ref["i"]]
            return "c%d" % ref["i"], kind, True
        if ref.get("j"):
            col = self.src["joins"][self.join["to"]]["cols"][ref["c"]]
        else:
            col = self.src["cols"][ref["c"]]
        return col["sql"], col["kind"], False

    def local(self, sql, kind, staged=False):
        """A time as an instant with the report time zone, so each function on it reads the local clock."""
        if staged:
            return "toDateTime64(%s, 3, %s)" % (sql, q(self.zone))
        if kind == "date":
            return "toDateTime(%s, %s)" % (sql, q(self.zone))
        return "toTimeZone(%s, %s)" % (sql, q(self.zone))

    def from_clause(self):
        text = self.src["from"]
        if self.join and self.join["strategy"] == "inner-join":
            target = self.src["joins"][self.join["to"]]["table"]
            text = text.replace("LEFT JOIN %s " % target, "INNER JOIN %s " % target)
        return text

    def week_shift(self, l):
        return "((toDayOfWeek(%s) - %d + 7) %% 7)" % (l, self.week)

    def week_start(self, l):
        return "(toDate(%s) - toIntervalDay(%s))" % (l, self.week_shift(l))

    def bucket(self, l, unit):
        if unit == "week":
            return self.week_start(l)
        if unit == "day-of-week":
            return "(%s + 1)" % self.week_shift(l)
        if unit == "week-of-year":
            return "ceil(toDayOfYear(%s) / 7)" % self.week_start(l)
        return {
            "minute": "toStartOfMinute(%s)", "hour": "toStartOfHour(%s)", "day": "toDate(%s)", "month": "toStartOfMonth(%s)", "quarter": "toStartOfQuarter(%s)", "year": "toStartOfYear(%s)",
            "minute-of-hour": "toMinute(%s)", "hour-of-day": "toHour(%s)", "day-of-month": "toDayOfMonth(%s)", "day-of-year": "toDayOfYear(%s)", "month-of-year": "toMonth(%s)", "quarter-of-year": "toQuarter(%s)",
        }[unit] % l

    def instant(self, text):
        return "toDateTime64(%s, 3, %s)" % (q(text.replace("T", " ")), q(self.zone))

    # ------------------------------------------------------------------ filters

    def filter(self, f):
        op = f["op"]
        if op == "and" or op == "or":
            return "(%s)" % (" %s " % op.upper()).join(self.filter(a) for a in f["a"])
        if op == "not":
            child = f["a"][0]
            if child["op"] in ("contains", "starts-with", "ends-with"):
                return "(NOT %s OR %s IS NULL)" % (self.filter(child), self.column(child)[0])
            return "(NOT %s)" % self.filter(child)
        if op == "segment":
            return "(%s)" % self.src["segments"][f["s"]]
        sql, kind, staged = self.column(f)
        if kind in ("time", "date"):
            return self.time_filter(f, sql, kind, staged)
        if op == "is-null":
            return "(%s IS NULL)" % sql
        if op == "not-null":
            return "(%s IS NOT NULL)" % sql
        if op == "is-empty":
            return "(%s IS NULL OR %s = '')" % (sql, sql)
        if op == "not-empty":
            return "(%s IS NOT NULL AND %s != '')" % (sql, sql)
        if kind == "num":
            sql = "toFloat64(%s)" % sql
        if kind == "str" and op in ("contains", "starts-with", "ends-with", "does-not-contain"):
            return self.text_filter(f, sql)
        vals = f["v"]
        if op == "=":
            return "(%s IN (%s))" % (sql, ", ".join(lit(v) for v in vals))
        if op == "!=":
            return "(%s)" % " AND ".join("(%s != %s OR %s IS NULL)" % (sql, lit(v), sql) for v in vals)
        if op == "between":
            return "(%s >= %s AND %s <= %s)" % (sql, lit(vals[0]), sql, lit(vals[1]))
        return "(%s %s %s)" % (sql, op, lit(vals[0]))

    def text_filter(self, f, sql):
        v = f["v"][0]
        x, y = ("lowerUTF8(%s)" % sql, "lowerUTF8(%s)" % q(v)) if f.get("ci") else (sql, q(v))
        op = f["op"]
        if op == "starts-with":
            return "startsWith(%s, %s)" % (x, y)
        if op == "ends-with":
            return "endsWith(%s, %s)" % (x, y)
        hit = "(positionUTF8(%s, %s) > 0)" % (x, y)
        return hit if op == "contains" else "(NOT %s OR %s IS NULL)" % (hit, sql)

    def time_filter(self, f, sql, kind, staged):
        op = f["op"]
        if op == "is-null":
            return "(%s IS NULL)" % sql
        if op == "not-null":
            return "(%s IS NOT NULL)" % sql
        l = self.local(sql, kind, staged)
        if op == "time-interval":
            return self.relative(f, l)
        unit, vals = f.get("unit"), f["v"]
        if unit in C.PART_UNITS:
            x, values = self.bucket(l, unit), [str(v) for v in vals]
        elif unit:
            x, values = self.as_time(self.bucket(l, unit), unit), [self.as_time(self.bucket(self.instant(v), unit), unit) for v in vals]
        elif self.in_aggregation:
            x, values = l, [self.instant(v if "T" in str(v) else v + "T00:00:00") for v in vals]
        elif all("T" not in str(v) for v in vals):
            x, values = "toDate(%s)" % l, ["toDate(%s)" % q(v) for v in vals]
        else:
            x, values = l, [self.instant(v) if "T" in str(v) else self.instant(v + "T00:00:00") for v in vals]
        # Metabase core writes `!=` with a unit or a date as the opposite of a range, and NULL stays out of that.
        exact = not unit and any("T" in str(v) for v in vals)
        return self.compare(sql if exact or self.in_aggregation else None, x, op, values)

    def as_time(self, sql, unit):
        return sql if unit in ("minute", "hour") else "toDateTime(%s, %s)" % (sql, q(self.zone))

    @staticmethod
    def compare(raw, x, op, values):
        if op == "=":
            return "(%s = %s)" % (x, values[0])
        if op == "!=":
            return "(%s != %s OR %s IS NULL)" % (x, values[0], raw) if raw else "(%s != %s)" % (x, values[0])
        if op == "between":
            return "(%s >= %s AND %s <= %s)" % (x, values[0], x, values[1])
        return "(%s %s %s)" % (x, op, values[0])

    def relative(self, f, l):
        n, unit = f["n"], f["unit"]
        now = "now(%s)" % q(self.zone)
        add = {"day": "addDays", "week": "addWeeks", "month": "addMonths", "quarter": "addQuarters", "year": "addYears"}[unit]
        x = self.bucket(l, unit)
        here = self.bucket(now, unit)
        return "(%s >= %s(%s, %d) AND %s < %s)" % (x, add, here, n, x, here)

    # ------------------------------------------------------------------ aggregations

    def measure(self, name, where):
        m = self.src["measures"][name]
        if m.get("tol"):
            self.tol = max(self.tol, m["tol"])
        if m.get("total") is True:
            return "(SELECT %s FROM %s WHERE %s)" % (m["sql"], self.from_clause(), where)
        if m.get("total"):
            return m["sql"] % {"total": "(SELECT %s FROM %s WHERE %s)" % (m["total"], self.from_clause(), where)}
        return m["sql"]

    def predicate(self, p):
        self.in_aggregation = True
        try:
            return self.filter(p)
        finally:
            self.in_aggregation = False

    def aggregate(self, a, where):
        if "m" in a:
            return self.measure(a["m"], where)
        if a.get("op") == "/":
            return "(toFloat64(%s) / nullIf(toFloat64(%s), 0))" % (self.aggregate(a["a"], where), self.aggregate(a["b"], where))
        f = a["f"]
        if f == "count":
            return "count()"
        if f == "count-where":
            return "sumOrNull(if(%s, 1, 0))" % self.predicate(a["p"])
        if f == "share":
            return "(sumOrNull(if(%s, 1, 0)) / count())" % self.predicate(a["p"])
        ref = a if "i" in a else {"c": a["c"]}
        sql = self.column(ref)[0]
        if "f32" in sql:
            self.tol = max(self.tol, 1e-5)
        if f == "sum" and self.column(ref)[1] == "count":
            # The driver reads the sum of a count column as the measure, so no rows give 0. See docs/query-compilation.md.
            return "count()"
        if f == "sum-where":
            return "sumOrNull(if(%s, toFloat64(%s), 0))" % (self.predicate(a["p"]), sql)
        return {"sum": "sumOrNull(%s)", "avg": "avgOrNull(toFloat64(%s))", "min": "minOrNull(%s)", "max": "maxOrNull(%s)", "distinct": "uniqExact(%s)",
                "stddev": "stddevPopOrNull(toFloat64(%s))", "var": "varPopOrNull(toFloat64(%s))"}[f] % sql

    def agg_kind(self, a):
        if "m" in a:
            return self.src["measures"][a["m"]]["kind"]
        if a.get("f") in ("min", "max"):
            return self.column(a if "i" in a else {"c": a["c"]})[1]
        return "num"

    def select_time(self, sql, kind):
        return "toDateTime64(%s, 3, %s)" % (sql, q(self.zone)) if kind in ("time", "date") else sql

    # ------------------------------------------------------------------ one stage

    def breakout(self, b):
        sql, kind, staged = self.column(b)
        if kind in ("time", "date"):
            unit = b.get("unit") or "day"
            return self.bucket(self.local(sql, kind, staged), unit), ("int" if unit in C.PART_UNITS else "time")
        if b.get("bin"):
            return "(floor(toFloat64(%s) / %r) * %r)" % (sql, float(b["bin"]), float(b["bin"])), "num"
        return sql, kind

    def stage(self, st):
        """The text of the first stage, with columns c0, c1, ..., and their kinds."""
        conds = [self.filter(f) for f in st.get("where", [])]
        if self.src.get("always"):
            conds.append("(%s)" % self.src["always"])
        where = " AND ".join(conds) or "1"
        rest = "%s WHERE %s" % (self.from_clause(), where)
        if st.get("fields"):
            cols, kinds = [], []
            for name in st["fields"]:
                sql, kind, _ = self.column({"c": name})
                cols.append(self.local(sql, kind) if kind in ("time", "date") else sql)
                kinds.append(kind)
            for name in st.get("measure_fields", []):
                row = self.src["measures"][name]["row"]
                cols.append(row)
                kinds.append("count" if row == "1" else "num")
            return self.finish(cols, rest, st, []), kinds
        by = [self.breakout(b) for b in st.get("by", [])]
        cols, kinds = [b[0] for b in by], [b[1] for b in by]
        for a in st.get("agg", []):
            k = self.agg_kind(a)
            text = self.select_time(self.aggregate(a, where), k)
            # A scalar subquery alone is no aggregation, and a query with no group gives one row also on no rows.
            cols.append("if(count() >= 0, %s, NULL)" % text if not by and "(SELECT " in text else text)
            kinds.append(k)
        return self.finish(cols, rest, st, ["c%d" % i for i in range(len(by))]), kinds

    def finish(self, cols, rest, st, group):
        text = "SELECT %s FROM %s" % (", ".join("%s AS c%d" % (c, i) for i, c in enumerate(cols)), rest)
        if group:
            text += " GROUP BY " + ", ".join(group)
        if st.get("order"):
            n_by = len(st.get("by", []))
            items = []
            for o in st["order"]:
                at = n_by + o["agg"] if "agg" in o else o.get("by", o.get("field", 0))
                items.append("c%d %s" % (at, o["dir"].upper()))
            text += " ORDER BY " + ", ".join(items)
        if st.get("limit"):
            text += " LIMIT %d" % st["limit"]
        return text

    def outer(self, inner_text):
        """The stage after the first one. Its columns read c0, c1, ... of the text before it."""
        st = self.spec["stage"]
        conds = [self.filter(f) for f in st.get("where", [])]
        where = " WHERE " + " AND ".join(conds) if conds else ""
        by = [self.breakout(b) for b in st.get("by", [])]
        aggs = st.get("agg", [])
        if not by and not aggs:
            at = st.get("fields", list(range(len(self.inner_kinds))))
            text = "SELECT %s FROM (%s)%s" % (", ".join("c%d AS o%d" % (i, j) for j, i in enumerate(at)), inner_text, where)
            if st.get("order"):
                text += " ORDER BY " + ", ".join("o%d %s" % (at.index(o["i"]), o["dir"].upper()) for o in st["order"])
            if st.get("limit"):
                text += " LIMIT %d" % st["limit"]
            return text
        out = ["%s AS o%d" % (b[0], j) for j, b in enumerate(by)]
        for k, a in enumerate(aggs):
            out.append("%s AS o%d" % (self.select_time(self.aggregate(a, "1"), self.agg_kind(a)), len(by) + k))
        text = "SELECT %s FROM (%s)%s" % (", ".join(out), inner_text, where)
        if by:
            text += " GROUP BY " + ", ".join("o%d" % j for j in range(len(by)))
        return text

    # ------------------------------------------------------------------ rolling windows and time shifts

    def special_measure(self):
        for a in self.spec.get("agg", []):
            m = self.src["measures"].get(a.get("m"))
            if m and (m.get("window") or m.get("shift")):
                return m
        return None

    def date_range(self):
        """(a, b, others) if the filters hold exactly `created_at >= a` and `created_at < b` with dates, next to filters on other columns. Else None."""
        a, b = None, None
        others = []
        for f in self.spec.get("where", []):
            on_time = f.get("c") == "created_at" and not f.get("unit") and "T" not in str(f.get("v"))
            if on_time and f["op"] == ">=" and a is None:
                a = f["v"][0]
            elif on_time and f["op"] == "<" and b is None:
                b = f["v"][0]
            elif "created_at" in str(f) or f.get("op") in ("or", "not"):
                return None
            else:
                others.append(f)
        return (a, b, others) if a and b else None

    def time_measure(self, m):
        spec = self.spec
        by = spec.get("by", [])
        if len(spec.get("agg", [])) != 1 or spec.get("stage") or spec.get("join"):
            raise Unknown("a special measure in a larger query")
        rng = self.date_range()
        if m.get("shift"):
            if rng is None:
                raise Refuse("a time shift reads a whole date range")
            if len(by) != 1 or by[0].get("c") != "created_at" or by[0].get("unit") != "month":
                raise Unknown("a time shift with another bucket")
            return self.shift(m, rng)
        if rng is None or len(by) != 1 or by[0].get("c") != "created_at" or by[0].get("unit") not in ("day", "month", "quarter", "year"):
            raise Refuse("a rolling window needs a bucket and a whole date range")
        unit = by[0]["unit"]
        a, b, others = rng
        if not self.aligned(a, unit) or not self.aligned(b, unit):
            raise Refuse("a rolling window with a filter inside a bucket")
        if m["window"]["size"] < {"day": 0, "month": 1, "quarter": 3, "year": 12}[unit]:
            raise Refuse("a rolling window that is shorter than the bucket")
        return self.window(m, unit, a, b, others)

    @staticmethod
    def aligned(day, unit):
        _y, mo, d = day.split("-")
        if unit == "day":
            return True
        if d != "01":
            return False
        if unit == "month":
            return True
        return int(mo) % 3 == 1 if unit == "quarter" else mo == "01"

    def shift(self, m, rng):
        a, b, others = rng
        moved = "addMonths(%s, %d)" % (self.local("o.created_at", "time"), m["shift"])
        conds = [self.filter(f) for f in others] + ["toDate(%s) >= toDate(%s)" % (moved, q(a)), "toDate(%s) < toDate(%s)" % (moved, q(b))]
        return "SELECT toStartOfMonth(%s) AS m, %s AS v FROM %s WHERE %s GROUP BY m" % (moved, m["sql"], self.from_clause(), " AND ".join(conds))

    def window(self, m, unit, a, b, others):
        w = m["window"]
        trunc = {"day": "toDate(d)", "month": "toStartOfMonth(d)", "quarter": "toStartOfQuarter(d)", "year": "toStartOfYear(d)"}[unit]
        zone = q(self.zone)
        series = ("SELECT DISTINCT %s AS b, addSeconds(toDateTime(%s + INTERVAL 1 %s, %s), -1) AS e FROM (SELECT toDate(%s) + toIntervalDay(number) AS d FROM numbers(toUInt64(dateDiff('day', toDate(%s), toDate(%s)))))"
                  % (trunc, trunc, unit.upper(), zone, q(a), q(a), q(b)))
        conds = [self.filter(f) for f in others] or ["1"]
        # The row at time 0 keeps each bucket when the filter leaves no row.
        rows = "SELECT toTimeZone(o.created_at, %s) AS t, toNullable(o.amount) AS amt FROM %s WHERE %s UNION ALL SELECT toDateTime(0, %s), CAST(NULL, 'Nullable(Decimal(12, 2))')" % (zone, self.from_clause(), " AND ".join(conds), zone)
        cond = "t > toDateTime(0, %s) AND %s" % (zone, w["to"].replace("'UTC'", zone))
        if w["from"]:
            cond += " AND t >= %s" % w["from"].replace("'UTC'", zone)
        arg = cond if "countIf" in m["sql"] else "if(%s, amt, NULL)" % cond
        return "SELECT b, %s FROM (%s) AS s CROSS JOIN (%s) AS r GROUP BY b" % (m["sql"] % arg, series, rows)

    # ------------------------------------------------------------------ the whole query

    def text(self):
        m = self.special_measure()
        if m:
            return self.time_measure(m)
        text, kinds = self.stage(self.spec)
        if not self.spec.get("stage"):
            return text
        self.inner_kinds = kinds
        return self.outer(text)


def truth(spec):
    """{"sql", "tol", "ordered", "expect"}. `expect` is "refusal" where a number has no meaning, and "unknown" where this form has no defined truth."""
    query = Query(spec)
    try:
        text = query.text()
    except Refuse as e:
        return {"sql": None, "expect": "refusal", "why": str(e)}
    except Unknown as e:
        return {"sql": None, "expect": "unknown", "why": str(e)}
    ordered = bool(spec.get("order") or (spec.get("stage") or {}).get("order"))
    return {"sql": re.sub(r"\s+", " ", text) + " SETTINGS join_use_nulls = 1", "tol": query.tol, "ordered": ordered, "expect": None}
