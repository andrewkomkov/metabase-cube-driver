"""M2: a week number query with a date parameter. Expected week numbers come from date arithmetic in this file, not from a database."""
from datetime import date
MODEL = """
cubes:
  - name: wk
    sql_table: audit.wk
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: label, sql: label, type: string }
      - { name: ts, sql: ts, type: time }
    measures:
      - { name: n, type: count }
"""
ROWS = "(1,'a','2025-01-04 23:59:59'),(2,'a','2025-01-05 00:00:00'),(3,'b','2025-01-06 12:00:00'),(4,'b','2025-01-12 00:00:00')"
SEED_CH = "CREATE TABLE audit.wk (id UInt32, label String, ts DateTime('UTC')) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.wk VALUES " + ROWS + ";\n"
SEED_PG = "CREATE TABLE audit.wk (id int, label text, ts timestamp);\nINSERT INTO audit.wk VALUES " + ROWS + ";\n"
def wn(y, m, d):
    return (date(y, m, d) - date(2001, 1, 7)).days // 7
A, B, C = wn(2025,1,4), wn(2025,1,5), wn(2025,1,12)
NUM = "CAST(FLOOR(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT)"
def q(cond):
    return f"SELECT w, c FROM (SELECT {NUM} AS w, MEASURE(n) c FROM wk WHERE {cond} GROUP BY 1) q ORDER BY 1"
X = "2025-01-05 00:00:00"
WHY = "Weeks start on Sunday 2001-01-07 here. 01-05 and 01-06 share a week, 01-12 starts the next."
CASES = [
 dict(id="untyped", wire=dict(sql=q("ts >= $1"), params=[X]), explain=q(f"ts >= TIMESTAMP '{X}'"), expected=[[B,2],[C,1]], why=WHY),
 dict(id="oid_timestamp", wire=dict(sql=q("ts >= $1"), params=[X], oids=[1114]), explain=q(f"ts >= TIMESTAMP '{X}'"), expected=[[B,2],[C,1]], why=WHY + " The client states the type."),
 dict(id="oid_varchar", wire=dict(sql=q("ts >= $1"), params=[X], oids=[1043]), explain=q(f"ts >= TIMESTAMP '{X}'"), expected=[[B,2],[C,1]], why=WHY + " The type of pgjdbc setString."),
 dict(id="one_second_before", wire=dict(sql=q("ts >= $1"), params=["2025-01-04 23:59:59"]), explain=q("ts >= TIMESTAMP '2025-01-04 23:59:59'"), expected=[[A,1],[B,2],[C,1]], why="Row 1 is on the bound, in the week before."),
 dict(id="empty_result", wire=dict(sql=q("ts >= $1"), params=["2030-01-01 00:00:00"]), explain=q("ts >= TIMESTAMP '2030-01-01 00:00:00'"), expected=[], why="No rows."),
 dict(id="in_inner_and_diff", wire=dict(sql=f"SELECT w, c FROM (SELECT CAST(FLOOR(DATEDIFF('day', $1, ts) / 7.0) AS BIGINT) AS w, MEASURE(n) c FROM wk GROUP BY 1) q ORDER BY 1", params=[X]), expected=[[-1,1],[0,2],[1,1]], why="The parameter is the origin: 01-05 is week 0. Row 1 is one second before it, so week -1."),
 dict(id="cast_control", wire=dict(sql=q("ts >= $1::timestamp"), params=[X]), expected=[[B,2],[C,1]], why=WHY),
 dict(id="literal_control", sql=q(f"ts >= TIMESTAMP '{X}'"), expected=[[B,2],[C,1]], why=WHY),
 dict(id="no_param_control", sql=f"SELECT {NUM} AS w, MEASURE(n) c FROM wk GROUP BY 1 ORDER BY 1", expected=[[A,1],[B,2],[C,1]], why="All rows."),
 dict(id="text_param_control", wire=dict(sql=q("label = $1"), params=["b"]), expected=[[B,1],[C,1]], why="A text parameter in the same shape: rows 3, 4."),
 dict(id="date_trunc_week_param", wire=dict(sql="SELECT DATE_TRUNC('week', ts) w, MEASURE(n) c FROM wk WHERE ts >= $1 GROUP BY 1 ORDER BY 1", params=[X]), expected=[["2024-12-30 00:00:00",1],["2025-01-06 00:00:00",2]], why="Weeks start on Monday in DATE_TRUNC. Row 2 falls in the week of 12-30, rows 3, 4 in the week of 01-06. No wrapper is needed."),
]
TAGS = ["v1.7.26", "v1.7.27"]
TAG_CASES = ["untyped", "cast_control"]
TAG_CONFIGS = ["pg-on"]
