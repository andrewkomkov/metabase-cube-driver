"""datetime64: milliseconds in a time dimension on ClickHouse."""
MODEL = """
cubes:
  - name: e
    sql_table: audit.e
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: ts, sql: ts, type: time }
      - { name: us, sql: us, type: time }
      - { name: ts_text, sql: "concat('', ts)", type: string }
    measures:
      - { name: n, type: count }
      - { name: total, sql: amt, type: sum }
      - { name: last_at, sql: ts, type: max }
      - { name: last_time, sql: "max({CUBE}.ts)", type: time }
"""
RAW = [(1,'2025-01-01 10:00:00.007','2025-01-01 10:00:00.007123',1),(2,'2025-01-01 10:00:00.500','2025-01-01 10:00:00.500000',10),(3,'2025-01-01 10:00:00.999','2025-01-01 10:00:00.999999',100),(4,'2025-01-01 10:00:01.000','2025-01-01 10:00:01.000000',1000),(5,'2024-12-31 23:59:59.999','2024-12-31 23:59:59.999000',10000),(6,'2025-01-02 00:00:00.000','2025-01-02 00:00:00.000000',100000)]
ROWS = ",".join(f"({a},'{b}','{c}',{d})" for a,b,c,d in RAW)
SEED_CH = f"CREATE TABLE audit.e (id UInt32, ts DateTime64(3,'UTC'), us DateTime64(6,'UTC'), amt Int32) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.e VALUES {ROWS};\n"
SEED_PG = f"CREATE TABLE audit.e (id int, ts timestamp(3), us timestamp(6), amt int);\nINSERT INTO audit.e VALUES {ROWS};\n"
CASES = [
 dict(id="raw_sql", sql="SELECT id, ts FROM e WHERE id IN (1,2,3) ORDER BY 1", expected=[[1,"2025-01-01 10:00:00.007"],[2,"2025-01-01 10:00:00.5"],[3,"2025-01-01 10:00:00.999"]], why="The column holds milliseconds.", truth="SELECT id, ts FROM audit.e WHERE id IN (1,2,3) ORDER BY 1"),
 dict(id="raw_rest", rest=dict(dimensions=["e.id","e.ts"], order={"e.id":"asc"}, filters=[dict(member="e.id",operator="equals",values=["1","2","3"])]), expected=[[1,"2025-01-01 10:00:00.007"],[2,"2025-01-01 10:00:00.5"],[3,"2025-01-01 10:00:00.999"]], why="REST time values have the form .SSS, so 3 digits exist."),
 dict(id="micro_rest", rest=dict(dimensions=["e.id","e.us"], order={"e.id":"asc"}, filters=[dict(member="e.id",operator="equals",values=["1"])]), expected=[[1,"2025-01-01 10:00:00.007"]], why="REST has 3 digits, so .007123 can read as .007. Milliseconds stay."),
 dict(id="filter_ms_window_sql", sql="SELECT MEASURE(total) t FROM e WHERE ts >= '2025-01-01 10:00:00.500' AND ts < '2025-01-01 10:00:00.501'", expected=[[10]], why="A 1 ms window around row 2.", truth="SELECT sum(amt) FROM audit.e WHERE ts >= '2025-01-01 10:00:00.500' AND ts < '2025-01-01 10:00:00.501'"),
 dict(id="filter_ms_window_rest", rest=dict(measures=["e.total"], timeDimensions=[dict(dimension="e.ts", dateRange=["2025-01-01T10:00:00.500","2025-01-01T10:00:00.500"])]), expected=[[10]], why="The docs say that a range includes both ends. Row 2 is on both."),
 dict(id="filter_gt_last_ms", sql="SELECT MEASURE(total) t FROM e WHERE ts > '2025-01-01 10:00:00.999' AND ts <= '2025-01-01 10:00:01.000'", expected=[[1000]], why="Row 3 is on the strict bound. Row 4 is on the upper bound."),
 dict(id="filter_le_ms", sql="SELECT MEASURE(total) t FROM e WHERE ts >= '2025-01-01 10:00:00.000' AND ts <= '2025-01-01 10:00:00.007'", expected=[[1]], why="Row 1 is on the upper bound. Row 2 is after it."),
 dict(id="filter_year_end", sql="SELECT MEASURE(n) c FROM e WHERE ts <= '2024-12-31 23:59:59.998'", expected=[[0]], why="Row 5 is 1 ms after the bound, so no row.", truth="SELECT count(*) FROM audit.e WHERE ts <= '2024-12-31 23:59:59.998'"),
 dict(id="filter_on_ms_upper", sql="SELECT MEASURE(n) c FROM e WHERE ts <= '2024-12-31 23:59:59.999'", expected=[[1]], why="Row 5 is on the bound.", truth="SELECT count(*) FROM audit.e WHERE ts <= '2024-12-31 23:59:59.999'"),
 dict(id="midnight_day_filter", sql="SELECT MEASURE(total) t FROM e WHERE ts >= '2025-01-01 00:00:00' AND ts < '2025-01-02 00:00:00'", expected=[[1111]], why="A day range: rows 1 to 4. Row 5 is 1 ms before it, row 6 on the open end. Whole second bounds.", truth="SELECT sum(amt) FROM audit.e WHERE ts >= '2025-01-01 00:00:00' AND ts < '2025-01-02 00:00:00'"),
 dict(id="bucket_second_control", sql="SELECT DATE_TRUNC('second', ts) s, MEASURE(total) t FROM e WHERE id IN (1,2,3,4) GROUP BY 1 ORDER BY 1", expected=[["2025-01-01 10:00:00",111],["2025-01-01 10:00:01",1000]], why="Truncation to a second is what the query asks."),
 dict(id="bucket_day_control", sql="SELECT DATE_TRUNC('day', ts) s, MEASURE(total) t FROM e GROUP BY 1 ORDER BY 1", expected=[["2024-12-31 00:00:00",10000],["2025-01-01 00:00:00",1111],["2025-01-02 00:00:00",100000]], why="Milliseconds do not move a row across midnight."),
 dict(id="order_by_ms", sql="SELECT id, ts_text FROM e WHERE id IN (1,2,3) ORDER BY ts_text DESC", expected=[[3,"2025-01-01 10:00:00.999"],[2,"2025-01-01 10:00:00.5"],[1,"2025-01-01 10:00:00.007"]], why="The order of the text form, a control. Postgres prints .500 as .5."),
 dict(id="order_by_time_selected", sql="SELECT id, ts FROM e WHERE id IN (1,2,3) ORDER BY ts DESC", expected=[[3,"2025-01-01 10:00:00.999"],[2,"2025-01-01 10:00:00.5"],[1,"2025-01-01 10:00:00.007"]], why="The latest row first, with the milliseconds."),
 dict(id="max_time_measure_rest", rest=dict(measures=["e.last_time"], filters=[dict(member="e.id",operator="equals",values=["1","2","3"])]), expected=[["2025-01-01 10:00:00.999"]], why="A measure of type time, as the docs ask for a time value."),
 dict(id="max_time_measure_sql", sql="SELECT MEASURE(last_time) x FROM e WHERE id IN (1,2,3)", expected=[["2025-01-01 10:00:00.999"]], why="The same through the SQL API."),
 dict(id="max_measure_rest", rest=dict(measures=["e.last_at"], filters=[dict(member="e.id",operator="equals",values=["1","2","3"])]), expected=[["2025-01-01 10:00:00.999"]], why="The largest of the three."),
 dict(id="time_zone_sql", setup=["SET TIME ZONE 'America/New_York'"], sql="SELECT id, ts FROM e WHERE id = 1", expected=[[1,"2025-01-01 05:00:00.007"]], why="UTC-5 in January."),
 dict(id="text_workaround", sql="SELECT ts_text FROM e WHERE id = 1", expected=[["2025-01-01 10:00:00.007"]], why="A string member with the same value."),
]
TAGS = ["v1.6.76", "v1.0.0"]
TAG_CASES = ["raw_rest", "filter_ms_window_rest"]
TAG_CONFIGS = ["ch-off"]
