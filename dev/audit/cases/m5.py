"""m5: an original_sql pre-aggregation with an index. Each cube differs in one setting."""
def cube(name, pre):
    return f"""
  - name: {name}
    sql_table: audit.pa
    dimensions:
      - {{ name: id, sql: id, type: number, primary_key: true, public: true }}
      - {{ name: st, sql: st, type: string }}
      - {{ name: ts, sql: ts, type: time }}
    measures:
      - {{ name: rev, sql: amt, type: sum }}
    pre_aggregations:
{pre}"""
ORIG = lambda name, ext, idx: f"""      - name: {name}
        type: original_sql
        external: {ext}
""" + ("""        indexes:
          - { name: st_i, columns: [st] }
""" if idx else "")
ROLL = lambda base: f"""      - name: roll
        measures: [rev]
        dimensions: [st]
        use_original_sql_pre_aggregations: {base}
        indexes:
          - {{ name: st_r, columns: [st] }}
"""
MODEL = "cubes:" + "".join([
 cube("c_db_noidx", ORIG("base", "false", False)),
 cube("c_db_idx", ORIG("base", "false", True)),
 cube("c_ext_noidx", ORIG("base", "true", False)),
 cube("c_ext_idx", ORIG("base", "true", True)),
 cube("c_db_roll", ORIG("base", "false", True) + ROLL("true")),
 cube("c_ext_roll", ORIG("base", "true", False) + ROLL("true")),
 cube("c_plain_roll", ROLL("false")),
]) + """
  - name: c_aliased
    sql: SELECT id, st AS c_aliased__st, amt, ts FROM audit.pa
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: st, sql: c_aliased__st, type: string }
    measures:
      - { name: rev, sql: amt, type: sum }
    pre_aggregations:
      - name: base
        type: original_sql
        external: false
        indexes:
          - { name: st_i, columns: [st] }
"""
ROWS = "(1,'a',10,'2025-01-01 00:00:00'),(2,'a',20,'2025-01-02 00:00:00'),(3,'b',40,'2025-01-03 00:00:00')"
SEED_CH = f"CREATE TABLE audit.pa (id UInt32, st String, amt Int32, ts DateTime('UTC')) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.pa VALUES {ROWS};\n"
SEED_PG = f"CREATE TABLE audit.pa (id int, st text, amt int, ts timestamp);\nINSERT INTO audit.pa VALUES {ROWS};\n"
R = [["a",30],["b",40]]
WHY = "Rows 1, 2 are a, row 3 is b."
T = "SELECT st, sum(amt) FROM audit.pa GROUP BY 1 ORDER BY 1"
def rq(c):
    return dict(measures=[c+".rev"], dimensions=[c+".st"], order={c+".st":"asc"})
CASES = [
 dict(id="db_no_index", rest=rq("c_db_noidx"), cols=["c_db_noidx.st","c_db_noidx.rev"], expected=R, expected_error=dict(ch="without indexes"), why=WHY + " ClickHouse: the docs state that a pre-aggregation needs an index, so the refusal is correct there.", truth=T),
 dict(id="db_index", rest=rq("c_db_idx"), cols=["c_db_idx.st","c_db_idx.rev"], expected=R, why=WHY + " The index that the docs ask for.", truth=T),
 dict(id="db_index_sql", sql="SELECT st, MEASURE(rev) r FROM c_db_idx GROUP BY 1 ORDER BY 1", expected=R, why=WHY + " Through the SQL API."),
 dict(id="ext_no_index", rest=rq("c_ext_noidx"), cols=["c_ext_noidx.st","c_ext_noidx.rev"], expected=R, why=WHY + " In Cube Store, so the ClickHouse rule on indexes does not apply."),
 dict(id="ext_index", rest=rq("c_ext_idx"), cols=["c_ext_idx.st","c_ext_idx.rev"], expected=R, why=WHY + " In Cube Store, with an index."),
 dict(id="ext_index_sql", sql="SELECT st, MEASURE(rev) r FROM c_ext_idx GROUP BY 1 ORDER BY 1", expected=R, why=WHY + " In Cube Store, through the SQL API."),
 dict(id="db_rollup_over", rest=rq("c_db_roll"), cols=["c_db_roll.st","c_db_roll.rev"], expected=R, why=WHY + " The docs form: a rollup with use_original_sql_pre_aggregations."),
 dict(id="ext_rollup_over", rest=rq("c_ext_roll"), cols=["c_ext_roll.st","c_ext_roll.rev"], expected=R, why=WHY + " The same, with the original_sql table in Cube Store."),
 dict(id="plain_rollup_control", rest=rq("c_plain_roll"), cols=["c_plain_roll.st","c_plain_roll.rev"], expected=R, why=WHY + " A rollup with no original_sql."),
 dict(id="alias_column_workaround", rest=rq("c_aliased"), cols=["c_aliased.st","c_aliased.rev"], expected=R, why=WHY + " The source column has the name of the member alias, so the index column exists."),
]
TAGS = ["v1.6.76", "v1.0.0"]
TAG_CASES = ["db_index"]
TAG_CONFIGS = ["ch-off"]
