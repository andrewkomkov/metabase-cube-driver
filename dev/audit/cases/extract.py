"""extract: EXTRACT and date_part on ClickHouse."""
MODEL = """
cubes:
  - name: xt
    sql_table: audit.xt
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: ts, sql: ts, type: time }
      - { name: d, sql: d, type: time }
    measures:
      - { name: n, type: count }
      - { name: total, sql: amt, type: sum }
      - { name: avg_a, sql: amt, type: avg }
"""
ROWS = "(1,5,'2024-02-29 23:30:00','2024-02-29'),(2,7,'2024-03-03 00:00:00','2024-03-03'),(3,11,'2025-01-01 00:00:00','2025-01-01'),(4,13,'2025-12-31 23:59:59','2025-12-31')"
SEED_CH = f"CREATE TABLE audit.xt (id UInt32, amt Int32, ts DateTime('UTC'), d Date) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.xt VALUES {ROWS};\n"
SEED_PG = f"CREATE TABLE audit.xt (id int, amt int, ts timestamp, d date);\nINSERT INTO audit.xt VALUES {ROWS};\n"
def g(part, m="total"):
    return f"SELECT EXTRACT({part} FROM ts) p, MEASURE({m}) v FROM xt GROUP BY 1 ORDER BY 1"
MON = [[1,11],[2,5],[3,7],[12,13]]
CASES = [
 dict(id="month_sum", sql=g("MONTH"), expected=MON, why="January 11, February 5, March 7, December 13.", truth=dict(pg="SELECT extract(month from ts), sum(amt) FROM audit.xt GROUP BY 1 ORDER BY 1", ch="SELECT toMonth(ts), sum(amt) FROM audit.xt GROUP BY 1 ORDER BY 1")),
 dict(id="month_count", sql=g("MONTH","n"), expected=[[1,1],[2,1],[3,1],[12,1]], why="One row per month."),
 dict(id="month_avg", sql=g("MONTH","avg_a"), expected=MON, why="One row per month, so the avg is the amount."),
 dict(id="year", sql=g("YEAR"), expected=[[2024,12],[2025,24]], why="5+7 and 11+13."),
 dict(id="day", sql=g("DAY"), expected=[[1,11],[3,7],[29,5],[31,13]], why="Leap day 29, month end 31."),
 dict(id="hour", sql=g("HOUR"), expected=[[0,18],[23,18]], why="Midnight rows 2, 3 and rows 1, 4 at hour 23."),
 dict(id="dow", sql=g("DOW"), expected=[[0,7],[3,24],[4,5]], why="Postgres DOW has Sunday as 0. 03-03 is a Sunday, 2025-01-01 and 12-31 are Wednesdays, 02-29 is a Thursday."),
 dict(id="dow_avg_no_nested_form", sql=g("DOW","avg_a"), expected=[[0,7],[3,12],[4,5]], why="An avg by day of week. Wednesday: (11+13)/2. The docs say that a non-additive measure cannot use the nested form, so this query has no other SQL API form."),
 dict(id="nested_avg_wrong_by_docs", sql="SELECT EXTRACT(DOW FROM d) p, AVG(v) a FROM (SELECT DATE_TRUNC('day', ts) d, MEASURE(avg_a) v FROM xt GROUP BY 1) q GROUP BY 1 ORDER BY 1", expected=[[0,7],[3,12],[4,5]], why="The nested form with a day inside. Each day has one row here, so an average of averages happens to be correct. The docs warn against it in general."),
 dict(id="quarter", sql=g("QUARTER"), expected=[[1,23],[4,13]], why="5+7+11 in Q1, 13 in Q4."),
 dict(id="date_part_fn", sql="SELECT date_part('month', ts) p, MEASURE(total) v FROM xt GROUP BY 1 ORDER BY 1", expected=MON, why="The function form."),
 dict(id="raw_rows", sql="SELECT id, EXTRACT(MONTH FROM ts) p FROM xt ORDER BY 1", expected=[[1,2],[2,3],[3,1],[4,12]], why="No measure."),
 dict(id="in_filter", sql="SELECT MEASURE(total) v FROM xt WHERE EXTRACT(YEAR FROM ts) = 2024", expected=[[12]], why="Rows 1, 2."),
 dict(id="date_column", sql="SELECT EXTRACT(MONTH FROM d) p, MEASURE(total) v FROM xt GROUP BY 1 ORDER BY 1", expected=MON, why="A Date column."),
 dict(id="outer_over_scan", sql="SELECT EXTRACT(YEAR FROM m) y, SUM(v) s FROM (SELECT DATE_TRUNC('month', ts) m, MEASURE(total) v FROM xt GROUP BY 1) q GROUP BY 1 ORDER BY 1", expected=[[2024,12],[2025,24]], why="The expression is on the outer query. A grouped inner query gives it month rows."),
 dict(id="time_zone", setup=["SET TIME ZONE 'America/New_York'"], sql=g("MONTH"), expected=[[2,5],[3,7],[12,24]], why="In New York, UTC-5: 2025-01-01 00:00 UTC is in December 2024. The month is 12 for rows 3 and 4."),
 dict(id="trunc_control", sql="SELECT DATE_TRUNC('year', ts) p, MEASURE(total) v FROM xt GROUP BY 1 ORDER BY 1", expected=[["2024-01-01 00:00:00",12],["2025-01-01 00:00:00",24]], why="DATE_TRUNC with the same data."),
]
EXTRA_CONFIGS = ["ch-on+nopush", "pg-on+nopush"]
TAGS = ["v1.6.76", "v1.0.0"]
TAG_CASES = ["month_sum", "raw_rows"]
TAG_CONFIGS = ["ch-off"]
