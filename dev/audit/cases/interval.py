"""interval: x + INTERVAL on ClickHouse."""
MODEL = """
cubes:
  - name: it
    sql_table: audit.it
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: ts, sql: ts, type: time }
      - { name: d, sql: d, type: time }
    measures:
      - { name: n, type: count }
"""
ROWS = "(1,'2024-01-31 10:00:00','2024-01-31'),(2,'2024-02-29 00:00:00','2024-02-29'),(3,'2025-03-09 01:30:00','2025-03-09'),(4,'2025-12-31 23:59:59','2025-12-31')"
SEED_CH = f"CREATE TABLE audit.it (id UInt32, ts DateTime('UTC'), d Date) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.it VALUES {ROWS};\n"
SEED_PG = f"CREATE TABLE audit.it (id int, ts timestamp, d date);\nINSERT INTO audit.it VALUES {ROWS};\n"
P1 = [[1,"2024-02-01 10:00:00"],[2,"2024-03-01 00:00:00"],[3,"2025-03-10 01:30:00"],[4,"2026-01-01 23:59:59"]]
CASES = [
 dict(id="raw_plus_day", sql="SELECT id, ts + INTERVAL '1 day' x FROM it ORDER BY 1", expected=P1, why="Each time plus one day. 2024 is a leap year, so 02-29 goes to 03-01.", truth=dict(pg="SELECT id, ts + interval '1 day' FROM audit.it ORDER BY 1", ch="SELECT id, addDays(ts, 1) FROM audit.it ORDER BY 1")),
 dict(id="raw_plus_day_no_order", sql="SELECT id, ts + INTERVAL '1 day' x FROM it", expected=P1, why="The same with no ORDER BY.", sorted_rows=True),
 dict(id="raw_plus_day_where_order", sql="SELECT id, ts + INTERVAL '1 day' x FROM it WHERE id = 2 ORDER BY 1", expected=[P1[1]], why="One row, with a filter and an ORDER BY on id."),
 dict(id="raw_plus_day_where", sql="SELECT id, ts + INTERVAL '1 day' x FROM it WHERE id = 2", expected=[P1[1]], why="One row, with a filter and no ORDER BY."),
 dict(id="raw_plus_day_order_time", sql="SELECT id, ts + INTERVAL '1 day' x FROM it ORDER BY 2", expected=P1, why="ORDER BY the expression."),
 dict(id="aggregated_no_order", sql="SELECT ts + INTERVAL '1 day' x, MEASURE(n) c FROM it GROUP BY 1", expected=[[b,1] for a,b in P1], why="The same with a measure and no ORDER BY.", sorted_rows=True),
 dict(id="raw_plus_day_order_limit", sql="SELECT id, ts + INTERVAL '1 day' x FROM it ORDER BY 1 LIMIT 10", expected=P1, why="ORDER BY and LIMIT. PR #11559 prefers pushdown only over post-processing with no limit."),
 dict(id="aggregated_order_limit", sql="SELECT ts + INTERVAL '1 day' x, MEASURE(n) c FROM it GROUP BY 1 ORDER BY 1 LIMIT 10", expected=[[b,1] for a,b in P1], why="The same with a measure."),
 dict(id="raw_minus_day", sql="SELECT id, ts - INTERVAL '1 day' x FROM it WHERE id = 2", expected=[[2,"2024-02-28 00:00:00"]], why="The leap day minus one day."),
 dict(id="raw_plus_month", sql="SELECT id, ts + INTERVAL '1 month' x FROM it ORDER BY 1", expected=[[1,"2024-02-29 10:00:00"],[2,"2024-03-29 00:00:00"],[3,"2025-04-09 01:30:00"],[4,"2026-01-31 23:59:59"]], why="Month end clamps: 01-31 goes to 02-29, as in the SQL standard and Postgres."),
 dict(id="raw_two_units", sql="SELECT id, ts + INTERVAL '1 day 2 hours' x FROM it WHERE id = 3", expected=[[3,"2025-03-10 03:30:00"]], why="Two units in one interval."),
 dict(id="date_column", sql="SELECT id, d + INTERVAL '1 day' x FROM it WHERE id = 1", expected=[[1,"2024-02-01 00:00:00"]], why="A Date column."),
 dict(id="aggregated", sql="SELECT ts + INTERVAL '1 day' x, MEASURE(n) c FROM it GROUP BY 1 ORDER BY 1", expected=[[b,1] for a,b in P1], why="The same with a measure."),
 dict(id="bucket_plus", sql="SELECT DATE_TRUNC('month', ts) + INTERVAL '1 month' x, MEASURE(n) c FROM it GROUP BY 1 ORDER BY 1", expected=[["2024-02-01 00:00:00",1],["2024-03-01 00:00:00",1],["2025-04-01 00:00:00",1],["2026-01-01 00:00:00",1]], why="The next month of each bucket."),
 dict(id="filter_column_plus", sql="SELECT MEASURE(n) c FROM it WHERE ts + INTERVAL '1 day' >= TIMESTAMP '2025-12-31 00:00:00'", expected=[[1]], why="Only row 4."),
 dict(id="date_add_function", sql="SELECT id, DATE_ADD(ts, INTERVAL '1 day') x FROM it ORDER BY 1", expected=P1, why="The function form that the docs list."),
 dict(id="filter_constant_control", sql="SELECT MEASURE(n) c FROM it WHERE ts >= TIMESTAMP '2025-01-02 00:00:00' - INTERVAL '1 day'", expected=[[2]], why="An interval on a constant: rows 3, 4. A relative date filter has this form."),
 dict(id="outer_over_scan", sql="SELECT m + INTERVAL '1 day' x, c FROM (SELECT DATE_TRUNC('year', ts) m, MEASURE(n) c FROM it GROUP BY 1) q ORDER BY 1", expected=[["2024-01-02 00:00:00",2],["2025-01-02 00:00:00",2]], why="The interval on the outer query of a grouped subquery."),
 dict(id="time_zone_dst", setup=["SET TIME ZONE 'America/New_York'"], sql="SELECT id, ts + INTERVAL '1 day' x FROM it WHERE id = 3", expected=[[3,"2025-03-09 20:30:00"]], why="Row 3 at 01:30 UTC on 03-09 is 20:30 on 03-08 in New York, before the clock change at 02:00 local on 03-09. Plus one local day is 20:30 on 03-09."),
 dict(id="no_interval_control", sql="SELECT id, ts x FROM it WHERE id = 1", expected=[[1,"2024-01-31 10:00:00"]], why="The same query with no interval."),
]
EXTRA_CONFIGS = ["ch-on+nopush", "pg-on+nopush"]
TAGS = ["v1.6.76", "v1.0.0"]
TAG_CASES = ["raw_plus_day", "date_add_function"]
TAG_CONFIGS = ["ch-off"]
