"""geo: a geo dimension in a view."""
MODEL = """
cubes:
  - name: places
    sql_table: audit.places
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: name, sql: name, type: string }
      - { name: lat, sql: lat, type: number }
      - { name: lon, sql: lon, type: number }
      - name: loc
        type: geo
        latitude: { sql: "{CUBE}.lat" }
        longitude: { sql: "{CUBE}.lon" }
    measures:
      - { name: n, type: count }
views:
  - name: v_list
    cubes:
      - { join_path: places, includes: [id, loc, n] }
  - name: v_star
    cubes:
      - { join_path: places, includes: "*" }
  - name: v_alias
    cubes:
      - join_path: places
        includes:
          - id
          - { name: loc, alias: where_at }
  - name: v_prefix
    cubes:
      - { join_path: places, prefix: true, includes: [id, loc] }
  - name: v_nogeo
    cubes:
      - { join_path: places, includes: [id, name, lat, lon] }
"""
ROWS = "(1,'x',10.5,20.5),(2,'y',-30.25,0)"
SEED_CH = f"CREATE TABLE audit.places (id UInt32, name String, lat Float64, lon Float64) ENGINE = MergeTree ORDER BY id;\nINSERT INTO audit.places VALUES {ROWS};\n"
SEED_PG = f"CREATE TABLE audit.places (id int, name text, lat float8, lon float8);\nINSERT INTO audit.places VALUES {ROWS};\n"
LOC = [[1,"10.5,20.5"],[2,"-30.25,0"]]
W = "The dimension is the latitude and the longitude with a comma, as the cube itself returns it."
def rq(v, loc="loc"):
    return dict(dimensions=[f"{v}.id", f"{v}.{loc}"], order={f"{v}.id":"asc"})
CASES = [
 dict(id="cube_rest_control", rest=rq("places"), expected=LOC, why=W + " Row 1: 10.5 and 20.5. Row 2: -30.25 and 0.", truth="SELECT id, lat, lon FROM audit.places ORDER BY 1"),
 dict(id="cube_sql_control", sql="SELECT id, loc FROM places ORDER BY 1", expected=LOC, why=W),
 dict(id="view_list_rest", rest=rq("v_list"), expected=LOC, why=W + " The docs have no exclusion of geo in views."),
 dict(id="view_star_rest", rest=rq("v_star"), expected=LOC, why=W + " With includes: *."),
 dict(id="view_alias_rest", rest=rq("v_alias","where_at"), expected=LOC, why=W + " With an alias."),
 dict(id="view_prefix_rest", rest=dict(dimensions=["v_prefix.places_id","v_prefix.places_loc"], order={"v_prefix.places_id":"asc"}), expected=LOC, why=W + " With a prefix."),
 dict(id="view_list_sql", sql="SELECT id, loc FROM v_list ORDER BY 1", expected=LOC, why=W + " Through the SQL API."),
 dict(id="view_geo_only", rest=dict(dimensions=["v_list.loc"], order={"v_list.loc":"asc"}), expected=[["-30.25,0"],["10.5,20.5"]], why=W + " No other member."),
 dict(id="view_geo_and_measure", rest=dict(dimensions=["v_list.loc"], measures=["v_list.n"], order={"v_list.loc":"asc"}), expected=[["-30.25,0",1],["10.5,20.5",1]], why=W + " With a count per place."),
 dict(id="view_measure_only_control", rest=dict(measures=["v_list.n"]), expected=[[2]], why="The view has a geo member, but this query does not use it."),
 dict(id="view_id_only_control", rest=dict(dimensions=["v_list.id"], order={"v_list.id":"asc"}), expected=[[1],[2]], why="The same."),
 dict(id="view_lat_lon_workaround", rest=dict(dimensions=["v_nogeo.id","v_nogeo.lat","v_nogeo.lon"], order={"v_nogeo.id":"asc"}), expected=[[1,10.5,20.5],[2,-30.25,0]], why="The two numbers as separate members of a view."),
 dict(id="meta_lists_member", meta="v_list", expected=[["v_list.loc","geo"]], why="/v1/meta offers the member, so a client can pick it."),
]
TAGS = ["v1.6.76", "v1.0.0"]
TAG_CASES = ["cube_rest_control", "view_list_rest"]
TAG_CONFIGS = ["pg-off"]
