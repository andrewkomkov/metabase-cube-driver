"""Each metric by a day in a zone. Each form of that day in ClickHouse goes through each path, and each path has the same truth: toDate(ts, zone) in ClickHouse.

The forms are in dev/cube/model/zone_day.yml.jinja. A and a64 have a UTC column, B a column typed in the zone, C and cs a day that the ETL stored as a Date and as a String,
and D a day in the SQL of the model. The paths are `report` (Metabase with a report time zone, a bucket on the member at), `rest` (Cube with the time zone in the request),
and `plain` (no report time zone, the member day_at or the text plain_day)."""
import datetime as dt
import json
import re
import time
import urllib.parse

ZONES = {"ny": "America/New_York", "kol": "Asia/Kolkata"}
FORMS = ("a", "a64", "b", "c", "cs", "d")
PATHS = ("report", "rest", "plain")
PARTS = ("day", "coarse", "filter", "stages", "rolling")
DATE_FORMS = ("c", "cs", "d")

AMT = "toFloat64(amount)"
PAID = "status IN ('completed', 'shipped')"
# One query reads all of these, and a stage of the truth reads the rows of one group. No value comes from the parts of another group.
PLAIN = {
    "m_count": "count()",
    "m_users": "uniqExact(user_id)",
    "m_users_approx": "uniqExact(user_id)",
    "m_revenue": "sum(amount)",
    "m_avg_amount": "avg(%s)" % AMT,
    "m_min_amount": "min(amount)",
    "m_max_amount": "max(amount)",
    "m_quantity_sum": "sum(quantity)",
    "m_avg_quantity": "avg(quantity)",
    "m_ratio": "sum(%s) / nullIf(count(), 0)" % AMT,
    "m_status_text": "min(status)",
    "m_has_completed": "toBool(max(status = 'completed'))",
    "m_count_completed": "countIf(status = 'completed')",
    "m_count_large_completed": "countIf(status = 'completed' AND quantity >= 3)",
    "m_paid_revenue": "sumIf(amount, %s)" % PAID,
    "b_median": "quantile(0.5)(%s)" % AMT,
    "b_p90": "quantile(0.9)(%s)" % AMT,
    "b_amount_x_quantity": "sum(amount * quantity)",
    "b_weighted_amount": "sum(%s * quantity) / nullIf(sum(quantity), 0)" % AMT,
    "c_ratio_of_sums": "sum(%s) / nullIf(sum(quantity), 0)" % AMT,
    "c_sum_of_row_ratios": "sum(%s / nullIf(quantity, 0))" % AMT,
    "c_avg_of_ratio": "avg(%s / nullIf(quantity, 0))" % AMT,
    "c_ratio_of_avgs": "avg(%s) / nullIf(avg(quantity), 0)" % AMT,
    "c_paid_share": "sumIf(%s, %s) / nullIf(sum(%s), 0)" % (AMT, PAID, AMT),
    "c_customers_per_row": "uniqExact(user_id) / nullIf(count(), 0)",
    "c_revenue_per_customer": "sum(%s) / nullIf(uniqExact(user_id), 0)" % AMT,
    "m_median_agg": "quantile(0.5)(%s)" % AMT,
}
TOL = {"m_users_approx": 0.1, "b_median": 1e-9, "b_p90": 1e-9, "m_median_agg": 1e-9, "b_weighted_amount": 1e-9, "c_ratio_of_avgs": 1e-9}
# A query with the median of number_agg is a query with stages, so it stays out of the one query of the others.
ONE_QUERY = [m for m in PLAIN if m != "m_median_agg"]

# Each rolling measure: the rows of a day D in the window of a bucket that starts on day b and ends on day e. See rolling_truth for the bucket of a row.
ROLLING = {
    "m_rev_trailing_7d": "D > addDays(e, -7) AND D <= e",
    "m_rev_trailing_1m": "D > addMonths(e, -1) AND D <= e",
    "m_rev_trailing_3m": "D > addMonths(e, -3) AND D <= e",
    "m_rev_leading_1m": "D >= b AND D < addMonths(b, 1)",
    "m_rev_leading_next": "D > e AND D <= addMonths(e, 1)",
    "m_rev_offset_end": "D > addMonths(e, -1) AND D <= e",
    "m_rev_offset_start": "D >= addMonths(b, -1) AND D < b",
    "m_rev_wtd": "D >= toMonday(b) AND D <= e",
    "m_rev_mtd": "D >= toStartOfMonth(b) AND D <= e",
    "m_rev_qtd": "D >= toStartOfQuarter(b) AND D <= e",
    "m_rev_ytd": "D >= toStartOfYear(b) AND D <= e",
    "m_rev_running": "D <= e",
}
# The measures that have a meaning by each unit. The driver stops a window that is shorter than the bucket, and a window of months after the end of a bucket.
BY_UNIT = {
    "day": list(ROLLING),
    "week": ["m_rev_trailing_7d", "m_rev_trailing_1m", "m_rev_trailing_3m", "m_rev_leading_1m", "m_rev_offset_end", "m_rev_offset_start", "m_rev_wtd", "m_rev_mtd", "m_rev_qtd", "m_rev_ytd", "m_rev_running"],
    "month": ["m_rev_trailing_1m", "m_rev_trailing_3m", "m_rev_leading_1m", "m_rev_offset_end", "m_rev_offset_start", "m_rev_mtd", "m_rev_qtd", "m_rev_ytd", "m_rev_running"],
    "year": ["m_rev_ytd", "m_rev_running"],
}
# These read rows in days by a week. See rolling_truth.
SIZED = ("m_rev_trailing_7d", "m_rev_trailing_1m", "m_rev_trailing_3m", "m_rev_leading_1m", "m_rev_offset_end", "m_rev_offset_start")
# Both days with a clock change, the ends of a month and of a year in the zone, and a week that starts in the range.
WINDOWS = {
    "day": [("2025-03-07", "2025-03-11"), ("2025-10-31", "2025-11-04"), ("2024-12-30", "2025-01-02"), ("2025-02-27", "2025-03-02"), ("2025-03-30", "2025-04-02")],
    "week": [("2025-03-03", "2025-03-23"), ("2025-10-27", "2025-11-09")],
    "month": [("2025-02-01", "2025-04-30"), ("2025-10-01", "2025-11-30"), ("2024-12-01", "2025-01-31")],
    "year": [("2025-01-01", "2025-12-31")],
}
UNIT_START = {"day": "toDate", "week": "toMonday", "month": "toStartOfMonth", "quarter": "toStartOfQuarter", "year": "toStartOfYear"}
FILTER_DAYS = ["2025-03-09", "2025-11-02", "2024-12-31", "2025-01-01", "2025-02-28", "2025-03-01"]
RANGES = [("2025-03-08", "2025-03-10"), ("2025-11-01", "2025-11-03"), ("2024-12-31", "2025-01-01"), ("2025-01-31", "2025-03-01")]


def day_of(zone):
    return "toDate(ts, '%s')" % zone


