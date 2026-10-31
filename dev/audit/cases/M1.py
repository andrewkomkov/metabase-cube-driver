"""M1: a date parameter on a query that Cube wraps. The wire client controls the parameter type OID."""
MODEL = """
cubes:
  - name: ev
    sql_table: audit.ev
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: amt_dim, sql: amt, type: number }
      - { name: label, sql: label, type: string }
      - { name: ts, sql: ts, type: time }
      - { name: d, sql: d, type: time }
    measures:
      - { name: n, type: count }
      - { name: total, sql: amt, type: sum }
"""
ROWS = "(1,100,'a','2025-05-09 23:59:59','2025-05-09'),(2,200,'a','2025-05-10 00:00:00','2025-05-10'),(3,400,'b','2025-05-10 00:00:01','2025-05-10'),(4,800,'b','2025-06-01 12:00:00','2025-06-01')"
SEED_CH = "CREATE TABLE audit.ev (id UInt32, amt Int32, label String, ts DateTime('UTC'), d Date) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.ev VALUES " + ROWS + ";\n"
SEED_PG = "CREATE TABLE audit.ev (id int, amt int, label text, ts timestamp, d date);\nINSERT INTO audit.ev VALUES " + ROWS + ";\n"
X = "2025-05-10 00:00:00"
PP = "SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1"
LIT = f"SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= TIMESTAMP '{X}'"
def w(sql, params, oids=()):
    return dict(sql=sql, params=list(params), oids=list(oids))
WHY = "Rows 2, 3, 4 are on or after the bound: 200+400+800."
CASES = [
 dict(id="no_oid", wire=w(PP, [X]), explain=LIT, expected=[[1400]], why=WHY + " Postgres infers the parameter type from the column. This is what psql \\bind, asyncpg and psycopg3 send.", truth=f"SELECT sum(amt) FROM audit.ev WHERE ts >= '{X}'"),
 dict(id="oid_unspecified", wire=w(PP, [X], [0]), explain=LIT, expected=[[1400]], why=WHY + " OID 0 is what pgjdbc sends for setTimestamp."),
 dict(id="oid_timestamp", wire=w(PP, [X], [1114]), explain=LIT, expected=[[1400]], why=WHY + " The client states the type timestamp."),
 dict(id="oid_timestamptz", wire=w(PP, [X + "+00"], [1184]), explain=LIT, expected=[[1400]], why=WHY + " The client states timestamptz."),
 dict(id="oid_date", wire=w(PP, ["2025-05-10"], [1082]), explain=LIT, expected=[[1400]], why=WHY + " The client states date. Midnight is the bound."),
 dict(id="oid_varchar", wire=w(PP, [X], [1043]), explain=LIT, expected=[[1400]], why=WHY + " OID 1043 is what pgjdbc sends for setString."),
 dict(id="iso_text", wire=w(PP, ["2025-05-10T00:00:00.000Z"]), explain=LIT, expected=[[1400]], why=WHY + " An ISO text value."),
 dict(id="lt_bound", wire=w("SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts < $1", [X]), expected=[[100]], why="Only row 1 is before the bound."),
 dict(id="two_params", wire=w("SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1 AND ts <= $2", [X, "2025-05-10 00:00:01"]), expected=[[600]], why="Rows 2 and 3."),
 dict(id="eq_param", wire=w("SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts = $1", [X]), expected=[[200]], why="Row 2."),
 dict(id="in_params", wire=w("SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts IN ($1, $2)", [X, "2025-06-01 12:00:00"]), expected=[[1000]], why="Rows 2 and 4."),
 dict(id="null_param", wire=w(PP, [None]), explain=LIT, expected=[[None]], why="ts >= NULL is never true. SUM of no rows is NULL."),
 dict(id="date_column", wire=w("SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, d FROM ev) q WHERE d >= $1", ["2025-05-10"]), expected=[[1400]], why="Rows 2, 3, 4 on the Date column."),
 dict(id="grouped_wrapper", wire=w("SELECT DATE_TRUNC('month', ts) m, SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1 GROUP BY 1 ORDER BY 1", [X]), expected=[["2025-05-01 00:00:00", 600], ["2025-06-01 00:00:00", 800]], why="Rows 2, 3 in May, row 4 in June."),
 dict(id="trunc_in_filter", wire=w("SELECT MEASURE(n) c FROM ev WHERE DATE_TRUNC('day', ts) >= $1", [X]), expected=[[3]], why="Days on or after 05-10: rows 2, 3, 4."),
 dict(id="issue_12459_form", wire=w("SELECT DATE_TRUNC('month', ts) m, label, SUM(total) * 0.5 x FROM ev WHERE ts >= $1 GROUP BY 1, 2 ORDER BY 1, 2", [X]), expected=[["2025-05-01 00:00:00","a",100],["2025-05-01 00:00:00","b",200],["2025-06-01 00:00:00","b",400]], why="The form of #12459: arithmetic on a measure and an ORDER BY. Half of 200, 400, 800."),
 dict(id="issue_12459_form_no_order", wire=w("SELECT DATE_TRUNC('month', ts) m, label, SUM(total) * 0.5 x FROM ev WHERE ts >= $1 GROUP BY 1, 2", [X]), expected=[["2025-05-01 00:00:00","a",100],["2025-05-01 00:00:00","b",200],["2025-06-01 00:00:00","b",400]], why="The same with no ORDER BY. #12459 says that this form works.", sorted_rows=True),
 dict(id="no_oid_limit", wire=w(PP + " LIMIT 10", [X]), expected=[[1400]], why=WHY + " With a LIMIT."),
 dict(id="issue_12459_form_limit", wire=w("SELECT DATE_TRUNC('month', ts) m, label, SUM(total) * 0.5 x FROM ev WHERE ts >= $1 GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 10", [X]), expected=[["2025-05-01 00:00:00","a",100],["2025-05-01 00:00:00","b",200],["2025-06-01 00:00:00","b",400]], why="The form of #12459 with a LIMIT."),
 dict(id="cast_control", wire=w("SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1::timestamp", [X]), expected=[[1400]], why=WHY),
 dict(id="cast_fn_control", wire=w("SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= CAST($1 AS timestamp)", [X]), expected=[[1400]], why=WHY),
 dict(id="cast_date_control", wire=w("SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1::date", ["2025-05-10"]), expected=[[1400]], why=WHY),
 dict(id="literal_control", sql=LIT, expected=[[1400]], why=WHY),
 dict(id="direct_scan_control", wire=w("SELECT MEASURE(n) c FROM ev WHERE ts >= $1", [X]), expected=[[3]], why="No outer aggregate, so no wrapper: rows 2, 3, 4."),
 dict(id="text_param_control", wire=w("SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, label FROM ev) q WHERE label = $1", ["a"]), expected=[[300]], why="A text parameter in the same shape: rows 1 and 2."),
 dict(id="number_param_control", wire=w("SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim FROM ev) q WHERE amt_dim > $1", [300]), expected=[[1200]], why="A number parameter in the same shape: rows 3 and 4."),
]
TAGS = ["v1.7.26", "v1.7.27", "v1.8.14"]
TAG_CASES = ["no_oid", "oid_timestamp", "two_params", "cast_control", "issue_12459_form"]
TAG_CONFIGS = ["pg-on", "pg-off"]
