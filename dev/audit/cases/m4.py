"""m4: a rolling measure inside another aggregate."""
MODEL = """
cubes:
  - name: rt
    sql_table: audit.rt
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: ts, sql: ts, type: time }
    measures:
      - { name: rev, sql: amt, type: sum }
      - { name: top, sql: amt, type: max }
      - { name: rev_30d, sql: amt, type: sum, rolling_window: { trailing: 30 day } }
      - { name: top_30d, sql: amt, type: max, rolling_window: { trailing: 30 day } }
"""
ROWS = "(1,5,'2025-01-10 00:00:00'),(2,7,'2025-01-20 00:00:00'),(3,11,'2025-02-05 00:00:00'),(4,-2,'2025-02-25 00:00:00')"
SEED_CH = "CREATE TABLE audit.rt (id UInt32, amt Int32, ts DateTime('UTC')) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.rt VALUES " + ROWS + ";\n"
SEED_PG = "CREATE TABLE audit.rt (id int, amt int, ts timestamp);\nINSERT INTO audit.rt VALUES " + ROWS + ";\n"
DOC = "Measure aggregation type doesn't match"
D = "The docs give this error when an aggregate does not match the measure type."
RANGE = "ts >= '2025-01-01' AND ts < '2025-03-01'"
CASES = [
 dict(id="max_of_regular_sum", sql="SELECT MAX(rev) x FROM rt", expected_error=DOC, why="MAX of a sum measure. " + D + " This is the control: the error that the docs promise."),
 dict(id="max_of_rolling_sum", sql="SELECT MAX(rev_30d) x FROM rt", expected_error=DOC, why="MAX of a rolling sum. " + D),
 dict(id="min_of_rolling_sum", sql="SELECT MIN(rev_30d) x FROM rt", expected_error=DOC, why="MIN of a rolling sum. " + D),
 dict(id="avg_of_rolling_sum", sql="SELECT AVG(rev_30d) x FROM rt", expected_error=DOC, why="AVG of a rolling sum. " + D),
 dict(id="max_of_rolling_by_bucket", sql=f"SELECT DATE_TRUNC('month', ts) m, MAX(rev_30d) x FROM rt WHERE {RANGE} GROUP BY 1 ORDER BY 1", expected_error=DOC, why="The same with a bucket and a range. " + D),
 dict(id="sum_of_rolling_sum_control", sql="SELECT SUM(rev_30d) x FROM rt", expected=[[21]], why="SUM matches the type. With no bucket, Cube gives the sum of all rows: 5+7+11-2."),
 dict(id="max_of_rolling_max_control", sql="SELECT MAX(top_30d) x FROM rt", expected=[[11]], why="MAX matches the type. The largest amount is 11."),
 dict(id="max_of_max_control", sql="SELECT MAX(top) x FROM rt", expected=[[11]], why="A plain max measure."),
 dict(id="measure_by_bucket_control", sql=f"SELECT DATE_TRUNC('month', ts) m, MEASURE(rev_30d) x FROM rt WHERE {RANGE} GROUP BY 1 ORDER BY 1", expected=[["2025-01-01 00:00:00",12],["2025-02-01 00:00:00",9]], why="The docs form. 30 days to 01-31: rows 1, 2. 30 days to 02-28: rows 3, 4."),
 dict(id="max_over_subquery_control", sql=f"SELECT MAX(x) y FROM (SELECT DATE_TRUNC('month', ts) m, MEASURE(rev_30d) x FROM rt WHERE {RANGE} GROUP BY 1) q", expected=[[12]], why="What a person means by this: the largest monthly value, 12 against 9."),
 dict(id="rest_control", rest=dict(measures=["rt.rev_30d"], timeDimensions=[dict(dimension="rt.ts", granularity="month", dateRange=["2025-01-01","2025-02-28"])]), expected=[["2025-01-01 00:00:00",12],["2025-02-01 00:00:00",9]], why="REST has no outer aggregate."),
]
TAGS = ["v1.6.76"]
TAG_CASES = ["max_of_rolling_sum", "max_of_regular_sum"]
TAG_CONFIGS = ["pg-off"]
