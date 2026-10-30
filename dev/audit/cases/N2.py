"""N2: a rolling measure on ClickHouse with the legacy planner."""
MODEL = """
cubes:
  - name: lt
    sql_table: audit.lt
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: ts, sql: ts, type: time }
    measures:
      - { name: rev, sql: amt, type: sum }
      - { name: r30, sql: amt, type: sum, rolling_window: { trailing: 30 day } }
      - { name: c30, type: count, rolling_window: { trailing: 30 day } }
      - { name: r100y, sql: amt, type: sum, rolling_window: { trailing: 100 year } }
  - name: lr
    sql_table: audit.lt
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: ts, sql: ts, type: time }
    measures:
      - { name: r30, sql: amt, type: sum, rolling_window: { trailing: 30 day } }
    pre_aggregations:
      - name: by_day
        dimensions: [id]
        measures: [r30]
        time_dimension: ts
        granularity: day
        external: true
        indexes:
          - { name: by_id, columns: [id] }
"""
ROWS = "(1,1,'2025-04-05 00:00:00'),(2,10,'2025-04-25 00:00:00'),(3,100,'2025-05-10 00:00:00'),(4,1000,'2025-05-28 00:00:00'),(5,10000,'2025-06-20 00:00:00')"
SEED_CH = f"CREATE TABLE audit.lt (id UInt32, amt Int64, ts DateTime('UTC')) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.lt VALUES {ROWS};\n"
SEED_PG = f"CREATE TABLE audit.lt (id int, amt bigint, ts timestamp);\nINSERT INTO audit.lt VALUES {ROWS};\n"
A,M,J = ["2025-04-01 00:00:00",11],["2025-05-01 00:00:00",1100],["2025-06-01 00:00:00",10000]
def rq(m, gran, a, b, c="lt"):
    return dict(measures=[f"{c}.{m}"], timeDimensions=[dict(dimension=f"{c}.ts", granularity=gran, dateRange=[a,b])], order={f"{c}.ts":"asc"})
TA = "30 days to 04-30: rows 1, 2. 30 days to 05-31: rows 3, 4."
CASES = [
 dict(id="two_months_rest", rest=rq("r30","month","2025-04-01","2025-05-31"), expected=[A,M], why=TA, truth="SELECT sum(amt) FROM audit.lt WHERE ts > '2025-05-01 23:59:59' AND ts <= '2025-05-31 23:59:59'"),
 dict(id="two_months_sql", sql="SELECT DATE_TRUNC('month', ts) m, MEASURE(r30) r FROM lt WHERE ts >= '2025-04-01' AND ts < '2025-06-01' GROUP BY 1 ORDER BY 1", expected=[A,M], why=TA),
 dict(id="three_months", rest=rq("r30","month","2025-04-01","2025-06-30"), expected=[A,M,J], why=TA + " 30 days to 06-30: row 5."),
 dict(id="one_month_control", rest=rq("r30","month","2025-04-01","2025-04-30"), expected=[A], why="One bucket: rows 1, 2."),
 dict(id="two_days", rest=rq("r30","day","2025-04-24","2025-04-25"), expected=[["2025-04-24 00:00:00",1],["2025-04-25 00:00:00",11]], why="To the end of 04-24: row 1. To the end of 04-25: rows 1, 2."),
 dict(id="two_weeks", rest=rq("r30","week","2025-05-05","2025-05-18"), expected=[["2025-05-05 00:00:00",110],["2025-05-12 00:00:00",110]], why="30 days to 05-11 starts after 04-11: rows 2, 3. 30 days to 05-18 starts after 04-18: rows 2, 3."),
 dict(id="count_two_months", rest=rq("c30","month","2025-04-01","2025-05-31"), expected=[["2025-04-01 00:00:00",2],["2025-05-01 00:00:00",2]], why="Two rows in each window."),
 dict(id="far_bound_two_months", rest=rq("r100y","month","2025-04-01","2025-05-31"), expected=[["2025-04-01 00:00:00",11],["2025-05-01 00:00:00",1111]], why="A far bound: all rows up to the end of each bucket."),
 dict(id="plain_sum_control", rest=rq("rev","month","2025-04-01","2025-05-31"), expected=[["2025-04-01 00:00:00",11],["2025-05-01 00:00:00",1100]], why="No window, so no series."),
 dict(id="rollup_two_months", rest=rq("r30","month","2025-04-01","2025-05-31","lr"), expected=[A,M], why=TA + " A rollup by day, so Cube Store runs the window."),
]
TAGS = ["v1.0.0", "v1.6.76", "v1.7.0"]
TAG_CASES = ["two_months_rest"]
TAG_CONFIGS = ["ch-def"]
