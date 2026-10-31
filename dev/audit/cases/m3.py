"""m3: a count of no rows, with and with no rollup."""
RPRE = """
    pre_aggregations:
      - name: by_st
        dimensions: [st]
        measures: [n, total]
        time_dimension: ts
        granularity: day
        indexes:
          - { name: by_st_i, columns: [st] }
"""
def cube(name, table, pre=""):
    return f"""
  - name: {name}
    sql_table: audit.{table}
    dimensions:
      - {{ name: id, sql: id, type: number, primary_key: true, public: true }}
      - {{ name: st, sql: st, type: string }}
      - {{ name: ts, sql: ts, type: time }}
    measures:
      - {{ name: n, type: count }}
      - {{ name: total, sql: amt, type: sum }}
      - {{ name: dist, sql: st, type: count_distinct }}
{pre}"""
MODEL = "cubes:" + cube("o", "ord") + cube("or_ext", "ord", RPRE.replace("granularity: day", "granularity: day\n        external: true")) + cube("or_db", "ord", RPRE.replace("granularity: day", "granularity: day\n        external: false")) + cube("oe_ext", "ord_empty", RPRE.replace("granularity: day", "granularity: day\n        external: true"))
ROWS = "(1,'a',10,'2025-01-10 00:00:00'),(2,'a',20,'2025-01-20 00:00:00'),(3,'b',30,'2025-02-10 00:00:00')"
SEED_CH = f"CREATE TABLE audit.ord (id UInt32, st LowCardinality(String), amt Int32, ts DateTime('UTC')) ENGINE = MergeTree ORDER BY id;\nCREATE TABLE audit.ord_empty AS audit.ord;\nINSERT INTO audit.ord VALUES {ROWS};\n"
SEED_PG = f"CREATE TABLE audit.ord (id int, st text, amt int, ts timestamp);\nCREATE TABLE audit.ord_empty (LIKE audit.ord);\nINSERT INTO audit.ord VALUES {ROWS};\n"
N0 = "A count of no rows is 0 in SQL: SQL:2016 part 2, 10.9, and both databases."
T0 = "SELECT count(*) FROM audit.ord WHERE st = 'nope'"
def rest(cube, filt="nope", extra=None):
    q = dict(measures=[cube + ".n"], filters=[dict(member=cube + ".st", operator="equals", values=[filt])])
    q.update(extra or {})
    return q
CASES = [
 dict(id="plain_no_rollup_sql", sql="SELECT MEASURE(n) c FROM o WHERE st = 'nope'", expected=[[0]], why=N0, truth=T0),
 dict(id="plain_no_rollup_rest", rest=rest("o"), expected=[[0]], why=N0, truth=T0),
 dict(id="ext_rollup_sql", sql="SELECT MEASURE(n) c FROM or_ext WHERE st = 'nope'", expected=[[0]], why=N0, truth=T0),
 dict(id="ext_rollup_count_star", sql="SELECT COUNT(*) c FROM or_ext WHERE st = 'nope'", expected=[[0]], why=N0),
 dict(id="ext_rollup_rest", rest=rest("or_ext"), expected=[[0]], why=N0, truth=T0),
 dict(id="db_rollup_rest", rest=rest("or_db"), expected=[[0]], why=N0 + " The rollup is in the database, not in Cube Store."),
 dict(id="db_rollup_sql", sql="SELECT MEASURE(n) c FROM or_db WHERE st = 'nope'", expected=[[0]], why=N0 + " The rollup is in the database."),
 dict(id="ext_rollup_one_match", rest=rest("or_ext", "b"), expected=[[1]], why="Row 3. A value with rows is correct."),
 dict(id="ext_rollup_group_by", rest=dict(measures=["or_ext.n"], dimensions=["or_ext.st"], filters=[dict(member="or_ext.st", operator="equals", values=["nope"])]), expected=[], why="GROUP BY of no rows gives no rows, not a 0."),
 dict(id="ext_rollup_date_range_empty", rest=dict(measures=["or_ext.n"], timeDimensions=[dict(dimension="or_ext.ts", dateRange=["2030-01-01", "2030-01-31"])]), expected=[[0]], why=N0 + " The date range has no rows."),
 dict(id="ext_rollup_null_filter", rest=dict(measures=["or_ext.n"], filters=[dict(member="or_ext.st", operator="notSet")]), expected=[[0]], why=N0 + " No row has a NULL status."),
 dict(id="ext_rollup_coalesce", sql="SELECT COALESCE(MEASURE(n), 0) c FROM or_ext WHERE st = 'nope'", expected=[[0]], why="The value with a COALESCE, the form that a client can write."),
 dict(id="ext_rollup_sum_is_null", rest=dict(measures=["or_ext.total"], filters=[dict(member="or_ext.st", operator="equals", values=["nope"])]), expected=[[None]], why="SUM of no rows is NULL in SQL. This is correct, so the rule of m3 is only for a count.", truth=dict(pg="SELECT sum(amt) FROM audit.ord WHERE st = 'nope'")),
 dict(id="ext_rollup_miss_dim", rest=dict(measures=["or_ext.n"], filters=[dict(member="or_ext.id", operator="equals", values=["99"])]), expected=[[0]], why=N0 + " A filter on id, which is not in the rollup, so the rollup misses."),
 dict(id="ext_rollup_miss_distinct", rest=dict(measures=["or_ext.dist"], filters=[dict(member="or_ext.st", operator="equals", values=["nope"])]), expected=[[0]], why="count_distinct is not in the rollup, so it reads the source. COUNT(DISTINCT) of no rows is 0."),
 dict(id="empty_table_rollup", rest=dict(measures=["oe_ext.n"]), expected=[[0]], why=N0 + " The table has no rows at all."),
 dict(id="empty_table_rollup_sql", sql="SELECT MEASURE(n) c FROM oe_ext", expected=[[0]], why=N0 + " The table has no rows at all."),
]
TAGS = ["v1.6.76", "v1.0.0"]
TAG_CASES = ["ext_rollup_rest"]
TAG_CONFIGS = ["pg-off"]
