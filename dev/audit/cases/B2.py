"""B2: an outer filter on a DATE_TRUNC alias. Amounts are powers of 10, so a bucket value names its rows."""
MODEL = """
cubes:
  - name: sales
    sql_table: audit.sales
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: ts, sql: ts, type: time }
      - { name: d, sql: d, type: time }
    measures:
      - { name: rev, sql: amt, type: sum }
      - { name: n, type: count }
  - name: sales_r
    sql_table: audit.sales
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: ts, sql: ts, type: time }
    measures:
      - { name: rev, sql: amt, type: sum }
    pre_aggregations:
      - name: by_month
        dimensions: [id]
        measures: [rev]
        time_dimension: ts
        granularity: month
        allow_non_strict_date_range_match: true
        indexes:
          - { name: by_id, columns: [id] }
"""
ROWS = """(1,1,'2025-03-10 08:00:00','2025-03-10'),(2,10,'2025-04-30 23:59:59','2025-04-30'),(3,100,'2025-05-01 00:00:00','2025-05-01'),(4,1000,'2025-05-14 12:00:00','2025-05-14'),(5,10000,'2025-05-15 00:00:00','2025-05-15'),(6,100000,'2025-05-31 23:59:59','2025-05-31'),(7,1000000,'2025-06-01 00:00:00','2025-06-01'),(8,10000000,'2025-07-20 06:00:00','2025-07-20')"""
SEED_CH = "CREATE TABLE audit.sales (id UInt32, amt Int64, ts DateTime('UTC'), d Date) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.sales VALUES " + ROWS + ";\n"
SEED_PG = "CREATE TABLE audit.sales (id int, amt bigint, ts timestamp, d date);\nINSERT INTO audit.sales VALUES " + ROWS + ";\n"
W = "(SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s"
MAR, APR, MAY, JUN, JUL = ["2025-03-01 00:00:00", 1], ["2025-04-01 00:00:00", 10], ["2025-05-01 00:00:00", 111100], ["2025-06-01 00:00:00", 1000000], ["2025-07-01 00:00:00", 10000000]
def tp(cond):
    return dict(pg=f"SELECT m, rev FROM (SELECT date_trunc('month', ts) m, sum(amt) rev FROM audit.sales GROUP BY 1) s WHERE {cond} ORDER BY 1",
                ch=f"SELECT m, rev FROM (SELECT toDateTime(toStartOfMonth(ts),'UTC') m, sum(amt) rev FROM audit.sales GROUP BY 1) s WHERE {cond} ORDER BY 1")
