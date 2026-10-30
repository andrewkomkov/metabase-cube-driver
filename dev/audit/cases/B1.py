"""B1: a strict lower time bound on ClickHouse. The amounts are powers of 10, so a sum names its rows."""
MODEL = """
cubes:
  - name: visits
    sql_table: audit.visits
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: amt_dim, sql: amt, type: number }
      - { name: ts, sql: ts, type: time }
      - { name: ts64, sql: ts64, type: time }
      - { name: d, sql: d, type: time }
      - { name: ts_null, sql: ts_null, type: time }
    measures:
      - { name: n, type: count }
      - { name: total, sql: amt, type: sum }
  - name: visits_r
    sql_table: audit.visits
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: ts, sql: ts, type: time }
    measures:
      - { name: n, type: count }
      - { name: total, sql: amt, type: sum }
    pre_aggregations:
      - name: by_second
        dimensions: [id]
        measures: [n, total]
        time_dimension: ts
        granularity: second
        allow_non_strict_date_range_match: true
        indexes:
          - { name: by_id, columns: [id] }
"""
SEED_CH = """
CREATE TABLE audit.visits (id UInt32, amt Int32, ts DateTime('UTC'), ts64 DateTime64(3,'UTC'), d Date, ts_null Nullable(DateTime('UTC'))) ENGINE = MergeTree ORDER BY id;
INSERT INTO audit.visits VALUES
(1,     1, '2024-02-28 23:59:59', '2024-02-28 23:59:59.999', '2024-02-28', '2024-02-28 23:59:59'),
(2,    10, '2024-02-29 00:00:00', '2024-02-29 00:00:00.000', '2024-02-29', '2024-02-29 00:00:00'),
(3,   100, '2024-02-29 00:00:01', '2024-02-29 00:00:00.001', '2024-02-29', NULL),
(4,  1000, '2024-02-29 12:00:00', '2024-02-29 00:00:00.002', '2024-02-29', '2024-02-29 12:00:00'),
(5, 10000, '2024-03-01 00:00:00', '2024-03-01 00:00:00.000', '2024-03-01', '2024-03-01 00:00:00'),
(6,100000, '2024-03-01 00:00:01', '2024-03-01 00:00:00.001', '2024-03-01', '2024-03-01 00:00:01');
"""
SEED_PG = """
CREATE TABLE audit.visits (id int, amt int, ts timestamp, ts64 timestamp(3), d date, ts_null timestamp);
INSERT INTO audit.visits VALUES
(1,     1, '2024-02-28 23:59:59', '2024-02-28 23:59:59.999', '2024-02-28', '2024-02-28 23:59:59'),
(2,    10, '2024-02-29 00:00:00', '2024-02-29 00:00:00.000', '2024-02-29', '2024-02-29 00:00:00'),
(3,   100, '2024-02-29 00:00:01', '2024-02-29 00:00:00.001', '2024-02-29', NULL),
(4,  1000, '2024-02-29 12:00:00', '2024-02-29 00:00:00.002', '2024-02-29', '2024-02-29 12:00:00'),
(5, 10000, '2024-03-01 00:00:00', '2024-03-01 00:00:00.000', '2024-03-01', '2024-03-01 00:00:00'),
(6,100000, '2024-03-01 00:00:01', '2024-03-01 00:00:00.001', '2024-03-01', '2024-03-01 00:00:01');
"""
X = "2024-02-29 00:00:00"
Y = "2024-03-01 00:00:00"
CASES = [
 dict(id="plain_gt_lte", sql=f"SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE ts > '{X}' AND ts <= '{Y}'",
      expected=[[3, 11100]], why="Rows 3, 4, 5 are after X and not after Y: 100+1000+10000.",
      truth=f"SELECT count(*), sum(amt) FROM audit.visits WHERE ts > '{X}' AND ts <= '{Y}'"),
 dict(id="gt_only", sql=f"SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE ts > '{X}'",
      expected=[[4, 111100]], why="Rows 3 to 6.", truth=f"SELECT count(*), sum(amt) FROM audit.visits WHERE ts > '{X}'"),
 dict(id="gt_and_lt", sql=f"SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE ts > '{X}' AND ts < '{Y}'",
      expected=[[2, 1100]], why="Rows 3, 4. Row 2 is on the lower bound, row 5 on the upper.",
      truth=f"SELECT count(*), sum(amt) FROM audit.visits WHERE ts > '{X}' AND ts < '{Y}'"),
 dict(id="one_second_below", sql=f"SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE ts > '2024-02-28 23:59:59' AND ts <= '{X}'",
      expected=[[1, 10]], why="Row 2 only. Row 1 is on the strict bound.",
      truth=f"SELECT count(*), sum(amt) FROM audit.visits WHERE ts > '2024-02-28 23:59:59' AND ts <= '{X}'"),
 dict(id="equal_bounds_empty", sql="SELECT MEASURE(n) n FROM visits WHERE ts > '2024-02-29 12:00:00' AND ts <= '2024-02-29 12:00:00'",
      expected=[[0]], why="No time is both after and not after one value."),
 dict(id="fraction_in_bound", sql=f"SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE ts > '2024-02-29 00:00:00.500' AND ts <= '{Y}'",
      expected=[[3, 11100]], why="Row 2 at 00:00:00 is before .500. Rows 3, 4, 5 qualify."),
 dict(id="date_col_daily", sql="SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE d > '2024-02-28' AND d <= '2024-02-29'",
      expected=[[3, 1110]], why="Date column. Only the leap day is after 02-28 and not after 02-29: rows 2, 3, 4.",
      truth="SELECT count(*), sum(amt) FROM audit.visits WHERE d > '2024-02-28' AND d <= '2024-02-29'"),
 dict(id="date_col_month_end", sql="SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE d > '2024-02-29' AND d <= '2024-03-01'",
      expected=[[2, 110000]], why="March 1 only: rows 5, 6.",
      truth="SELECT count(*), sum(amt) FROM audit.visits WHERE d > '2024-02-29' AND d <= '2024-03-01'"),
 dict(id="datetime64_col", sql="SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE ts64 > '2024-02-29 00:00:00.000' AND ts64 <= '2024-03-01 00:00:00.000'",
      expected=[[3, 11100]], why="DateTime64(3): rows 3, 4, 5. Row 2 is on the bound, row 6 is 1 ms after the upper bound.",
      truth="SELECT count(*), sum(amt) FROM audit.visits WHERE ts64 > '2024-02-29 00:00:00.000' AND ts64 <= '2024-03-01 00:00:00.000'"),
 dict(id="nullable_col", sql=f"SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE ts_null > '{X}' AND ts_null <= '{Y}'",
      expected=[[2, 11000]], why="Row 3 is NULL, so only rows 4, 5.",
      truth=f"SELECT count(*), sum(amt) FROM audit.visits WHERE ts_null > '{X}' AND ts_null <= '{Y}'"),
 dict(id="not_lte_form", sql=f"SELECT MEASURE(n) n FROM visits WHERE NOT (ts <= '{X}') AND ts <= '{Y}'",
      expected=[[3]], why="NOT (ts <= X) is ts > X."),
 dict(id="between_control", sql=f"SELECT MEASURE(n) n FROM visits WHERE ts BETWEEN '{X}' AND '{Y}'",
      expected=[[4]], why="BETWEEN is inclusive on both sides: rows 2 to 5."),
 dict(id="gte_control", sql=f"SELECT MEASURE(n) n FROM visits WHERE ts >= '2024-02-29 00:00:01' AND ts <= '{Y}'",
      expected=[[3]], why="The next second, with >=. The same rows as plain_gt_lte."),
 dict(id="typed_literal", sql=f"SELECT MEASURE(n) n FROM visits WHERE ts > TIMESTAMP '{X}' AND ts <= TIMESTAMP '{Y}'",
      expected=[[3]], why="The same as plain_gt_lte, with typed literals."),
 dict(id="by_day_bucket", sql=f"SELECT DATE_TRUNC('day', ts) b, MEASURE(total) t FROM visits WHERE ts > '{X}' AND ts <= '{Y}' GROUP BY 1 ORDER BY 1",
      expected=[["2024-02-29 00:00:00", 1100], ["2024-03-01 00:00:00", 10000]], why="Rows 3, 4 fall on the leap day and row 5 on March 1."),
 dict(id="raw_rows", sql=f"SELECT id FROM visits WHERE ts > '{X}' AND ts <= '{Y}' ORDER BY 1",
      expected=[[3], [4], [5]], why="Ungrouped rows."),
 dict(id="outer_filter_postproc", sql=f"SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM visits) q WHERE ts > '{X}' AND ts <= '{Y}'",
      expected=[[11100]], why="The same rows, with an aggregate over a subquery."),
 dict(id="bind_cast", wire=dict(sql="SELECT MEASURE(n) n FROM visits WHERE ts > $1::timestamp AND ts <= $2::timestamp", params=[X, Y]),
      expected=[[3]], why="The same as plain_gt_lte, with parameters."),
 dict(id="sql_time_zone", setup=["SET TIME ZONE 'America/New_York'"], sql="SELECT MEASURE(n) n FROM visits WHERE ts > '2024-02-28 19:00:00' AND ts <= '2024-02-29 19:00:00'",
      expected=[[3]], why="New York is UTC-5 in February, so the bounds are X and Y."),
 dict(id="rest_after_before", rest=dict(measures=["visits.n", "visits.total"], filters=[dict(member="visits.ts", operator="afterDate", values=[X]), dict(member="visits.ts", operator="beforeOrOnDate", values=[Y])]),
      expected=[[3, 11100]], why="The same condition as plain_gt_lte, in REST. The docs define afterDate with >."),
 dict(id="rest_after_only", rest=dict(measures=["visits.n"], filters=[dict(member="visits.ts", operator="afterDate", values=[X])]),
      expected=[[4]], why="Rows 3 to 6."),
 dict(id="rest_date_range", rest=dict(measures=["visits.n"], timeDimensions=[dict(dimension="visits.ts", dateRange=[X, Y])]),
      expected=[[4]], why="A date range is inclusive: rows 2 to 5."),
 dict(id="rest_tz", rest=dict(measures=["visits.n"], timezone="America/New_York", filters=[dict(member="visits.ts", operator="afterDate", values=["2024-02-28T19:00:00"]), dict(member="visits.ts", operator="beforeOrOnDate", values=["2024-02-29T19:00:00"])]),
      expected=[[3]], why="New York is UTC-5 in February, so the bounds are X and Y."),
 dict(id="rollup_rest", rest=dict(measures=["visits_r.n"], timeDimensions=[dict(dimension="visits_r.ts", dateRange=["2024-02-29T00:00:00.001", "2024-03-01T00:00:00.000"])]),
      expected=[[3]], why="A rollup with one row per second, a range from X plus 1 ms: rows 3, 4, 5."),
 dict(id="rollup_sql", sql=f"SELECT MEASURE(n) n FROM visits_r WHERE ts > '{X}' AND ts <= '{Y}'",
      expected=[[3]], why="The same range through the SQL API, on the cube with a rollup."),
]
TAGS = ["v1.6.76", "v1.0.0"]
TAG_CASES = ["plain_gt_lte", "date_col_daily", "rest_after_before"]
TAG_CONFIGS = ["ch-off"]
