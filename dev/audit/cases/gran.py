"""gran: a custom granularity that a rollup serves. Bins are derived by hand from the origin and the interval."""
GR = """
        - { name: fq_offset, interval: 3 months, offset: 1 month }
        - { name: fq_recent, interval: 3 months, origin: "2025-02-01" }
        - { name: fq_old, interval: 3 months, origin: "2000-02-01" }
        - { name: mon_recent, interval: 1 month, origin: "2025-08-01" }
        - { name: wk_recent, interval: 7 days, origin: "2025-01-06" }
        - { name: wk_future, interval: 7 days, origin: "2025-12-29" }
"""
def cube(name, pre):
    return f"""
  - name: {name}
    sql_table: audit.ft
    dimensions:
      - {{ name: id, sql: id, type: number, primary_key: true, public: true }}
      - {{ name: k, sql: k, type: string }}
      - name: ts
        sql: ts
        type: time
        granularities:{GR}    measures:
      - {{ name: rev, sql: amt, type: sum }}
{pre}"""
PRE = """    pre_aggregations:
      - name: by_month
        dimensions: [k]
        measures: [rev]
        time_dimension: ts
        granularity: month
        external: true
        indexes:
          - { name: i, columns: [k] }
      - name: by_day
        dimensions: [k]
        measures: [rev]
        time_dimension: ts
        granularity: day
        external: true
        indexes:
          - { name: j, columns: [k] }
"""
MODEL = "cubes:" + cube("ff", PRE) + cube("fn", "")
ROWS = "(7,'k',1000000,'2024-02-29 12:00:00'),(8,'k',10000000,'2024-11-01 00:00:00'),(1,'k',1,'2024-11-15 00:00:00'),(2,'k',10,'2025-01-31 23:59:59'),(3,'k',100,'2025-02-01 00:00:00'),(4,'k',1000,'2025-04-30 00:00:00'),(5,'k',10000,'2025-05-01 00:00:00'),(6,'k',100000,'2025-08-15 00:00:00')"
SEED_CH = f"CREATE TABLE audit.ft (id UInt32, k String, amt Int64, ts DateTime('UTC')) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.ft VALUES {ROWS};\n"
SEED_PG = f"CREATE TABLE audit.ft (id int, k text, amt bigint, ts timestamp);\nINSERT INTO audit.ft VALUES {ROWS};\n"
Q = [["2024-02-01 00:00:00",1000000],["2024-11-01 00:00:00",10000011],["2025-02-01 00:00:00",1100],["2025-05-01 00:00:00",10000],["2025-08-01 00:00:00",100000]]
QW = "3-month bins on February, May, August, November. 02-29 goes to Feb 2024. 11-01, 11-15, 01-31 go to Nov 2024. 02-01 and 04-30 go to Feb 2025."
W7 = [["2024-02-26 00:00:00",1000000],["2024-10-28 00:00:00",10000000],["2024-11-11 00:00:00",1],["2025-01-27 00:00:00",110],["2025-04-28 00:00:00",11000],["2025-08-11 00:00:00",100000]]
W7W = "7-day bins on Mondays, because the origin is a Monday. Each row goes to the Monday on or before it."
M1 = [["2024-02-01 00:00:00",1000000],["2024-11-01 00:00:00",10000001],["2025-01-01 00:00:00",10],["2025-02-01 00:00:00",100],["2025-04-01 00:00:00",1000],["2025-05-01 00:00:00",10000],["2025-08-01 00:00:00",100000]]
def rq(c, g, **kw):
    q = dict(measures=[c+".rev"], timeDimensions=[dict(dimension=c+".ts", granularity=g, **kw)], order={c+".ts":"asc"})
    return q
def case(i, c, g, exp, why, **kw):
    return dict(id=i, rest=rq(c, g, **kw), cols=[f"{c}.ts.{g}", c+".rev"], expected=exp, why=why)
CASES = [
 case("offset_no_rollup","fn","fq_offset",Q,QW+" No rollup."),
 case("offset_rollup","ff","fq_offset",Q,QW+" A rollup by month."),
 case("recent_origin_no_rollup","fn","fq_recent",Q,QW+" No rollup."),
 case("recent_origin_rollup","ff","fq_recent",Q,QW+" The origin is after rows 7, 8, 1, 2."),
 case("old_origin_no_rollup","fn","fq_old",Q,QW+" No rollup. The origin 2000-02-01 is 25 years before the rows."),
 case("recent_origin_near_rows_no_rollup","fn","fq_recent",[["2025-02-01 00:00:00",1100],["2025-05-01 00:00:00",10000]],"Rows on and after the origin, no rollup.",dateRange=["2025-02-01","2025-07-31"]),
 case("recent_origin_one_interval_before_no_rollup","fn","fq_recent",[["2024-11-01 00:00:00",10000001]],"Row 8 is exactly one calendar interval before the origin, 92 days. Row 1 is in the same bin. No rollup.",dateRange=["2024-11-01","2024-11-30"]),
 case("old_origin_rollup","ff","fq_old",Q,QW+" The origin 2000-02-01 is before all rows. It gives the same bins."),
 case("recent_origin_after_origin_only","ff","fq_recent",[["2025-02-01 00:00:00",1100],["2025-05-01 00:00:00",10000]],"A range from the origin on: no row is before it.",dateRange=["2025-02-01","2025-07-31"]),
 case("recent_origin_one_row_before","ff","fq_recent",[["2024-11-01 00:00:00",10]],"Only row 2, one second before the origin. Its bin ends at the origin.",dateRange=["2025-01-01","2025-01-31"]),
 case("recent_origin_on_bin_start","ff","fq_recent",[["2024-11-01 00:00:00",10000001]],"Row 8 is exactly one interval before the origin, and row 1 is in the same bin.",dateRange=["2024-11-01","2024-11-30"]),
 case("leap_day","ff","fq_recent",[["2024-02-01 00:00:00",1000000]],"02-29 is in the bin of Feb 2024, four intervals before the origin.",dateRange=["2024-02-01","2024-02-29"]),
 case("month_interval_no_rollup","fn","mon_recent",M1,"Bins of one month: each row goes to the first of its month."),
 case("month_interval_rollup","ff","mon_recent",M1,"The same on the rollup. With an interval of one month, no division is left over."),
 case("days_no_rollup","fn","wk_recent",W7,W7W),
 case("days_rollup_origin_inside","ff","wk_recent",W7,W7W+" The rollup by day. Some rows are before the origin."),
 case("days_rollup_origin_after","ff","wk_future",W7,W7W+" 2025-12-29 is a Monday too, so the same bins, with all rows before the origin."),
 dict(id="rest_time_zone", rest=dict(measures=["ff.rev"], timezone="America/New_York", timeDimensions=[dict(dimension="ff.ts", granularity="fq_old", dateRange=["2025-01-01","2025-01-31"])]), cols=["ff.ts.fq_old","ff.rev"], expected=[["2024-11-01 00:00:00",110]], why="Another time zone with an old origin, the control. In New York, row 2 at 01-31 23:59:59 UTC and row 3 at 02-01 00:00 UTC are both in January."),
]
TAGS = ["v1.6.76", "v1.0.0"]
TAG_CASES = ["recent_origin_rollup", "recent_origin_no_rollup"]
TAG_CONFIGS = ["pg-off"]
