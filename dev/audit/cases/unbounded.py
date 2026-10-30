"""unbounded: a trailing unbounded window on ClickHouse."""
MODEL = """
cubes:
  - name: u
    sql_table: audit.ut
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: ts, sql: ts, type: time }
    measures:
      - { name: rev, sql: amt, type: sum }
      - { name: cum, sql: amt, type: sum, rolling_window: { trailing: unbounded } }
      - { name: cum_100y, sql: amt, type: sum, rolling_window: { trailing: 100 year } }
  - name: ur
    sql_table: audit.ut
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: ts, sql: ts, type: time }
    measures:
      - { name: cum, sql: amt, type: sum, rolling_window: { trailing: unbounded } }
    pre_aggregations:
      - name: by_day
        dimensions: [id]
        measures: [cum]
        time_dimension: ts
        granularity: day
        external: true
        indexes:
          - { name: by_id, columns: [id] }
"""
ROWS = "(1,1,'2025-03-15 00:00:00'),(2,10,'2025-03-31 23:59:59'),(3,100,'2025-04-01 00:00:00'),(4,1000,'2025-05-20 00:00:00'),(5,10000,'2025-07-01 00:00:00')"
SEED_CH = f"CREATE TABLE audit.ut (id UInt32, amt Int32, ts DateTime('UTC')) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.ut VALUES {ROWS};\n"
SEED_PG = f"CREATE TABLE audit.ut (id int, amt int, ts timestamp);\nINSERT INTO audit.ut VALUES {ROWS};\n"
def s(measure, a, b, gran="month", tbl="u"):
    return f"SELECT DATE_TRUNC('{gran}', ts) m, MEASURE({measure}) c FROM {tbl} WHERE ts >= '{a}' AND ts < '{b}' GROUP BY 1 ORDER BY 1"
TWO = [["2025-04-01 00:00:00",111],["2025-05-01 00:00:00",1111]]
TWHY = "Everything up to 04-30: rows 1, 2, 3. Up to 05-31: also row 4."
INEQ = "SELECT d, count() FROM (SELECT toDateTime('2025-04-30 23:59:59','UTC') d UNION ALL SELECT toDateTime('2025-05-31 23:59:59','UTC')) t LEFT JOIN audit.ut o ON o.ts <= t.d GROUP BY 1 ORDER BY 1"
ZR = [["2025-04-30 23:59:59",3],["2025-05-31 23:59:59",4]]
CASES = [
 dict(id="two_months_sql", sql=s("cum","2025-04-01","2025-06-01"), expected=TWO, why=TWHY, truth=dict(pg="SELECT sum(amt) FROM audit.ut WHERE ts <= '2025-05-31 23:59:59'", ch="SELECT sum(amt) FROM audit.ut WHERE ts <= '2025-05-31 23:59:59'")),
 dict(id="two_months_rest", rest=dict(measures=["u.cum"], timeDimensions=[dict(dimension="u.ts", granularity="month", dateRange=["2025-04-01","2025-05-31"])]), expected=TWO, why=TWHY),
 dict(id="one_month_sql", sql=s("cum","2025-04-01","2025-05-01"), expected=[TWO[0]], why="One bucket: rows 1, 2, 3."),
 dict(id="two_days", sql=s("cum","2025-04-01","2025-04-03","day"), expected=[["2025-04-01 00:00:00",111],["2025-04-02 00:00:00",111]], why="Rows 1, 2, 3 on both days."),
 dict(id="one_day_on_row", sql=s("cum","2025-03-31","2025-04-01","day"), expected=[["2025-03-31 00:00:00",11]], why="A bucket that ends after row 2, at 23:59:59."),
 dict(id="before_all_rows", sql=s("cum","2025-01-01","2025-03-01"), expected=[["2025-01-01 00:00:00",None],["2025-02-01 00:00:00",None]], why="No rows are before these buckets. A SUM of no rows is NULL."),
 dict(id="after_all_rows", sql=s("cum","2025-08-01","2025-10-01"), expected=[["2025-08-01 00:00:00",11111],["2025-09-01 00:00:00",11111]], why="All rows, with no new row. The buckets stay."),
 dict(id="no_bucket", sql="SELECT MEASURE(cum) c FROM u WHERE ts >= '2025-04-01' AND ts < '2025-06-01'", expected=[[1111]], why="With a range and no bucket: the window ends at the end of the range. Rows 1 to 4."),
 dict(id="far_bound_workaround", sql=s("cum_100y","2025-04-01","2025-06-01"), expected=TWO, why="A far bound in place of unbounded. " + TWHY),
 dict(id="rollup_hit", rest=dict(measures=["ur.cum"], timeDimensions=[dict(dimension="ur.ts", granularity="month", dateRange=["2025-04-01","2025-05-31"])]), expected=TWO, why="The query runs in Cube Store if the rollup hits. " + TWHY),
 dict(id="db_only_inequality_join", direct=dict(ch=INEQ, pg="SELECT t.d, count(o.id) FROM (VALUES (timestamp '2025-04-30 23:59:59'), (timestamp '2025-05-31 23:59:59')) t(d) LEFT JOIN audit.ut o ON o.ts <= t.d GROUP BY 1 ORDER BY 1"), expected=ZR, why="The same join with no Cube: 3 rows up to 04-30, 4 up to 05-31."),
 dict(id="db_only_inequality_old_analyzer", direct=dict(ch=INEQ + " SETTINGS enable_analyzer = 0", pg="SELECT t.d, count(o.id) FROM (VALUES (timestamp '2025-04-30 23:59:59'), (timestamp '2025-05-31 23:59:59')) t(d) LEFT JOIN audit.ut o ON o.ts <= t.d GROUP BY 1 ORDER BY 1"), expected=ZR, why="The same join with the old ClickHouse analyzer."),
 dict(id="db_only_inequality_join_condition_setting", direct=dict(ch=INEQ + " SETTINGS allow_experimental_join_condition = 1", pg="SELECT t.d, count(o.id) FROM (VALUES (timestamp '2025-04-30 23:59:59'), (timestamp '2025-05-31 23:59:59')) t(d) LEFT JOIN audit.ut o ON o.ts <= t.d GROUP BY 1 ORDER BY 1"), expected=ZR, why="The same join with the setting for join conditions."),
 dict(id="db_only_cross_join", direct=dict(ch="SELECT d, countIf(o.ts <= t.d) FROM (SELECT toDateTime('2025-04-30 23:59:59','UTC') d UNION ALL SELECT toDateTime('2025-05-31 23:59:59','UTC')) t CROSS JOIN audit.ut o GROUP BY 1 ORDER BY 1", pg="SELECT t.d, count(o.id) FROM (VALUES (timestamp '2025-04-30 23:59:59'), (timestamp '2025-05-31 23:59:59')) t(d) CROSS JOIN audit.ut o WHERE o.ts <= t.d GROUP BY 1 ORDER BY 1"), expected=ZR, why="A cross join with the condition after it, the form a fix can write."),
]
EXTRA_CONFIGS = ["ch-on+ch=25.3-alpine", "ch-off+ch=25.3-alpine"]
TAGS = ["v1.6.76", "v1.0.0"]
TAG_CASES = ["two_months_sql", "one_month_sql"]
TAG_CONFIGS = ["ch-off"]
