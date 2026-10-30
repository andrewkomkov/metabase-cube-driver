"""propagate: two sub_query dimensions with the same SQL, one with propagate_filters_to_sub_query."""
MODEL = """
cubes:
  - name: purchases
    sql_table: audit.purchases
    joins:
      - { name: customers, relationship: many_to_one, sql: "{CUBE}.customer_id = {customers.id}" }
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: st, sql: st, type: string }
    measures:
      - { name: n, type: count }
      - { name: total, sql: amt, type: sum }
  - name: customers
    sql_table: audit.customers
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: all_n, sql: "{purchases.n}", type: number, sub_query: true }
      - { name: all_n2, sql: "{purchases.n}", type: number, sub_query: true }
      - { name: filt_n, sql: "{purchases.n}", type: number, sub_query: true, propagate_filters_to_sub_query: true }
      - { name: filt_n2, sql: "{purchases.n}", type: number, sub_query: true, propagate_filters_to_sub_query: true }
      - { name: filt_other, sql: "{purchases.n} + 0", type: number, sub_query: true, propagate_filters_to_sub_query: true }
      - { name: all_total, sql: "{purchases.total}", type: number, sub_query: true }
      - { name: filt_total, sql: "{purchases.total}", type: number, sub_query: true, propagate_filters_to_sub_query: true }
views:
  - name: cv
    cubes:
      - { join_path: customers, includes: [id, all_n, filt_n, filt_other] }
      - { join_path: customers.purchases, includes: [st] }
"""
CUST = "(1),(2),(3)"
PUR = "(1,1,'paid',10),(2,1,'paid',20),(3,1,'open',40),(4,2,'open',80)"
SEED_CH = f"CREATE TABLE audit.customers (id UInt32) ENGINE = MergeTree ORDER BY id;\nCREATE TABLE audit.purchases (id UInt32, customer_id UInt32, st LowCardinality(String), amt Int32) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.customers VALUES {CUST};\nINSERT INTO audit.purchases VALUES {PUR};\n"
SEED_PG = f"CREATE TABLE audit.customers (id int);\nCREATE TABLE audit.purchases (id int, customer_id int, st text, amt int);\nINSERT INTO audit.customers VALUES {CUST};\nINSERT INTO audit.purchases VALUES {PUR};\n"
MODEL = MODEL.replace("  - name: customers\n", "  - name: customers\n    joins:\n      - { name: purchases, relationship: one_to_many, sql: \"{CUBE}.id = {purchases}.customer_id\" }\n", 1).replace("""    joins:
      - { name: customers, relationship: many_to_one, sql: "{CUBE}.customer_id = {customers.id}" }
""", "", 1)
def rq(dims, st="paid"):
    q = dict(dimensions=["customers.id"] + ["customers."+d for d in dims], order={"customers.id":"asc"})
    if st:
        q["filters"] = [dict(member="purchases.st", operator="equals", values=[st])]
    return q
CASES = [
 dict(id="propagated_alone", rest=rq(["filt_n"]), expected=[[1,2]], why="Customer 1 has 2 paid purchases, no one else has any.", truth="SELECT customer_id, count(*) FROM audit.purchases WHERE st = 'paid' GROUP BY 1"),
 dict(id="plain_alone", rest=rq(["all_n"]), expected=[[1,3]], why="Customer 1 passes the filter. The dimension with no propagation counts all 3 of the purchases.", truth="SELECT count(*) FROM audit.purchases WHERE customer_id = 1"),
 dict(id="plain_then_propagated", rest=rq(["all_n","filt_n"]), expected=[[1,3,2]], why="All 3, of which 2 are paid. Each dimension keeps its meaning next to the other."),
 dict(id="propagated_then_plain", rest=rq(["filt_n","all_n"]), expected=[[1,2,3]], why="The same with the columns swapped. Column order must not change a value."),
 dict(id="sums_same_sql", rest=rq(["all_total","filt_total"]), expected=[[1,70,30]], why="All: 10+20+40. Paid: 10+20. The same pair with sum."),
 dict(id="other_sql", rest=rq(["all_n","filt_other"]), expected=[[1,3,2]], why="SQL that differs by + 0, with the same meaning."),
 dict(id="different_measures", rest=rq(["all_total","filt_n"]), expected=[[1,70,2]], why="Different measures: no shared SQL."),
 dict(id="two_propagated_control", rest=rq(["filt_n","filt_n2"]), expected=[[1,2,2]], why="Both with propagation, so sharing a subquery gives the right number."),
 dict(id="two_plain_control", rest=rq(["all_n","all_n2"]), expected=[[1,3,3]], why="Both with no propagation."),
 dict(id="other_status", rest=rq(["all_n","filt_n"],"open"), expected=[[1,3,1],[2,1,1]], why="Open: customer 1 has 1 of 3, customer 2 has 1 of 1."),
 dict(id="no_match", rest=rq(["all_n","filt_n"],"nope"), expected=[], why="No purchase has this status, so no customer passes."),
 dict(id="no_filter_control", rest=rq(["all_n","filt_n"],None), expected=[[1,3,3],[2,1,1],[3,0,0]], why="With no filter, both are the same. Customer 3 has zero purchases. The subquery left-joins from customers, so the count is 0."),
 dict(id="sql_view_together", sql="SELECT id, all_n, filt_n FROM cv WHERE st = 'paid' GROUP BY 1, 2, 3 ORDER BY 1", expected=[[1,3,2]], why="The matrix claim: in one SQL API query."),
 dict(id="sql_view_swapped", sql="SELECT id, filt_n, all_n FROM cv WHERE st = 'paid' GROUP BY 1, 2, 3 ORDER BY 1", expected=[[1,2,3]], why="The same with the columns swapped."),
 dict(id="sql_view_other_sql", sql="SELECT id, all_n, filt_other FROM cv WHERE st = 'paid' GROUP BY 1, 2, 3 ORDER BY 1", expected=[[1,3,2]], why="SQL that differs by + 0."),
]
TAGS = ["v1.5.0", "v1.6.76", "v1.7.0"]
TAG_CASES = ["plain_then_propagated", "other_sql"]
TAG_CONFIGS = ["pg-on"]