def days_between(lo, hi):
    a, b = dt.date.fromisoformat(lo), dt.date.fromisoformat(hi)
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def buckets(unit, lo, hi):
    """(start, last day) of each bucket of the range."""
    a, last = dt.date.fromisoformat(lo), dt.date.fromisoformat(hi)
    out = []
    while a <= last:
        if unit == "day":
            e = a
        elif unit == "week":
            e = a + dt.timedelta(days=6)
        elif unit == "month":
            e = (a.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
        else:
            e = a.replace(month=12, day=31)
        out.append((a.isoformat(), e.isoformat()))
        a = e + dt.timedelta(days=1)
    return out


class Grid:
    """The checks. `run` is the module conformance/run.py, which has the helpers of the suite."""

    def __init__(self, run):
        self.run = run
        self.truths = {}

    def truth(self, sql):
        if sql not in self.truths:
            self.truths[sql] = self.run.ch(sql)
        return self.truths[sql]

    # ----- one comparison

    def compare(self, label, got, want, members):
        """got and want: {key: {member: value}}. Each path gives its own form of a value, so this compares numbers and text and not rows."""
        if sorted(got) != sorted(want):
            only_got = sorted(set(got) - set(want))[:4]
            only_want = sorted(set(want) - set(got))[:4]
            raise self.run.Fail("%s: keys differ, %d rows against %d. Only in the result %s, only in the truth %s" % (label, len(got), len(want), only_got, only_want))
        for key in sorted(want):
            for m in members:
                g, w = self.value(got[key].get(m)), self.value(want[key].get(m))
                if not self.same(g, w, TOL.get(m, 0.0)):
                    raise self.run.Fail("%s: %s of %s is %r, truth %r" % (label, m, key, got[key].get(m), want[key].get(m)))

    def value(self, v):
        if isinstance(v, bool) or v is None:
            return v
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            if v in ("true", "false"):
                return v == "true"
            try:
                return float(v)
            except ValueError:
                return v
        return v

    def same(self, a, b, tol):
        if isinstance(a, float) and isinstance(b, bool):
            return a == float(b)
        if isinstance(a, bool) and isinstance(b, float):
            return float(a) == b
        if isinstance(a, float) and isinstance(b, float):
            return abs(a - b) <= max(abs(b) * max(tol, 1e-9), 1e-6)
        return a == b

    # ----- each path gives {key: {member: value}}

    def cube_of(self, p, form):
        return "%s_%s" % (p, form)

    def path_rows(self, ctx, path, p, form, members, unit=None, lo=None, hi=None, member=None, by=(), where=None):
        """Rows of a path. `unit` is a bucket on the time member of the path. `by` has more dimensions, and the key of a row is these values and the first day of the bucket."""
        cube = self.cube_of(p, form)
        zone = ZONES[p]
        member = member or ("day_at" if path == "plain" else "at")
        if path == "rest":
            return self.rest(cube, zone, members, unit, lo, hi, member, by, where)
        if path == "plain":
            return self.metabase(ctx, cube, None, members, unit, lo, hi, member, by, where)
        return self.metabase(ctx, cube, zone, members, unit, lo, hi, member, by, where)

    def rest(self, cube, zone, members, unit, lo, hi, member, by, where):
        query = {"measures": ["%s.%s" % (cube, m) for m in members], "dimensions": ["%s.%s" % (cube, d) for d in by], "timezone": zone, "limit": 50000}
        if unit or lo:
            td = {"dimension": "%s.%s" % (cube, member)}
            if unit:
                td["granularity"] = unit
            if lo:
                td["dateRange"] = [lo, hi]
            query["timeDimensions"] = [td]
        if where:
            query["filters"] = where
        res = self.run.pa_load(query)
        if res.get("error"):
            raise self.run.Fail("Cube: %s" % str(res["error"])[:400])
        out = {}
        for row in res["data"]:
            key = tuple(str(row["%s.%s" % (cube, d)]) for d in by)
            if unit:
                key += (row["%s.%s.%s" % (cube, member, unit)][:10],)
            out[key] = {m: row.get("%s.%s" % (cube, m)) for m in members}
        return out

    def metabase(self, ctx, cube, zone, members, unit, lo, hi, member, by, where):
        kw = {"agg": ["m:%s.%s" % (cube, m) for m in members]}
        kw["by"] = ["d:%s.%s" % (cube, d) for d in by]
        if unit:
            kw["by"].append("d:%s.%s@%s" % (cube, member, unit))
        if lo:
            kw["where"] = [[">=", "d:%s.%s" % (cube, member), lo], ["<", "d:%s.%s" % (cube, member), next_day(hi)]]
        if where:
            kw["where"] = kw.get("where", []) + where
        if not kw["by"]:
            del kw["by"]

        def body():
            return ctx.mb.mbql(cube, **kw)["rows"]
        rows = self.in_zone(ctx, zone, body)
        out = {}
        width = len(kw.get("by", []))
        for row in rows:
            key = []
            for i, x in enumerate(row[:width]):
                key.append(str(x)[:10] if unit and i == width - 1 else str(x))
            out[tuple(key)] = dict(zip(members, row[width:]))
        return out

    def in_zone(self, ctx, zone, fn):
        if zone is None:
            return fn()
        box = []
        self.run.with_report_timezone(ctx, zone, lambda: box.append(fn()))
        return box[0]

    # ----- truth: {key: {member: value}}

    def sql_rows(self, sql, members, width):
        out = {}
        for row in self.truth(sql):
            out[tuple(str(x)[:10] if isinstance(x, str) and re.match(r"^\d{4}-\d{2}-\d{2}", x) else str(x) for x in row[:width])] = dict(zip(members, row[width:]))
        return out

    def plain_truth(self, zone, unit, members, where="1"):
        key = "%s(%s)" % (UNIT_START[unit], day_of(zone))
        select = ", ".join(PLAIN[m] for m in members)
        return self.sql_rows("SELECT %s AS k, %s FROM zone_events WHERE %s GROUP BY k" % (key, select, where), members, 1)

    # ----- the parts

    def part_day(self, ctx, path, p, form):
        """Each day of the data, with each measure that reads the rows of the group."""
        zone = ZONES[p]
        want = self.plain_truth(zone, "day", ONE_QUERY)
        self.compare("day", self.path_rows(ctx, path, p, form, ONE_QUERY, unit="day"), want, ONE_QUERY)
        if path == "plain":
            # The text of the day needs no zone at all, in Metabase and in Cube.
            cube = self.cube_of(p, form)
            self.compare("text", self.metabase(ctx, cube, None, ONE_QUERY, None, None, None, None, ["plain_day"], None), want, ONE_QUERY)
            # A report time zone does not move the text.
            self.compare("text in a zone", self.metabase(ctx, cube, zone, ONE_QUERY, None, None, None, None, ["plain_day"], None), want, ONE_QUERY)
        self.compare("median", self.path_rows(ctx, path, p, form, ["m_median_agg"], unit="day", lo="2025-03-07", hi="2025-03-11"),
                     self.plain_truth(zone, "day", ["m_median_agg"], "%s BETWEEN '2025-03-07' AND '2025-03-11'" % day_of(zone)), ["m_median_agg"])
        self.joined(ctx, path, p, form)

    def joined(self, ctx, path, p, form):
        """A metric over a join and a ratio over a join with fan-out, on the rows of that day."""
        D = day_of(ZONES[p])
        cities = self.sql_rows("SELECT %s AS k, uniqExact(u.city) FROM zone_events e LEFT JOIN users u ON e.user_id = u.id GROUP BY k" % D, ["b_distinct_cities"], 1)
        self.compare("over a join", self.path_rows(ctx, path, p, form, ["b_distinct_cities"], unit="day"), cities, ["b_distinct_cities"])
        lo, hi = "2025-03-07", "2025-03-11"
        paid = self.sql_rows("SELECT k, p / r FROM (SELECT %s AS k, sum(toFloat64(amount)) AS r FROM zone_events WHERE k BETWEEN '%s' AND '%s' GROUP BY k) AS a "
                             "JOIN (SELECT %s AS k, sum(toFloat64(y.paid)) AS p FROM zone_events e JOIN (SELECT id AS order_id, amount AS paid FROM orders UNION ALL SELECT id, toDecimal64(1, 2) FROM orders) AS y "
                             "ON e.id = y.order_id GROUP BY k) AS b USING k" % (D, lo, hi, D), ["c_cross_paid_ratio"], 1)
        self.compare("fan-out", self.path_rows(ctx, path, p, form, ["c_cross_paid_ratio"], unit="day", lo=lo, hi=hi), paid, ["c_cross_paid_ratio"])

    def part_coarse(self, ctx, path, p, form):
        """Each week, month, quarter and year in the zone. A distinct count and a ratio read the rows of the bucket. A week starts on the day of the setting of Metabase, and Cube starts it on Monday."""
        zone = ZONES[p]
        members = ["m_count", "m_users", "m_revenue", "m_ratio", "c_customers_per_row", "c_revenue_per_customer", "b_median"]

        def compare():
            for unit in ("week", "month", "quarter", "year"):
                self.compare(unit, self.path_rows(ctx, path, p, form, members, unit=unit), self.plain_truth(zone, unit, members), members)
        self.on_monday(ctx, path, compare)
        if path != "rest":
            sunday = "toStartOfWeek(%s, 0)" % day_of(zone)
            want = self.sql_rows("SELECT %s AS k, %s FROM zone_events GROUP BY k" % (sunday, ", ".join(PLAIN[m] for m in members)), members, 1)
            self.run.with_start_of_week(ctx, "sunday", lambda: self.compare("week from Sunday", self.path_rows(ctx, path, p, form, members, unit="week"), want, members))

    def on_monday(self, ctx, path, fn):
        if path == "rest":
            fn()
        else:
            self.run.with_start_of_week(ctx, "monday", fn)

    def part_filter(self, ctx, path, p, form):
        """A filter on one day, also on a day with a clock change, and a range. The row on each side of midnight stays on its day."""
        zone = ZONES[p]
        D = day_of(zone)
        members = ["m_count", "m_users", "m_revenue", "c_revenue_per_customer"]
        for day in FILTER_DAYS:
            self.compare("day " + day, self.path_rows(ctx, path, p, form, members, unit="day", lo=day, hi=day), self.plain_truth(zone, "day", members, "%s = '%s'" % (D, day)), members)
        for lo, hi in RANGES:
            where = "%s BETWEEN '%s' AND '%s'" % (D, lo, hi)
            self.compare("range %s" % lo, self.path_rows(ctx, path, p, form, members, unit="day", lo=lo, hi=hi), self.plain_truth(zone, "day", members, where), members)
            got = self.path_rows(ctx, path, p, form, ["m_count", "m_revenue"], lo=lo, hi=hi)
            want = self.sql_rows("SELECT count(), sum(amount) FROM zone_events WHERE %s" % where, ["m_count", "m_revenue"], 0)
            self.compare("range with no bucket %s" % lo, got, want, ["m_count", "m_revenue"])
        if path == "plain":
            cube = self.cube_of(p, form)
            want = self.sql_rows("SELECT count() FROM zone_events WHERE %s = '2025-03-09'" % D, ["m_count"], 0)
            for z in (None, zone):
                self.compare("text filter", self.metabase(ctx, cube, z, ["m_count"], None, None, None, None, [], [["=", "d:%s.plain_day" % cube, "2025-03-09"]]), want, ["m_count"])

    def part_stages(self, ctx, path, p, form):
        """Measures with stages. Each stage reads the rows of the day in the zone."""
        zone = ZONES[p]
        D = day_of(zone)
        lo, hi = "2025-03-07", "2025-03-11"
        days = days_between(lo, hi)
        inlist = ", ".join("'%s'" % d for d in days)
        total = "(SELECT sum(toFloat64(amount)) FROM zone_events)"
        rev = "sum(toFloat64(amount))"

        def only(member, want, **kw):
            self.compare(member, self.path_rows(ctx, path, p, form, [member], **kw), want, [member])
        only("m_rev_grand_total", self.sql_rows("SELECT %s AS k, any(%s) FROM zone_events GROUP BY k" % (D, total), ["m_rev_grand_total"], 1), unit="day")
        only("c_share_of_total", self.sql_rows("SELECT %s AS k, %s / any(%s) FROM zone_events GROUP BY k" % (D, rev, total), ["c_share_of_total"], 1), unit="day")
        only("m_rev_group_by", self.sql_rows(
            "SELECT status, %s AS k, any(s) FROM zone_events e JOIN (SELECT status, sum(toFloat64(amount)) AS s FROM zone_events GROUP BY status) x USING status GROUP BY status, k" % D, ["m_rev_group_by"], 2),
            unit="day", by=["status"])
        only("m_rev_reduce_by", self.sql_rows(
            "SELECT status, k, any(s) FROM (SELECT status, %s AS k FROM zone_events) e JOIN (SELECT %s AS k, sum(toFloat64(amount)) AS s FROM zone_events GROUP BY k) x USING k GROUP BY status, k" % (D, D), ["m_rev_reduce_by"], 2),
            unit="day", by=["status"])
        only("m_rev_add_group_by", self.sql_rows("SELECT k, avg(s) FROM (SELECT %s AS k, status, sum(toFloat64(amount)) AS s FROM zone_events GROUP BY k, status) GROUP BY k" % D, ["m_rev_add_group_by"], 1), unit="day")
        only("m_rank", self.sql_rows(
            "SELECT status, k, rank() OVER (PARTITION BY k ORDER BY s DESC) FROM (SELECT status, %s AS k, sum(amount) AS s FROM zone_events WHERE %s IN (%s) GROUP BY status, k)" % (D, D, inlist), ["m_rank"], 2),
            unit="day", by=["status"], lo=lo, hi=hi)
        # A prior period is the revenue of the same bucket one period before, in the zone. Each row of the range has its own earlier day.
        prior_of = {"m_rev_prior_day": "addDays(d, -1)", "m_rev_prior_month": "addMonths(d, -1)"}
        for member, earlier in prior_of.items():
            only(member, self.sql_rows(
                "SELECT d, sumIf(toFloat64(amount), %s = %s) FROM zone_events CROSS JOIN (SELECT toDate(arrayJoin([%s])) AS d) AS b GROUP BY d" % (D, earlier, inlist), [member], 1),
                unit="day", lo=lo, hi=hi)
        prior = "sumIf(toFloat64(amount), %s = addMonths(d, -1))" % D
        this = "sumIf(toFloat64(amount), %s = d)" % D
        cross = "FROM zone_events CROSS JOIN (SELECT toDate(arrayJoin([%s])) AS d) AS b GROUP BY d" % inlist
        only("b_mom", self.sql_rows("SELECT d, (%s - %s) / nullIf(%s, 0) %s" % (this, prior, prior, cross), ["b_mom"], 1), unit="day", lo=lo, hi=hi)
        only("c_to_prior", self.sql_rows("SELECT d, %s / nullIf(%s, 0) %s" % (this, prior, cross), ["c_to_prior"], 1), unit="day", lo=lo, hi=hi)

    def rolling_truth(self, zone, unit, lo, hi, members, spans=None, week_start="toMonday"):
        """Cube reads the rows of a window in buckets, and it compares the start of a bucket with the bounds of the window. By a month a window holds whole months. By a week a window of a size
        reads days, and a window to date reads weeks. The same rule holds with no time zone, see the plain path."""
        spans = spans or buckets(unit, lo, hi)
        array = ", ".join("('%s', '%s')" % s for s in spans)

        def row_bucket(member):
            if unit == "week":
                return day_of(zone) if member in SIZED else "%s(%s)" % (week_start, day_of(zone))
            return "%s(%s)" % (UNIT_START[unit], day_of(zone))
        select = ", ".join("sumIf(toFloat64(amount), %s)" % re.sub(r"\bD\b", row_bucket(m), ROLLING[m]) for m in members)
        sql = "SELECT b, %s FROM zone_events CROSS JOIN (SELECT toDate(x.1) AS b, toDate(x.2) AS e FROM (SELECT arrayJoin([%s]) AS x)) AS w GROUP BY b, e" % (select, array)
        return self.sql_rows(sql, members, 1)

    def part_rolling(self, ctx, path, p, form):
        """Each rolling and to-date window, by day, week, month and year in the zone. Under a time zone stock Cube reads the bounds of a bucket in UTC, so the driver stops them there."""
        zone = ZONES[p]

        def compare():
            for unit, windows in WINDOWS.items():
                members = BY_UNIT[unit]
                for lo, hi in windows:
                    got = self.path_rows(ctx, path, p, form, members, unit=unit, lo=lo, hi=hi)
                    self.compare("%s %s" % (unit, lo), got, self.rolling_truth(zone, unit, lo, hi, members), members)
            # A ratio with a window reads the same rows.
            lo, hi = "2025-03-07", "2025-03-11"
            three = self.rolling_truth(zone, "day", lo, hi, ["m_rev_trailing_3m"])
            moving = {k: {"b_moving_avg_3m": float(v["m_rev_trailing_3m"]) / 3} for k, v in three.items()}
            self.compare("moving average", self.path_rows(ctx, path, p, form, ["b_moving_avg_3m"], unit="day", lo=lo, hi=hi), moving, ["b_moving_avg_3m"])
        self.on_monday(ctx, path, compare)
        if path != "rest":
            # A week from Sunday, also over the day of the clock change, with the custom granularity of the model. Cube has a window to date only from Monday, so this reads windows of a size.
            members = ["m_rev_trailing_7d", "m_rev_trailing_1m"]
            lo, hi = "2025-03-02", "2025-03-22"
            want = self.rolling_truth(zone, "week", lo, hi, members)
            self.run.with_start_of_week(ctx, "sunday", lambda: self.compare("week from Sunday", self.path_rows(ctx, path, p, form, members, unit="week", lo=lo, hi=hi), want, members))

    def run_part(self, ctx, part, path, p, form):
        getattr(self, "part_" + part)(ctx, path, p, form)

    # ----- rollups. Each proof reads usedPreAggregations of the request that the path sends.

    def table_of(self, res, name):
        """The table in Cube Store that served a query, or None if no rollup did."""
        used = res.get("usedPreAggregations") or {}
        for key, value in used.items():
            if key.split(".")[-1].split("__usage_")[0].endswith("_" + name):
                return value.get("targetTableName")
        if used:
            raise self.run.Fail("Cube read other rollups: %s, expected %s" % (sorted(used), name))
        return None

    def expect_table(self, res, name):
        table = self.table_of(res, name)
        if not table:
            raise self.run.Fail("no rollup %s served the query: %s" % (name, sorted(res.get("usedPreAggregations") or {})))
        return table

    def load_rows(self, query, member, measures, unit):
        res = self.run.pa_load(query)
        cube = member.split(".")[0]
        rows = {}
        for row in res["data"]:
            key = (row["%s.%s" % (member, unit)][:10],) if unit else tuple(row[d] for d in query.get("dimensions", []))
            rows[key] = {m: row["%s.%s" % (cube, m)] for m in measures}
        return res, rows

    def by_day_query(self, cube, member, measures, unit="day", tz=None, lo=None, hi=None):
        td = {"dimension": "%s.%s" % (cube, member), "granularity": unit}
        if lo:
            td["dateRange"] = [lo, hi]
        query = {"measures": ["%s.%s" % (cube, m) for m in measures], "timeDimensions": [td]}
        if tz:
            query["timezone"] = tz
        return query

    def load_sql_of(self, query):
        _status, text, _h = self.run.http("GET", self.run.CUBE + "/cubejs-api/v1/sql?query=" + urllib.parse.quote(json.dumps(query)))
        return json.loads(text)["sql"]["preAggregations"]

    def rollup_tables(self, ctx, p):
        """A query in the zone reads a table that Cube built in that zone. A query in UTC reads another table, so a UTC day never gets the label of a day in the zone."""
        zone = ZONES[p]
        cube = p + "_roll"
        members = ["m_count", "m_revenue"]
        res_zone, got_zone = self.load_rows(self.by_day_query(cube, "at", members, tz=zone), cube + ".at", members, "day")
        res_utc, got_utc = self.load_rows(self.by_day_query(cube, "at", members), cube + ".at", members, "day")
        in_zone, in_utc = self.expect_table(res_zone, "by_at_day"), self.expect_table(res_utc, "by_at_day")
        if in_zone == in_utc:
            raise self.run.Fail("one table %s served UTC and %s" % (in_utc, zone))
        self.compare("rollup in the zone", got_zone, self.plain_truth(zone, "day", members), members)
        utc = self.sql_rows("SELECT toDate(ts) AS k, count(), sum(amount) FROM zone_events GROUP BY k", members, 1)
        self.compare("rollup in UTC", got_utc, utc, members)
        if got_zone == got_utc:
            raise self.run.Fail("the days in UTC and in %s are equal, so this proves nothing" % zone)
        # A ratio that is not additive reads the rows of the day in the zone, at the grain of the rollup.
        ratio = ["m_users", "c_revenue_per_customer"]
        query = dict(self.by_day_query(cube, "at", ratio, tz=zone), dimensions=[cube + ".status"], limit=50000)
        res = self.run.pa_load(query)
        self.expect_table(res, "by_at_day")
        got = {(r[cube + ".status"], r[cube + ".at.day"][:10]): {m: r["%s.%s" % (cube, m)] for m in ratio} for r in res["data"]}
        want = self.sql_rows("SELECT status, %s AS k, uniqExact(user_id), sum(toFloat64(amount)) / nullIf(uniqExact(user_id), 0) FROM zone_events GROUP BY status, k" % day_of(zone), ratio, 2)
        self.compare("a ratio from the rollup", got, want, ratio)
        loads = self.load_sql_of(self.by_day_query(cube, "at", members, tz=zone))
        if not any(zone in json.dumps(x.get("loadSql")) for x in loads):
            raise self.run.Fail("the load of the rollup does not name %s: %s" % (zone, json.dumps(loads)[:400]))

    def rollup_job(self, ctx, p):
        """A job builds the rollup for UTC only. A query in the zone must not read that table. A job for the zone gives a table that the query reads."""
        zone = ZONES[p]
        cube = p + "_roll_job"
        members = ["m_revenue"]
        name = cube + ".by_day"

        def job(tz):
            selector = {"action": "post", "selector": {"contexts": [{"securityContext": {}}], "timezones": [tz], "preAggregations": [name]}}
            status, text, _h = self.run.http("POST", self.run.CUBE + "/cubejs-api/v1/pre-aggregations/jobs", selector)
            if status != 200:
                raise self.run.Fail("the jobs API: HTTP %s %s" % (status, text[:300]))
        truth = self.plain_truth(zone, "day", members)

        def load(query):
            """A job and a query can write one table in the same second. Cube then gives this error once, and the next request reads the table."""
            for _ in range(30):
                try:
                    return self.load_rows(query, cube + ".at", members, "day")
                except self.run.Fail as e:
                    if "Unique constraint violation" not in str(e):
                        raise
                    time.sleep(2)
            raise self.run.Fail("a job and a query stop each other: %s" % json.dumps(query)[:200])
        job("UTC")
        utc_table = None
        for _ in range(45):
            res, _rows = load(self.by_day_query(cube, "at", members))
            utc_table = self.table_of(res, "by_day")
            if utc_table:
                break
            time.sleep(2)
        if not utc_table:
            raise self.run.Fail("the job built no table for UTC")
        res, got = load(self.by_day_query(cube, "at", members, tz=zone))
        if self.table_of(res, "by_day") == utc_table:
            raise self.run.Fail("a query in %s read the table of UTC %s" % (zone, utc_table))
        self.compare("before the job of the zone", got, truth, members)
        job(zone)
        for _ in range(45):
            res, got = load(self.by_day_query(cube, "at", members, tz=zone))
            table = self.table_of(res, "by_day")
            if table:
                if table == utc_table:
                    raise self.run.Fail("a query in %s read the table of UTC %s" % (zone, utc_table))
                self.compare("after the job of the zone", got, truth, members)
                return
            time.sleep(2)
        raise self.run.Fail("the job for %s gave no table that the query reads" % zone)

    def metabase_request(self, ctx, cube, zone, **kw):
        """The rows of Metabase and the reply of Cube to the request in the plan of the same query."""
        self.run.ensure_table(ctx, cube)

        def body():
            query = ctx.mb.inner(cube, **kw)
            request = self.run.request_of(self.run.compile_query(ctx, query))
            return ctx.mb.raw(query)["rows"], self.run.pa_load(request)
        return self.in_zone(ctx, zone, body)

    def rollup_metabase(self, ctx, p):
        """Metabase in the zone reads the rollup of the zone: a ratio that is not additive at the same grain, a sum by month from days. A distinct count by month reads the rows."""
        zone = ZONES[p]
        cube = p + "_roll"
        at = "d:%s.at" % cube
        D = day_of(zone)
        rows, res = self.metabase_request(ctx, cube, zone, agg=["m:%s.m_revenue" % cube], by=[at + "@day"])
        self.expect_table(res, "by_at_day")
        self.compare("day", {(r[0][:10],): {"m_revenue": r[1]} for r in rows}, self.plain_truth(zone, "day", ["m_revenue"]), ["m_revenue"])
        ratio = ["m_users", "c_revenue_per_customer"]
        rows, res = self.metabase_request(ctx, cube, zone, agg=["m:%s.%s" % (cube, m) for m in ratio], by=["d:%s.status" % cube, at + "@day"])
        self.expect_table(res, "by_at_day")
        want = self.sql_rows("SELECT status, %s AS k, uniqExact(user_id), sum(toFloat64(amount)) / nullIf(uniqExact(user_id), 0) FROM zone_events GROUP BY status, k" % D, ratio, 2)
        self.compare("a ratio at the grain of the rollup", {(r[0], r[1][:10]): dict(zip(ratio, r[2:])) for r in rows}, want, ratio)
        rows, res = self.metabase_request(ctx, cube, zone, agg=["m:%s.m_revenue" % cube], by=[at + "@month"])
        self.expect_table(res, "by_at_day")
        self.compare("month", {(r[0][:10],): {"m_revenue": r[1]} for r in rows}, self.plain_truth(zone, "month", ["m_revenue"]), ["m_revenue"])
        rows, res = self.metabase_request(ctx, cube, zone, agg=["m:%s.m_users" % cube], by=[at + "@month"])
        if self.table_of(res, "by_at_day"):
            raise self.run.Fail("a distinct count by month read a rollup by day and status")
        self.compare("distinct by month", {(r[0][:10],): {"m_users": r[1]} for r in rows}, self.plain_truth(zone, "month", ["m_users"]), ["m_users"])
        rows, res = self.metabase_request(ctx, cube, zone, agg=["m:%s.m_revenue" % cube], where=[["between", at, "2025-03-09", "2025-03-09"]])
        self.expect_table(res, "by_at_day")
        self.compare("the day of a clock change", {(): {"m_revenue": rows[0][0]}}, self.sql_rows("SELECT sum(amount) FROM zone_events WHERE %s = '2025-03-09'" % D, ["m_revenue"], 0), ["m_revenue"])

    def rollup_rolling(self, ctx, p):
        """A window from the rollup in the zone, on Cube and through Metabase."""
        zone = ZONES[p]
        cube = p + "_roll"
        members = ["m_rev_mtd", "m_rev_trailing_7d"]
        for lo, hi in (("2025-03-07", "2025-03-11"), ("2025-02-27", "2025-03-02")):
            want = self.rolling_truth(zone, "day", lo, hi, members)
            res, got = self.load_rows(self.by_day_query(cube, "at", members, tz=zone, lo=lo, hi=hi), cube + ".at", members, "day")
            self.expect_table(res, "by_at_day")
            self.compare("Cube %s" % lo, got, want, members)
            at = "d:%s.at" % cube
            rows, res = self.metabase_request(ctx, cube, zone, agg=["m:%s.%s" % (cube, m) for m in members], by=[at + "@day"], where=[[">=", at, lo], ["<", at, next_day(hi)]])
            self.expect_table(res, "by_at_day")
            self.compare("Metabase %s" % lo, {(r[0][:10],): dict(zip(members, r[1:])) for r in rows}, want, members)

    def rollup_stored(self, ctx, p):
        """A rollup on the stored day. A report time zone, also of another zone, does not move it."""
        zone = ZONES[p]
        other = ZONES["kol" if p == "ny" else "ny"]
        cube = p + "_roll"
        members = ["m_count", "m_revenue"]
        want = self.plain_truth(zone, "day", members)
        for tz in (zone, other, None):
            res, got = self.load_rows(self.by_day_query(cube, "stored_day", members, tz=tz), cube + ".stored_day", members, "day")
            self.expect_table(res, "by_stored_day")
            self.compare("Cube in %s" % tz, got, want, members)
            rows, res = self.metabase_request(ctx, cube, tz, agg=["m:%s.%s" % (cube, m) for m in members], by=["d:%s.stored_day@day" % cube])
            self.expect_table(res, "by_stored_day")
            self.compare("Metabase in %s" % tz, {(r[0][:10],): dict(zip(members, r[1:])) for r in rows}, want, members)

    def rollup_text(self, ctx, p):
        """A rollup on the stored day as text. It has no zone."""
        zone = ZONES[p]
        cube = p + "_roll"
        members = ["m_count", "m_revenue", "m_users"]
        want = self.plain_truth(zone, "day", members)
        for tz in (zone, None):
            query = {"measures": ["%s.%s" % (cube, m) for m in members], "dimensions": [cube + ".text_day"], "limit": 50000}
            if tz:
                query["timezone"] = tz
            res = self.run.pa_load(query)
            self.expect_table(res, "by_text_day")
            self.compare("Cube", {(r[cube + ".text_day"],): {m: r["%s.%s" % (cube, m)] for m in members} for r in res["data"]}, want, members)
            rows, res = self.metabase_request(ctx, cube, tz, agg=["m:%s.%s" % (cube, m) for m in members], by=["d:%s.text_day" % cube])
            self.expect_table(res, "by_text_day")
            self.compare("Metabase", {(r[0],): dict(zip(members, r[1:])) for r in rows}, want, members)

    # ----- other checks in the zone

    def stored_day_other_zone(self, ctx, p):
        """The stored day of one zone under the report time zone of another zone. The form C must not move."""
        other = ZONES["kol" if p == "ny" else "ny"]
        members = ["m_count", "m_revenue"]
        want = self.plain_truth(ZONES[p], "day", members)
        for form in ("c", "cs", "d"):
            cube = self.cube_of(p, form)
            self.compare(form, self.metabase(ctx, cube, other, members, "day", None, None, "at", (), None), want, members)
            self.compare(form, self.rest(cube, other, members, "day", None, None, "at", (), None), want, members)

    def time_measure(self, ctx, p):
        """A measure of type time. With SQL_UTILS.convertTz it has the clock of the zone, on each form. A plain max(ts) is the same instant in UTC, with no offset, so the catalog uses the first form."""
        zone = ZONES[p]
        local = {tuple(r[:1]): r[1] for r in self.truth("SELECT toString(%s) AS k, toString(max(toTimeZone(ts, '%s'))) FROM zone_events GROUP BY k" % (day_of(zone), zone))}
        utc = {tuple(r[:1]): r[1] for r in self.truth("SELECT toString(%s) AS k, toString(max(ts)) FROM zone_events GROUP BY k" % day_of(zone))}
        for form in ("a", "b", "d"):
            cube = self.cube_of(p, form)
            for member, want in (("m_last_local", local), ("m_last_at", utc)):
                for name, got in (("Metabase", self.metabase(ctx, cube, zone, [member], "day", None, None, "at", (), None)), ("Cube", self.rest(cube, zone, [member], "day", None, None, "at", (), None))):
                    for key, truth in want.items():
                        if dt.datetime.fromisoformat(got[key][member]) != dt.datetime.fromisoformat(truth):
                            raise self.run.Fail("%s %s %s: %s is %s, truth %s" % (form, name, member, key, got[key][member], truth))

    def native(self, ctx, p):
        """Native SQL on the SQL API in the zone: a bucket with a measure and with a window. A person writes the same text as in the query builder."""
        zone = ZONES[p]
        cube = self.cube_of(p, "a")
        D = day_of(zone)
        lo, hi = "2025-03-07", "2025-03-11"

        def body(member, want):
            sql = ("SELECT DATE_TRUNC('day', at) AS d, MEASURE(%s) AS v FROM %s WHERE at >= TIMESTAMP '%s 00:00:00' AND at < TIMESTAMP '%s 00:00:00' GROUP BY 1 ORDER BY 1"
                   % (member, cube, lo, next_day(hi)))
            rows = self.in_zone(ctx, zone, lambda: ctx.mb.native(sql)["rows"])
            self.compare(member, {(r[0][:10],): {member: r[1]} for r in rows}, want, [member])
        body("m_revenue", self.plain_truth(zone, "day", ["m_revenue"], "%s BETWEEN '%s' AND '%s'" % (D, lo, hi)))
        for member in ("m_rev_mtd", "m_rev_trailing_1m"):
            body(member, self.rolling_truth(zone, "day", lo, hi, [member]))

    def mcp(self, ctx, p):
        """The MCP tool in the zone, with a measure, a ratio and a window."""
        zone = ZONES[p]
        cube = self.cube_of(p, "a")
        self.run.ensure_table(ctx, cube)
        at = "d:%s.at" % cube
        lo, hi = "2025-03-07", "2025-03-11"
        where = [[">=", at, lo], ["<", at, next_day(hi)]]

        def rows(member):
            return self.in_zone(ctx, zone, lambda: ctx.mcp.call("execute_query", query=self.run.mcp_query(ctx.mb, cube, agg=["m:%s.%s" % (cube, member)], by=[at + "@day"], where=where))["rows"])
        for member in ("m_revenue", "c_revenue_per_customer"):
            self.compare(member, {(r[0][:10],): {member: r[1]} for r in rows(member)}, self.plain_truth(zone, "day", [member], "%s BETWEEN '%s' AND '%s'" % (day_of(zone), lo, hi)), [member])
        self.compare("window", {(r[0][:10],): {"m_rev_mtd": r[1]} for r in rows("m_rev_mtd")}, self.rolling_truth(zone, "day", lo, hi, ["m_rev_mtd"]), ["m_rev_mtd"])

    def sunday_refused(self, ctx):
        """With no custom granularity a week from Sunday is a number, so Cube has no time series for a window."""
        at = "d:metric_types.created_at"
        for zone in (None, ZONES["ny"]):
            # Under a zone, stock Cube stops first with the reason of the window.
            needle = "weeks from Monday only" if zone is None or self.run.DIALECT else "reads the bounds"

            def body(needle=needle):
                query = ctx.mb.inner("metric_types", agg=["m:metric_types.m_rev_trailing_3m"], by=[at + "@week"], where=[[">=", at, "2025-03-02"], ["<", at, "2025-03-23"]])
                self.run.with_start_of_week(ctx, "sunday", lambda: self.run.expect_clear_error(ctx, query, needle))
            self.in_zone(ctx, zone, body)

    def catalog(self, ctx, p):
        """The cubes of dev/cube/model/metrics.yml by a day in the zone: a UTC column of the catalog, with each measure that has a rule here."""
        zone = ZONES[p]
        D = "toDate(created_at, '%s')" % zone
        cube = "metric_types"
        at = "d:%s.created_at" % cube
        members = ["m_count", "m_users", "m_revenue", "m_avg_amount", "m_min_amount", "m_max_amount", "m_quantity_sum", "m_ratio", "m_count_completed", "m_paid_revenue", "b_median", "b_p90", "b_weighted_amount", "c_ratio_of_sums", "c_customers_per_order", "c_revenue_per_customer"]
        amt = "toFloat64(amount)"
        truth = ("count()", "uniqExact(user_id)", "sum(amount)", "avg(%s)" % amt, "min(amount)", "max(amount)", "sum(quantity)", "sum(%s) / nullIf(count(), 0)" % amt, "countIf(status = 'completed')", "sumIf(amount, %s)" % PAID,
                 "quantile(0.5)(%s)" % amt, "quantile(0.9)(%s)" % amt, "sum(%s * quantity) / nullIf(sum(quantity), 0)" % amt, "sum(%s) / nullIf(sum(quantity), 0)" % amt, "uniqExact(user_id) / nullIf(count(), 0)", "sum(%s) / nullIf(uniqExact(user_id), 0)" % amt)
        want = self.sql_rows("SELECT %s AS k, %s FROM orders GROUP BY k" % (D, ", ".join(truth)), members, 1)
        rows = self.in_zone(ctx, zone, lambda: ctx.mb.mbql(cube, agg=["m:%s.%s" % (cube, m) for m in members], by=[at + "@day"])["rows"])
        self.compare("metric_types", {(r[0][:10],): dict(zip(members, r[1:])) for r in rows}, want, members)
        # The windows of the catalog.
        lo, hi = "2025-03-07", "2025-03-11"
        windows = ["m_rev_trailing_3m", "m_rev_leading_1m", "m_rev_offset_end", "m_rev_offset_start", "m_rev_mtd", "m_rev_ytd", "m_rev_running"]
        spans = buckets("day", lo, hi)
        array = ", ".join("('%s', '%s')" % s for s in spans)
        select = ", ".join("sumIf(toFloat64(amount), %s)" % re.sub(r"\bD\b", D, ROLLING[m]) for m in windows)
        wanted = self.sql_rows("SELECT b, %s FROM orders CROSS JOIN (SELECT toDate(x.1) AS b, toDate(x.2) AS e FROM (SELECT arrayJoin([%s]) AS x)) AS w GROUP BY b, e" % (select, array), windows, 1)

        rows = self.in_zone(ctx, zone, lambda: ctx.mb.mbql(cube, agg=["m:%s.%s" % (cube, m) for m in windows], by=[at + "@day"], where=[[">=", at, lo], ["<", at, next_day(hi)]])["rows"])
        self.compare("the windows of metric_types", {(r[0][:10],): dict(zip(windows, r[1:])) for r in rows}, wanted, windows)
        # A rollup of the catalog with windows in the zone.
        rollup = "metric_rollup_sales"
        rat = "d:%s.created_at" % rollup
        members = ["revenue", "units", "customers", "per_unit", "per_customer"]
        truth = ("sum(amount)", "sum(quantity)", "uniqExact(user_id)", "sum(%s) / nullIf(sum(quantity), 0)" % amt, "sum(%s) / nullIf(uniqExact(user_id), 0)" % amt)
        want = self.sql_rows("SELECT %s AS k, %s FROM orders GROUP BY k" % (D, ", ".join(truth)), members, 1)
        self.run.ensure_table(ctx, rollup)
        rows = self.in_zone(ctx, zone, lambda: ctx.mb.mbql(rollup, agg=["m:%s.%s" % (rollup, m) for m in members], by=[rat + "@day"])["rows"])
        self.compare("metric_rollup_sales", {(r[0][:10],): dict(zip(members, r[1:])) for r in rows}, want, members)
        # The change from the prior year, and a table with hand-written edges of a ratio.
        sales = "metric_sales"
        sat = "d:%s.sold_at" % sales
        Ds = "toDate(sold_at, '%s')" % zone
        self.run.ensure_table(ctx, sales)
        days = ["2025-03-08", "2025-03-09", "2025-03-10"]
        inlist = ", ".join("toDate('%s')" % d for d in days)
        cur = "sumIf(toFloat64(amount), %s = d)" % Ds
        pre = "sumIf(toFloat64(amount), %s = addYears(d, -1))" % Ds
        want = self.sql_rows("SELECT d, (%s - %s) / nullIf(%s, 0) FROM metric_sales CROSS JOIN (SELECT arrayJoin([%s]) AS d) AS b GROUP BY d" % (cur, pre, pre, inlist), ["yoy"], 1)
        rows = self.in_zone(ctx, zone, lambda: ctx.mb.mbql(sales, agg=["m:%s.yoy" % sales], by=[sat + "@day"], where=[[">=", sat, days[0]], ["<", sat, "2025-03-11"]])["rows"])
        self.compare("yoy", {(r[0][:10],): {"yoy": r[1]} for r in rows}, want, ["yoy"])
        edges = "metric_edges"
        self.run.ensure_table(ctx, edges)
        Dd = "toDate(day, '%s')" % zone
        cols = ["rows", "num", "den", "ratio", "sum_of_ratios", "avg_of_ratios"]
        truth = ("count()", "sum(num)", "sum(den)", "sum(num) / nullIf(sum(den), 0)", "sum(num / nullIf(den, 0))", "avg(num / nullIf(den, 0))")
        want = self.sql_rows("SELECT %s AS k, %s FROM metric_edges GROUP BY k" % (Dd, ", ".join(truth)), cols, 1)
        rows = self.in_zone(ctx, zone, lambda: ctx.mb.mbql(edges, agg=["m:%s.%s" % (edges, c) for c in cols], by=["d:%s.day@day" % edges])["rows"])
        self.compare("metric_edges", {(r[0][:10],): dict(zip(cols, r[1:])) for r in rows}, want, cols)
        # A balance is the total of the last day of a group, so a day comes from the zone. A customer month is a month in the zone.
        balances = "metric_balances"
        self.run.ensure_table(ctx, balances)
        Db = "toDate(ts, '%s')" % zone
        bat = "d:%s.ts" % balances
        total = "(SELECT %s AS d, sum(toFloat64(balance)) AS v FROM metric_balances GROUP BY d)" % Db
        want = self.sql_rows("SELECT d, v FROM %s WHERE d BETWEEN '2025-03-08' AND '2025-03-10'" % total, ["balance_last_day"], 1)
        rows = self.in_zone(ctx, zone, lambda: ctx.mb.mbql(balances, agg=["m:%s.balance_last_day" % balances], by=[bat + "@day"], where=[[">=", bat, "2025-03-08"], ["<", bat, "2025-03-11"]])["rows"])
        self.compare("balance by day", {(r[0][:10],): {"balance_last_day": r[1]} for r in rows}, want, ["balance_last_day"])
        want = self.sql_rows("SELECT toStartOfMonth(d) AS m, argMax(v, d) FROM %s WHERE d >= '2025-02-01' AND d < '2025-04-01' GROUP BY m" % total, ["balance_last_day"], 1)
        rows = self.in_zone(ctx, zone, lambda: ctx.mb.mbql(balances, agg=["m:%s.balance_last_day" % balances], by=[bat + "@month"], where=[[">=", bat, "2025-02-01"], ["<", bat, "2025-04-01"]])["rows"])
        self.compare("balance by month", {(r[0][:10],): {"balance_last_day": r[1]} for r in rows}, want, ["balance_last_day"])
        months = "metric_customer_months"
        self.run.ensure_table(ctx, months)
        Dm = "toStartOfMonth(created_at, '%s')" % zone
        want = self.sql_rows(
            "SELECT m, uniqExactIf(user_id, next) / uniqExact(user_id) FROM (SELECT a.user_id, a.m AS m, n.user_id != 0 AS next FROM (SELECT DISTINCT user_id, %s AS m FROM orders) AS a "
            "LEFT JOIN (SELECT DISTINCT user_id, %s AS m FROM orders) AS n ON a.user_id = n.user_id AND addMonths(a.m, 1) = n.m) GROUP BY m" % (Dm, Dm), ["retention"], 1)
        rows = self.in_zone(ctx, zone, lambda: ctx.mb.mbql(months, agg=["m:%s.retention" % months], by=["d:%s.month_at@month" % months])["rows"])
        self.compare("retention by month", {(r[0][:10],): {"retention": r[1]} for r in rows}, want, ["retention"])


def next_day(day):
    return (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()


ROLLUPS = {"tables": "rollup_tables", "job": "rollup_job", "metabase": "rollup_metabase", "rolling": "rollup_rolling", "stored_day": "rollup_stored", "text_day": "rollup_text"}
OTHER = {"stored_day_other_zone": "stored_day_other_zone", "time_measure": "time_measure", "native": "native", "mcp": "mcp", "catalog": "catalog"}


# What stock Cube cannot do. A driver refusal is a clear stop, and the REST API of stock Cube has no driver in front of it, so it gives the wrong number. A check fails if a limit goes away.
REASON_DATE = "Stock Cube reads a Date as a time at midnight UTC under a time zone, so a day moves. The driver stops this in Metabase for a dimension with meta date_only. A Cube request gets the moved day. The dialect fixes it."
REASON_ROLLING = "Stock Cube reads the bounds of a rolling window in UTC under a time zone and adds the wrong months. The driver stops this in Metabase. A Cube request gets the wrong number. The dialect fixes it."
REASON_SHIFT = "Stock Cube adds the interval of a time shift in UTC, so next to a clock change a row moves to another day. The driver stops this in Metabase. A Cube request gets the wrong number. The dialect fixes it."
REASON_SECONDS = "Stock Cube compares a DateTime64 in whole seconds, so a row in the last second of a day or a range leaves the result. /v1/meta does not show the column type. The dialect fixes it."
REASON_ROLLUP = "Stock Cube reads a Date as a time at midnight UTC, and it filters the rollup of a window with the offset of today. The driver stops these in Metabase. A Cube request gets the wrong number. The dialect fixes them."


def stock_limit(fid):
    """The reason of a limit on stock Cube for this check, or None."""
    parts = fid.split(".")
    if len(parts) == 5:
        _, p, form, path, part = parts
        date_form = form in DATE_FORMS
        if path == "plain":
            return None
        if part == "rolling":
            return REASON_ROLLING
        if path == "report":
            if date_form:
                return REASON_DATE
            return REASON_SHIFT if part == "stages" else None
        if date_form:
            return REASON_DATE if p == "ny" else None
        if form == "a64" and part in ("day", "filter"):
            return REASON_SECONDS
        return REASON_SHIFT if part == "stages" and form in ("a", "a64") and (p == "ny" or form == "a64") else None
    return STOCK_OTHER.get(".".join(parts[2:]))


STOCK_OTHER = {"rollup.rolling": REASON_ROLLUP, "rollup.stored_day": REASON_DATE, "stored_day_other_zone": REASON_DATE, "native": REASON_ROLLING, "mcp": REASON_ROLLING, "catalog": REASON_ROLLING, "time_measure": REASON_DATE}


def feature_ids():
    ids = ["zone.%s.%s.%s.%s" % (p, form, path, part) for p in ZONES for form in FORMS for path in PATHS for part in PARTS]
    ids += ["zone.%s.rollup.%s" % (p, name) for p in ZONES for name in ROLLUPS]
    ids += ["zone.%s.%s" % (p, name) for p in ZONES for name in OTHER]
    ids.append("zone.rolling.sunday_week_with_no_granularity_refused")
    return ids


def register(run):
    grid = Grid(run)

    def with_limit(fid, fn):
        """On stock Cube a check with a limit passes only if the limit still shows. The entry has cap_stock: partial."""
        def body(ctx):
            reason = None if run.DIALECT else stock_limit(fid)
            if not reason:
                fn(ctx)
                return
            try:
                fn(ctx)
            except run.Fail:
                return
            raise run.Fail("this limit of stock Cube went away, so update the entry: %s" % reason)
        run.check(fid)(body)
    for p in ZONES:
        for name, method in ROLLUPS.items():
            with_limit("zone.%s.rollup.%s" % (p, name), lambda ctx, p=p, method=method: (run.ensure_table(ctx, p + "_roll"), getattr(grid, method)(ctx, p)) and None)
        for name, method in OTHER.items():
            with_limit("zone.%s.%s" % (p, name), lambda ctx, p=p, method=method: getattr(grid, method)(ctx, p) and None)
    run.check("zone.rolling.sunday_week_with_no_granularity_refused")(lambda ctx: grid.sunday_refused(ctx))
    for fid in feature_ids():
        parts = fid.split(".")
        if len(parts) != 5:
            continue
        _, p, form, path, part = parts

        def body(ctx, p=p, form=form, path=path, part=part):
            run.ensure_table(ctx, grid.cube_of(p, form))
            grid.run_part(ctx, part, path, p, form)
        with_limit(fid, body)
