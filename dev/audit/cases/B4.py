"""B4: an outer filter on a measure column of a subquery with no GROUP BY."""
MODEL = """
cubes:
  - name: items_t
    sql_table: audit.items
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: cat, sql: cat, type: string }
      - { name: amt_dim, sql: amt, type: number }
      - { name: created_at, sql: ts, type: time }
    measures:
      - { name: rev, sql: amt, type: sum }
      - { name: n, type: count }
  - name: items
    sql_table: audit.items
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: cat, sql: cat, type: string }
      - { name: amt_dim, sql: amt, type: number }
    measures:
      - { name: rev, sql: amt, type: sum }
      - { name: avg_amt, sql: amt, type: avg }
      - { name: n, type: count }
"""
ROWS = "(1,'a',100,'2025-01-01'),(2,'a',200,'2025-01-02'),(3,'b',300,'2025-01-03'),(4,'b',0,'2025-01-04'),(5,'b',-50,'2025-01-05'),(6,'c',NULL,'2025-01-06')"
SEED_CH = "CREATE TABLE audit.items (id UInt32, cat LowCardinality(String), amt Nullable(Int32), ts DateTime('UTC')) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.items VALUES " + ROWS + ";\n"
SEED_PG = "CREATE TABLE audit.items (id int, cat text, amt int, ts timestamp);\nINSERT INTO audit.items VALUES " + ROWS + ";\n"
Q = "(SELECT id, amt_dim, rev FROM items) q"
CASES = [
 dict(id="row_values_control", sql="SELECT id, rev FROM items ORDER BY 1", expected=[[1,100],[2,200],[3,300],[4,0],[5,-50],[6,None]],
      why="A measure column with no GROUP BY. Each row has its own amount, so a per-row reading of the column is what Cube itself returns."),
 dict(id="gt_between", sql=f"SELECT SUM(amt_dim) s, COUNT(*) c FROM {Q} WHERE rev > 150", expected=[[500,2]], why="Rows 2 and 3 are above 150.", truth="SELECT sum(amt), count(*) FROM audit.items WHERE amt > 150"),
 dict(id="gt_exact_bound", sql=f"SELECT SUM(amt_dim) s, COUNT(*) c FROM {Q} WHERE rev > 200", expected=[[300,1]], why="Row 2 is on the strict bound. Only row 3."),
 dict(id="ge_exact_bound", sql=f"SELECT SUM(amt_dim) s, COUNT(*) c FROM {Q} WHERE rev >= 200", expected=[[500,2]], why="Rows 2 and 3."),
 dict(id="above_each_row_below_total", sql=f"SELECT SUM(amt_dim) s, COUNT(*) c FROM {Q} WHERE rev > 400", expected=[[None,0]], why="No row is above 400. The total of all rows, 550, is. SUM of no rows is NULL, COUNT is 0.", truth="SELECT sum(amt), count(*) FROM audit.items WHERE amt > 400"),
 dict(id="above_total", sql=f"SELECT SUM(amt_dim) s, COUNT(*) c FROM {Q} WHERE rev > 1000", expected=[[None,0]], why="Nothing is above 1000. An aggregate with no GROUP BY gives one row."),
 dict(id="zero_value", sql=f"SELECT SUM(amt_dim) s, COUNT(*) c FROM {Q} WHERE rev = 0", expected=[[0,1]], why="Row 4 only."),
 dict(id="negative_value", sql=f"SELECT SUM(amt_dim) s, COUNT(*) c FROM {Q} WHERE rev < 0", expected=[[-50,1]], why="Row 5 only."),
 dict(id="null_value", sql=f"SELECT COUNT(*) c FROM {Q} WHERE rev IS NULL", expected=[[1]], why="Row 6 only."),
 dict(id="breakout", sql=f"SELECT rev, COUNT(*) c FROM {Q} GROUP BY 1 ORDER BY 1 NULLS LAST", expected=[[-50,1],[0,1],[100,1],[200,1],[300,1],[None,1]], why="Six rows with six values."),
 dict(id="breakout_plain_order", sql=f"SELECT rev, COUNT(*) c FROM {Q} WHERE rev IS NOT NULL GROUP BY 1 ORDER BY 1", expected=[[-50,1],[0,1],[100,1],[200,1],[300,1]], why="The same breakout with a filter on the column and a plain ORDER BY. Rows with an amount."),
 dict(id="breakout_no_filter_plain", sql=f"SELECT rev, COUNT(*) c FROM {Q} GROUP BY 1 ORDER BY 1", expected=[[-50,1],[0,1],[100,1],[200,1],[300,1],[None,1]], why="Six rows with six values. NULL sorts last in ascending order, as in Postgres."),
 dict(id="breakout_cube_with_time_dim", sql="SELECT rev, COUNT(*) c FROM (SELECT id, amt_dim, rev FROM items_t) q GROUP BY 1 ORDER BY 1", expected=[[-50,1],[0,1],[100,1],[200,1],[300,1],[None,1]], why="The findings report this form as one row with the total. This cube has a time dimension, as the findings cube has. The docs give six rows."),
 dict(id="count_measure", sql="SELECT SUM(amt_dim) s, COUNT(*) c FROM (SELECT id, amt_dim, n FROM items) q WHERE n > 0", expected=[[550,6]], why="Per row, a count measure is 1. SUM ignores the NULL."),
 dict(id="avg_measure", sql="SELECT SUM(amt_dim) s, COUNT(*) c FROM (SELECT id, amt_dim, avg_amt FROM items) q WHERE avg_amt > 150", expected=[[500,2]], why="Per row, the avg is the amount."),
 dict(id="raw_rows_refused", sql=f"SELECT id, amt_dim, rev FROM {Q} WHERE rev > 150 ORDER BY 1", expected=[[2,200,200],[3,300,300]], why="The same filter with no outer aggregate: rows 2 and 3."),
 dict(id="grouped_inner_control", sql="SELECT SUM(amt_dim) s, COUNT(*) c FROM (SELECT id, amt_dim, MEASURE(rev) AS rev FROM items GROUP BY 1, 2) q WHERE rev > 150", expected=[[500,2]], why="MEASURE and GROUP BY by the primary key: one row per id. The docs form."),
 dict(id="direct_having_control", sql="SELECT cat, MEASURE(n) c FROM items GROUP BY 1 HAVING MEASURE(rev) > 250 ORDER BY 1", expected=[["a",2]], why="Totals per category: a 300, b 250, c NULL. Only a is above 250."),
 dict(id="direct_where_measure", sql="SELECT cat, MEASURE(n) c FROM items WHERE rev > 250 GROUP BY 1 ORDER BY 1", expected=[["a",2]], why="WHERE on a measure in a grouped query. The REST filter on a measure is HAVING, so the same result as direct_having_control."),
 dict(id="rest_ungrouped_measure_filter", rest=dict(dimensions=["items.id"], measures=["items.rev"], ungrouped=True, filters=[dict(member="items.rev", operator="gt", values=["150"])]), expected_error="Measure filters aren't allowed", why="REST refuses this combination, so an error is the behaviour that Cube has chosen for it."),
]
TAGS = ["v1.6.76", "v1.0.0"]
TAG_CASES = ["gt_between", "above_each_row_below_total"]
TAG_CONFIGS = ["pg-off"]
