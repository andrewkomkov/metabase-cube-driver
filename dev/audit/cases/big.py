"""big: numbers above 2^53 in a number member. 2^53 = 9007199254740992 is the last integer before a double skips one."""
MODEL = """
cubes:
  - name: bn
    sql_table: audit.bn
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: u, sql: u, type: number }
      - { name: i, sql: i, type: number }
      - { name: dc, sql: dc, type: number }
      - { name: f, sql: f, type: number }
      - { name: small, sql: small, type: number }
      - { name: u_text, sql: "concat('', u)", type: string }
      - { name: dc_text, sql: "concat('', dc)", type: string }
    measures:
      - { name: sum_u, sql: u, type: sum }
      - { name: sum_i, sql: i, type: sum }
      - { name: sum_dc, sql: dc, type: sum }
      - { name: sum_small, sql: small, type: sum }
      - { name: n, type: count }
"""
ROWS = """(1,9007199254740992,9007199254740992,9007199254740992.5,0.5,1),
(2,9007199254740993,-9007199254740993,3000000000000.00000003,0.25,2),
(3,18446744073709551615,9223372036854775807,0.00000001,9007199254740993,3)"""
SEED_CH = f"CREATE TABLE audit.bn (id UInt32, u UInt64, i Int64, dc Decimal(38,8), f Float64, small Int32) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.bn VALUES {ROWS};\n"
SEED_PG = f"CREATE TABLE audit.bn (id int, u numeric(20,0), i bigint, dc numeric(38,8), f float8, small int);\nINSERT INTO audit.bn VALUES {ROWS};\n"
def sql(col, i):
    return f"SELECT {col} FROM bn WHERE id = {i}"
def rest(col, i):
    return dict(dimensions=[f"bn.{col}"], filters=[dict(member="bn.id", operator="equals", values=[str(i)])])
CASES = [
 dict(id="sql_2p53_control", sql=sql("u",1), expected=[["9007199254740992"]], why="2^53 fits a double."),
 dict(id="sql_2p53_plus1", sql=sql("u",2), expected=[["9007199254740993"]], why="2^53+1. The row holds this value. Read it from the table.", truth="SELECT u FROM audit.bn WHERE id = 2"),
 dict(id="sql_uint64_max", sql=sql("u",3), expected=[["18446744073709551615"]], why="UInt64 max in the table.", truth="SELECT u FROM audit.bn WHERE id = 3"),
 dict(id="sql_negative", sql=sql("i",2), expected=[["-9007199254740993"]], why="Int64, negative.", truth="SELECT i FROM audit.bn WHERE id = 2"),
 dict(id="sql_decimal", sql=sql("dc",2), expected=[["3000000000000.00000003"]], why="Decimal(38,8): 21 significant digits.", truth="SELECT dc FROM audit.bn WHERE id = 2"),
 dict(id="sql_small_decimal_control", sql=sql("dc",3), expected=[["0.00000001"]], why="A small decimal fits a double closely enough to print the same."),
 dict(id="sql_measure_sum", sql="SELECT MEASURE(sum_i) x FROM bn WHERE id = 2", expected=[["-9007199254740993"]], why="A sum over one row."),
 dict(id="sql_expression_pushdown", sql="SELECT u + 0 x FROM bn WHERE id = 2", expected=[["9007199254740993"]], why="An expression on the column. Cube can push it down."),
 dict(id="sql_float_control", sql=sql("f",3), expected=[["9007199254740992"]], why="A Float64 cannot hold 2^53+1. Rounding is correct here, so this is the control. The table has 9007199254740992.", truth="SELECT f FROM audit.bn WHERE id = 3"),
 dict(id="sql_text_workaround", sql=sql("u_text",2), expected=[["9007199254740993"]], why="The docs have no number type wider than double. A string member keeps each digit."),
 dict(id="sql_text_decimal_workaround", sql=sql("dc_text",2), expected=dict(ch=[["3000000000000.00000003"]], pg=[["3000000000000.00000003"]]), why="The same for a decimal."),
 dict(id="rest_2p53_plus1", rest=rest("u",2), expected=[["9007199254740993"]], why="REST returns numbers as strings, so it can keep each digit."),
 dict(id="rest_uint64_max", rest=rest("u",3), expected=[["18446744073709551615"]], why="The same for UInt64 max."),
 dict(id="rest_negative", rest=rest("i",2), expected=[["-9007199254740993"]], why="The same for Int64."),
 dict(id="rest_decimal", rest=rest("dc",2), expected=[["3000000000000.00000003"]], why="The same for a Decimal."),
 dict(id="rest_measure_sum", rest=dict(measures=["bn.sum_i"], filters=[dict(member="bn.id", operator="equals", values=["2"])]), expected=[["-9007199254740993"]], why="A sum over one row."),
 dict(id="rest_small_control", rest=dict(measures=["bn.sum_small"]), expected=[[6]], why="1+2+3."),
 dict(id="sql_small_control", sql="SELECT MEASURE(sum_small) x FROM bn", expected=[[6]], why="1+2+3."),
]
EXTRA_CONFIGS = ["ch-on+ch=25.3-alpine"]
