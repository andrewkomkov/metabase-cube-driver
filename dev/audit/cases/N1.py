"""N1: a rolling measure with a date range that starts inside a bucket. 30 days trail the end of a bucket: end > row >= end - 30 days."""
MODEL = """
cubes:
  - name: nt
    sql_table: audit.nt
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: ts, sql: ts, type: time }
    measures:
      - { name: rev, sql: amt, type: sum }
      - { name: r30, sql: amt, type: sum, rolling_window: { trailing: 30 day } }
      - { name: cum, sql: amt, type: sum, rolling_window: { trailing: unbounded } }
  - name: nr
    sql_table: audit.nt
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
ROWS = "(1,1,'2025-03-20 00:00:00'),(2,10,'2025-04-10 00:00:00'),(3,100,'2025-04-20 00:00:00'),(4,1000,'2025-05-05 00:00:00'),(5,10000,'2025-05-25 00:00:00'),(6,100000,'2025-06-10 00:00:00')"
SEED_CH = f"CREATE TABLE audit.nt (id UInt32, amt Int64, ts DateTime('UTC')) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.nt VALUES {ROWS};\n"
SEED_PG = f"CREATE TABLE audit.nt (id int, amt bigint, ts timestamp);\nINSERT INTO audit.nt VALUES {ROWS};\n"
A = ["2025-04-01 00:00:00",110]
M = ["2025-05-01 00:00:00",11000]
AW = "A month bucket starts on the first, as DATE_TRUNC and the non-rolling control do. 30 days up to 04-30: rows 2, 3. 30 days up to 05-31: rows 4, 5."
def rq(m, gran, a, b, c="nt"):
    return dict(measures=[f"{c}.{m}"], timeDimensions=[dict(dimension=f"{c}.ts", granularity=gran, dateRange=[a,b])], order={f"{c}.ts":"asc"})
CASES = [
 dict(id="month_mid_start_rest", rest=rq("r30","month","2025-04-15","2025-05-31"), expected=[A,M], why=AW),
 dict(id="month_first_start_control", rest=rq("r30","month","2025-04-01","2025-05-31"), expected=[A,M], why=AW + " The range starts on the first."),
 dict(id="month_second_day", rest=rq("r30","month","2025-04-02","2025-05-31"), expected=[A,M], why=AW + " One day after the first."),
 dict(id="month_mid_start_sql", sql="SELECT DATE_TRUNC('month', ts) m, MEASURE(r30) r FROM nt WHERE ts >= '2025-04-15' AND ts < '2025-06-01' GROUP BY 1 ORDER BY 1", expected=[A,M], why=AW),
 dict(id="month_end_start", rest=rq("r30","month","2025-01-31","2025-03-31"), expected=[["2025-01-01 00:00:00",None],["2025-02-01 00:00:00",None],["2025-03-01 00:00:00",1]], why="Starts on a month end, which a step of a month can move. 3 buckets. Only row 1 is in 30 days to 03-31."),
 dict(id="leap_day_start", rest=rq("r30","month","2024-02-29","2024-03-31"), expected=[["2024-02-01 00:00:00",None],["2024-03-01 00:00:00",None]], why="Starts on a leap day. 2 buckets, no rows."),
 dict(id="week_mid_start", rest=rq("r30","week","2025-05-07","2025-05-20"), expected=[["2025-05-05 00:00:00",1100],["2025-05-12 00:00:00",1100],["2025-05-19 00:00:00",11000]], why="Weeks start on Monday. To 05-11: rows 3, 4. To 05-18: rows 3, 4. To 05-25: rows 4, 5."),
 dict(id="year_mid_start", rest=rq("r30","year","2025-03-15","2025-06-30"), expected=[["2025-01-01 00:00:00",None]], why="The year bucket ends on 12-31, with no row in the 30 days before."),
 dict(id="day_control", rest=rq("r30","day","2025-05-05","2025-05-06"), expected=[["2025-05-05 00:00:00",1110],["2025-05-06 00:00:00",1110]], why="A day has no inside start. 30 days to the end of 05-05 starts after 04-05: rows 2, 3, 4."),
 dict(id="unbounded_mid_start", rest=rq("cum","month","2025-04-15","2025-05-31"), expected=[["2025-04-01 00:00:00",111],["2025-05-01 00:00:00",11111]], why="The same bucket rule with an unbounded window."),
 dict(id="non_rolling_control", rest=rq("rev","month","2025-04-15","2025-05-31"), expected=[["2025-04-01 00:00:00",100],["2025-05-01 00:00:00",11000]], why="A plain sum with the same range: the label stays on the first, and the rows are filtered."),
 dict(id="rollup_mid_start", rest=rq("r30","month","2025-04-15","2025-05-31","nr"), expected=[A,M], why=AW + " A rollup by day."),
 dict(id="time_zone_mid_start", rest=dict(measures=["nt.r30"], timezone="America/New_York", timeDimensions=[dict(dimension="nt.ts", granularity="month", dateRange=["2025-04-15","2025-04-30"])]), expected=[["2025-04-01 00:00:00",110]], why="The label in New York time is the first of April. Rows 2, 3 are in the 30 days before."),
]
TAGS = ["v1.3.0", "v1.5.0", "v1.6.52", "v1.6.53", "v1.7.0"]
TAG_CASES = ["month_mid_start_rest", "month_first_start_control"]
TAG_CONFIGS = ["pg-on"]
