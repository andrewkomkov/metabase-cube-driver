"""The repeated hour on Postgres and on Cube. tz_events has no row in that hour, so this adds two rows on Postgres for one query, and removes them."""
import subprocess

import lib
import run_grid


def psql(sql):
    subprocess.run(["docker", "exec", "-i", "mcd-review-postgres-1", "psql", "-q", "-U", "shop", "-d", "shop", "-c", sql], check=True)


mb = lib.Metabase().login()
mb.api("PUT", "/api/setting/report-timezone", {"value": "America/New_York"})
pg = [d for d in mb.api("GET", "/api/database")["data"] if d["name"] == "PG"][0]["id"]
t = [x for x in mb.api("GET", "/api/database/%d/metadata?include_hidden=true" % pg)["tables"] if x["name"] == "tz_events"][0]
ts = [f for f in t["fields"] if f["name"] == "ts"][0]["id"]
psql("INSERT INTO tz_events VALUES (-1, '2025-11-02 05:30:00+00', '2025-11-02 05:30:00', '2025-11-02 05:30:00+00', 'x', 1), (-2, '2025-11-02 06:30:00+00', '2025-11-02 06:30:00', '2025-11-02 06:30:00+00', 'x', 2)")
try:
    res = mb.api("POST", "/api/dataset", {"database": pg, "type": "query", "query": {"source-table": t["id"], "aggregation": [["count"]], "breakout": [["field", ts, {"temporal-unit": "hour"}]], "filter": ["=", ["field", [f for f in t["fields"] if f["name"] == "status"][0]["id"], None], "x"]}})
    print("PG", res["data"]["rows"])
finally:
    psql("DELETE FROM tz_events WHERE id < 0")
mb.api("PUT", "/api/setting/report-timezone", {"value": None})
print("July revenue", lib.ch("SELECT sum(amount) FROM orders WHERE toStartOfMonth(created_at) = '2025-07-01'"))