CASES = [
 dict(id="ge_mid_month", sql=f"SELECT m, rev FROM {W} WHERE m >= '2025-05-15' ORDER BY 1", expected=[JUN, JUL],
      why="The bucket label of May is 05-01, before 05-15, so May leaves. The June and July buckets stay whole.", truth=tp("m >= '2025-05-15 00:00:00'")),
 dict(id="ge_first_of_month", sql=f"SELECT m, rev FROM {W} WHERE m >= '2025-05-01' ORDER BY 1", expected=[MAY, JUN, JUL],
      why="A bound on the bucket start. May stays with all of rows 3 to 6.", truth=tp("m >= '2025-05-01 00:00:00'")),
 dict(id="ge_one_second_after", sql=f"SELECT m, rev FROM {W} WHERE m >= '2025-05-01 00:00:01' ORDER BY 1", expected=[JUN, JUL], why="One second after the May label, so May leaves."),
 dict(id="ge_one_second_before", sql=f"SELECT m, rev FROM {W} WHERE m >= '2025-04-30 23:59:59' ORDER BY 1", expected=[MAY, JUN, JUL], why="The April label 04-01 is before the bound. The value 10 of April must not appear."),
 dict(id="gt_first_of_month", sql=f"SELECT m, rev FROM {W} WHERE m > '2025-05-01' ORDER BY 1", expected=[JUN, JUL], why="Strict: the May label equals the bound, so May leaves.", truth=tp("m > '2025-05-01 00:00:00'")),
 dict(id="lt_mid_month", sql=f"SELECT m, rev FROM {W} WHERE m < '2025-05-15' ORDER BY 1", expected=[MAR, APR, MAY],
      why="The May label is before 05-15, so May stays, whole: rows 3 to 6.", truth=tp("m < '2025-05-15 00:00:00'")),
 dict(id="le_first_of_month", sql=f"SELECT m, rev FROM {W} WHERE m <= '2025-05-01' ORDER BY 1", expected=[MAR, APR, MAY], why="The May label equals the bound. May stays, whole.", truth=tp("m <= '2025-05-01 00:00:00'")),
 dict(id="lt_first_of_month", sql=f"SELECT m, rev FROM {W} WHERE m < '2025-05-01' ORDER BY 1", expected=[MAR, APR], why="Before the May label: March and April."),
 dict(id="eq_mid_month", sql=f"SELECT m, rev FROM {W} WHERE m = '2025-05-15' ORDER BY 1", expected=[], why="No bucket label is 05-15."),
 dict(id="eq_first_of_month", sql=f"SELECT m, rev FROM {W} WHERE m = '2025-05-01' ORDER BY 1", expected=[MAY], why="The May bucket, whole."),
 dict(id="between_mid_months", sql=f"SELECT m, rev FROM {W} WHERE m BETWEEN '2025-04-15' AND '2025-06-15' ORDER BY 1", expected=[MAY, JUN], why="The labels 05-01 and 06-01 are inside. Each bucket stays whole.", truth=tp("m BETWEEN '2025-04-15 00:00:00' AND '2025-06-15 00:00:00'")),
 dict(id="empty_far_future", sql=f"SELECT m, rev FROM {W} WHERE m >= '2030-01-01' ORDER BY 1", expected=[], why="No rows."),
 dict(id="outer_sum", sql=f"SELECT SUM(rev) x FROM {W} WHERE m >= '2025-05-15'", expected=[[11000000]], why="June plus July."),
 dict(id="typed_literal", sql=f"SELECT m, rev FROM {W} WHERE m >= TIMESTAMP '2025-05-15 00:00:00' ORDER BY 1", expected=[JUN, JUL], why="The same as ge_mid_month."),
 dict(id="cte_form", sql="WITH s AS (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) SELECT m, rev FROM s WHERE m >= '2025-05-15' ORDER BY 1", expected=[JUN, JUL], why="A CTE has the meaning of the subquery."),
 dict(id="alias_as_column", sql="SELECT ts, rev FROM (SELECT DATE_TRUNC('month', ts) AS ts, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE ts >= '2025-05-15' ORDER BY 1", expected=[JUN, JUL], why="The alias has the name of the column. The outer ts is the bucket."),
 dict(id="year_bucket", sql="SELECT y, rev FROM (SELECT DATE_TRUNC('year', ts) AS y, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE y >= '2025-03-01' ORDER BY 1", expected=[], why="The only label is 2025-01-01, before 03-01."),
 dict(id="week_bucket", sql="SELECT w, rev FROM (SELECT DATE_TRUNC('week', ts) AS w, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE w >= '2025-05-13' ORDER BY 1", expected=[["2025-05-26 00:00:00", 1100000], ["2025-07-14 00:00:00", 10000000]],
      why="Weeks start on Monday. 05-14 and 05-15 are in the week of 05-12, before the bound. 05-31 and 06-01 are in the week of 05-26."),
 dict(id="date_column", sql="SELECT m, rev FROM (SELECT DATE_TRUNC('month', d) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m >= '2025-05-15' ORDER BY 1", expected=[JUN, JUL], why="The same as ge_mid_month, on a Date column."),
 dict(id="bind_cast", wire=dict(sql=f"SELECT m, rev FROM {W} WHERE m >= $1::timestamp ORDER BY 1", params=["2025-05-15 00:00:00"]), expected=[JUN, JUL], why="The same as ge_mid_month, with a parameter."),
 dict(id="sql_time_zone", setup=["SET TIME ZONE 'America/New_York'"], sql=f"SELECT m, rev FROM {W} WHERE m >= '2025-05-15' ORDER BY 1", expected=[["2025-07-01 00:00:00", 10000000]],
      why="In New York, UTC-4 in May: row 7 at 06-01 00:00 UTC falls in May, so no June bucket. The May label is before the bound. Only July is left."),
 dict(id="rollup_miss_or_hit", sql="SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales_r GROUP BY 1) s WHERE m >= '2025-05-15' ORDER BY 1", expected=[JUN, JUL], why="The same as ge_mid_month, on a cube with a rollup by month."),
 dict(id="direct_where_control", sql="SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales WHERE DATE_TRUNC('month', ts) >= '2025-05-15' GROUP BY 1 ORDER BY 1", expected=[JUN, JUL], why="The same condition with no subquery."),
 dict(id="row_filter_control", sql="SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales WHERE ts >= '2025-05-15' GROUP BY 1 ORDER BY 1", expected=[["2025-05-01 00:00:00", 110000], JUN, JUL], why="A filter on rows, not on buckets, so May keeps rows 5 and 6. This is the docs form of a partial bucket."),
 dict(id="rest_date_range_control", rest=dict(measures=["sales.rev"], timeDimensions=[dict(dimension="sales.ts", granularity="month", dateRange=["2025-05-15", "2025-07-31"])]), expected=[["2025-05-01 00:00:00", 110000], JUN, JUL], why="REST has a date range on rows, the same as row_filter_control."),
]
TAGS = ["v1.6.76", "v1.0.0", "v0.35.58"]
TAG_CASES = ["ge_mid_month", "lt_mid_month"]
TAG_CONFIGS = ["pg-off"]
