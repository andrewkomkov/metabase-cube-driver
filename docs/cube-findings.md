# Cube findings

Each claim in this repository that says Cube has a defect or a limit, checked with a clean repro and the Cube source. No entry uses Metabase or the driver. Only the Cube SQL API, the Cube REST API and direct SQL on the database produce the output.

## How this was checked

- **Versions:** Cube `v1.8.15`, ClickHouse `26.10.3-alpine`, Postgres `17-alpine`. On 2026-10-30, `v1.8.15` is the newest tag by version number, so the pinned run and the newest run are the same. GitHub marks `v1.6.76` as the latest release, but that is a backport. `master` has 20 more commits. I read their titles and the text of the two that touch joins, #12483 and #12476. Neither names a case here. I did not run `master`.
- **Configs:** `ch` is ClickHouse and `pg` is Postgres. `on` and `off` are `CUBEJS_TESSERACT_SQL_PLANNER`. The default in v1.8.15 is on, and the demo uses that. The output shows configs that give the same result in one group. A security or read-only entry ran only where the database does not matter, and `claim.json` lists its configs.
- **Stack:** the compose project `mcd-repro` on `nettop.local`, in `~/mcd-repro`, with ports 4500 and 4501 on the loopback interface. I copied the files with `rsync`, so the checkout `~/metabase-cube-driver` did not change. I removed the project, its volumes and the directory after the runs.
- **Source:** a read-only clone of `cube-js/cube` at `v1.8.15`. Links go to this tag.
- **Issues:** about 250 searches on `cube-js/cube`, with several forms of each query. "None found" means no match in these searches, not proof that no report exists.

## Run

```sh
cd dev/repro
python3 run.py B1-strict-lower-bound     # all configs of this claim, writes results/
python3 run.py --config ch-on B1-strict-lower-bound
python3 run.py --down                    # removes the stack and its volumes
```

`run.py` waits while available memory is below 1500 MB. Each directory holds `model/`, `seed.clickhouse.sql`, `seed.postgres.sql`, `claim.json` with the exact queries, and `results/` with the full output, including `EXPLAIN` and the generated SQL. The output below comes from these files. It removes the stack traces and shortens long lines, shown as ` ...`.

The verdicts follow one rule: a `bug` verdict means that the result contradicts the docs or the meaning of SQL, and the code path is shown.

## Result

65 claims.

| Verdict | Claims |
| --- | --- |
| `cube-bug` | 12 |
| `cube-clickhouse-bug` | 9 |
| `documented-behaviour` | 29 |
| `unclear-intent` | 7 |
| `our-demo-model` | 3 |
| `our-driver` | 1 |
| `not-reproduced` | 4 |

| Entry | Claims | Verdict |
| --- | --- | --- |
| [B1](#b1) | review B1 | `cube-clickhouse-bug` |
| [B2](#b2) | review B2 | `cube-bug` |
| [B3](#b3) | review B3 | `our-driver` |
| [B4](#b4) | review B4 | `cube-bug` |
| [B5](#b5) | review B5 | `our-demo-model` |
| [B6](#b6) | review B6, `measure.rolling.native_sql_limit` | `unclear-intent` |
| [B7](#b7) | review B7 | `documented-behaviour` |
| [M1](#m1) | review M1 | `cube-bug` |
| [M2](#m2) | review M2 | `cube-bug` |
| [M3](#m3) | review M3 | `not-reproduced` |
| [m1 (minor)](#m1-minor) | review m1 | `unclear-intent` |
| [m2 (minor)](#m2-minor) | review m2 | `documented-behaviour` |
| [m3 (minor)](#m3-minor) | review m3 | `cube-bug` |
| [m4 (minor)](#m4-minor) | review m4 | `cube-bug` |
| [m5 (minor)](#m5-minor) | review m5, `preagg.original_sql` | `cube-clickhouse-bug` |
| [m6 (minor)](#m6-minor) | review m6 | `documented-behaviour` |
| [unbounded](#unbounded) | `measure.rolling.unbounded`, review m4 (first part) | `cube-clickhouse-bug` |
| [geo](#geo) | `dimension.type.geo.view` | `cube-bug` |
| [gran](#gran) | `granularity.custom.offset` | `cube-bug` |
| [rollup_join](#rollup_join) | `preagg.rollup_join` | `not-reproduced` |
| [rollup_lambda](#rollup_lambda) | `preagg.rollup_lambda` | `our-demo-model` |
| [rest_only](#rest_only) | `restapi.compare_date_range`, `restapi.total_query` | `documented-behaviour` |
| [percentile](#percentile) | `mb.agg.median`, `mb.agg.percentile` | `documented-behaviour` |
| [timezone](#timezone) | `mb.report_timezone`, docs/query-compilation.md: `SET TIME ZONE` has no effect | `not-reproduced` |
| [readonly](#readonly) | `mb.other.actions`, `mb.other.model_persistence`, `mb.other.uploads` | `documented-behaviour` |
| [min_time](#min_time) | `semantic.measure.min_on_time_column` | `our-demo-model` |
| [big](#big) | `compile.ch.uint64_above_double`, `compile.ch.decimal_above_double` | `cube-bug` |
| [propagate](#propagate) | `dimension.opt.propagate_filters_next_to_another_sub_query` | `cube-bug` |
| [time_shift](#time_shift) | `measure.multi_stage.time_shift_no_bucket_limit` | `unclear-intent` |
| [datetime64](#datetime64) | `clickhouse.datetime64_millis` | `cube-clickhouse-bug` |
| [rolling_type](#rolling_type) | `measure.opt.rolling_type_deprecated` | `documented-behaviour` |
| [impersonation](#impersonation) | `security.sql_user_impersonation`, docs/security.md: the table of SET user | `documented-behaviour` |
| [meta_cloud](#meta_cloud) | `view.meta_auto_run`, `view.meta_default_ui_filters` | `documented-behaviour` |
| [nonstrict](#nonstrict) | docs/query-compilation.md: required settings, first row | `documented-behaviour` |
| [limit](#limit) | docs/query-compilation.md: required settings, second row | `documented-behaviour` |
| [stream](#stream) | docs/query-compilation.md: `CUBESQL_STREAM_MODE` | `unclear-intent` |
| [join_on](#join_on) | docs/query-compilation.md: join on columns, docs/semantic-sync.md: Cube ignores the condition | `documented-behaviour` |
| [join_sub](#join_sub) | docs/query-compilation.md: join of a question | `unclear-intent` |
| [left_join](#left_join) | docs/semantic-sync.md and docs/query-compilation.md: left join direction, review m11 (first item) | `documented-behaviour` |
| [datediff](#datediff) | docs/query-compilation.md: datetime-diff in months | `documented-behaviour` |
| [interval](#interval) | docs/query-compilation.md: datetime-add in an aggregated query and the week section | `cube-clickhouse-bug` |
| [extract](#extract) | docs/query-compilation.md: EXTRACT next to a measure that is not additive | `cube-clickhouse-bug` |
| [rolling_range](#rolling_range) | docs/query-compilation.md: `Date range is required for time series` | `documented-behaviour` |
| [other_agg](#other_agg) | docs/query-compilation.md: `Measure aggregation type doesn't match` | `documented-behaviour` |
| [cda](#cda) | docs/query-compilation.md: count_distinct_approx | `documented-behaviour` |
| [nonadditive](#nonadditive) | docs/query-compilation.md: measures that are not additive | `documented-behaviour` |
| [meta_view](#meta_view) | docs/semantic-sync.md: two limits | `documented-behaviour` |
| [week](#week) | docs/query-compilation.md: week section | `documented-behaviour` |
| [jwt_dev](#jwt_dev) | docs/security.md: dev mode | `documented-behaviour` |
| [jwt_log](#jwt_log) | docs/security.md: the JWT in the log | `unclear-intent` |
| [N1](#n1) | new finding, from B6 | `cube-bug` |
| [N2](#n2) | new finding, from B6 | `cube-clickhouse-bug` |

## Verdicts that differ from the matrix or the review

| Entry | Before | Now |
| --- | --- | --- |
| [B1](#b1) | Review layer: Cube SQL API. Actual layer: ClickHouse dialect. | `cube-clickhouse-bug` |
| [B4](#b4) | Review truth for the direct form: no rows. I read this form as a filter on the aggregate, with unclear intent. | `cube-bug` |
| [B5](#b5) | Review layer: Cube. Actual: the model must use `alias`, and #8896 tracks the missing error. | `our-demo-model` |
| [B6](#b6) | Matrix and review: wrong number from Cube. Verdict here: unclear intent for no time dimension, and defined behaviour for the rest. | `unclear-intent` |
| [B7](#b7) | Review: a blocker from Cube. Verdict here: documented padding that reaches the SQL API. | `documented-behaviour` |
| [M3](#m3) | Review: reproduced through MCP, with `COALESCE` as the cause. Here `COALESCE` alone is not enough. | `not-reproduced` |
| [m5 (minor)](#m5-minor) | Matrix: rollup build. Actual: any `original_sql` with an index, also with no rollup. | `cube-clickhouse-bug` |
| [gran](#gran) | Matrix cause: offset in months. Actual cause: a time before the origin in Cube Store. | `cube-bug` |
| [rollup_join](#rollup_join) | Matrix: blocked, needs two data sources. | `not-reproduced` |
| [rollup_lambda](#rollup_lambda) | Matrix: blocked, not modelled. It needs only a model. | `our-demo-model` |
| [timezone](#timezone) | Matrix: blocked by Cube. Actual: the driver can do this. `mb.report_timezone` is `our-driver` work. | `not-reproduced` |
| [min_time](#min_time) | Matrix: blocked by Cube. Actual: the model must use `type: time`, as the matrix already suggests. | `our-demo-model` |
| [big](#big) | Matrix cause: JavaScript. Actual: the Double type in cubesql, and JSON numbers in the ClickHouse driver. | `cube-bug` |
| [propagate](#propagate) | Matrix cause: the filter is ignored. Actual: both columns read the first subquery when the SQL is equal. | `cube-bug` |
| [time_shift](#time_shift) | Matrix: wrong number from Cube. | `unclear-intent` |
| [datetime64](#datetime64) | Matrix cause: Cube reads a time as whole seconds. Actual: only the ClickHouse dialect. | `cube-clickhouse-bug` |
| [impersonation](#impersonation) | Docs here: it cannot go back. Actual: it can, with a custom `canSwitchSqlUser`. | `documented-behaviour` |
| [join_on](#join_on) | Docs here: ignored. Actual: refused in the `ON` form, and a silent empty result in the `WHERE` form. | `documented-behaviour` |
| [join_sub](#join_sub) | Docs here: only on a whole cube. Actual: also on `SELECT *` of a cube. | `unclear-intent` |
| [interval](#interval) | Docs here: raw rows work. Actual: the `+ INTERVAL` form fails on raw rows too. | `cube-clickhouse-bug` |
| [extract](#extract) | Docs here: only a measure that is not additive. Actual: each measure on ClickHouse. | `cube-clickhouse-bug` |

## Fix path with no fork of Cube

`dialectFactory` in `cube.js` can return a subclass of `ClickHouseQuery`. A package `x-cubejs-driver` with a `static dialectClass()` is the other form of this. `dev/repro/probe-clickhouse-subclass/cube.js` is a working example. Import the class from `@cubejs-backend/schema-compiler/dist/src/adapter/ClickHouseQuery`, because the package index does not export it.

| Defect | Subclass fixes it | Method | Tested |
| --- | --- | --- | --- |
| B1, strict lower bound | Yes | `dateTimeCast` | Yes |
| `datetime64`, milliseconds | Yes | `convertTz`, `dateTimeCast` | Yes |
| `interval` | Yes | `sqlTemplates`: `expressions.interval_single_date_part` | Yes |
| `extract` | Yes | `sqlTemplates`: `expressions.extract` | Yes |
| `percentile` | Yes | `sqlTemplates`: `functions.PERCENTILECONT`, `expressions.within_group` | Yes |
| `m5`, `original_sql` | Yes, it drops the index | `preAggregationLoadSql`, `indexSql` | Yes |
| `N2`, legacy rolling | Possible | `seriesSql` | No |
| `big`, on REST only | Possible, in a `ClickHouseDriver` subclass | `clickhouse_settings` | No |
| `unbounded` | No, the join comes from Rust. Use `trailing: 100 year` | None | The probe still fails, the workaround works |
| B2, B4, M1, M2, `m4`, `propagate`, `N1`, `big` on the SQL API | No, the cause is in Rust | None | None |
| `geo` | No, the cause is in the data model compiler | None | None |
| `gran` | No, it is in Cube Store. Use `origin` before all rows | None | The workaround works |
| `m3`, count on a rollup | Not in `ClickHouseQuery`, it runs in Cube Store | Not tested | No |

Each subclass change above ran with `python3 run.py probe-clickhouse-subclass`:

```text
# b1_strict_bound
$ psql> SELECT MEASURE(count) c, MEASURE(revenue) r FROM orders WHERE created_at > '2025-03-15 05:00:00' AND created_at <= '2025-03-16 05:00:00'
[ch-off, ch-on]
c | r
2 | 1100
# millis_raw
$ psql> SELECT id, ts FROM events ORDER BY 1
[ch-off, ch-on]
id | ts
1 | 2025-01-01 10:00:00.007000
2 | 2025-01-01 10:00:00.500000
# millis_filter
$ psql> SELECT MEASURE(count) c FROM events WHERE ts >= '2025-01-01 10:00:00.007' AND ts < '2025-01-01 10:00:00.008'
[ch-off, ch-on]
c
1
# interval_aggregated
$ psql> SELECT created_at + INTERVAL '1 day' d, MEASURE(count) c FROM orders GROUP BY 1 ORDER BY 1
[ch-off, ch-on]
d | c
2025-03-15 12:00:00.000000 | 1
2025-03-16 05:00:00.000000 | 1
2025-03-16 05:00:01.000000 | 1
2025-03-17 05:00:00.000000 | 1
2025-03-17 05:00:01.000000 | 1
# extract_avg
$ psql> SELECT EXTRACT(MONTH FROM created_at) m, MEASURE(avg_amount) a FROM orders GROUP BY 1 ORDER BY 1
[ch-off, ch-on]
m | a
3 | 2222.2
# percentile_cont
$ psql> SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount_dim) p FROM orders
[ch-off, ch-on]
p
100
# unbounded_two_months
$ psql> SELECT DATE_TRUNC('month',created_at) m, MEASURE(cumulative_count) c FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-05-01' GROUP BY 1 ORDER BY 1
[ch-off]
psql:<stdin>:1: ERROR:  Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__table4.orders__created_at_month, __table2.date_to). ; query id: < ...
[ch-on]
psql:<stdin>:1: ERROR:  Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__table8.orders__created_at_month, __table2.date_to). ; query id: < ...
# original_sql_with_index
$ POST /cubejs-api/v1/load {"measures": ["base_orders.revenue"], "dimensions": ["base_orders.status"], "order": {"base_orders.status": "asc"}}
[ch-off, ch-on]
HTTP 200 [{"base_orders.status": "a", "base_orders.revenue": "11111"}]
```

Work that the driver of this repository can do from these results: send a date parameter with a cast, `?::timestamp`, or as a literal (M1, M2). Use `SET TIME ZONE` for the report time zone (`timezone`). Add the inner `GROUP BY` in a nested measure (B4). Put the date filter inside the bucket query (B2).

## Findings

### B1

**A strict lower date bound includes the bound**

- **Claims:** review B1
- **Verdict:** `cube-clickhouse-bug`
- **Claim text:** Review: the Cube SQL API reads the two bounds as one inclusive date range. The layer is the Cube SQL API.
- **Command:** `python3 run.py B1-strict-lower-bound`

**Output:**

```text
# gt_and_lte_text
$ psql> SELECT MEASURE(count) c, MEASURE(revenue) r FROM orders WHERE created_at > '2025-03-15 05:00:00' AND created_at <= '2025-03-16 05:00:00'
[ch-off, ch-on]
c | r
3 | 1110
[pg-off, pg-on]
c | r
2 | 1100
# gt_and_lt
$ psql> SELECT MEASURE(count) c, MEASURE(revenue) r FROM orders WHERE created_at > '2025-03-15 05:00:00' AND created_at < '2025-03-16 05:00:00'
[ch-off, ch-on]
c | r
2 | 110
[pg-off, pg-on]
c | r
1 | 100
# gte_next_second
$ psql> SELECT MEASURE(count) c, MEASURE(revenue) r FROM orders WHERE created_at >= '2025-03-15 05:00:01' AND created_at <= '2025-03-16 05:00:00'
[ch-off, ch-on, pg-off, pg-on]
c | r
2 | 1100
# ch_parse_drops_millis
$ clickhouse> SELECT parseDateTimeBestEffort('2025-03-15T05:00:00.001Z') a, parseDateTime64BestEffort('2025-03-15T05:00:00.001Z', 3) b
[ch-off, ch-on]
a	b
2025-03-15 05:00:00	2025-03-15 05:00:00.001
# ch_cube_where_directly
$ clickhouse> SELECT count(*) c, sum(amount) r FROM repro.orders WHERE created_at >= parseDateTimeBestEffort('2025-03-15T05:00:00.001Z') AND created_at <= parseDateTimeBestEffort('2025-03-16T05:00:00.000Z')
[ch-off, ch-on]
c	r
3	1110
# ch_fixed_where
$ clickhouse> SELECT count(*) c, sum(amount) r FROM repro.orders WHERE created_at >= parseDateTime64BestEffort('2025-03-15T05:00:00.001Z', 3) AND created_at <= parseDateTime64BestEffort('2025-03-16T05:00:00.000Z', 3)
[ch-off, ch-on]
c	r
2	1100
```

**Truth:** `truth` step: count 2, sum 1100, the same on both databases.

**Why this verdict:** Postgres gives the truth with the same request, and running the generated `WHERE` on ClickHouse gives the wrong row. With `parseDateTime64BestEffort` the same `WHERE` gives the truth. The cause is in the ClickHouse dialect, not in the SQL API rewrite. The review named the wrong layer.

**Code:**

- [`rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs:5378`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs#L5378): `resolve_time_delta` makes the lower bound of `afterDate` one millisecond later, `...05:00:00.001Z`. This is correct.
- [`packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts:163-175`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L163): `dateTimeCast` uses `parseDateTimeBestEffort`, which returns `DateTime` with whole seconds, so `.001` is lost and `>=` includes the bound.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/rest-api/query-format.mdx:642`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/rest-api/query-format.mdx#L642): a date range uses `>=` and `<=`. The inclusive REST result `rest_inDateRange` is documented.
- [`docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx:385`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L385): time filters become date ranges when possible.

**Issues:**

- [#10326](https://github.com/cube-js/cube/issues/10326) (open): the same cause, `parseDateTimeBestEffort` truncates to seconds. It is reported through `build_range_end`.
- [#2536](https://github.com/cube-js/cube/issues/2536) (open): partial, the same function on `DateTime64`.
- [#11585](https://github.com/cube-js/cube/issues/11585) (closed): close only: `afterDate` is strict, which agrees with `gt_only` here.
- [PR #12231](https://github.com/cube-js/cube/pull/12231), merged and in v1.8.15, added `parseDateTime64BestEffort` for filter values and `time_in`, but `dateRange` still goes through `dateTimeCast`, so it does not fix this.

**Fix layer and test:** `packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts`, `dateTimeCast`: return `parseDateTime64BestEffort(value, 3, 'UTC')`. Test idea: `gt_and_lte_text` with rows on the bound and one second later, in `clickhouse-time-filters.test.ts`.

**Subclass or package:** Yes, tested: override `dateTimeCast` in a `ClickHouseQuery` subclass, through `dialectFactory`. `probe-clickhouse-subclass`, step `b1_strict_bound`, gives 2 and 1100 on both planners.


<details><summary>Issue draft: ClickHouse: a strict lower time bound in the SQL API includes the bound</summary>

**Title:** ClickHouse: a strict lower time bound in the SQL API includes the bound

**Describe the bug**

`created_at > X AND created_at <= Y` returns the rows at `X`. The SQL API correctly sends `X` plus one millisecond, but the ClickHouse dialect parses it with `parseDateTimeBestEffort`, which drops milliseconds.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: clickhouse/clickhouse-server:26.10.3-alpine
    environment: { CLICKHOUSE_DB: repro, CLICKHOUSE_USER: repro, CLICKHOUSE_PASSWORD: repro, CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: "1" }
    healthcheck: { test: [CMD-SHELL, "clickhouse-client -u repro --password repro -q 'SELECT 1'"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: clickhouse
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "8123"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: orders
    sql_table: repro.orders
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: status, sql: status, type: string }
      - { name: amount_dim, sql: amount, type: number }
      - { name: created_at, sql: created_at, type: time }
    measures:
      - { name: count, type: count }
      - { name: revenue, sql: amount, type: sum }
```

`seed.sql`:

```sql
CREATE TABLE repro.orders (id UInt32, status String, amount Int32, created_at DateTime('UTC')) ENGINE = MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES
(1, 'a', 1, '2025-03-14 12:00:00'),
(2, 'a', 10, '2025-03-15 05:00:00'),
(3, 'a', 100, '2025-03-15 05:00:01'),
(4, 'a', 1000, '2025-03-16 05:00:00'),
(5, 'a', 10000, '2025-03-16 05:00:01');
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
docker compose exec -T db clickhouse-client -u repro --password repro --multiquery < seed.sql
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
docker run --rm --network host -e PGPASSWORD=repro postgres:17-alpine psql -h 127.0.0.1 -p 15432 -U repro -d repro -c "SELECT MEASURE(count) c, MEASURE(revenue) r FROM orders WHERE created_at > '2025-03-15 05:00:00' AND created_at <= '2025-03-16 05:00:00'"
```

**Expected behavior**

`c = 2`, `r = 1100`. Postgres gives this result with the same model.

**Actual behavior**

`c = 3`, `r = 1110`. It has the row at the excluded bound. There is no error.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

Cause: `ClickHouseQuery.dateTimeCast`. `SELECT parseDateTimeBestEffort('2025-03-15T05:00:00.001Z')` gives `2025-03-15 05:00:00`. `parseDateTime64BestEffort(value, 3, 'UTC')` keeps the value, and a subclass with this change gives the correct result. #12231 did not change this path. Related: #10326.

</details>


### B2

**An outer filter on a bucket changes the rows inside the bucket**

- **Claims:** review B2
- **Verdict:** `cube-bug`
- **Claim text:** Review: Cube pushes the outer filter below the aggregation.
- **Command:** `python3 run.py B2-outer-filter-pushdown`

**Output:**

```text
# outer_filter_alias_m
$ psql> SELECT m, revenue FROM (SELECT DATE_TRUNC('month', created_at) AS m, MEASURE(revenue) AS revenue FROM orders GROUP BY 1) s WHERE m >= '2025-05-15' ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
m | revenue
2025-05-01 00:00:00.000000 | 100
2025-06-01 00:00:00.000000 | 1000
# outer_sum
$ psql> SELECT SUM(revenue) FROM (SELECT DATE_TRUNC('month', created_at) AS m, MEASURE(revenue) AS revenue FROM orders GROUP BY 1) s WHERE m >= '2025-05-15'
[ch-off, ch-on, pg-off, pg-on]
SUM(s.revenue)
1100
# direct_where_date_trunc
$ psql> SELECT DATE_TRUNC('month', created_at) AS m, MEASURE(revenue) AS revenue FROM orders WHERE DATE_TRUNC('month', created_at) >= '2025-05-15' GROUP BY 1 ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
m | revenue
2025-06-01 00:00:00.000000 | 1000
# inner_only
$ psql> SELECT DATE_TRUNC('month', created_at) AS m, MEASURE(revenue) AS revenue FROM orders GROUP BY 1 ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
m | revenue
2025-04-01 00:00:00.000000 | 1
2025-05-01 00:00:00.000000 | 110
2025-06-01 00:00:00.000000 | 1000
# outer_rolling
$ psql> SELECT m, r FROM (SELECT DATE_TRUNC('month', created_at) AS m, MEASURE(revenue_trailing_30d) AS r FROM orders WHERE created_at >= '2025-04-01' AND created_at < '2025-07-01' GROUP BY 1) s WHERE m >= '2025-05-15' ORDER BY 1
[ch-off]
m | r
2025-04-01 00:00:00.000000 | 1100
2025-05-01 00:00:00.000000 | 1100
2025-06-01 00:00:00.000000 | 1100
[ch-on, pg-off, pg-on]
m | r
2025-04-01 00:00:00.000000 | 
2025-05-01 00:00:00.000000 | 100
2025-06-01 00:00:00.000000 | 1000
```

**Truth:** `truth` step: one row, `2025-06-01, 1000`. May is out of the range, because `2025-05-01 < 2025-05-15`. The direct form gives this result.

**Why this verdict:** The result contradicts the SQL meaning, on both databases and both planners. The `EXPLAIN` in the results shows `afterOrOnDate 2025-05-15` on `orders.created_at`, and the granularity is gone. The inner query alone gives `2025-05-01, 110`, so May changes from 110 to 100.

**Code:**

- [`rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs:3811`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs#L3811): `// TODO: all other operators need special granularity handlers like Eq`. Only `=` reads the granularity.
- [`rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs:242`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs#L242): `push-down-filter-projection` moves the outer filter through the projection.
- [`rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs:5226`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs#L5226): `transform_binary_expr_date_trunc_column_with_literal` handles `date_trunc(col) >= literal` correctly, so the direct form works.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx:385`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L385): filters become date ranges. Nothing says that the meaning of the filter changes.

**Issues:**

- [#8344](https://github.com/cube-js/cube/issues/8344) (closed): an outer filter on a `date_trunc` alias goes to the date range. The symptom was no rows, and it was reported fixed in v0.35.58.

**Fix layer and test:** cubesql in Rust, `filters.rs`: keep the granularity for `>`, `>=`, `<`, `<=` as the Eq branch does, or reuse the rule at line 5226. Test idea: a snapshot of the CubeScan filter for `SELECT m FROM (SELECT DATE_TRUNC('month', d) m ...) WHERE m >= '2025-05-15'`.

**Subclass or package:** No. The cause is in cubesql. The driver can avoid it: put the filter inside the bucket query, or round the bound up to the next bucket.


<details><summary>Issue draft: SQL API: an outer filter on a DATE_TRUNC alias filters the rows inside the bucket</summary>

**Title:** SQL API: an outer filter on a DATE_TRUNC alias filters the rows inside the bucket

**Describe the bug**

An outer `WHERE` on a `DATE_TRUNC` alias of a subquery is pushed to the time dimension with no granularity. The bucket stays in the result with a part of its rows.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: postgres:17-alpine
    environment: { POSTGRES_DB: repro, POSTGRES_USER: repro, POSTGRES_PASSWORD: repro }
    healthcheck: { test: [CMD-SHELL, "pg_isready -U repro -d repro"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: postgres
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "5432"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: orders
    sql_table: repro.orders
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: status, sql: status, type: string }
      - { name: amount_dim, sql: amount, type: number }
      - { name: created_at, sql: created_at, type: time }
    measures:
      - { name: count, type: count }
      - { name: revenue, sql: amount, type: sum }
      - { name: revenue_trailing_30d, sql: amount, type: sum, rolling_window: { trailing: 30 day } }
```

`seed.sql`:

```sql
CREATE TABLE repro.orders (id int PRIMARY KEY, status text, amount int, created_at timestamp);
INSERT INTO repro.orders VALUES
(1, 'a', 1, '2025-04-15 00:00:00'),
(2, 'a', 10, '2025-05-01 00:00:00'),
(3, 'a', 100, '2025-05-20 00:00:00'),
(4, 'a', 1000, '2025-06-10 00:00:00');
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
(echo 'CREATE SCHEMA repro;'; cat seed.sql) | docker compose exec -T -e PGPASSWORD=repro db psql -U repro -d repro -q
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
docker run --rm --network host -e PGPASSWORD=repro postgres:17-alpine psql -h 127.0.0.1 -p 15432 -U repro -d repro -c "SELECT m, revenue FROM (SELECT DATE_TRUNC('month', created_at) AS m, MEASURE(revenue) AS revenue FROM orders GROUP BY 1) s WHERE m >= '2025-05-15' ORDER BY 1"
```

**Expected behavior**

One row: `2025-06-01, 1000`.

**Actual behavior**

Two rows: `2025-05-01, 100` and `2025-06-01, 1000`. The inner query alone gives `2025-05-01, 110`.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

`EXPLAIN` shows `afterOrOnDate 2025-05-15` with no granularity. The same condition with no subquery is correct. `filters.rs` has the comment `TODO: all other operators need special granularity handlers like Eq`. ClickHouse gives the same result.

</details>


### B3

**A rolling measure through a model gives the total**

- **Claims:** review B3
- **Verdict:** `our-driver`
- **Claim text:** Review: the check of the driver does not run for a column by name. The review does not name Cube as the layer.
- **Command:** `python3 run.py minor-m4-rolling-max`

**Output:**

```text
# sum_of_rolling
$ psql> SELECT SUM(revenue_trailing_30d) x FROM orders
[ch-off, ch-on, pg-off, pg-on]
x
111
# raw_rolling_column
$ psql> SELECT id, revenue_trailing_30d FROM orders ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
id | revenue_trailing_30d
1 | 1
2 | 10
3 | 100
```

**Truth:** The total of all rows is 111. A rolling window has no meaning with no time bucket, see the entry B6.

**Why this verdict:** Cube returns the same total for this query form. The driver has to stop the query, as the review says. I agree with the review.

**Code:**

- `check-rolling-measure!` in `src/metabase/driver/cube.clj`, in this repository.

**Issues:**

- none found


### B4

**An outer filter on a measure column has no effect**

- **Claims:** review B4
- **Verdict:** `cube-bug`
- **Claim text:** Review: the layer is the driver and Cube. `restore-nested-measures` makes the inner column `MEASURE(revenue)`, and Cube reads the outer filter as a filter on the aggregate.
- **Command:** `python3 run.py B4-filter-on-measure-column`

**Output:**

```text
# subq_ungrouped_filter
$ psql> SELECT SUM(amount_dim) s, COUNT(*) c FROM (SELECT id, amount_dim, revenue FROM orders) q WHERE revenue > 150
[ch-off, ch-on, pg-off, pg-on]
s | c
600 | 3
# subq_ungrouped_breakout
$ psql> SELECT revenue, COUNT(*) c FROM (SELECT id, amount_dim, revenue FROM orders) q GROUP BY 1 ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
revenue | c
600 | 3
# subq_ungrouped_rows
$ psql> SELECT id, amount_dim, revenue FROM (SELECT id, amount_dim, revenue FROM orders) q WHERE revenue > 150 ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
psql:<stdin>:1: ERROR:  Database Execution Error: Measure filters aren't allowed in ungrouped query
# subq_filter_grouped
$ psql> SELECT SUM(amount_dim) s, COUNT(*) c FROM (SELECT id, amount_dim, MEASURE(revenue) AS revenue FROM orders GROUP BY 1, 2) q WHERE revenue > 150
[ch-off, ch-on, pg-off, pg-on]
s | c
500 | 2
# subq_breakout
$ psql> SELECT revenue, COUNT(*) c FROM (SELECT id, amount_dim, MEASURE(revenue) AS revenue FROM orders GROUP BY 1, 2) q GROUP BY 1 ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
revenue | c
100 | 1
200 | 1
300 | 1
# subq_measure_no_group_filter
$ psql> SELECT SUM(amount_dim) s, COUNT(*) c FROM (SELECT id, status, amount_dim, created_at, MEASURE(revenue) AS revenue FROM orders) q WHERE q.revenue > 150
[ch-off, ch-on, pg-off, pg-on]
psql:<stdin>:1: ERROR:  Planning Error: Initial planning error: Error during planning: Expression #orders.id could not be resolved from available columns: #measure(orders.revenue)
QUERY: SELECT SUM(amount_dim) AS s, COUNT(*) AS c FROM (SELECT id, status, amount_dim, created_at, MEASURE(revenue) AS revenue FROM orders) q WHERE q.revenue > 150
# where_measure_by_status
$ psql> SELECT status, MEASURE(count) c FROM orders WHERE revenue > 250 GROUP BY 1 ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
status | c
a | 2
b | 1
# having_measure_by_status
$ psql> SELECT status, MEASURE(count) c FROM orders GROUP BY 1 HAVING MEASURE(revenue) > 250 ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
status | c
a | 2
b | 1
```

**Truth:** `truth_filter`: sum 500, count 2. A breakout has 3 groups. The `EXPLAIN` of `subq_ungrouped_filter` shows `HAVING (sum(amount) > ?)` with no group, so the aggregate of all rows is compared.

**Why this verdict:** The inner `SELECT id, amount_dim, revenue` gives one value per row, see `raw_rolling_column` in `minor-m4-rolling-max`, so SQL reads the outer filter per row. Cube compares the total. The number is wrong with no error, on both databases and both planners. Inner `MEASURE(revenue)` with `GROUP BY` gives the truth. Inner `MEASURE(revenue)` with no `GROUP BY` fails to plan. So the driver can avoid this by writing the inner `GROUP BY`. The direct `WHERE revenue > x` by status gives the HAVING result, and I do not call that a bug: a measure has no row value there, and the review's truth (no rows) assumed one.

**Code:**

- [`rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs:242`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs#L242): `push-down-filter-projection` moves the outer filter through the projection.
- [`rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs:3697`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs#L3697): the filter rule reads the column as the measure member, and the query then has a measure filter.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx): this page does not tell that a `WHERE` on a measure column is a `HAVING`. The REST filter on a measure is a `HAVING`, see `rest_measure_filter`.

**Issues:**

- none found. Close: #9774

**Fix layer and test:** cubesql in Rust: do not turn a filter over an ungrouped subquery into a measure filter. Stop the rewrite with an error, as `subq_ungrouped_rows` does. The driver can add the inner `GROUP BY`. Test idea: `subq_ungrouped_filter` must give 500 and 2 or fail.

**Subclass or package:** No. The cause is in cubesql.


<details><summary>Issue draft: SQL API: an outer filter on a measure column of an ungrouped subquery compares the total</summary>

**Title:** SQL API: an outer filter on a measure column of an ungrouped subquery compares the total

**Describe the bug**

The subquery selects a measure column per row. The outer `WHERE` on it becomes a `HAVING` on the sum of all rows, so the filter has no effect or removes all rows.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: postgres:17-alpine
    environment: { POSTGRES_DB: repro, POSTGRES_USER: repro, POSTGRES_PASSWORD: repro }
    healthcheck: { test: [CMD-SHELL, "pg_isready -U repro -d repro"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: postgres
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "5432"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: orders
    sql_table: repro.orders
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: status, sql: status, type: string }
      - { name: amount_dim, sql: amount, type: number }
      - { name: created_at, sql: created_at, type: time }
    measures:
      - { name: count, type: count }
      - { name: revenue, sql: amount, type: sum }
```

`seed.sql`:

```sql
CREATE TABLE repro.orders (id int PRIMARY KEY, status text, amount int, created_at timestamp);
INSERT INTO repro.orders VALUES
(1, 'a', 100, '2025-01-01 00:00:00'),
(2, 'a', 200, '2025-01-02 00:00:00'),
(3, 'b', 300, '2025-01-03 00:00:00');
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
(echo 'CREATE SCHEMA repro;'; cat seed.sql) | docker compose exec -T -e PGPASSWORD=repro db psql -U repro -d repro -q
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
docker run --rm --network host -e PGPASSWORD=repro postgres:17-alpine psql -h 127.0.0.1 -p 15432 -U repro -d repro -c "SELECT SUM(amount_dim) s, COUNT(*) c FROM (SELECT id, amount_dim, revenue FROM orders) q WHERE revenue > 150"
```

**Expected behavior**

`s = 500`, `c = 2`, as with `SELECT sum(amount), count(*) FROM repro.orders WHERE amount > 150`. An error is also acceptable: the same query that selects rows fails with `Measure filters aren't allowed in ungrouped query`.

**Actual behavior**

`s = 600`, `c = 3`. The generated SQL has `HAVING (sum(amount) > ?)`.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

The same subquery with `MEASURE(revenue)` and `GROUP BY` gives the correct result. A breakout on `revenue` of the ungrouped subquery gives one row `600, 3` and not three rows.

</details>


### B5

**A view with two join paths to one cube uses one path**

- **Claims:** review B5
- **Verdict:** `our-demo-model`
- **Claim text:** Review: the layer is Cube. The REST API gives the same wrong rows. The docs do not include this case.
- **Command:** `python3 run.py B5-two-join-paths`

**Output:**

```text
# rest_two_paths
$ POST /cubejs-api/v1/load {"measures": ["two_paths.count"], "dimensions": ["two_paths.customer_region", "two_paths.destination_region"], "order": {"two_paths.customer_region": "asc", "two_paths.destination_region": "asc"}}
[ch-off]
HTTP 200 [{"two_paths.customer_region": "South", "two_paths.destination_region": "South", "two_paths.count": "2"}, {"two_paths.customer_region": "West", "two_paths.destination_region": "West", "two_paths.count": "1"}]
SQL: SELECT
      `regions`.region `two_paths__customer_region`, `regions`.region `two_paths__destination_region`, count(`orders`.id) `two_paths__count`
    FROM
      repro.orders AS `orders`
LEFT JOIN repro.users AS `users` ON `orders`.user_id = `users`.id
LEFT JOIN repro.regions AS `regions` ON `users`.state = `regions`.state
LEFT JOIN repro.shipments AS `shipments` ON `orders`.id = `shipments`.order_id  GROUP BY `two_paths__customer_region`, `two_paths__destination_region` ORDER BY `two_paths__customer_region` ASC, `two_paths__destination_region` ASC LIMIT 10000
PARAMS: []
[ch-on]
HTTP 200 [{"two_paths.customer_region": "South", "two_paths.destination_region": "South", "two_paths.count": "2"}, {"two_paths.customer_region": "West", "two_paths.destination_region": "West", "two_paths.count": "1"}]
SQL: SELECT `regions`.region `two_paths__customer_region`, `regions`.region `two_paths__destination_region`, count(`orders`.id) `two_paths__count` 
FROM  repro.orders  AS `orders`
LEFT JOIN  repro.users  AS `users` ON `orders`.user_id = `users`.id
LEFT JOIN  repro.regions  AS `regions` ON `users`.state = `regions`.state
LEFT JOIN  repro.shipments  AS `shipments` ON `orders`.id = `shipments`.order_id
GROUP BY 1, 2
ORDER BY  1  ASC,  2  ASC
LIMIT 10000
PARAMS: []
[pg-off]
HTTP 200 [{"two_paths.customer_region": "South", "two_paths.destination_region": "South", "two_paths.count": "2"}, {"two_paths.customer_region": "West", "two_paths.destination_region": "West", "two_paths.count": "1"}]
SQL: SELECT
      "regions".region "two_paths__customer_region", "regions".region "two_paths__destination_region", count("orders".id) "two_paths__count"
    FROM
      repro.orders AS "orders"
LEFT JOIN repro.users AS "users" ON "orders".user_id = "users".id
LEFT JOIN repro.regions AS "regions" ON "users".state = "regions".state
LEFT JOIN repro.shipments AS "shipments" ON "orders".id = "shipments".order_id  GROUP BY 1, 2 ORDER BY 1 ASC, 2 ASC LIMIT 10000
PARAMS: []
[pg-on]
HTTP 200 [{"two_paths.customer_region": "South", "two_paths.destination_region": "South", "two_paths.count": "2"}, {"two_paths.customer_region": "West", "two_paths.destination_region": "West", "two_paths.count": "1"}]
SQL: SELECT "regions".region "two_paths__customer_region", "regions".region "two_paths__destination_region", count("orders".id) "two_paths__count" 
FROM  repro.orders  AS "orders"
LEFT JOIN  repro.users  AS "users" ON "orders".user_id = "users".id
LEFT JOIN  repro.regions  AS "regions" ON "users".state = "regions".state
LEFT JOIN  repro.shipments  AS "shipments" ON "orders".id = "shipments".order_id
GROUP BY 1, 2
ORDER BY  1  ASC,  2  ASC
LIMIT 10000
PARAMS: []
# rest_customer_only
$ POST /cubejs-api/v1/load {"measures": ["two_paths.count"], "dimensions": ["two_paths.customer_region"], "order": {"two_paths.customer_region": "asc"}}
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"two_paths.customer_region": "South", "two_paths.count": "2"}, {"two_paths.customer_region": "West", "two_paths.count": "1"}]
# rest_destination_only
$ POST /cubejs-api/v1/load {"measures": ["two_paths.count"], "dimensions": ["two_paths.destination_region"], "order": {"two_paths.destination_region": "asc"}}
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"two_paths.destination_region": "South", "two_paths.count": "2"}, {"two_paths.destination_region": "West", "two_paths.count": "1"}]
# rest_two_aliases
$ POST /cubejs-api/v1/load {"measures": ["two_aliases.count"], "dimensions": ["two_aliases.customer_region", "two_aliases.destination_region"], "order": {"two_aliases.customer_region": "asc", "two_aliases.destination_region": "asc"}}
[ch-off, pg-off]
HTTP 400 ERROR 'orders.billing_region.region' goes through a join alias, and join aliases require the Tesseract SQL planner
SQL ERROR: {"type":"UserError","error":"'orders.billing_region.region' goes through a join alias, and join aliases require the Tesseract SQL planner","stack":"Error: 'orders.billing_region.region' goes through a join alias, and join aliases require the Tessera ...
[ch-on, pg-on]
HTTP 200 [{"two_aliases.customer_region": "South", "two_aliases.destination_region": "South", "two_aliases.count": "1"}, {"two_aliases.customer_region": "South", "two_aliases.destination_region": "West", "two_aliases.count": "1"}, {"two_aliases.customer_region ...
# sql_two_aliases
$ psql> SELECT customer_region, destination_region, MEASURE(count) c FROM two_aliases GROUP BY 1, 2 ORDER BY 1, 2
[ch-off, pg-off]
psql:<stdin>:1: ERROR:  Database Execution Error: 'orders.billing_region.region' goes through a join alias, and join aliases require the Tesseract SQL planner
[ch-on, pg-on]
customer_region | destination_region | c
South | South | 1
South | West | 1
West | South | 1
```

**Truth:** `truth` step: `South, South, 1`, `South, West, 1`, `West, South, 1`. The join alias form gives this on Tesseract.

**Why this verdict:** The docs give `alias` for this case. With two aliases, the same data gives the truth, on both databases. The demo model has no alias. The silent wrong result is still poor: Cube could refuse two paths to one cube in one view, and #8896 tracks this. The demo should change the model. The legacy planner refuses the alias with a clear message.

**Code:**

- [`packages/cubejs-schema-compiler/src/compiler/CubeSymbols.ts:1173`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/compiler/CubeSymbols.ts#L1173): a view member resolves by name, so both view members point at one cube alias. The generated SQL has `regions` once.

**Docs:**

- [`docs-mintlify/reference/data-modeling/joins.mdx:92`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/joins.mdx#L92): `alias` joins one cube more than once, and each alias is a separate copy. It needs Tesseract.
- [`docs-mintlify/docs/data-modeling/joins.mdx:426`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/docs/data-modeling/joins.mdx#L426): diamond subgraphs. The example uses one path in each view, not two paths in one view.

**Issues:**

- [#8896](https://github.com/cube-js/cube/issues/8896) (open): the exact case. A maintainer says that this is not supported and suggests `extends`.
- [#10265](https://github.com/cube-js/cube/issues/10265) (open): the request for join aliases.
- [#12424](https://github.com/cube-js/cube/issues/12424) (open): partial, aliases across cubes in a multi-fact view.
- [PR #12483](https://github.com/cube-js/cube/pull/12483), after v1.8.15, changes joins across several views and not this case.


### B6

**A rolling measure with no bucket, or a filter inside a bucket**

- **Claims:** review B6, `measure.rolling.native_sql_limit`
- **Verdict:** `unclear-intent`
- **Claim text:** Review and matrix: a wrong number from Cube. With no time bucket, it gives the total of all rows. With a filter inside a bucket, it gives the value of the whole bucket.
- **Command:** `python3 run.py B6-rolling-no-bucket`

**Output:**

```text
# sql_no_bucket
$ psql> SELECT MEASURE(revenue_trailing_30d) FROM orders
[ch-off, ch-on, pg-off, pg-on]
measure(orders.revenue_trailing_30d)
1111
# sql_no_bucket_date_filter
$ psql> SELECT MEASURE(revenue_trailing_30d) FROM orders WHERE created_at >= '2025-04-01' AND created_at < '2025-05-01'
[ch-off, ch-on, pg-off, pg-on]
measure(orders.revenue_trailing_30d)
100
# sql_filter_inside_bucket
$ psql> SELECT DATE_TRUNC('month',created_at) m, MEASURE(revenue_trailing_30d) r FROM orders WHERE created_at >= '2025-04-15' AND created_at < '2025-06-01' GROUP BY 1 ORDER BY 1
[ch-off]
m | r
2025-04-01 00:00:00.000000 | 1100
2025-05-01 00:00:00.000000 | 1100
[ch-on, pg-off]
m | r
2025-04-01 00:00:00.000000 | 100
2025-05-01 00:00:00.000000 | 1000
[pg-on]
m | r
2025-04-15 00:00:00.000000 | 1100
2025-05-15 00:00:00.000000 | 
# sql_whole_buckets
$ psql> SELECT DATE_TRUNC('month',created_at) m, MEASURE(revenue_trailing_30d) r FROM orders WHERE created_at >= '2025-04-01' AND created_at < '2025-06-01' GROUP BY 1 ORDER BY 1
[ch-off]
m | r
2025-04-01 00:00:00.000000 | 1100
2025-05-01 00:00:00.000000 | 1100
[ch-on, pg-off, pg-on]
m | r
2025-04-01 00:00:00.000000 | 100
2025-05-01 00:00:00.000000 | 1000
# rest_no_time
$ POST /cubejs-api/v1/load {"measures": ["orders.revenue_trailing_30d"]}
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"orders.revenue_trailing_30d": "1111"}]
# rest_date_range_no_granularity
$ POST /cubejs-api/v1/load {"measures": ["orders.revenue_trailing_30d"], "timeDimensions": [{"dimension": "orders.created_at", "dateRange": ["2025-04-01", "2025-04-30"]}]}
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"orders.revenue_trailing_30d": "100"}]
```

**Truth:** Total of all rows 1111. Trailing 30 days to 2025-04-30 is 100, and to 2025-05-31 is 1000.

**Why this verdict:** Docs and tests define the form with a date range. With no time dimension at all, the docs state a requirement and Cube returns the total. It is not clear if that is intended, and an error fits the docs better. With a filter inside a bucket on ClickHouse and Tesseract, the bucket value is the correct window of that bucket, because the date range only selects buckets. So the claim is correct and it is not a wrong window. See the separate findings N1 and N2 for the rows that really are wrong in this output.

**Code:**

- [`packages/cubejs-schema-compiler/test/integration/postgres/rolling-window-offset-no-granularity.test.ts:5`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/test/integration/postgres/rolling-window-offset-no-granularity.test.ts#L5): a date range with no granularity gives one aggregate row, and this is tested on purpose.
- [`packages/cubejs-schema-compiler/test/integration/postgres/sql-generation.test.ts:1361`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/test/integration/postgres/sql-generation.test.ts#L1361): the same.

**Docs:**

- [`docs-mintlify/reference/data-modeling/measures.mdx:459`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/measures.mdx#L459): a rolling window requires one time dimension with a date range. With Tesseract, the date range can be omitted on some data sources.

**Issues:**

- [#9899](https://github.com/cube-js/cube/issues/9899) (closed): in the SQL API, no `date_trunc` means one window. A maintainer calls this expected.


### B7

**Native BETWEEN with a date text includes the whole last day**

- **Claims:** review B7
- **Verdict:** `documented-behaviour`
- **Claim text:** Review: the layer is Cube, and the result is a blocker.
- **Command:** `python3 run.py B7-between-date-text`

**Output:**

```text
# between_date_text
$ psql> SELECT MEASURE(count) c FROM orders WHERE created_at BETWEEN '2025-03-11' AND '2025-04-13'
[ch-off, ch-on, pg-off, pg-on]
c
3
# between_timestamp
$ psql> SELECT MEASURE(count) c FROM orders WHERE created_at BETWEEN timestamp '2025-03-11 00:00:00' AND timestamp '2025-04-13 00:00:00'
[ch-off, ch-on, pg-off, pg-on]
c
2
# lte_date_text
$ psql> SELECT MEASURE(count) c FROM orders WHERE created_at >= '2025-03-11' AND created_at <= '2025-04-13'
[ch-off, ch-on, pg-off, pg-on]
c
2
# between_date_cast
$ psql> SELECT MEASURE(count) c FROM orders WHERE created_at BETWEEN CAST('2025-03-11' AS date) AND CAST('2025-04-13' AS date)
[ch-off, ch-on, pg-off, pg-on]
c
2
# rest_date_only
$ POST /cubejs-api/v1/load {"measures": ["orders.count"], "timeDimensions": [{"dimension": "orders.created_at", "dateRange": ["2025-03-11", "2025-04-13"]}]}
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"orders.count": "3"}]
# truth_sql_between
$ clickhouse> SELECT count(*) c FROM repro.orders WHERE created_at BETWEEN '2025-03-11' AND '2025-04-13'
[ch-off, ch-on, pg-off, pg-on]
c
2
```

**Truth:** `truth_sql_between`: 2 on both databases, because a date text is midnight in SQL.

**Why this verdict:** The padding is documented for `dateRange`, and tests fix the text that the SQL API sends. The result still differs from SQL, and the SQL API docs do not warn about it. `>=` and `<=` with the same text give 2, and a `CAST` to date gives 2. So only `BETWEEN` with text has this result. The driver can send timestamps. I keep this as documented, and a docs request to Cube is fair.

**Code:**

- [`rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs:5066-5072`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs#L5066): `transform_between_dates` keeps the literal text in `inDateRange`.
- [`rust/cubesql/cubesql/src/compile/sql_filters/test_suite.rs:2082`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/sql_filters/test_suite.rs#L2082): a test expects `inDateRange` with `2023-01-01`, `2023-12-31`, so this text is intended.
- [`rust/cubesql/cubesql/src/compile/mod.rs:8100`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/mod.rs#L8100): another test expects the lower bound `2025-01-01` as text.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/rest-api/query-format.mdx:644`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/rest-api/query-format.mdx#L644): a date in `YYYY-MM-DD` format is padded to the start and the end of the day.

**Issues:**

- none found. Close: #7006, #10284


### M1

**A date parameter on a query with post-processing fails**

- **Claims:** review M1
- **Verdict:** `cube-bug`
- **Claim text:** Review: `Can't parse date: 'replaced_placeholder'`. Not proved: an inline literal can prevent this.
- **Command:** `python3 run.py M1-bind-date-postprocessing`

**Output:**

```text
# bind_untyped
$ psql script:
[ch-off, ch-on, pg-off, pg-on]
SELECT SUM(amount_dim) s FROM (SELECT id, amount_dim, created_at FROM orders) q WHERE created_at >= $1 \bind '2025-05-10 00:00:00' \g
psql:<stdin>:1: ERROR:  Internal Error: Can't parse date: 'replaced_placeholder'
QUERY: SELECT SUM(amount_dim) AS s FROM (SELECT id, amount_dim, created_at FROM orders) q WHERE created_at >= 'replaced_placeholder'
# bind_sum_outer
$ psql script:
[ch-off, ch-on, pg-off, pg-on]
SELECT SUM(amount_dim) s FROM (SELECT id, amount_dim, created_at FROM orders) q WHERE created_at >= $1::timestamp \bind '2025-05-10 00:00:00' \g
s
600
# literal_sum_outer
$ psql> SELECT SUM(amount_dim) s FROM (SELECT id, amount_dim, created_at FROM orders) q WHERE created_at >= timestamp '2025-05-10 00:00:00'
[ch-off, ch-on, pg-off, pg-on]
s
600
# bind_direct_scan_untyped
$ psql script:
[ch-off, ch-on, pg-off, pg-on]
SELECT MEASURE(count) c FROM orders WHERE created_at >= $1 \bind '2025-05-10 00:00:00' \g
c
2
# bind_untyped_text_filter
$ psql script:
[ch-off, ch-on, pg-off, pg-on]
SELECT SUM(amount_dim) s FROM (SELECT id, amount_dim, status FROM orders) q WHERE status = $1 \bind 'a' \g
s
300
```

**Truth:** `truth` step: 600.

**Why this verdict:** Both are now proved: a literal works, and a parameter with a cast `$1::timestamp` works too. Only a parameter with no cast fails, on both databases and both planners.

**Code:**

- [`rust/cubesql/cubesql/src/sql/statement.rs:826`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/sql/statement.rs#L826): at Parse, each placeholder becomes the text `replaced_placeholder`.
- [`rust/cube/cubesqlplanner/cubesqlplanner/src/planner/time_dimension/date_time_helper.rs:41`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cube/cubesqlplanner/cubesqlplanner/src/planner/time_dimension/date_time_helper.rs#L41): the planner then cannot parse this text as a date.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/index.mdx`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/index.mdx): the SQL API takes the Postgres protocol, which has parameters.

**Issues:**

- [#12459](https://github.com/cube-js/cube/issues/12459) (open): the exact error and cause. It reports a regression in v1.7.27 and names Metabase.

**Fix layer and test:** cubesql in Rust, `statement.rs`: do not plan a wrapper query with the placeholder text as a time value, or defer it to Bind. Test idea: a query in `test_wrapper.rs` with `$1` on a time dimension. The driver can write `?::timestamp` or a literal now.

**Subclass or package:** No. The cause is in cubesql. The driver in this repository can add the cast.

**Issue draft:** Do not open a new issue. Add this repro to [#12459](https://github.com/cube-js/cube/issues/12459): `SELECT SUM(amount_dim) s FROM (SELECT id, amount_dim, created_at FROM orders) q WHERE created_at >= $1 \bind '2025-05-10 00:00:00' \g` fails on v1.8.15, with ClickHouse and Postgres and with Tesseract on and off. `$1::timestamp` works. See `dev/repro/M1-bind-date-postprocessing`.


### M2

**A week number query with a date parameter fails**

- **Claims:** review M2
- **Verdict:** `cube-bug`
- **Claim text:** Review: `Rewrite Error: Can't parse date: 'replaced_placeholder'`, in the week number form.
- **Command:** `python3 run.py M2-week-number-bind`

**Output:**

```text
# bind_week_number_untyped
$ psql script:
[ch-off, ch-on, pg-off, pg-on]
SELECT w, c FROM (SELECT CAST(FLOOR((DATEDIFF('day', timestamp '2001-01-07 00:00:00', created_at)) / 7.0) AS bigint) AS w, MEASURE(count) c FROM orders WHERE created_at >= $1 GROUP BY 1) q ORDER BY 1 \bind '2025-05-10 00:00:00' \g
psql:<stdin>:1: ERROR:  Internal Error: Can't parse date: 'replaced_placeholder'
QUERY: SELECT w, c FROM (SELECT CAST(floor((DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', created_at)) / 7.0) AS BIGINT) AS w, MEASURE(count) AS c FROM orders WHERE created_at >= 'replaced_placeholder' GROUP BY 1) q ORDER BY 1
# bind_week_number
$ psql script:
[ch-off, ch-on, pg-off, pg-on]
SELECT w, c FROM (SELECT CAST(FLOOR((DATEDIFF('day', timestamp '2001-01-07 00:00:00', created_at)) / 7.0) AS bigint) AS w, MEASURE(count) c FROM orders WHERE created_at >= $1::timestamp GROUP BY 1) q ORDER BY 1 \bind '2025-05-10 00:00:00' \ ...
w | c
1270 | 1
1273 | 1
# literal_week_number
$ psql> SELECT w, c FROM (SELECT CAST(FLOOR((DATEDIFF('day', timestamp '2001-01-07 00:00:00', created_at)) / 7.0) AS bigint) AS w, MEASURE(count) c FROM orders WHERE created_at >= timestamp '2025-05-10 00:00:00' GROUP BY 1) q ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
w | c
1270 | 1
1273 | 1
```

**Truth:** `truth` step: `1270, 1` and `1273, 1`.

**Why this verdict:** The same cause as M1. The cast and the literal both work.

**Code:**

- [`rust/cubesql/cubesql/src/sql/statement.rs:826`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/sql/statement.rs#L826): the same path as M1.

**Issues:**

- [#12459](https://github.com/cube-js/cube/issues/12459) (open): the same cause.

**Fix layer and test:** The same as M1.

**Subclass or package:** No. The driver can add the cast.

**Issue draft:** The same as M1: add to [#12459](https://github.com/cube-js/cube/issues/12459).


### M3

**MCP count in a date range fails**

- **Claims:** review M3
- **Verdict:** `not-reproduced`
- **Claim text:** Review: `COALESCE` from `zero-when-empty` makes this query post-processing, and Cube then fails on the parameter.
- **Command:** `python3 run.py M3-coalesce-bind`, `python3 run.py M3-coalesce-bind-rollup`

**Output:**

```text
# bind_coalesce_untyped
$ psql script:
[ch-off, ch-on, pg-off, pg-on]
SELECT COALESCE(MEASURE(count), 0) c FROM orders WHERE created_at >= $1 AND created_at < $2 \bind '2025-05-10 00:00:00' '2025-05-21 00:00:00' \g
c
1
# bind_coalesce_count_star_untyped
$ psql script:
[ch-off, ch-on, pg-off, pg-on]
SELECT COALESCE(COUNT(*), 0) c FROM orders WHERE created_at >= $1 AND created_at < $2 \bind '2025-05-10 00:00:00' '2025-05-21 00:00:00' \g
c
1
# bind_coalesce_order_by_untyped
$ psql script:
[ch-off, ch-on, pg-off, pg-on]
SELECT status, COALESCE(MEASURE(count), 0) c FROM orders WHERE created_at >= $1 GROUP BY 1 ORDER BY 1 \bind '2025-05-10 00:00:00' \g
psql:<stdin>:1: ERROR:  Internal Error: Can't parse date: 'replaced_placeholder'
QUERY: SELECT status, COALESCE(MEASURE(count), 0) AS c FROM orders WHERE created_at >= 'replaced_placeholder' GROUP BY 1 ORDER BY 1
# bind_arithmetic_order_by_untyped
$ psql script:
[ch-off, ch-on, pg-off, pg-on]
SELECT DATE_TRUNC('month', created_at) m, MEASURE(revenue) * 2 x FROM orders WHERE created_at >= $1 GROUP BY 1 ORDER BY 1 \bind '2025-05-10 00:00:00' \g
psql:<stdin>:1: ERROR:  Internal Error: Can't parse date: 'replaced_placeholder'
QUERY: SELECT DATE_TRUNC('month', created_at) AS m, MEASURE(revenue) * 2 AS x FROM orders WHERE created_at >= 'replaced_placeholder' GROUP BY 1 ORDER BY 1
```
```text
# bind_revenue_and_coalesce_untyped
$ psql script:
[ch-off, ch-on, pg-off, pg-on]
SELECT MEASURE(revenue) r, COALESCE(MEASURE(count), 0) c FROM orders WHERE created_at >= $1 AND created_at < $2 \bind '2025-05-10 00:00:00' '2025-05-21 00:00:00' \g
r | c
200 | 1
```

**Truth:** `truth` step: 1.

**Why this verdict:** With no breakout, the minimal `COALESCE` query with parameters works, with and without a rollup. With a breakout and `ORDER BY` it fails, as #12459 says. So the claim is not reproduced in this form with no Metabase. The MCP path may send more than this SQL. I did not capture that SQL, because this work uses no Metabase.

**Code:**

- [`rust/cubesql/cubesql/src/sql/statement.rs:826`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/sql/statement.rs#L826): the same path as M1, for the forms with `GROUP BY` and `ORDER BY`.

**Issues:**

- [#12459](https://github.com/cube-js/cube/issues/12459) (open): it needs an expression on a measure and an `ORDER BY`.


### m1 (minor)

**Segment errors are raw**

- **Claims:** review m1
- **Verdict:** `unclear-intent`
- **Claim text:** Review: `Can't use OR operator with segments`, and `No field named 'users.vip'` for a segment of a joined cube.
- **Command:** `python3 run.py minor-m1-segment-errors`

**Output:**

```text
# or_two_segments
$ psql> SELECT MEASURE(count) c FROM orders WHERE paid = TRUE OR large = TRUE
[ch-off, ch-on, pg-off, pg-on]
psql:<stdin>:1: ERROR:  Rewrite Error: Can't use OR operator with segments. Please check logs for additional information
QUERY: SELECT MEASURE(count) AS c FROM orders WHERE paid = true OR large = true
# and_two_segments
$ psql> SELECT MEASURE(count) c FROM orders WHERE paid = TRUE AND large = FALSE
[ch-off, ch-on, pg-on]
c
1
[pg-off]
psql:<stdin>:1: ERROR:  Database Execution Error: syntax error at or near "="
# segment_of_joined_short
$ psql> SELECT MEASURE(orders.revenue) r FROM orders WHERE users.vip = TRUE
[ch-off, ch-on, pg-off, pg-on]
psql:<stdin>:1: ERROR:  Planning Error: Initial planning error: Error during planning: No field named 'users.vip'. Valid fields are 'orders.count', 'orders.revenue', 'orders.id', 'orders.status', 'orders.amount_dim', 'or ...
QUERY: SELECT MEASURE(orders.revenue) AS r FROM orders WHERE users.vip = true
# segment_of_joined_cross
$ psql> SELECT MEASURE(o.revenue) r FROM orders o CROSS JOIN users u WHERE o.__cubeJoinField = u.__cubeJoinField AND u.vip = TRUE
[ch-off, ch-on, pg-off, pg-on]
r
100
# truth_or
$ clickhouse> SELECT count(*) c FROM repro.orders WHERE status = 'a' OR amount > 300
[ch-off, ch-on, pg-off, pg-on]
c
2
# truth_vip
$ clickhouse> SELECT sum(o.amount) r FROM repro.orders o JOIN repro.users u ON o.user_id = u.id WHERE u.vip = 1
[ch-off, ch-on, pg-off, pg-on]
r
100
```

**Truth:** `truth_or` 2 and `truth_vip` 100.

**Why this verdict:** `OR` on segments is refused on purpose, and the docs do not tell this. A segment of a joined cube works with the `CROSS JOIN` form and gives the truth, so `No field named` comes from a query with no join, and the driver has to write the join. `paid = TRUE AND large = FALSE` on the legacy planner on Postgres gives a syntax error, which is a small separate defect on a deprecated path.

**Code:**

- [`rust/cubesql/cubesql/src/compile/rewrite/converter.rs:1953`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/rewrite/converter.rs#L1953): the error is written on purpose.

**Issues:**

- none found. Close: #8417


### m2 (minor)

**COUNT(*) and SUM over a fan-out**

- **Claims:** review m2
- **Verdict:** `documented-behaviour`
- **Claim text:** Review: `COUNT(*)` and `SUM` do not agree over a fan-out. The docs must tell which rule applies.
- **Command:** `python3 run.py minor-m2-fan-out`

**Output:**

```text
# count_star_cross
$ psql> SELECT COUNT(*) c FROM orders o CROSS JOIN payments p WHERE o.__cubeJoinField = p.__cubeJoinField
[ch-off, ch-on, pg-off, pg-on]
c
4
# sum_dim_cross
$ psql> SELECT SUM(o.quantity_dim) s FROM orders o CROSS JOIN payments p WHERE o.__cubeJoinField = p.__cubeJoinField
[ch-off, ch-on, pg-off, pg-on]
s
12
# both_cross
$ psql> SELECT COUNT(*) c, SUM(o.quantity_dim) s FROM orders o CROSS JOIN payments p WHERE o.__cubeJoinField = p.__cubeJoinField
[ch-off, pg-off]
c | s
2 | 12
[ch-on, pg-on]
c | s
4 | 12
# truth_sql_join
$ clickhouse> SELECT count(*) c, sum(o.quantity) s FROM repro.orders o JOIN repro.payments p ON o.id = p.order_id
[ch-off, ch-on]
c	s
4	24
[pg-off, pg-on]
c | s
4 | 24
# truth_order_total
$ clickhouse> SELECT sum(quantity) s FROM repro.orders
[ch-off, ch-on, pg-off, pg-on]
s
12
```

**Truth:** A SQL join gives 4 and 24. The real order total is 12.

**Why this verdict:** Cube removes the fan-out on purpose, so `SUM` gives the real total 12. With both aggregates, Tesseract gives `COUNT(*)` 4 and the legacy planner gives 2.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/joins.mdx:15`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/joins.mdx#L15): Cube solves fan and chasm traps based on the selected dimensions.

**Issues:**

- none found


### m3 (minor)

**Native COUNT(*) on no rows gives NULL**

- **Claims:** review m3
- **Verdict:** `cube-bug`
- **Claim text:** Review: `null` in native SQL. The truth is 0.
- **Command:** `python3 run.py minor-m3-count-no-rows`, `python3 run.py minor-m3-count-no-rows-rollup`

**Output:**

```text
# count_star
$ psql> SELECT COUNT(*) c FROM orders WHERE status = 'nope'
[ch-off, ch-on, pg-off, pg-on]
c
0
# measure_count
$ psql> SELECT MEASURE(count) c FROM orders WHERE status = 'nope'
[ch-off, ch-on, pg-off, pg-on]
c
0
# measure_sum
$ psql> SELECT MEASURE(revenue) r FROM orders WHERE status = 'nope'
[ch-off, ch-on]
r
0
[pg-off, pg-on]
r
```
```text
# count_star
$ psql> SELECT COUNT(*) c FROM orders WHERE status = 'nope'
[ch-off, ch-on, pg-off, pg-on]
c
# measure_count
$ psql> SELECT MEASURE(count) c FROM orders WHERE status = 'nope'
[ch-off, ch-on, pg-off, pg-on]
c
# rest_count
$ POST /cubejs-api/v1/load {"measures": ["orders.count"], "filters": [{"member": "orders.status", "operator": "equals", "values": ["nope"]}]}
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"orders.count": null}]
# truth
$ clickhouse> SELECT count(*) c FROM repro.orders WHERE status = 'nope'
[ch-off, ch-on, pg-off, pg-on]
c
0
```

**Truth:** 0.

**Why this verdict:** With no rollup the count is 0, on both databases. With a rollup it is NULL, on both databases and both planners, in REST too. The demo orders cube has rollups, so the review saw this. A count of no rows is 0 in SQL. The driver adds `COALESCE` in MBQL only.

**Code:**

- [`packages/cubejs-schema-compiler/src/adapter/BaseQuery.ts:4274-4275`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/adapter/BaseQuery.ts#L4274): a count measure on a rollup is `sum(column)`, and a `sum` of no rows is NULL.

**Issues:**

- none found. Close: #11973

**Fix layer and test:** `BaseQuery.aggregateOnGroupedColumn`: for `count`, return `coalesce(sum(x), 0)`. Test idea: a count with a filter that matches nothing on a rollup.

**Subclass or package:** Not in `ClickHouseQuery`: the query runs in Cube Store, through `CubeStoreQuery`. A `dialectFactory` for `cubestore`, or `externalDialectFactory`, could do it. I did not test this.


<details><summary>Issue draft: A count measure served by a rollup returns NULL on no rows</summary>

**Title:** A count measure served by a rollup returns NULL on no rows

**Describe the bug**

With a rollup, a count that matches no rows returns NULL. With no rollup it returns 0.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: postgres:17-alpine
    environment: { POSTGRES_DB: repro, POSTGRES_USER: repro, POSTGRES_PASSWORD: repro }
    healthcheck: { test: [CMD-SHELL, "pg_isready -U repro -d repro"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: postgres
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "5432"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: orders
    sql_table: repro.orders
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: status, sql: status, type: string }
      - { name: amount_dim, sql: amount, type: number }
      - { name: created_at, sql: created_at, type: time }
    measures:
      - { name: count, type: count }
      - { name: revenue, sql: amount, type: sum }
    pre_aggregations:
      - name: by_status
        measures: [count, revenue]
        dimensions: [status]
        refresh_key: { every: 1 hour }
        indexes:
          - { name: status_idx, columns: [status] }
```

`seed.sql`:

```sql
CREATE TABLE repro.orders (id int PRIMARY KEY, status text, amount int, created_at timestamp);
INSERT INTO repro.orders VALUES
(1, 'a', 100, '2025-05-05 00:00:00'),
(2, 'a', 200, '2025-05-15 00:00:00'),
(3, 'b', 400, '2025-06-01 00:00:00');
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
(echo 'CREATE SCHEMA repro;'; cat seed.sql) | docker compose exec -T -e PGPASSWORD=repro db psql -U repro -d repro -q
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
curl -s -X POST http://127.0.0.1:4000/cubejs-api/v1/load -H 'Content-Type: application/json' -d '{"query": {"measures": ["orders.count"], "filters": [{"member": "orders.status", "operator": "equals", "values": ["nope"]}]}}'
# Run it again if the reply is {"error":"Continue wait"}.
```

**Expected behavior**

`"orders.count": "0"`.

**Actual behavior**

`"orders.count": null`.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

`BaseQuery.aggregateOnGroupedColumn` writes `sum(x)` for a count. ClickHouse gives the same result.

</details>


### m4 (minor)

**Raw errors from a rolling measure**

- **Claims:** review m4
- **Verdict:** `cube-bug`
- **Claim text:** Review: `Unknown expression or function identifier` for `max` of the column. With one month, `trailing: unbounded` works.
- **Command:** `python3 run.py minor-m4-rolling-max`, `python3 run.py rolling-unbounded`

**Output:**

```text
# max_of_rolling
$ psql> SELECT MAX(revenue_trailing_30d) x FROM orders
[ch-off]
psql:<stdin>:1: ERROR:  Database Execution Error: Query failed: Error: Unknown expression or function identifier `amount` in scope SELECT max(amount) AS max_orders_reven FROM (SELECT sum(orders_revenu ...
[ch-on]
psql:<stdin>:1: ERROR:  Database Execution Error: Query failed: Error: Unknown expression or function identifier `orders.amount` in scope WITH cte_0 AS (SELECT max(orders.amount) AS orders__revenue_tr ...
[pg-off]
psql:<stdin>:1: ERROR:  Database Execution Error: column "amount" does not exist
[pg-on]
psql:<stdin>:1: ERROR:  Database Execution Error: missing FROM-clause entry for table "orders"
```
```text
# one_month
$ psql> SELECT DATE_TRUNC('month',created_at) m, MEASURE(cumulative_count) c FROM orders WHERE created_at >= '2025-04-01' AND created_at < '2025-05-01' GROUP BY 1 ORDER BY 1
[ch-off]
psql:<stdin>:1: ERROR:  Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__table4.orders__created_at_month, __table2.date_to). ; query id: < ...
[ch-on, pg-off, pg-on]
m | c
2025-04-01 00:00:00.000000 | 2
```

**Truth:** The docs say that a wrong aggregate gives `Measure aggregation type doesn't match`.

**Why this verdict:** Cube generates invalid SQL, `max(orders.amount)` outside the scope of its table. It is an error and not a wrong number. The result for one month agrees with the review.

**Code:**

- [`rust/cubesql/cubesql/src/compile/rewrite/rules/members.rs:2868`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/rewrite/rules/members.rs#L2868): the documented error. This path does not reach it for a rolling measure.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx:70`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L70): the documented error.

**Issues:**

- none found

**Fix layer and test:** cubesql in Rust: refuse `MAX` on a rolling measure with the documented error. Test idea: `SELECT MAX(rolling) FROM cube` must give that error.

**Subclass or package:** No.


<details><summary>Issue draft: SQL API: MAX of a rolling window measure generates invalid SQL</summary>

**Title:** SQL API: MAX of a rolling window measure generates invalid SQL

**Describe the bug**

`MAX` of a rolling measure does not give the documented error. The database gets invalid SQL.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: postgres:17-alpine
    environment: { POSTGRES_DB: repro, POSTGRES_USER: repro, POSTGRES_PASSWORD: repro }
    healthcheck: { test: [CMD-SHELL, "pg_isready -U repro -d repro"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: postgres
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "5432"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: orders
    sql_table: repro.orders
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: status, sql: status, type: string }
      - { name: amount_dim, sql: amount, type: number }
      - { name: created_at, sql: created_at, type: time }
    measures:
      - { name: count, type: count }
      - { name: revenue, sql: amount, type: sum }
      - { name: cumulative_count, type: count, rolling_window: { trailing: unbounded } }
      - { name: cumulative_100y, type: count, rolling_window: { trailing: 100 year } }
      - { name: revenue_trailing_30d, sql: amount, type: sum, rolling_window: { trailing: 30 day } }
```

`seed.sql`:

```sql
CREATE TABLE repro.orders (id int PRIMARY KEY, status text, amount int, created_at timestamp);
INSERT INTO repro.orders VALUES
(1, 'a', 1, '2025-03-20 00:00:00'),
(2, 'a', 10, '2025-04-20 00:00:00'),
(3, 'a', 100, '2025-05-20 00:00:00');
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
(echo 'CREATE SCHEMA repro;'; cat seed.sql) | docker compose exec -T -e PGPASSWORD=repro db psql -U repro -d repro -q
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
docker run --rm --network host -e PGPASSWORD=repro postgres:17-alpine psql -h 127.0.0.1 -p 15432 -U repro -d repro -c "SELECT MAX(revenue_trailing_30d) x FROM orders"
```

**Expected behavior**

`Measure aggregation type doesn't match`, as for `MIN(count)`.

**Actual behavior**

`missing FROM-clause entry for table \"orders\"`. ClickHouse gives `Unknown expression or function identifier`.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

The legacy planner gives `column \"amount\" does not exist`.

</details>


### m5 (minor)

**original_sql pre-aggregation fails**

- **Claims:** review m5, `preagg.original_sql`
- **Verdict:** `cube-clickhouse-bug`
- **Claim text:** Matrix: Cube fails to build the rollup on an `original_sql` pre-aggregation on ClickHouse: `Missing columns: 'lambda_orders__status'`.
- **Command:** `python3 run.py preagg-original-sql`

**Output:**

```text
# rest_only_base_idx
$ POST /cubejs-api/v1/load {"measures": ["only_base_idx.revenue"], "dimensions": ["only_base_idx.status"], "order": {"only_base_idx.status": "asc"}}
[ch-off, ch-on]
HTTP 400 ERROR Error: Command failed: Error: Missing columns: 'only_base_idx__status' while processing: '(only_base_idx__status)', required columns: 'only_base_idx__status', available columns: '_block_number' ' ...
[pg-off]
HTTP 200 [{"only_base_idx.status": "a", "only_base_idx.revenue": "300"}, {"only_base_idx.status": "b", "only_base_idx.revenue": "400"}]
[pg-on]
HTTP 400 ERROR Error: column "only_base_idx__status" does not exist
# rest_only_base
$ POST /cubejs-api/v1/load {"measures": ["only_base.revenue"], "dimensions": ["only_base.status"], "order": {"only_base.status": "asc"}}
[ch-off, ch-on]
HTTP 400 ERROR ClickHouse doesn't support pre-aggregations without indexes
SQL ERROR: {"type":"UserError","error":"ClickHouse doesn't support pre-aggregations without indexes","stack":"Error: ClickHouse doesn't support pre-aggregations without indexes\n    at ClickHouseQuery.preAggreg ...
[pg-off, pg-on]
HTTP 200 [{"only_base.status": "a", "only_base.revenue": "300"}, {"only_base.status": "b", "only_base.revenue": "400"}]
# rest_rollup_over_original
$ POST /cubejs-api/v1/load {"measures": ["lambda_orders.revenue"], "dimensions": ["lambda_orders.status"], "order": {"lambda_orders.status": "asc"}}
[ch-off, ch-on]
HTTP 400 ERROR Error: Command failed: Error: Missing columns: 'lambda_orders__status' while processing: '(lambda_orders__status)', required columns: 'lambda_orders__status', available columns: '_block_number' ' ...
[pg-off, pg-on]
HTTP 400 ERROR Error: relation "dev_pre_aggregations.lambda_orders_base" does not exist
# rest_rollup_internal
$ POST /cubejs-api/v1/load {"measures": ["rollup_internal.revenue"], "dimensions": ["rollup_internal.status"], "order": {"rollup_internal.status": "asc"}}
[ch-off, ch-on]
HTTP 400 ERROR Error: Command failed: Error: Missing columns: 'rollup_internal__id' while processing: '(rollup_internal__id)', required columns: 'rollup_internal__id', available columns: '_block_number' '_row_e ...
[pg-off]
HTTP 200 [{"rollup_internal.status": "a", "rollup_internal.revenue": "300"}, {"rollup_internal.status": "b", "rollup_internal.revenue": "400"}]
[pg-on]
HTTP 400 ERROR Error: column "rollup_internal__id" does not exist
# log
$ docker compose logs cube | grep -E 'Missing columns|Unknown identifier|UNKNOWN_IDENTIFIER|CREATE TABLE|ADD INDEX'
[ch-off, ch-on]
          "ALTER TABLE dev_pre_aggregations.lambda_orders_base ADD INDEX lambda_orders_base_status_idx (`lambda_orders__status`) TYPE minmax GRANULARITY 1",
      "CREATE TABLE dev_pre_aggregations.lambda_orders_base ENGINE = MergeTree() ORDER BY (`lambda_orders__status`) AS SELECT id, status, amount, created_at FROM repro.orders",
          "ALTER TABLE dev_pre_aggregations.lambda_orders_base ADD INDEX lambda_orders_base_status_idx (`lambda_orders__status`) TYPE minmax GRANULARITY 1",
Error: Command failed: Error: Missing columns: 'lambda_orders__status' while processing: '(lambda_orders__status)', required columns: 'lambda_orders__status', available columns: '_block_number' '_row_exists' '_ ...
Error: Command failed: Error: Missing columns: 'lambda_orders__status' while processing: '(lambda_orders__status)', required columns: 'lambda_orders__status', available columns: '_block_number' '_row_exists' '_ ...
Error: Command failed: Error: Missing columns: 'lambda_orders__status' while processing: '(lambda_orders__status)', required columns: 'lambda_orders__status', available columns: '_block_number' '_row_exists' '_ ...
Internal Error: Error during processing PostgreSQL message: DataFusionError: Arrow error: External error: Database Execution Error: Command failed: Error: Missing columns: 'lambda_orders__status' while processi ...
Internal Error: Error during processing PostgreSQL message: DataFusionError: Arrow error: External error: Database Execution Error: Command failed: Error: Missing columns: 'lambda_orders__status' while processi ...
matching lines: 34
[pg-off]
      "CREATE TABLE dev_pre_aggregations.lambda_orders_daily AS SELECT\n      \"lambda_orders\".status \"lambda_orders__status\", date_trunc('day', (\"lambda_orders\".created_at::timestamptz AT TIME ZONE 'UTC') ...
      "CREATE TABLE dev_pre_aggregations.lambda_orders_daily AS SELECT\n      \"lambda_orders\".status \"lambda_orders__status\", date_trunc('day', (\"lambda_orders\".created_at::timestamptz AT TIME ZONE 'UTC') ...
      "CREATE TABLE dev_pre_aggregations.base_no_index_daily AS SELECT\n      \"base_no_index\".status \"base_no_index__status\", date_trunc('day', (\"base_no_index\".created_at::timestamptz AT TIME ZONE 'UTC') ...
      "CREATE TABLE dev_pre_aggregations.base_no_index_daily AS SELECT\n      \"base_no_index\".status \"base_no_index__status\", date_trunc('day', (\"base_no_index\".created_at::timestamptz AT TIME ZONE 'UTC') ...
      "CREATE TABLE dev_pre_aggregations.lambda_orders_daily AS SELECT\n      \"lambda_orders\".status \"lambda_orders__status\", date_trunc('day', (\"lambda_orders\".created_at::timestamptz AT TIME ZONE 'UTC') ...
      "CREATE TABLE dev_pre_aggregations.lambda_orders_daily AS SELECT\n      \"lambda_orders\".status \"lambda_orders__status\", date_trunc('day', (\"lambda_orders\".created_at::timestamptz AT TIME ZONE 'UTC') ...
matching lines: 6
[pg-on]
      "CREATE TABLE dev_pre_aggregations.only_base_idx_base AS SELECT id, status, amount, created_at FROM repro.orders",
      "CREATE TABLE dev_pre_aggregations.only_base_idx_base AS SELECT id, status, amount, created_at FROM repro.orders",
      "CREATE TABLE dev_pre_aggregations.rollup_internal_base AS SELECT id, status, amount, created_at FROM repro.orders",
      "CREATE TABLE dev_pre_aggregations.rollup_internal_base AS SELECT id, status, amount, created_at FROM repro.orders",
      "CREATE TABLE dev_pre_aggregations.base_no_index_daily AS SELECT \"base_no_index\".status \"base_no_index__status\", date_trunc('day', (\"base_no_index\".created_at::timestamptz AT TIME ZONE 'UTC')) \"bas ...
      "CREATE TABLE dev_pre_aggregations.base_no_index_daily AS SELECT \"base_no_index\".status \"base_no_index__status\", date_trunc('day', (\"base_no_index\".created_at::timestamptz AT TIME ZONE 'UTC')) \"bas ...
      "CREATE TABLE dev_pre_aggregations.lambda_orders_daily AS SELECT \"lambda_orders\".status \"lambda_orders__status\", date_trunc('day', (\"lambda_orders\".created_at::timestamptz AT TIME ZONE 'UTC')) \"lam ...
      "CREATE TABLE dev_pre_aggregations.lambda_orders_daily AS SELECT \"lambda_orders\".status \"lambda_orders__status\", date_trunc('day', (\"lambda_orders\".created_at::timestamptz AT TIME ZONE 'UTC')) \"lam ...
matching lines: 10
```

**Truth:** `truth` step: `a 300`, `b 400`.

**Why this verdict:** Together the rules leave no working form on ClickHouse: with no index it refuses, and with an index it fails. The cause is the index, not the rollup. On Postgres with Tesseract, the index fails in the same way, and the legacy planner works. So `lambda_orders` in the demo cannot work on ClickHouse.

**Code:**

- [`packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts:289-292`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L289): `preAggregationLoadSql` needs an index and writes `ORDER BY` with `evaluateIndexColumns`.
- [`packages/cubejs-schema-compiler/src/adapter/BaseQuery.ts:4695`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/adapter/BaseQuery.ts#L4695): `evaluateIndexColumns` gives the member alias `cube__status`. An `original_sql` table keeps the cube `sql` columns, so this name does not exist.

**Docs:**

- [`docs-mintlify/admin/connect-to-data/data-sources/clickhouse.mdx:48`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/admin/connect-to-data/data-sources/clickhouse.mdx#L48): ClickHouse pre-aggregations must have indexes.
- [`docs-mintlify/reference/data-modeling/pre-aggregations.mdx:146`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/pre-aggregations.mdx#L146): `original_sql` is stored in the data source.

**Issues:**

- none found. Close: [#7421](https://github.com/cube-js/cube/issues/7421) (open): the same option fails on Postgres., #9074

**Fix layer and test:** `ClickHouseQuery.preAggregationLoadSql` and `BaseQuery.evaluateIndexColumns`: for `originalSql`, use the column name of the source. Test idea: an `original_sql` pre-aggregation with an index on ClickHouse.

**Subclass or package:** Yes, tested: override `preAggregationLoadSql` and `indexSql` for `originalSql`. `probe-clickhouse-subclass`, step `original_sql_with_index`, gives a result on both planners. The probe uses `ORDER BY tuple()`, so it drops the index.


<details><summary>Issue draft: ClickHouse: an original_sql pre-aggregation with an index cannot build</summary>

**Title:** ClickHouse: an original_sql pre-aggregation with an index cannot build

**Describe the bug**

ClickHouse requires an index on each pre-aggregation. For `original_sql`, Cube writes the member alias in `ORDER BY`, and the table has no such column.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: clickhouse/clickhouse-server:26.10.3-alpine
    environment: { CLICKHOUSE_DB: repro, CLICKHOUSE_USER: repro, CLICKHOUSE_PASSWORD: repro, CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: "1" }
    healthcheck: { test: [CMD-SHELL, "clickhouse-client -u repro --password repro -q 'SELECT 1'"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: clickhouse
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "8123"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: lambda_orders
    sql: "SELECT id, status, amount, created_at FROM repro.orders"
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true }
      - { name: status, sql: status, type: string }
      - { name: created_at, sql: created_at, type: time }
    measures:
      - { name: revenue, sql: amount, type: sum }
    pre_aggregations:
      - name: base
        type: original_sql
        refresh_key: { every: 1 hour }
        indexes:
          - { name: status_idx, columns: [status] }
      - name: daily
        type: rollup
        use_original_sql_pre_aggregations: true
        measures: [revenue]
        dimensions: [status]
        time_dimension: created_at
        granularity: day
        indexes:
          - { name: status_idx, columns: [status] }
        refresh_key: { every: 1 hour }
  - name: only_base
    sql: "SELECT id, status, amount, created_at FROM repro.orders"
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true }
      - { name: status, sql: status, type: string }
    measures:
      - { name: revenue, sql: amount, type: sum }
    pre_aggregations:
      - name: base
        type: original_sql
        refresh_key: { every: 1 hour }
  - name: only_base_idx
    sql: "SELECT id, status, amount, created_at FROM repro.orders"
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true }
      - { name: status, sql: status, type: string }
    measures:
      - { name: revenue, sql: amount, type: sum }
    pre_aggregations:
      - name: base
        type: original_sql
        refresh_key: { every: 1 hour }
        indexes:
          - { name: status_idx, columns: [status] }
  - name: rollup_internal
    sql: "SELECT id, status, amount, created_at FROM repro.orders"
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true }
      - { name: status, sql: status, type: string }
      - { name: created_at, sql: created_at, type: time }
    measures:
      - { name: revenue, sql: amount, type: sum }
    pre_aggregations:
      - name: daily
        type: rollup
        external: false
        use_original_sql_pre_aggregations: true
        measures: [revenue]
        dimensions: [status]
        time_dimension: created_at
        granularity: day
        indexes:
          - { name: status_idx, columns: [status] }
        refresh_key: { every: 1 hour }
      - name: base
        type: original_sql
        external: false
        refresh_key: { every: 1 hour }
        indexes:
          - { name: id_idx, columns: [id] }
  - name: base_no_index
    sql: "SELECT id, status, amount, created_at FROM repro.orders"
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true }
      - { name: status, sql: status, type: string }
      - { name: created_at, sql: created_at, type: time }
    measures:
      - { name: revenue, sql: amount, type: sum }
    pre_aggregations:
      - name: base
        type: original_sql
        refresh_key: { every: 1 hour }
      - name: daily
        type: rollup
        use_original_sql_pre_aggregations: true
        measures: [revenue]
        dimensions: [status]
        time_dimension: created_at
        granularity: day
        refresh_key: { every: 1 hour }
```

`seed.sql`:

```sql
CREATE TABLE repro.orders (id UInt32, status String, amount Int32, created_at DateTime('UTC')) ENGINE = MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES
(1, 'a', 100, '2025-05-05 00:00:00'),
(2, 'a', 200, '2025-05-15 00:00:00'),
(3, 'b', 400, '2025-06-01 00:00:00');
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
docker compose exec -T db clickhouse-client -u repro --password repro --multiquery < seed.sql
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
curl -s -X POST http://127.0.0.1:4000/cubejs-api/v1/load -H 'Content-Type: application/json' -d '{"query": {"measures": ["only_base_idx.revenue"], "dimensions": ["only_base_idx.status"]}}'
# Run it again if the reply is {"error":"Continue wait"}.
```

**Expected behavior**

The pre-aggregation builds, and `a 300`, `b 400` are returned.

**Actual behavior**

`Missing columns: 'only_base_idx__status'`.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

With no index, Cube gives `ClickHouse doesn't support pre-aggregations without indexes`. So `original_sql` has no working form on ClickHouse. On Postgres with Tesseract the same index gives `column \"only_base_idx__status\" does not exist`.

</details>


### m6 (minor)

**Native SQL reads hidden members in dev mode**

- **Claims:** review m6
- **Verdict:** `documented-behaviour`
- **Claim text:** Review: `SELECT COUNT(*) FROM secret_audit` works in dev mode and is refused with dev mode off.
- **Command:** `python3 run.py minor-m6-hidden-dev-mode`, `python3 run.py minor-m6-hidden-prod-mode`

**Output:**

```text
# hidden_cube
$ psql> SELECT COUNT(*) c FROM secret_audit
[ch-on]
c
3
# hidden_measure
$ psql> SELECT MEASURE(hidden_measure) x FROM orders
[ch-on]
x
700
```
```text
# hidden_cube
$ psql> SELECT COUNT(*) c FROM secret_audit
[ch-on]
psql:<stdin>:1: ERROR:  Planning Error: Initial planning error: Error during planning: Table or CTE with name 'secret_audit' not found
QUERY: SELECT COUNT(*) AS c FROM secret_audit
# hidden_measure
$ psql> SELECT MEASURE(hidden_measure) x FROM orders
[ch-on]
psql:<stdin>:1: ERROR:  Planning Error: Initial planning error: Error during planning: Invalid identifier '#hidden_measure' for schema fields:[orders.count, orders.revenue, orders.id, orders.status, o ...
QUERY: SELECT MEASURE(hidden_measure) AS x FROM orders
# rest_hidden_cube
$ POST /cubejs-api/v1/load {"measures": ["secret_audit.count"]}
[ch-on]
HTTP 500 ERROR Error: You requested hidden member: 'secret_audit.count'. Please make it visible using `public: true`. Please note primaryKey fields are `public: false` by default: https://docs.cube.de ...
```

**Truth:** Not a number claim. The rows are the same in the two modes.

**Why this verdict:** The claim is correct, and this is the intended behaviour of dev mode.

**Code:**

- [`packages/cubejs-api-gateway/src/gateway.ts:822`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-api-gateway/src/gateway.ts#L822): `isDevMode || ... || item.isVisible`: dev mode shows hidden members.

**Docs:**

- [`docs-mintlify/docs/data-modeling/dev-mode.mdx`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/docs/data-modeling/dev-mode.mdx): dev mode is an authentication bypass, see the warning in `snippets/dev-mode-warning.mdx`.

**Issues:**

- [#6742](https://github.com/cube-js/cube/issues/6742) (closed): the exact case. A maintainer says that it is not a bug.


### unbounded

**A trailing unbounded window fails on ClickHouse**

- **Claims:** `measure.rolling.unbounded`, review m4 (first part)
- **Verdict:** `cube-clickhouse-bug`
- **Claim text:** Matrix: Cube writes the window as a join with only an inequality, and ClickHouse 26.8 rejects it.
- **Command:** `python3 run.py rolling-unbounded`

**Output:**

```text
# two_months
$ psql> SELECT DATE_TRUNC('month',created_at) m, MEASURE(cumulative_count) c FROM orders WHERE created_at >= '2025-04-01' AND created_at < '2025-06-01' GROUP BY 1 ORDER BY 1
[ch-off]
psql:<stdin>:1: ERROR:  Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__table4.orders__created_at_month, __table2.date_to). ; query id: <id>
[ch-on]
psql:<stdin>:1: ERROR:  Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__table8.orders__created_at_month, __table2.date_to). ; query id: <id>
[pg-off, pg-on]
m | c
2025-04-01 00:00:00.000000 | 2
2025-05-01 00:00:00.000000 | 3
# two_months_100y
$ psql> SELECT DATE_TRUNC('month',created_at) m, MEASURE(cumulative_100y) c FROM orders WHERE created_at >= '2025-04-01' AND created_at < '2025-06-01' GROUP BY 1 ORDER BY 1
[ch-off]
m | c
2025-04-01 00:00:00.000000 | 5
2025-05-01 00:00:00.000000 | 5
[ch-on, pg-off, pg-on]
m | c
2025-04-01 00:00:00.000000 | 2
2025-05-01 00:00:00.000000 | 3
# rest_two_months
$ POST /cubejs-api/v1/load {"measures": ["orders.cumulative_count"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month", "dateRange": ["2025-04-01", "2025-05-31"]}]}
[ch-off]
HTTP 400 ERROR Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__table4.orders__created_at_month, __table2.date_to). ; query id: <id>
[ch-on]
HTTP 400 ERROR Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__table8.orders__created_at_month, __table2.date_to). ; query id: <id>
[pg-off, pg-on]
HTTP 200 [{"orders.created_at.month": "2025-04-01T00:00:00.000", "orders.cumulative_count": "2", "orders.created_at": "2025-04-01T00:00:00.000"}, {"orders.created_at.month": "2025-05-01T00:00:00.000", "orders.cumulative_count": "3 ...
# ch_inequality_join_named_table
$ clickhouse> SELECT d, count() FROM (SELECT toDateTime('2025-04-30 23:59:59','UTC') d UNION ALL SELECT toDateTime('2025-05-31 23:59:59','UTC')) dates LEFT JOIN repro.orders o ON o.created_at <= dates.d GROUP BY 1 ORDER BY 1
[ch-off, ch-on]
Received exception from server (version 26.8.36):
Code: 403. DB::Exception: Received from localhost:9000. DB::Exception: Cannot determine join keys in JOIN ON expression lessOrEquals(__table6.created_at, __table1.d). (INVALID_JOIN_ON_EXPRESSION)
(query: SELECT d, count() FROM (SELECT toDateTime('2025-04-30 23:59:59','UTC') d UNION ALL SELECT toDateTime('2025-05-31 23:59:59','UTC')) dates LEFT JOIN repro.orders o ON o.created_at <= dates.d GROUP BY 1 ORDER BY 1)
```

**Truth:** `truth` step: 2 and 3.

**Why this verdict:** The claim is correct. Postgres gives the truth, and the same join on ClickHouse alone fails. A far bound `trailing: 100 year` gives the truth on Tesseract, so the demo can use that.

**Code:**

- [`rust/cube/cubesqlplanner/cubesqlplanner/src/physical_plan_builder/processors/multi_stage_rolling_window.rs:29`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cube/cubesqlplanner/cubesqlplanner/src/physical_plan_builder/processors/multi_stage_rolling_window.rs#L29): the join condition of the window.
- [`packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts:353`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L353): the time series of ClickHouse.

**Docs:**

- [`docs-mintlify/reference/data-modeling/measures.mdx:459`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/measures.mdx#L459): rolling windows are documented with no such limit on ClickHouse.

**Issues:**

- [#9600](https://github.com/cube-js/cube/issues/9600) (open): rolling windows with a granularity fail on ClickHouse.
- [#6333](https://github.com/cube-js/cube/issues/6333) (closed): the same behaviour with an older error text.
- The same error text with another cause: #10493, #10316.

**Fix layer and test:** The Tesseract rolling window in Rust: for ClickHouse, write a `CROSS JOIN` with `WHERE`, or a lower bound, for an unbounded window. Test idea: `cumulative_count` by month for two months on ClickHouse.

**Subclass or package:** Not tested as a fix: `unbounded_two_months` still fails with the subclass, because the join comes from Rust. The model workaround `trailing: 100 year` works now.


<details><summary>Issue draft: ClickHouse: rolling_window trailing unbounded fails with two time buckets</summary>

**Title:** ClickHouse: rolling_window trailing unbounded fails with two time buckets

**Describe the bug**

The window is a join with only `<=`, and ClickHouse does not take it.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: clickhouse/clickhouse-server:26.10.3-alpine
    environment: { CLICKHOUSE_DB: repro, CLICKHOUSE_USER: repro, CLICKHOUSE_PASSWORD: repro, CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: "1" }
    healthcheck: { test: [CMD-SHELL, "clickhouse-client -u repro --password repro -q 'SELECT 1'"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: clickhouse
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "8123"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: orders
    sql_table: repro.orders
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: status, sql: status, type: string }
      - { name: amount_dim, sql: amount, type: number }
      - { name: created_at, sql: created_at, type: time }
    measures:
      - { name: count, type: count }
      - { name: revenue, sql: amount, type: sum }
      - { name: cumulative_count, type: count, rolling_window: { trailing: unbounded } }
      - { name: cumulative_100y, type: count, rolling_window: { trailing: 100 year } }
      - { name: revenue_trailing_30d, sql: amount, type: sum, rolling_window: { trailing: 30 day } }
```

`seed.sql`:

```sql
CREATE TABLE repro.orders (id UInt32, status String, amount Int32, created_at DateTime('UTC')) ENGINE = MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES
(1, 'a', 1, '2025-03-20 00:00:00'),
(2, 'a', 10, '2025-04-20 00:00:00'),
(3, 'a', 100, '2025-05-20 00:00:00');
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
docker compose exec -T db clickhouse-client -u repro --password repro --multiquery < seed.sql
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
docker run --rm --network host -e PGPASSWORD=repro postgres:17-alpine psql -h 127.0.0.1 -p 15432 -U repro -d repro -c "SELECT DATE_TRUNC('month',created_at) m, MEASURE(cumulative_count) c FROM orders WHERE created_at >= '2025-04-01' AND created_at < '2025-06-01' GROUP BY 1 ORDER BY 1"
```

**Expected behavior**

`2025-04-01, 2` and `2025-05-01, 3`, as on Postgres.

**Actual behavior**

`Cannot determine join keys in JOIN ON expression lessOrEquals(...)`.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

`trailing: 100 year` works on Tesseract. One bucket works too. Related: #9600, #6333.

</details>


### geo

**A geo dimension in a view fails**

- **Claims:** `dimension.type.geo.view`
- **Verdict:** `cube-bug`
- **Claim text:** Matrix: Cube v1.8.15 fails each query on a geo dimension of a view, also on the REST API: `Cannot read properties of undefined (reading 'sql')`.
- **Command:** `python3 run.py geo-dimension-in-view`

**Output:**

```text
# rest_cube
$ POST /cubejs-api/v1/load {"dimensions": ["users.location"]}
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"users.location": "10.5,20.5"}, {"users.location": "30.5,40.5"}]
# rest_view
$ POST /cubejs-api/v1/load {"dimensions": ["user_places.location"]}
[ch-off, ch-on, pg-off, pg-on]
HTTP 500 ERROR TypeError: Cannot read properties of undefined (reading 'sql')
SQL ERROR: {"error":"TypeError: Cannot read properties of undefined (reading 'sql')","stack":"TypeError: Cannot read properties of undefined (reading 'sql')\n    at QueryPlanner.evaluateSymbolSql (/cu ...
# rest_view_id_only
$ POST /cubejs-api/v1/load {"dimensions": ["user_places.id"]}
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"user_places.id": "1"}, {"user_places.id": "2"}]
# sql_view
$ psql> SELECT location FROM user_places ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
psql:<stdin>:1: ERROR:  Database Execution Error: TypeError: Cannot read properties of undefined (reading 'sql')
```

**Truth:** Two rows, `10.5,20.5` and `30.5,40.5`.

**Why this verdict:** The claim is correct, on both databases and both planners. `/v1/meta` lists the member, so the failure contradicts the metadata.

**Code:**

- [`packages/cubejs-schema-compiler/src/compiler/CubeSymbols.ts:1199-1216`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/compiler/CubeSymbols.ts#L1199): a view dimension copies `sql` and other fields, but not `latitude` and `longitude`.
- [`packages/cubejs-schema-compiler/src/adapter/BaseQuery.ts:3724-3726`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/adapter/BaseQuery.ts#L3724): the geo branch reads `symbol.latitude.sql`.
- [`rust/cube/cubesqlplanner/cubesqlplanner/src/planner/symbols/dimension_symbol.rs:497`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cube/cubesqlplanner/cubesqlplanner/src/planner/symbols/dimension_symbol.rs#L497): Tesseract has the same branch.

**Docs:**

- [`docs-mintlify/reference/data-modeling/dimensions.mdx:960`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/dimensions.mdx#L960): `geo` is a dimension type.
- [`docs-mintlify/reference/data-modeling/view.mdx`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/view.mdx): nothing excludes geo.

**Issues:**

- none found. Close: #10856

**Fix layer and test:** `CubeSymbols.ts`: copy the type or resolve the geo pair through the view member, the same as `sql`. Test idea: a view with a geo dimension in `packages/cubejs-schema-compiler/test`.

**Subclass or package:** No. The cause is in the compiler of the data model.


<details><summary>Issue draft: A geo dimension in a view fails with Cannot read properties of undefined (reading 'sql')</summary>

**Title:** A geo dimension in a view fails with Cannot read properties of undefined (reading 'sql')

**Describe the bug**

`/v1/meta` lists a geo dimension in a view. Each query on it fails. The same dimension on the cube works.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: postgres:17-alpine
    environment: { POSTGRES_DB: repro, POSTGRES_USER: repro, POSTGRES_PASSWORD: repro }
    healthcheck: { test: [CMD-SHELL, "pg_isready -U repro -d repro"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: postgres
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "5432"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: users
    sql_table: repro.users
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - name: location
        type: geo
        latitude: { sql: latitude }
        longitude: { sql: longitude }
    measures:
      - { name: count, type: count }
views:
  - name: user_places
    cubes:
      - { join_path: users, includes: [id, location, count] }
```

`seed.sql`:

```sql
CREATE TABLE repro.users (id int, latitude float8, longitude float8);
INSERT INTO repro.users VALUES (1,10.5,20.5),(2,30.5,40.5);
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
(echo 'CREATE SCHEMA repro;'; cat seed.sql) | docker compose exec -T -e PGPASSWORD=repro db psql -U repro -d repro -q
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
curl -s -X POST http://127.0.0.1:4000/cubejs-api/v1/load -H 'Content-Type: application/json' -d '{"query": {"dimensions": ["user_places.location"]}}'
# Run it again if the reply is {"error":"Continue wait"}.
```

**Expected behavior**

Rows as for `users.location`: `10.5,20.5` and `30.5,40.5`.

**Actual behavior**

HTTP 500, `TypeError: Cannot read properties of undefined (reading 'sql')`.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

`CubeSymbols` copies `sql` to a view dimension but not `latitude` and `longitude`. `BaseQuery` reads `symbol.latitude.sql`. ClickHouse and the legacy planner give the same error.

</details>


### gran

**A custom granularity with months on a rollup is wrong before the origin**

- **Claims:** `granularity.custom.offset`
- **Verdict:** `cube-bug`
- **Claim text:** Matrix: a wrong number from Cube Store. A custom granularity with an offset in months, on a measure that a rollup of months serves, gets `DATE_BIN(INTERVAL '3 MONTH', month, to_timestamp('2026-02-01'))`.
- **Command:** `python3 run.py granularity-offset-rollup`

**Output:**

```text
# rest_offset_rollup
$ POST /cubejs-api/v1/load {"measures": ["orders.revenue"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "fiscal_quarter"}], "order": {"orders.created_at": "asc"}}
[ch-on, pg-on]
HTTP 200 [{"orders.created_at.fiscal_quarter": "2025-02-01T00:00:00.000", "orders.revenue": "111", "orders.created_at": "2025-02-01T00:00:00.000"}, {"orders.created_at.fiscal_quarter": "2025-05-01T00:00:00.000", "orders.revenue": "11000", "orders.created_at": "2025-05-01T00:00:00.000"}, {"orders.created_at.fiscal_quarter": "2025-08-01T00:00:00.000", "orders.revenue": "100000", "orders.created_at": "2025-08-01T00:00:0 ...
# rest_offset_no_rollup
$ POST /cubejs-api/v1/load {"measures": ["orders.revenue_no_rollup"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "fiscal_quarter"}], "order": {"orders.created_at": "asc"}}
[ch-on, pg-on]
HTTP 200 [{"orders.created_at.fiscal_quarter": "2024-11-01T00:00:00.000", "orders.revenue_no_rollup": "11", "orders.created_at": "2024-11-01T00:00:00.000"}, {"orders.created_at.fiscal_quarter": "2025-02-01T00:00:00.000", "orders.revenue_no_rollup": "1100", "orders.created_at": "2025-02-01T00:00:00.000"}, {"orders.created_at.fiscal_quarter": "2025-05-01T00:00:00.000", "orders.revenue_no_rollup": "110000", "orders.crea ...
# rest_origin_rollup
$ POST /cubejs-api/v1/load {"measures": ["orders.revenue"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "fiscal_quarter_origin"}], "order": {"orders.created_at": "asc"}}
[ch-on, pg-on]
HTTP 200 [{"orders.created_at.fiscal_quarter_origin": "2025-02-01T00:00:00.000", "orders.revenue": "1111", "orders.created_at": "2025-02-01T00:00:00.000"}, {"orders.created_at.fiscal_quarter_origin": "2025-05-01T00:00:00.000", "orders.revenue": "110000", "orders.created_at": "2025-05-01T00:00:00.000"}]
# rest_old_origin_rollup
$ POST /cubejs-api/v1/load {"measures": ["orders.revenue"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "fiscal_quarter_old_origin"}], "order": {"orders.created_at": "asc"}}
[ch-on, pg-on]
HTTP 200 [{"orders.created_at.fiscal_quarter_old_origin": "2024-11-01T00:00:00.000", "orders.revenue": "11", "orders.created_at": "2024-11-01T00:00:00.000"}, {"orders.created_at.fiscal_quarter_old_origin": "2025-02-01T00:00:00.000", "orders.revenue": "1100", "orders.created_at": "2025-02-01T00:00:00.000"}, {"orders.created_at.fiscal_quarter_old_origin": "2025-05-01T00:00:00.000", "orders.revenue": "110000", "orders.c ...
```

**Truth:** `truth` step: `2024-11-01 11`, `2025-02-01 1100`, `2025-05-01 110000`.

**Why this verdict:** The cause is not the offset. Any origin after a row gives the wrong bin, in Cube Store, for each source database. An `offset` uses an origin in the current year, so old rows go wrong. An `origin` before all rows, `2000-02-01`, gives the truth, so the demo can use that in place of the note in the matrix.

**Code:**

- [`rust/cubestore/cubestore/src/queryplanner/udfs.rs:218-233`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubestore/cubestore/src/queryplanner/udfs.rs#L218): `calc_intervals` divides with `/`. It rounds a negative month count toward zero, so a time before the origin goes to the next bin.
- [`rust/cubestore/cubestore/src/queryplanner/udfs.rs:237`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubestore/cubestore/src/queryplanner/udfs.rs#L237): `calc_bin_timestamp_ym` uses that count.

**Docs:**

- [`docs-mintlify/reference/data-modeling/dimensions.mdx:1103`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/dimensions.mdx#L1103): `offset` and `origin` shift the intervals, so a time belongs to the interval that holds it.

**Issues:**

- none found. Close: #10085, #11474

**Fix layer and test:** `rust/cubestore/cubestore/src/queryplanner/udfs.rs`: use floor division, for example `div_euclid`. Test idea: `DATE_BIN(INTERVAL '3 MONTH', '2025-03-01', '2026-02-01')` must be `2025-02-01`.

**Subclass or package:** No. It is in Cube Store. The model can set `origin` before all rows.


<details><summary>Issue draft: Cube Store: DATE_BIN with months gives the next bin for a time before the origin</summary>

**Title:** Cube Store: DATE_BIN with months gives the next bin for a time before the origin

**Describe the bug**

A custom granularity in months on a rollup puts rows before the origin in the wrong bin. The same query with no rollup is correct.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: postgres:17-alpine
    environment: { POSTGRES_DB: repro, POSTGRES_USER: repro, POSTGRES_PASSWORD: repro }
    healthcheck: { test: [CMD-SHELL, "pg_isready -U repro -d repro"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: postgres
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "5432"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: orders
    sql_table: repro.orders
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: status, sql: status, type: string }
      - name: created_at
        sql: created_at
        type: time
        granularities:
          - { name: fiscal_quarter, interval: 3 months, offset: 1 month }
          - { name: fiscal_quarter_origin, interval: 3 months, origin: "2025-02-01" }
          - { name: fiscal_quarter_old_origin, interval: 3 months, origin: "2000-02-01" }
    measures:
      - { name: revenue, sql: amount, type: sum }
      - { name: revenue_no_rollup, sql: amount * 1, type: sum }
    pre_aggregations:
      - name: monthly
        measures: [revenue]
        dimensions: [status]
        time_dimension: created_at
        granularity: month
        refresh_key: { every: 1 hour }
        indexes:
          - { name: status_idx, columns: [status] }
```

`seed.sql`:

```sql
CREATE TABLE repro.orders (id int PRIMARY KEY, status text, amount int, created_at timestamp);
INSERT INTO repro.orders VALUES
(1, 'a', 1, '2024-12-15 00:00:00'),
(2, 'a', 10, '2025-01-15 00:00:00'),
(3, 'a', 100, '2025-02-15 00:00:00'),
(4, 'a', 1000, '2025-03-15 00:00:00'),
(5, 'a', 10000, '2025-05-15 00:00:00'),
(6, 'a', 100000, '2025-07-15 00:00:00');
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
(echo 'CREATE SCHEMA repro;'; cat seed.sql) | docker compose exec -T -e PGPASSWORD=repro db psql -U repro -d repro -q
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
curl -s -X POST http://127.0.0.1:4000/cubejs-api/v1/load -H 'Content-Type: application/json' -d '{"query": {"measures": ["orders.revenue"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "fiscal_quarter_origin"}], "order": {"orders.created_at": "asc"}}}'
# Run it again if the reply is {"error":"Continue wait"}.
```

**Expected behavior**

`2024-11-01: 11`, `2025-02-01: 1100`, `2025-05-01: 110000`. `orders.revenue_no_rollup` gives these.

**Actual behavior**

`2025-02-01: 1111`, `2025-05-01: 110000`.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

`calc_intervals` in `rust/cubestore/cubestore/src/queryplanner/udfs.rs` truncates a negative count toward zero. An `origin` before all rows avoids this. ClickHouse gives the same result, if the rollup has an index.

</details>


### rollup_join

**rollup_join needs two data sources**

- **Claims:** `preagg.rollup_join`
- **Verdict:** `not-reproduced`
- **Claim text:** Matrix: `rollup_join` needs two data sources. The demo stack has one.
- **Command:** `python3 run.py preagg-rollup-join-one-source`

**Output:**

```text
# rest_join
$ POST /cubejs-api/v1/load {"measures": ["orders.revenue"], "dimensions": ["users.state"], "order": {"users.state": "asc"}}
[ch-off]
HTTP 200 [{"users.state": "TX", "orders.revenue": "10"}, {"users.state": "WA", "orders.revenue": "100"}]
SQL: SELECT
      `users`.state `users__state`, sum(`orders`.amount) `orders__revenue`
    FROM
      repro.orders AS `orders`
LEFT JOIN repro.users AS `users` ON `orders`.user_id = `users`.id  GROUP BY `users__state` ORDER BY `users__state` ASC LIMIT 10000
PARAMS: []
[ch-on, pg-on]
HTTP 200 [{"users.state": "TX", "orders.revenue": "10"}, {"users.state": "WA", "orders.revenue": "100"}]
SQL: SELECT "users__state" "users__state", sum("orders__revenue") "orders__revenue" 
FROM  dev_pre_aggregations.orders_orders_rollup  AS "orders__orders_rollup"
LEFT JOIN  dev_pre_aggregations.users_users_rollup  AS "users__users_rollup" ON "orders__user_id" = "users__id"
GROUP BY 1
ORDER BY  1  ASC
LIMIT 10000
PARAMS: []
# pre_aggs_used
$ POST /cubejs-api/v1/sql token=good
[ch-off]
HTTP 200 {"sql":{"external":false,"sql":["SELECT\n      `users`.state `users__state`, sum(`orders`.amount) `orders__revenue`\n    FROM\n      repro.orders AS `orders`\nLEFT JOIN repro.users AS `users` ON `orders`.user_id = `users`.id  GROUP ...
[ch-on, pg-on]
HTTP 200 {"sql":{"external":true,"sql":["SELECT \"users__state\" \"users__state\", sum(\"orders__revenue\") \"orders__revenue\" \nFROM  dev_pre_aggregations.orders_orders_rollup  AS \"orders__orders_rollup\"\nLEFT JOIN  dev_pre_aggregations ...
```

**Truth:** `truth` step: `TX 10`, `WA 100`.

**Why this verdict:** With Tesseract, a `rollup_join` on one data source runs from Cube Store and gives the truth, so this entry can have a check. The legacy planner reads the source and gives the truth.

**Docs:**

- [`docs-mintlify/reference/data-modeling/pre-aggregations.mdx:433`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/pre-aggregations.mdx#L433): `rollup_join` is not required for one data source. It does not say that it is refused.

**Issues:**

- none found for one data source


### rollup_lambda

**rollup_lambda needs special source data**

- **Claims:** `preagg.rollup_lambda`
- **Verdict:** `our-demo-model`
- **Claim text:** Matrix: `rollup_lambda` needs streaming or partitioned source data. Not modelled yet.
- **Command:** `python3 run.py preagg-rollup-lambda`

**Output:**

```text
# rest_lambda
$ POST /cubejs-api/v1/load {"measures": ["orders.revenue"], "dimensions": ["orders.status"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month"}], "order": {"orders.created_at": "asc"}}
[ch-off]
HTTP 400 ERROR Error: Stream query failed: Error: aborted; query id: <id>
[ch-on, pg-off, pg-on]
HTTP 200 [{"orders.status": "a", "orders.created_at.month": "2025-05-01T00:00:00.000", "orders.revenue": "300", "orders.created_at": "2025-05-01T00:00:00.000"}, {"orders.status": "b", "orders.created_at.month": "2025-06-01T00:00:00.000", "orders.revenue": "400", "orders.created_at": "2025-06-01T00:00:00.000"}]
```

**Truth:** `truth` step: May 300 and June 400.

**Why this verdict:** An ordinary table with a partitioned rollup gives the truth on Tesseract, on ClickHouse too. So the demo can add this. With the lambda second in the list, Cube silently reads only the batch and loses June, and the docs warn about the order. The legacy planner on ClickHouse fails as #12285 says.

**Docs:**

- [`docs-mintlify/docs/pre-aggregations/lambda-pre-aggregations.mdx`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/docs/pre-aggregations/lambda-pre-aggregations.mdx): the batch needs a time dimension and a partition granularity, and the lambda comes first in the list. An ordinary table is enough.

**Issues:**

- [#12285](https://github.com/cube-js/cube/issues/12285) (open): the exact error of the legacy planner on ClickHouse, `Stream query failed: aborted`.
- [#12286](https://github.com/cube-js/cube/issues/12286) (open)
- [#11317](https://github.com/cube-js/cube/issues/11317) (closed)


### rest_only

**compareDateRange and total exist only in the REST API**

- **Claims:** `restapi.compare_date_range`, `restapi.total_query`
- **Verdict:** `documented-behaviour`
- **Claim text:** Matrix: these exist only in the REST API. No Metabase query shape maps to them.
- **Command:** `python3 run.py restapi-compare-date-range-total`

**Output:**

```text
# rest_compare
$ POST /cubejs-api/v1/load {"measures": ["orders.count"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month", "compareDateRange": [["2025-05-01", "2025-05-31"], ["2025-06-01" ...
[ch-on, pg-on]
HTTP 400 ERROR 'compareDateRangeQuery' query type is not supported by the client.Please update the client.
# rest_compare_multi
$ POST /cubejs-api/v1/load token=good
[ch-on, pg-on]
HTTP 200 {"queryType":"compareDateRangeQuery","results":[{"query":{"measures":["orders.count"],"dimensions":[],"timeDimensions":[{"dimension":"orders.created_at","dateRange":["2025-05-01T00:00:00.000" ...
# rest_total
$ POST /cubejs-api/v1/load {"measures": ["orders.count"], "dimensions": ["orders.status"], "limit": 1, "total": true}
[ch-on, pg-on]
HTTP 200 [{"orders.status": "a", "orders.count": "2"}]
total=2
```

**Truth:** Not a number claim.

**Why this verdict:** The claim is correct. `compareDateRange` also needs `queryType: multi`.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/rest-api/query-format.mdx:652`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/rest-api/query-format.mdx#L652): `compareDateRange` is a REST option. The SQL API pages have no such form.

**Issues:**

- none found


### percentile

**PERCENTILE_CONT on ClickHouse**

- **Claims:** `mb.agg.median`, `mb.agg.percentile`
- **Verdict:** `documented-behaviour`
- **Claim text:** Matrix: the Cube template for ClickHouse has no `PERCENTILE_CONT`: `Can't detect Cube query`.
- **Command:** `python3 run.py agg-percentile-cont`

**Output:**

```text
# percentile_cont_raw_column
$ psql> SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount_dim) p FROM orders
[ch-off, ch-on]
psql:<stdin>:1: ERROR:  Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information
QUERY: SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount_dim) AS p FROM orders
[pg-off, pg-on]
p
200
# median_fn
$ psql> SELECT MEDIAN(amount_dim) p FROM orders
[ch-off, ch-on, pg-off, pg-on]
psql:<stdin>:1: ERROR:  Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information
QUERY: SELECT MEDIAN(amount_dim) AS p FROM orders
# truth
$ clickhouse> SELECT quantile(0.5)(amount) p FROM repro.orders
[ch-off, ch-on, pg-off, pg-on]
p
200
```

**Truth:** 200.

**Why this verdict:** The claim is correct and documented. `MEDIAN` fails on each database, because the SQL API has no such function.

**Code:**

- [`packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts:326`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L326): `delete templates.functions.PERCENTILECONT`.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/reference.mdx:558`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/reference.mdx#L558): `PERCENTILE_CONT` pushdown is not supported with ClickHouse.

**Issues:**

- [#10951](https://github.com/cube-js/cube/issues/10951) (open): partial, a docs issue that names this line.

**Subclass or package:** Yes, tested: add `functions.PERCENTILECONT` as `quantile({{ args[0] }})` and `expressions.within_group`, and remove the sort text. `probe-clickhouse-subclass`, step `percentile_cont`, gives 100 on both planners.


### timezone

**The SQL API has no time zone**

- **Claims:** `mb.report_timezone`, docs/query-compilation.md: `SET TIME ZONE` has no effect
- **Verdict:** `not-reproduced`
- **Claim text:** Matrix: the Cube SQL API has no time zone for a query. The docs in this repository: `SET TIME ZONE` has no effect.
- **Command:** `python3 run.py sql-api-time-zone`

**Output:**

```text
# set_time_zone
$ psql script:
[ch-off, ch-on, pg-off, pg-on]
SET TIME ZONE 'Asia/Tokyo';
SHOW TIME ZONE;
SELECT DATE_TRUNC('day', created_at) d, MEASURE(revenue) r FROM orders GROUP BY 1 ORDER BY 1;
setting
d | r
2025-01-02 00:00:00.000000 | 110
# utc_default
$ psql> SELECT DATE_TRUNC('day', created_at) d, MEASURE(revenue) r FROM orders GROUP BY 1 ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
d | r
2025-01-01 00:00:00.000000 | 10
2025-01-02 00:00:00.000000 | 100
# at_time_zone
$ psql> SELECT DATE_TRUNC('day', created_at AT TIME ZONE 'Asia/Tokyo') d, MEASURE(revenue) r FROM orders GROUP BY 1 ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
d | r
2025-01-01 00:00:00.000000 | 10
2025-01-02 00:00:00.000000 | 100
# rest_timezone
$ POST /cubejs-api/v1/load {"timezone": "Asia/Tokyo", "measures": ["orders.revenue"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "day"}], "order": {"orders.created_at": "asc" ...
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"orders.created_at.day": "2025-01-02T00:00:00.000", "orders.revenue": "110", "orders.created_at": "2025-01-02T00:00:00.000"}]
# truth_tokyo
$ clickhouse> SELECT toStartOfDay(created_at,'Asia/Tokyo') d, sum(amount) r FROM repro.orders GROUP BY 1 ORDER BY 1
[ch-off, ch-on]
d	r
2025-01-02 00:00:00	110
[pg-off, pg-on]
d | r
2025-01-02 00:00:00 | 110
```

**Truth:** `truth_tokyo`: `2025-01-02, 110`.

**Why this verdict:** `SET TIME ZONE` works on both databases and both planners, and buckets move to the correct day. `AT TIME ZONE` does not move them. So the driver can use it per session, with care for the connection pool. The earlier claim is wrong for this version.

**Code:**

- [`rust/cubesql/cubesql/src/compile/router.rs:618`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/router.rs#L618): `SET TIME ZONE` stores `query_timezone`.
- [`rust/cubesql/cubesql/src/compile/rewrite/converter.rs:1690`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/rewrite/converter.rs#L1690): the CubeScan takes `query.timezone` from the session.
- [`rust/cubesql/cubesql/src/compile/mod.rs:19187`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/mod.rs#L19187): `test_set_time_zone`.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/reference.mdx:211`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/reference.mdx#L211): `SET TIME ZONE` sets the session time zone.

**Issues:**

- [#4142](https://github.com/cube-js/cube/issues/4142) (closed): the request. A maintainer says that it is implemented for v1.6.11.
- [#9936](https://github.com/cube-js/cube/issues/9936) (closed): the older state.


### readonly

**The SQL API is read only**

- **Claims:** `mb.other.actions`, `mb.other.model_persistence`, `mb.other.uploads`
- **Verdict:** `documented-behaviour`
- **Claim text:** Matrix: Metabase actions, model persistence and uploads write data. The SQL API is read only.
- **Command:** `python3 run.py sql-api-read-only`

**Output:**

```text
# insert
$ psql> INSERT INTO orders (id) VALUES (9)
[ch-on]
psql:<stdin>:1: ERROR:  Unsupported Error: Unsupported query type: INSERT INTO orders (id) VALUES (9)
QUERY: INSERT INTO orders (id) VALUES (9)
# create_table
$ psql> CREATE TABLE x AS SELECT 1 a
[ch-on]
psql:<stdin>:1: ERROR:  Unsupported Error: Unsupported query type: CREATE TABLE x AS SELECT 1 AS a
QUERY: CREATE TABLE x AS SELECT 1 AS a
# update
$ psql> UPDATE orders SET id = 1
[ch-on]
psql:<stdin>:1: ERROR:  Unsupported Error: Unsupported query type: UPDATE orders SET id = 1
QUERY: UPDATE orders SET id = 1
# delete
$ psql> DELETE FROM orders
[ch-on]
psql:<stdin>:1: ERROR:  Unsupported Error: Unsupported query type: DELETE FROM orders
QUERY: DELETE FROM orders
```

**Truth:** Not a number claim.

**Why this verdict:** The claim is correct.

**Code:**

- [`rust/cubesql/cubesql/src/compile/router.rs:256`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/router.rs#L256): `Unsupported query type`.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/reference.mdx:267`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/reference.mdx#L267): the SQL API is a read-only data source.

**Issues:**

- none found


### min_time

**A min measure on a time column**

- **Claims:** `semantic.measure.min_on_time_column`
- **Verdict:** `our-demo-model`
- **Claim text:** Matrix: Cube REST returns the time, but `/v1/meta` types the measure as number and the SQL API returns NULL for `MEASURE()`.
- **Command:** `python3 run.py measure-min-on-time`

**Output:**

```text
# rest_min_time
$ POST /cubejs-api/v1/load {"measures": ["orders.first_min"]}
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"orders.first_min": "2025-05-05T00:00:00.000"}]
# sql_measure_min
$ psql> SELECT MEASURE(first_min) x FROM orders
[ch-off, ch-on, pg-off, pg-on]
x
# sql_measure_time
$ psql> SELECT MEASURE(first_time) x FROM orders
[ch-off, ch-on, pg-off, pg-on]
x
2025-05-05T00:00:00.000
# sql_measure_min_num
$ psql> SELECT MEASURE(amount_min) x FROM orders
[ch-off, ch-on, pg-off, pg-on]
x
100
# truth
$ clickhouse> SELECT min(created_at) x FROM repro.orders
[ch-off, ch-on, pg-off, pg-on]
x
2025-05-05 00:00:00
```

**Truth:** `2025-05-05 00:00:00`.

**Why this verdict:** The claim is correct, but the docs ask for a number in `min`, so the demo measure breaks that rule. `type: time` with `sql: min(created_at)` gives the time. The silent NULL is poor, and #8153 tracks it.

**Code:**

- [`rust/cubesql/cubesql/src/transport/ext.rs:117`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/transport/ext.rs#L117): a `min` measure has the type Double in the SQL API.

**Docs:**

- [`docs-mintlify/reference/data-modeling/measures.mdx:372`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/measures.mdx#L372): `min` takes a non-aggregated numeric expression.

**Issues:**

- [#8153](https://github.com/cube-js/cube/issues/8153) (open): the exact case. A maintainer calls it intended.


### big

**Numbers above 2^53 lose digits**

- **Claims:** `compile.ch.uint64_above_double`, `compile.ch.decimal_above_double`
- **Verdict:** `cube-bug`
- **Claim text:** Matrix: Cube returns numbers through JavaScript.
- **Command:** `python3 run.py big-numbers-precision`

**Output:**

```text
# sql_uint64
$ psql> SELECT big_int FROM events
[ch-off, ch-on, pg-off, pg-on]
big_int
9007199254740996
# sql_decimal
$ psql> SELECT big_dec FROM events
[ch-off, ch-on, pg-off, pg-on]
big_dec
3000000000000
# sql_measures
$ psql> SELECT MEASURE(sum_big_int) a, MEASURE(sum_big_dec) b FROM events
[ch-off, ch-on, pg-off, pg-on]
a | b
9007199254740996 | 3000000000000
# rest_dims
$ POST /cubejs-api/v1/load {"dimensions": ["events.big_int", "events.big_dec"]}
[ch-off, ch-on]
HTTP 200 [{"events.big_int": "9007199254740996", "events.big_dec": "3000000000000"}]
[pg-off, pg-on]
HTTP 200 [{"events.big_int": "9007199254740995", "events.big_dec": "3000000000000.00000003"}]
# sql_text_dims
$ psql> SELECT big_int_text, big_dec_text FROM events
[ch-off, ch-on, pg-off, pg-on]
big_int_text | big_dec_text
9007199254740995 | 3000000000000.00000003
# truth
$ clickhouse> SELECT big_int, big_dec FROM repro.events
[ch-off, ch-on]
big_int	big_dec
9007199254740995	3000000000000.00000003
[pg-off, pg-on]
big_int | big_dec
9007199254740995 | 3000000000000.00000003
```

**Truth:** `9007199254740995` and `3000000000000.00000003`.

**Why this verdict:** There are two causes. On the SQL API, each database loses digits, because the type is Double. On REST, Postgres keeps the digits and ClickHouse does not, so the second cause is the ClickHouse driver. The workaround in the matrix, a text dimension, gives the truth. The matrix says only JavaScript.

**Code:**

- [`rust/cubesql/cubesql/src/transport/ext.rs:105`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/transport/ext.rs#L105): a `number` dimension is `ColumnType::Double` in the SQL API.
- [`rust/cubesql/cubesql/src/transport/ext.rs:115`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/transport/ext.rs#L115): a `sum` measure too.
- [`packages/cubejs-clickhouse-driver/src/ClickHouseDriver.ts:354`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-clickhouse-driver/src/ClickHouseDriver.ts#L354): the driver reads `JSONCompact`, and ClickHouse writes 64-bit values as JSON numbers, so JavaScript rounds them.

**Issues:**

- [#9136](https://github.com/cube-js/cube/issues/9136) (closed): the same symptom on BigQuery, fixed in that driver with `wrapIntegers`.
- ClickHouse: none found. Close: #8741

**Fix layer and test:** cubesql in Rust for the SQL API: keep the database type for a `number` member. `packages/cubejs-clickhouse-driver`: set `output_format_json_quote_64bit_integers` and `output_format_json_quote_decimals`. Test idea: `9007199254740995` through both APIs.

**Subclass or package:** Only for REST: a `ClickHouseDriver` subclass can set these settings in `clickhouse_settings`. I did not test it. The driver of this repository reads the SQL API, so it does not help here.


<details><summary>Issue draft: SQL API: a number member above 2^53 loses digits on each database</summary>

**Title:** SQL API: a number member above 2^53 loses digits on each database

**Describe the bug**

The SQL API types a `number` member as Double. REST on Postgres keeps the digits.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: postgres:17-alpine
    environment: { POSTGRES_DB: repro, POSTGRES_USER: repro, POSTGRES_PASSWORD: repro }
    healthcheck: { test: [CMD-SHELL, "pg_isready -U repro -d repro"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: postgres
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "5432"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: events
    sql_table: repro.events
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: big_int, sql: big_int, type: number }
      - { name: big_dec, sql: big_dec, type: number }
      - { name: big_int_text, sql: "CAST(big_int AS VARCHAR)", type: string }
      - { name: big_dec_text, sql: "CAST(big_dec AS VARCHAR)", type: string }
    measures:
      - { name: sum_big_int, sql: big_int, type: sum }
      - { name: sum_big_dec, sql: big_dec, type: sum }
```

`seed.sql`:

```sql
CREATE TABLE repro.events (id int, big_int bigint, big_dec numeric(38,8));
INSERT INTO repro.events VALUES (1, 9007199254740995, 3000000000000.00000003);
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
(echo 'CREATE SCHEMA repro;'; cat seed.sql) | docker compose exec -T -e PGPASSWORD=repro db psql -U repro -d repro -q
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
docker run --rm --network host -e PGPASSWORD=repro postgres:17-alpine psql -h 127.0.0.1 -p 15432 -U repro -d repro -c "SELECT big_int, big_dec FROM events"
```

**Expected behavior**

`9007199254740995 | 3000000000000.00000003`.

**Actual behavior**

`9007199254740996 | 3000000000000`. REST on Postgres gives the correct text. On ClickHouse REST also rounds, because the driver reads JSON numbers.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

`transport/ext.rs` maps `number` to `ColumnType::Double`. A dimension with `CAST(x AS VARCHAR)` keeps the digits.

</details>


### propagate

**Two sub_query dimensions with the same SQL**

- **Claims:** `dimension.opt.propagate_filters_next_to_another_sub_query`
- **Verdict:** `cube-bug`
- **Claim text:** Matrix: in one SQL API query with a second `sub_query` dimension, the propagated dimension ignores the filter.
- **Command:** `python3 run.py propagate-filters-next-to-sub-query`

**Output:**

```text
# rest_alone
$ POST /cubejs-api/v1/load {"dimensions": ["users.id", "users.paid_orders"], "filters": [{"member": "orders.status", "operator": "equals", "values": ["early"]}], "order": {"users.id": "asc"}}
[ch-on, pg-off, pg-on]
HTTP 200 [{"users.id": "1", "users.paid_orders": "1"}]
# rest_together
$ POST /cubejs-api/v1/load {"dimensions": ["users.id", "users.lifetime_orders", "users.paid_orders"], "filters": [{"member": "orders.status", "operator": "equals", "values": ["early"]}], "order": {"users.id": "asc"}}
[ch-on]
HTTP 200 [{"users.id": "1", "users.lifetime_orders": "2", "users.paid_orders": "2"}]
SQL: SELECT `users`.id `users__id`, `users_lifetime_orders_subquery`.`lifetime_orders` `users__lifetime_orders`, `users_lifetime_orders_subquery`.`lifetime_orders` `users__paid_orders` 
FROM  repro.orders  AS `orders`
LEFT JOIN  repro.users  AS `users` ON `orders`.user_id = `users`.id
LEFT JOIN (SELECT `users`.id `users__id`, count(`orders`.id) `lifetime_orders` 
FROM  repro.orders  AS `orders`
LEFT JOIN  repro.users  AS `users` ON `orders`.user_id = `users`.id
GROUP BY 1) AS `users_lifetime_orders_subquery` ON ((`users_lifetime_orders_subquery`.`users__id` = `users`.id))
LEFT JOIN (SELECT `users`.id `users__id`, count(`orders`.id) `paid_orders` 
FROM  repro.orders  AS `orders`
LEFT JOIN  repro.users  AS `users` ON `orders`.user_id = `users`.id
WHERE (`orders`.status = ?)
GROUP BY 1) AS `users_paid_orders_subquery` ON ((`users_paid_orders_subquery`.`users__id` = `users`.id))
WHERE (`orders`.status = ?)
GROUP BY 1, 2, 3
ORDER BY  1  ASC
LIMIT 10000
PARAMS: ["early", "early"]
[pg-off]
HTTP 200 [{"users.id": "1", "users.lifetime_orders": "2", "users.paid_orders": "1"}]
SQL: SELECT
      "users".id "users__id", "users__lifetime_orders" "users__lifetime_orders", "users__paid_orders" "users__paid_orders"
    FROM
      repro.orders AS "orders"
LEFT JOIN repro.users AS "users" ON "orders".user_id = "users".id
LEFT JOIN (SELECT
      "users_lifetime_orders_subquery__users".id "users__id", count("users_lifetime_orders_subquery__orders".id) "users__lifetime_orders"
    FROM
      repro.orders AS "users_lifetime_orders_subquery__orders"
LEFT JOIN repro.users AS "users_lifetime_orders_subquery__users" ON "users_lifetime_orders_subquery__orders".user_id = "users_lifetime_orders_subquery__users".id  GROUP BY 1) AS "users_lifetime_orders_subquery" ON "users_lifetime_orders_subquery"."users__id" = ...
LEFT JOIN (SELECT
      "users_paid_orders_subquery__users".id "users__id", count("users_paid_orders_subquery__orders".id) "users__paid_orders"
    FROM
      repro.orders AS "users_paid_orders_subquery__orders"
LEFT JOIN repro.users AS "users_paid_orders_subquery__users" ON "users_paid_orders_subquery__orders".user_id = "users_paid_orders_subquery__users".id  WHERE ("users_paid_orders_subquery__orders".status = $1) GROUP BY 1) AS "users_paid_orders_subquery" ON "user ...
PARAMS: ["early", "early"]
[pg-on]
HTTP 200 [{"users.id": "1", "users.lifetime_orders": "2", "users.paid_orders": "2"}]
SQL: SELECT "users".id "users__id", "users_lifetime_orders_subquery"."lifetime_orders" "users__lifetime_orders", "users_lifetime_orders_subquery"."lifetime_orders" "users__paid_orders" 
FROM  repro.orders  AS "orders"
LEFT JOIN  repro.users  AS "users" ON "orders".user_id = "users".id
LEFT JOIN (SELECT "users".id "users__id", count("orders".id) "lifetime_orders" 
FROM  repro.orders  AS "orders"
LEFT JOIN  repro.users  AS "users" ON "orders".user_id = "users".id
GROUP BY 1) AS "users_lifetime_orders_subquery" ON (("users_lifetime_orders_subquery"."users__id" = "users".id))
LEFT JOIN (SELECT "users".id "users__id", count("orders".id) "paid_orders" 
FROM  repro.orders  AS "orders"
LEFT JOIN  repro.users  AS "users" ON "orders".user_id = "users".id
WHERE ("orders".status = $1)
GROUP BY 1) AS "users_paid_orders_subquery" ON (("users_paid_orders_subquery"."users__id" = "users".id))
WHERE ("orders".status = $2)
GROUP BY 1, 2, 3
ORDER BY  1  ASC
LIMIT 10000
PARAMS: ["early", "early"]
# rest_together_swapped
$ POST /cubejs-api/v1/load {"dimensions": ["users.id", "users.paid_orders", "users.lifetime_orders"], "filters": [{"member": "orders.status", "operator": "equals", "values": ["early"]}], "order": {"users.id": "asc"}}
[ch-on, pg-on]
HTTP 200 [{"users.id": "1", "users.paid_orders": "1", "users.lifetime_orders": "1"}]
[pg-off]
HTTP 200 [{"users.id": "1", "users.paid_orders": "1", "users.lifetime_orders": "2"}]
# rest_other_sql
$ POST /cubejs-api/v1/load {"dimensions": ["users.id", "users.lifetime_orders", "users.paid_orders_other_sql"], "filters": [{"member": "orders.status", "operator": "equals", "values": ["early"]}], "order": {"users.id": "asc"}}
[ch-on, pg-off, pg-on]
HTTP 200 [{"users.id": "1", "users.lifetime_orders": "2", "users.paid_orders_other_sql": "1"}]
# truth
$ clickhouse> SELECT user_id AS id, count(*) AS lifetime, sum(CASE WHEN status='early' THEN 1 ELSE 0 END) AS filtered FROM repro.orders GROUP BY 1 ORDER BY 1
[ch-on]
id	lifetime	filtered
1	2	1
2	1	0
[pg-off, pg-on]
id | lifetime | filtered
1 | 2 | 1
2 | 1 | 0
```

**Truth:** `truth` step: user 1 has 2 orders, 1 with the filter.

**Why this verdict:** The generated SQL joins both subqueries, then selects the first for both columns. The order of the dimensions decides the result. So the filter is not ignored. With other SQL, `{orders.count} + 0`, Tesseract gives the truth. The legacy planner gives the truth. This depends on Tesseract and not on the database.

**Code:**

- [`rust/cube/cubesqlplanner/cubesqlplanner/src/physical_plan_builder/builder.rs:213`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cube/cubesqlplanner/cubesqlplanner/src/physical_plan_builder/builder.rs#L213): `find_reference_for_member` finds the column by the measure, and both dimensions have the measure `{orders.count}`, so the first subquery wins.
- [`rust/cube/cubesqlplanner/cubesqlplanner/src/planner/planners/dimension_subquery_planner.rs:116`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cube/cubesqlplanner/cubesqlplanner/src/planner/planners/dimension_subquery_planner.rs#L116): the filter reaches the second subquery correctly.

**Docs:**

- [`docs-mintlify/reference/data-modeling/dimensions.mdx:787`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/dimensions.mdx#L787): filters pass to the subquery.

**Issues:**

- none found. Close: #8081, #8479

**Fix layer and test:** Tesseract in Rust, `builder.rs` and `dimension_subquery_planner.rs`: key the reference by the sub_query dimension. Test idea: two dimensions with `{orders.count}`, one with propagation, in `tests/subquery_dimensions`.

**Subclass or package:** No. It is in Rust. The model can use different SQL in each dimension.


<details><summary>Issue draft: Tesseract: two sub_query dimensions with the same SQL return the first subquery</summary>

**Title:** Tesseract: two sub_query dimensions with the same SQL return the first subquery

**Describe the bug**

Two `sub_query` dimensions have the same `sql`. The SQL joins two subqueries, and both columns read the first one.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: postgres:17-alpine
    environment: { POSTGRES_DB: repro, POSTGRES_USER: repro, POSTGRES_PASSWORD: repro }
    healthcheck: { test: [CMD-SHELL, "pg_isready -U repro -d repro"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: postgres
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "5432"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: users
    sql_table: repro.users
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: lifetime_orders, sql: "{orders.count}", type: number, sub_query: true }
      - { name: paid_orders, sql: "{orders.count}", type: number, sub_query: true, propagate_filters_to_sub_query: true }
      - { name: paid_orders_other_sql, sql: "{orders.count} + 0", type: number, sub_query: true, propagate_filters_to_sub_query: true }
      - { name: total_ids, sql: "{orders.count_ids}", type: number, sub_query: true }
  - name: orders
    sql_table: repro.orders
    joins:
      - { name: users, relationship: many_to_one, sql: "{CUBE}.user_id = {users.id}" }
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: status, sql: status, type: string }
    measures:
      - { name: count, type: count }
      - { name: count_ids, sql: id, type: count_distinct }
```

`seed.sql`:

```sql
CREATE TABLE repro.users (id int);
CREATE TABLE repro.orders (id int, user_id int, status text);
INSERT INTO repro.users VALUES (1),(2);
INSERT INTO repro.orders VALUES (1,1,'early'),(2,1,'late'),(3,2,'late');
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
(echo 'CREATE SCHEMA repro;'; cat seed.sql) | docker compose exec -T -e PGPASSWORD=repro db psql -U repro -d repro -q
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
curl -s -X POST http://127.0.0.1:4000/cubejs-api/v1/load -H 'Content-Type: application/json' -d '{"query": {"dimensions": ["users.id", "users.lifetime_orders", "users.paid_orders"], "filters": [{"member": "orders.status", "operator": "equals", "values": ["early"]}]}}'
# Run it again if the reply is {"error":"Continue wait"}.
```

**Expected behavior**

`lifetime_orders: 2`, `paid_orders: 1`.

**Actual behavior**

`lifetime_orders: 2`, `paid_orders: 2`. With the dimensions swapped, both are 1.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

The legacy planner is correct. A different `sql`, `{orders.count} + 0`, is correct. ClickHouse gives the same result.

</details>


### time_shift

**A time shift measure with no bucket gives the total**

- **Claims:** `measure.multi_stage.time_shift_no_bucket_limit`
- **Verdict:** `unclear-intent`
- **Claim text:** Matrix: a wrong number from Cube. `/v1/meta` does not tell which measure has a time shift.
- **Command:** `python3 run.py time-shift-no-bucket`

**Output:**

```text
# sql_no_bucket
$ psql> SELECT MEASURE(revenue_prior_month) x FROM orders
[ch-off, ch-on, pg-off, pg-on]
x
111
# rest_no_bucket
$ POST /cubejs-api/v1/load {"measures": ["orders.revenue_prior_month"]}
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"orders.revenue_prior_month": "111"}]
# rest_date_range_no_granularity
$ POST /cubejs-api/v1/load {"measures": ["orders.revenue_prior_month"], "timeDimensions": [{"dimension": "orders.created_at", "dateRange": ["2025-05-01", "2025-05-31"]}]}
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"orders.revenue_prior_month": "1"}]
# rest_by_month
$ POST /cubejs-api/v1/load {"measures": ["orders.revenue_prior_month"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month", "dateRange": ["2025-05-01", "2025-06-30"]}], "orde ...
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"orders.created_at.month": "2025-05-01T00:00:00.000", "orders.revenue_prior_month": "1", "orders.created_at": "2025-05-01T00:00:00.000"}, {"orders.created_at.month": "2025-06-01T00:00:00.00 ...
# truth_all
$ clickhouse> SELECT sum(amount) FROM repro.orders
[ch-off, ch-on]
sum(amount)
111
[pg-off, pg-on]
sum
111
# meta_mentions
$ GET /cubejs-api/v1/meta?extended=true
[ch-off, ch-on, pg-off, pg-on]
timeShift: 0
time_shift: 0
multiStage: 0
```

**Truth:** With no date range, shifting all rows by a month keeps the same sum, 111.

**Why this verdict:** The number has a defined meaning: a sum over all time does not change after a shift. With a date range, it gives the value of the earlier month, and this is correct. So I do not call this wrong. The `/v1/meta` part of the claim is correct.

**Docs:**

- [`docs-mintlify/reference/data-modeling/measures.mdx`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/measures.mdx): `time_shift` is documented with a time dimension.

**Issues:**

- none found. Close: #11536, #11240, #11030, #12009


### datetime64

**A DateTime64 loses milliseconds**

- **Claims:** `clickhouse.datetime64_millis`
- **Verdict:** `cube-clickhouse-bug`
- **Claim text:** Matrix: Cube reads a time as whole seconds.
- **Command:** `python3 run.py datetime64-millis`

**Output:**

```text
# sql_raw
$ psql> SELECT id, ts FROM events ORDER BY 1
[ch-off, ch-on]
id | ts
1 | 2025-01-01 10:00:00.000000
2 | 2025-01-01 10:00:00.000000
[pg-off, pg-on]
id | ts
1 | 2025-01-01 10:00:00.007000
2 | 2025-01-01 10:00:00.500000
# sql_filter_ms
$ psql> SELECT MEASURE(count) c FROM events WHERE ts >= '2025-01-01 10:00:00.007' AND ts < '2025-01-01 10:00:00.008'
[ch-off, ch-on]
c
0
[pg-off, pg-on]
c
1
# rest_raw
$ POST /cubejs-api/v1/load {"dimensions": ["events.id", "events.ts"], "order": {"events.id": "asc"}}
[ch-off, ch-on]
HTTP 200 [{"events.id": "1", "events.ts": "2025-01-01T10:00:00.000"}, {"events.id": "2", "events.ts": "2025-01-01T10:00:00.000"}]
[pg-off, pg-on]
HTTP 200 [{"events.id": "1", "events.ts": "2025-01-01T10:00:00.007"}, {"events.id": "2", "events.ts": "2025-01-01T10:00:00.500"}]
# rest_filter_ms
$ POST /cubejs-api/v1/load {"measures": ["events.count"], "timeDimensions": [{"dimension": "events.ts", "dateRange": ["2025-01-01T10:00:00.007", "2025-01-01T10:00:00.007"]}]}
[ch-off, ch-on]
HTTP 200 [{"events.count": "0"}]
[pg-off, pg-on]
HTTP 200 [{"events.count": "1"}]
# truth_raw
$ clickhouse> SELECT id, ts FROM repro.events ORDER BY 1
[ch-off, ch-on]
id	ts
1	2025-01-01 10:00:00.007
2	2025-01-01 10:00:00.500
[pg-off, pg-on]
id | ts
1 | 2025-01-01 10:00:00.007
2 | 2025-01-01 10:00:00.5
# truth_filter
$ clickhouse> SELECT count(*) c FROM repro.events WHERE ts >= '2025-01-01 10:00:00.007' AND ts < '2025-01-01 10:00:00.008'
[ch-off, ch-on, pg-off, pg-on]
c
1
```

**Truth:** `.007` and `.500`. The filter finds 1.

**Why this verdict:** Postgres keeps milliseconds through both APIs, so the cause is the ClickHouse dialect and not Cube in general.

**Code:**

- [`packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts:91`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L91): `convertTz` writes `toDateTime64(field, 0)`, so a selected time has whole seconds.
- [`packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts:163-175`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L163): the same `parseDateTimeBestEffort` as in B1 for a filter.

**Issues:**

- [#9051](https://github.com/cube-js/cube/issues/9051) (open): the exact case.
- [#10326](https://github.com/cube-js/cube/issues/10326) (open): the filter.
- [#2536](https://github.com/cube-js/cube/issues/2536) (open): partial.

**Fix layer and test:** `ClickHouseQuery.convertTz` and `dateTimeCast`: use precision 3. Test idea: a `DateTime64(3)` column through both APIs.

**Subclass or package:** Yes, tested: override `convertTz` and `dateTimeCast`. `probe-clickhouse-subclass`, steps `millis_raw` and `millis_filter`, give the truth on both planners.

**Issue draft:** Do not open a new issue. Add this repro to [#9051](https://github.com/cube-js/cube/issues/9051) and [#10326](https://github.com/cube-js/cube/issues/10326): see `dev/repro/datetime64-millis`. Postgres keeps milliseconds, and `toDateTime64(field, 3)` with `parseDateTime64BestEffort` fixes both on ClickHouse.


### rolling_type

**Deprecated rolling window types**

- **Claims:** `measure.opt.rolling_type_deprecated`
- **Verdict:** `documented-behaviour`
- **Claim text:** Matrix: `year_to_date`, `quarter_to_date` and `month_to_date` are deprecated and Tesseract does not read them.
- **Command:** `python3 run.py rolling-type-deprecated-a`, `python3 run.py rolling-type-deprecated-b`

**Output:**

```text
# rest_mtd_type
$ POST /cubejs-api/v1/load {"measures": ["orders.rev_mtd_type"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month", "dateRange": ["2025-04-01", "2025-04-30"]}]}
[ch-off, pg-off]
HTTP 200 [{"orders.created_at.month": "2025-04-01T00:00:00.000", "orders.rev_mtd_type": "10", "orders.created_at": "2025-04-01T00:00:00.000"}]
[ch-on, pg-on]
HTTP 200 [{"orders.created_at.month": "2025-04-01T00:00:00.000", "orders.rev_mtd_type": null, "orders.created_at": "2025-04-01T00:00:00.000"}]
# rest_to_date
$ POST /cubejs-api/v1/load {"measures": ["orders.rev_mtd_new"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month", "dateRange": ["2025-04-01", "2025-04-30"]}]}
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"orders.created_at.month": "2025-04-01T00:00:00.000", "orders.rev_mtd_new": "10", "orders.created_at": "2025-04-01T00:00:00.000"}]
```

**Truth:** 10, the April row. `to_date` gives this on each planner.

**Why this verdict:** The claim is correct and documented. Tesseract returns NULL with no error.

**Code:**

- [`packages/cubejs-schema-compiler/src/compiler/CubeValidator.ts:467`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/compiler/CubeValidator.ts#L467): the validator still takes the old type.

**Docs:**

- [`docs-mintlify/reference/data-modeling/measures.mdx:532`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/measures.mdx#L532): Tesseract does not recognize them.

**Issues:**

- none found


### impersonation

**SET user and __user**

- **Claims:** `security.sql_user_impersonation`, docs/security.md: the table of SET user
- **Verdict:** `documented-behaviour`
- **Claim text:** Matrix and docs: a super user can read as anybody through `__user`. `SET user` works one way, so a pool cannot give the connection back. A normal user is refused.
- **Command:** `python3 run.py sql-user-impersonation`, `python3 run.py sql-user-switch-custom-rule`, `python3 run.py sql-user-normal-user`

**Output:**

```text
# set_user_once
$ psql script:
[ch-on]
SET user = 'alice';
SELECT current_user;
SELECT MEASURE(count) FROM orders;
SET user = 'bob';
SELECT current_user;
current_user
alice
measure(orders.count)
3
psql:<stdin>:4: ERROR:  User Error: user 'alice' is not allowed to switch to 'bob'
QUERY: SET user = 'bob'
current_user
alice
# where_user_super
$ psql> SELECT __user, MEASURE(count) c FROM orders WHERE __user = 'alice' GROUP BY 1
[ch-on]
__user | c
 | 3
```
```text
# switch_there_and_back
$ psql script:
[ch-on]
SET user = 'alice';
SELECT current_user;
SET user = 'bob';
SELECT current_user;
SET user = 'repro';
SELECT current_user;
current_user
alice
current_user
bob
current_user
repro
```
```text
# set_user
$ psql script:
[ch-on]
SET user = 'alice';
SELECT current_user;
psql:<stdin>:1: ERROR:  User Error: user 'repro' is not allowed to switch to 'alice'
QUERY: SET user = 'alice'
current_user
repro
# where_user
$ psql> SELECT MEASURE(count) c FROM orders WHERE __user = 'alice'
[ch-on]
psql:<stdin>:1: ERROR:  Database Execution Error: You cannot change security context via __user from repro to alice, because it's not allowed.
```

**Truth:** Not a number claim.

**Why this verdict:** The claim is correct with the default rule. The first run of `sql-user-impersonation` has a `canSwitchSqlUser` that allows only the super user. With `canSwitchSqlUser: async () => true`, the session can go back, so the limit depends on the configuration. It still does not suit a pool that gets native SQL from people.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/security.mdx:156`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/security.mdx#L156): `__user` changes the security context for a query.
- [`docs-mintlify/reference/core-data-apis/sql-api/security.mdx:152`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/security.mdx#L152): `canSwitchSqlUser` defines the rule.

**Issues:**

- none found


### meta_cloud

**meta.auto_run and meta.default_ui_filters**

- **Claims:** `view.meta_auto_run`, `view.meta_default_ui_filters`
- **Verdict:** `documented-behaviour`
- **Claim text:** Matrix: these control Cube Cloud workbooks. Metabase has no such behaviour.

**Output:**

No run. This is a claim about the docs.

**Truth:** Not a number claim.

**Why this verdict:** The claim is correct.

**Docs:**

- [`docs-mintlify/reference/data-modeling/view.mdx:240`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/view.mdx#L240): `auto_run` is for workbooks in the Cube cloud platform.
- [`docs-mintlify/docs/explore-analyze/workbooks/querying-data.mdx`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/docs/explore-analyze/workbooks/querying-data.mdx): `default_ui_filters`.

**Issues:**

- none found


### nonstrict

**Default non-strict date range on a rollup**

- **Claims:** docs/query-compilation.md: required settings, first row
- **Verdict:** `documented-behaviour`
- **Claim text:** Docs here: the default `true` lets a rollup of months answer a filter on one day. In the demo, that filter gave NULL where the truth was 14.
- **Command:** `python3 run.py non-strict-range-default`, `python3 run.py non-strict-range-false`

**Output:**

```text
# rest_one_day
$ POST /cubejs-api/v1/load {"measures": ["orders.count"], "timeDimensions": [{"dimension": "orders.created_at", "dateRange": ["2025-05-05", "2025-05-05"]}]}
[ch-off, ch-on, pg-off, pg-on]
HTTP 200 [{"orders.count": null}]
# sql_one_day
$ psql> SELECT MEASURE(count) c FROM orders WHERE created_at >= '2025-05-05' AND created_at < '2025-05-06'
[ch-off, ch-on, pg-off, pg-on]
c
# truth
$ clickhouse> SELECT count(*) c FROM repro.orders WHERE created_at >= '2025-05-05' AND created_at < '2025-05-06'
[ch-off, ch-on, pg-off, pg-on]
c
1
```
```text
# rest_one_day
$ POST /cubejs-api/v1/load {"measures": ["orders.count"], "timeDimensions": [{"dimension": "orders.created_at", "dateRan ...
[ch-on, pg-on]
HTTP 200 [{"orders.count": "1"}]
```

**Truth:** 1.

**Why this verdict:** The claim is correct, and the setting is documented. The NULL comes from the rollup, see the entry m3.

**Docs:**

- [`docs-mintlify/reference/configuration/environment-variables.mdx:1647`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/configuration/environment-variables.mdx#L1647): the default is `true`.
- [`docs-mintlify/reference/data-modeling/pre-aggregations.mdx`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/pre-aggregations.mdx): `allow_non_strict_date_range_match`.

**Issues:**

- none found. Close: #12284


### limit

**Post-processing reads at most CUBEJS_DB_QUERY_LIMIT rows**

- **Claims:** docs/query-compilation.md: required settings, second row
- **Verdict:** `documented-behaviour`
- **Claim text:** Docs here: post-processing computes from 50000 rows with no error.
- **Command:** `python3 run.py post-processing-query-limit`

**Output:**

```text
# count_outer
$ psql> SELECT COUNT(*) c FROM (SELECT id, MEASURE(count) n FROM orders GROUP BY 1) q
[ch-on, pg-on]
c
10
# truth
$ clickhouse> SELECT count(*) c FROM repro.orders
[ch-on, pg-on]
c
20
```

**Truth:** 20. The limit in this repro is 10, so it needs few rows.

**Why this verdict:** The claim is correct and documented.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx:397`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L397): the row limit edge case, with the same warning.
- [`docs-mintlify/reference/configuration/environment-variables.mdx:1250`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/configuration/environment-variables.mdx#L1250): the default is 50000.

**Issues:**

- [#11170](https://github.com/cube-js/cube/issues/11170) (open): the exact behaviour.
- [#6883](https://github.com/cube-js/cube/issues/6883) (closed): partial.


### stream

**Stream mode with a rollup**

- **Claims:** docs/query-compilation.md: `CUBESQL_STREAM_MODE`
- **Verdict:** `unclear-intent`
- **Claim text:** Docs here: `CUBESQL_STREAM_MODE=true` removes the limit, but each query that a rollup serves fails.
- **Command:** `python3 run.py stream-mode-rollup`

**Output:**

```text
# sql_rollup_month
$ psql> SELECT DATE_TRUNC('month',created_at) m, MEASURE(count) c FROM orders GROUP BY 1 ORDER BY 1
[ch-on, pg-on]
psql:<stdin>:1: ERROR:  Internal Error: Error: Streaming queries to Cube Store aren't supported
# sql_no_rollup
$ psql> SELECT id FROM orders ORDER BY 1
[ch-on, pg-on]
id
1
2
3
```

**Truth:** Not a number claim.

**Why this verdict:** The claim is correct. The code refuses on purpose, and the docs do not tell this, and they recommend this mode.

**Code:**

- [`packages/cubejs-query-orchestrator/src/orchestrator/QueryQueue.ts:243`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-query-orchestrator/src/orchestrator/QueryQueue.ts#L243): the error is written on purpose.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx:451`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L451): stream mode is a workaround for the limit, with no word on rollups.

**Issues:**

- none found. Close: #10875


### join_on

**A join ON condition**

- **Claims:** docs/query-compilation.md: join on columns, docs/semantic-sync.md: Cube ignores the condition
- **Verdict:** `documented-behaviour`
- **Claim text:** Docs here: Cube ignores the `ON` columns and uses the model join.
- **Command:** `python3 run.py join-on-condition`

**Output:**

```text
# on_cube_join_field
$ psql> SELECT o.id, u.vip FROM orders o LEFT JOIN users u ON o.__cubeJoinField = u.__cubeJoinField ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
id | vip
1 | 1
2 | 0
# on_other_columns
$ psql> SELECT o.id, u.vip FROM orders o LEFT JOIN users u ON o.other_id = u.id ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
psql:<stdin>:1: ERROR:  Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information
QUERY: SELECT o.id, u.vip FROM orders o LEFT JOIN users u ON o.other_id = u.id ORDER BY 1
# on_model_columns
$ psql> SELECT o.id, u.vip FROM orders o LEFT JOIN users u ON o.user_id = u.id ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
psql:<stdin>:1: ERROR:  Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information
QUERY: SELECT o.id, u.vip FROM orders o LEFT JOIN users u ON o.user_id = u.id ORDER BY 1
# cross_other_where
$ psql> SELECT o.id, u.vip FROM orders o CROSS JOIN users u WHERE o.other_id = u.id ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
id | vip
# truth_on_other
$ clickhouse> SELECT o.id, u.vip FROM repro.orders o LEFT JOIN repro.users u ON o.other_id = u.id ORDER BY 1
[ch-off, ch-on]
id	vip
1	0
2	1
[pg-off, pg-on]
id | vip
1 | 0
2 | 1
```

**Truth:** `truth_on_other`: `1, 0` and `2, 1`.

**Why this verdict:** The docs of Cube say this for `__cubeJoinField` and `CROSS JOIN`. The text in this repository is not exact: `JOIN ... ON a = b` on other columns is refused with `Can't detect Cube query`, and it is not ignored. `CROSS JOIN ... WHERE o.other_id = u.id` applies the model join and then the filter, so it gives no rows with no error.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/joins.mdx:83`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/joins.mdx#L83): `CROSS JOIN` or `__cubeJoinField` joins as in the data model.

**Issues:**

- none found


### join_sub

**A join of a question or with a filter inside**

- **Claims:** docs/query-compilation.md: join of a question
- **Verdict:** `unclear-intent`
- **Claim text:** Docs here: Cube has `__cubeJoinField` only on a whole cube.
- **Command:** `python3 run.py join-on-condition`

**Output:**

```text
# left_filtered_projected
$ psql> SELECT o.id, u.vip FROM orders o LEFT JOIN (SELECT id, vip FROM users WHERE vip = 1) u ON o.__cubeJoinField = u.__cubeJoinField ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
psql:<stdin>:1: ERROR:  Planning Error: Initial planning error: Error during planning: No field named 'u.__cubeJoinField'. Valid fields are 'o.count', 'o.id', 'o.user_id', 'o.other_id', 'o.__user', 'o.__cubeJoi ...
QUERY: SELECT o.id, u.vip FROM orders o LEFT JOIN (SELECT id, vip FROM users WHERE vip = 1) u ON o.__cubeJoinField = u.__cubeJoinField ORDER BY 1
# left_filtered_star
$ psql> SELECT o.id, u.vip FROM orders o LEFT JOIN (SELECT * FROM users WHERE vip = 1) u ON o.__cubeJoinField = u.__cubeJoinField ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
id | vip
1 | 1
# subquery_with_filter
$ psql> SELECT o.id, u.vip FROM orders o CROSS JOIN (SELECT * FROM users WHERE vip = 1) u WHERE o.__cubeJoinField = u.__cubeJoinField ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
id | vip
1 | 1
# subquery_plain
$ psql> SELECT o.id, u.vip FROM orders o LEFT JOIN (SELECT id, vip, __cubeJoinField FROM users) u ON o.__cubeJoinField = u.__cubeJoinField ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
id | vip
1 | 1
2 | 0
```

**Truth:** SQL keeps order 2 with NULL in `left_filtered_star`: `1, 1` and `2, NULL`.

**Why this verdict:** A subquery that lists its columns has no `__cubeJoinField`, as the docs here say. A subquery with `SELECT *` has it, so the claim is too wide. Then a `LEFT JOIN` with a filter inside loses order 2, because the filter applies to the whole query. The docs of Cube do not define this.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/joins.mdx:83`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/joins.mdx#L83)

**Issues:**

- none found


### left_join

**A left join starts at the model**

- **Claims:** docs/semantic-sync.md and docs/query-compilation.md: left join direction, review m11 (first item)
- **Verdict:** `documented-behaviour`
- **Claim text:** Docs here: a left join from `products` to `orders` drops products with no orders. Review m11: `COUNT(*)` stops with `Can't find join path`.
- **Command:** `python3 run.py join-on-condition`

**Output:**

```text
# from_users_left_orders
$ psql> SELECT u.id, MEASURE(o.count) c FROM users u LEFT JOIN orders o ON u.__cubeJoinField = o.__cubeJoinField GROUP BY 1 ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
id | c
1 | 1
2 | 1
# from_users_count_star
$ psql> SELECT COUNT(*) c FROM users u LEFT JOIN orders o ON u.__cubeJoinField = o.__cubeJoinField
[ch-off, ch-on, pg-off, pg-on]
psql:<stdin>:1: ERROR:  Internal Error: Can't find join path to join 'users,orders'
QUERY: SELECT COUNT(*) AS c FROM users u LEFT JOIN orders o ON u.__cubeJoinField = o.__cubeJoinField
# truth_left
$ clickhouse> SELECT u.id, count(o.id) c FROM repro.users u LEFT JOIN repro.orders o ON o.user_id = u.id GROUP BY 1 ORDER BY 1
[ch-off, ch-on]
id	c
1	1
2	1
3	1
[pg-off, pg-on]
id | c
1 | 1
2 | 1
3 | 0
```

**Truth:** `truth_left`: user 3 has 0.

**Why this verdict:** Both statements are correct, and each is a result of the model join.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/joins.mdx:83`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/joins.mdx#L83): the join is as in the data model.
- [`docs-mintlify/docs/data-modeling/joins.mdx:424`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/docs/data-modeling/joins.mdx#L424): the direction comes from the path.

**Issues:**

- none found


### datediff

**DATEDIFF counts boundaries**

- **Claims:** docs/query-compilation.md: datetime-diff in months
- **Verdict:** `documented-behaviour`
- **Claim text:** Docs here: Cube has no form for whole months. Its `DATEDIFF` counts boundaries.
- **Command:** `python3 run.py datediff-months`

**Output:**

```text
# datediff_month
$ psql> SELECT id, DATEDIFF('month', created_at, shipped_at) d FROM orders ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
id | d
1 | 1
2 | 0
# age_fn
$ psql> SELECT id, EXTRACT(MONTH FROM AGE(shipped_at, created_at)) d FROM orders ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
psql:<stdin>:1: ERROR:  Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information
QUERY: SELECT id, EXTRACT(MONTH FROM AGE(shipped_at, created_at)) AS d FROM orders ORDER BY 1
# truth_boundaries
$ clickhouse> SELECT id, dateDiff('month',created_at,shipped_at) d FROM repro.orders ORDER BY 1
[ch-off, ch-on]
id	d
1	1
2	0
[pg-off, pg-on]
id | d
1 | 1
2 | 0
# truth_whole_months
$ clickhouse> SELECT id, age('month',created_at,shipped_at) d FROM repro.orders ORDER BY 1
[ch-off, ch-on]
id	d
1	0
2	0
[pg-off, pg-on]
id | d
1 | 0
2 | 0
```

**Truth:** Boundaries: 1 and 0. Whole months: 0 and 0.

**Why this verdict:** The claim is correct, on both databases.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/reference.mdx:483`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/reference.mdx#L483): `DATEDIFF` comes from Redshift, which counts boundaries.

**Issues:**

- none found


### interval

**An interval on ClickHouse**

- **Claims:** docs/query-compilation.md: datetime-add in an aggregated query and the week section
- **Verdict:** `cube-clickhouse-bug`
- **Claim text:** Docs here: Cube cannot generate SQL for an interval on ClickHouse. It works on raw rows.
- **Command:** `python3 run.py interval-and-extract`

**Output:**

```text
# interval_raw
$ psql> SELECT id, created_at + INTERVAL '1 day' d FROM orders ORDER BY 1
[ch-off, ch-on]
psql:<stdin>:1: ERROR:  Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/interval_single_date_part"  ...
QUERY: SELECT id, created_at + INTERVAL '1 day' AS d FROM orders ORDER BY 1
[pg-off, pg-on]
id | d
1 | 2025-05-06 00:00:00.000000
2 | 2025-05-16 00:00:00.000000
3 | 2025-06-02 00:00:00.000000
# interval_aggregated
$ psql> SELECT created_at + INTERVAL '1 day' d, MEASURE(count) c FROM orders GROUP BY 1 ORDER BY 1
[ch-off, ch-on]
psql:<stdin>:1: ERROR:  Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/interval_single_date_part"  ...
QUERY: SELECT created_at + INTERVAL '1 day' AS d, MEASURE(count) AS c FROM orders GROUP BY 1 ORDER BY 1
[pg-off, pg-on]
d | c
2025-05-06 00:00:00.000000 | 1
2025-05-16 00:00:00.000000 | 1
2025-06-02 00:00:00.000000 | 1
```

**Truth:** Postgres gives each date plus one day.

**Why this verdict:** The missing template is the cause. Raw rows fail in this form too, so `It works on raw rows` is not true for `x + INTERVAL`. `ClickHouseQuery` already writes the correct form for `DATE_ADD`.

**Code:**

- [`rust/cubesql/cubesql/src/transport/service.rs:951`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/transport/service.rs#L951): it needs the template `expressions/interval` or `expressions/interval_single_date_part`.
- [`packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts:322`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L322): ClickHouse defines only `DATE_ADD`, with no interval expression.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/reference.mdx:481`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/reference.mdx#L481): `DATE_ADD` and intervals are listed as supported.

**Issues:**

- none found. Close: #10591

**Fix layer and test:** `ClickHouseQuery.sqlTemplates`: add `expressions.interval_single_date_part = 'INTERVAL {{ num }} {{ date_part }}'`. Test idea: `SELECT d + INTERVAL '1 day'` on ClickHouse.

**Subclass or package:** Yes, tested: `probe-clickhouse-subclass`, steps `interval_raw` and `interval_aggregated`, work on both planners.


<details><summary>Issue draft: ClickHouse: SQL API fails on an interval, template not found</summary>

**Title:** ClickHouse: SQL API fails on an interval, template not found

**Describe the bug**

`x + INTERVAL '1 day'` fails because the ClickHouse dialect has no interval template.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: clickhouse/clickhouse-server:26.10.3-alpine
    environment: { CLICKHOUSE_DB: repro, CLICKHOUSE_USER: repro, CLICKHOUSE_PASSWORD: repro, CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: "1" }
    healthcheck: { test: [CMD-SHELL, "clickhouse-client -u repro --password repro -q 'SELECT 1'"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: clickhouse
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "8123"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: orders
    sql_table: repro.orders
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: status, sql: status, type: string }
      - { name: amount_dim, sql: amount, type: number }
      - { name: created_at, sql: created_at, type: time }
    measures:
      - { name: count, type: count }
      - { name: revenue, sql: amount, type: sum }
      - { name: avg_amount, sql: amount, type: avg }
      - { name: distinct_status, sql: status, type: count_distinct }
```

`seed.sql`:

```sql
CREATE TABLE repro.orders (id UInt32, status String, amount Int32, created_at DateTime('UTC')) ENGINE = MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES
(1, 'a', 100, '2025-05-05 00:00:00'),
(2, 'a', 200, '2025-05-15 00:00:00'),
(3, 'b', 400, '2025-06-01 00:00:00');
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
docker compose exec -T db clickhouse-client -u repro --password repro --multiquery < seed.sql
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
docker run --rm --network host -e PGPASSWORD=repro postgres:17-alpine psql -h 127.0.0.1 -p 15432 -U repro -d repro -c "SELECT id, created_at + INTERVAL '1 day' d FROM orders ORDER BY 1"
```

**Expected behavior**

Each `created_at` plus one day, as on Postgres.

**Actual behavior**

`Can't generate SQL for interval ... template not found`.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

Adding `templates.expressions.interval_single_date_part = 'INTERVAL {{ num }} {{ date_part }}'` in a subclass makes it work.

</details>


### extract

**EXTRACT on ClickHouse**

- **Claims:** docs/query-compilation.md: EXTRACT next to a measure that is not additive
- **Verdict:** `cube-clickhouse-bug`
- **Claim text:** Docs here: `EXTRACT` in native SQL next to a measure that is not additive: Cube cannot plan it.
- **Command:** `python3 run.py interval-and-extract`

**Output:**

```text
# extract_count
$ psql> SELECT EXTRACT(MONTH FROM created_at) m, MEASURE(count) c FROM orders GROUP BY 1 ORDER BY 1
[ch-off, ch-on]
psql:<stdin>:1: ERROR:  Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information
QUERY: SELECT EXTRACT(MONTH FROM created_at) AS m, MEASURE(count) AS c FROM orders GROUP BY 1 ORDER BY 1
[pg-off, pg-on]
m | c
5 | 2
6 | 1
# extract_avg
$ psql> SELECT EXTRACT(MONTH FROM created_at) m, MEASURE(avg_amount) a FROM orders GROUP BY 1 ORDER BY 1
[ch-off, ch-on]
psql:<stdin>:1: ERROR:  Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information
QUERY: SELECT EXTRACT(MONTH FROM created_at) AS m, MEASURE(avg_amount) AS a FROM orders GROUP BY 1 ORDER BY 1
[pg-off, pg-on]
m | a
5 | 150
6 | 400
# trunc_avg
$ psql> SELECT DATE_TRUNC('month', created_at) m, MEASURE(avg_amount) a FROM orders GROUP BY 1 ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
m | a
2025-05-01 00:00:00.000000 | 150
2025-06-01 00:00:00.000000 | 400
# truth_extract_avg
$ clickhouse> SELECT toMonth(created_at) m, avg(amount) a FROM repro.orders GROUP BY 1 ORDER BY 1
[ch-off, ch-on]
m	a
5	150
6	400
[pg-off, pg-on]
m | a
5 | 150.0000000000000000
6 | 400.0000000000000000
```

**Truth:** `truth_extract_avg`: `5, 150` and `6, 400`.

**Why this verdict:** It depends on ClickHouse, not on the measure type: on ClickHouse a `count` and a `sum` fail too, and on Postgres each works.

**Code:**

- [`rust/cubesql/cubesql/src/compile/rewrite/rules/wrapper/extract.rs:76`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/rewrite/rules/wrapper/extract.rs#L76): the rule needs the template `expressions/extract`, and the ClickHouse dialect has none.

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/reference.mdx:484`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/reference.mdx#L484): `EXTRACT` is listed as supported.

**Issues:**

- [#8313](https://github.com/cube-js/cube/issues/8313) (closed): the same error, not reproduced on v1.8.1. It did not use ClickHouse.
- Close: #7727, #10591, #8418

**Fix layer and test:** `ClickHouseQuery.sqlTemplates`: add `expressions.extract`. Test idea: `EXTRACT(MONTH FROM d)` with `MEASURE()` on ClickHouse.

**Subclass or package:** Yes, tested: `probe-clickhouse-subclass`, step `extract_avg`, works on both planners.


<details><summary>Issue draft: ClickHouse: SQL API cannot plan EXTRACT next to a measure</summary>

**Title:** ClickHouse: SQL API cannot plan EXTRACT next to a measure

**Describe the bug**

The ClickHouse dialect has no `expressions/extract` template, so a query with `EXTRACT` and a measure cannot be planned.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: clickhouse/clickhouse-server:26.10.3-alpine
    environment: { CLICKHOUSE_DB: repro, CLICKHOUSE_USER: repro, CLICKHOUSE_PASSWORD: repro, CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: "1" }
    healthcheck: { test: [CMD-SHELL, "clickhouse-client -u repro --password repro -q 'SELECT 1'"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: clickhouse
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "8123"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: orders
    sql_table: repro.orders
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: status, sql: status, type: string }
      - { name: amount_dim, sql: amount, type: number }
      - { name: created_at, sql: created_at, type: time }
    measures:
      - { name: count, type: count }
      - { name: revenue, sql: amount, type: sum }
      - { name: avg_amount, sql: amount, type: avg }
      - { name: distinct_status, sql: status, type: count_distinct }
```

`seed.sql`:

```sql
CREATE TABLE repro.orders (id UInt32, status String, amount Int32, created_at DateTime('UTC')) ENGINE = MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES
(1, 'a', 100, '2025-05-05 00:00:00'),
(2, 'a', 200, '2025-05-15 00:00:00'),
(3, 'b', 400, '2025-06-01 00:00:00');
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
docker compose exec -T db clickhouse-client -u repro --password repro --multiquery < seed.sql
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
docker run --rm --network host -e PGPASSWORD=repro postgres:17-alpine psql -h 127.0.0.1 -p 15432 -U repro -d repro -c "SELECT EXTRACT(MONTH FROM created_at) m, MEASURE(count) c FROM orders GROUP BY 1 ORDER BY 1"
```

**Expected behavior**

`5, 2` and `6, 1`, as on Postgres.

**Actual behavior**

`Can't detect Cube query and it may be not supported yet`.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

Adding `templates.expressions.extract = 'EXTRACT({{ date_part }} FROM {{ expr }})'` in a subclass makes it work.

</details>


### rolling_range

**A rolling measure by bucket with no date filter**

- **Claims:** docs/query-compilation.md: `Date range is required for time series`
- **Verdict:** `documented-behaviour`
- **Claim text:** Docs here: Cube fails with `Date range is required for time series`.
- **Command:** `python3 run.py rolling-bucket-no-date-range`

**Output:**

```text
# sql_bucket_no_filter
$ psql> SELECT DATE_TRUNC('month',created_at) m, MEASURE(revenue_trailing_30d) r FROM orders GROUP BY 1 ORDER BY 1
[ch-off, pg-off]
psql:<stdin>:1: ERROR:  Database Execution Error: Time series queries without dateRange aren't supported
[ch-on]
psql:<stdin>:1: ERROR:  Database Execution Error: Date range is required for time series
[pg-on]
m | r
2025-03-01 00:00:00.000000 | 1
2025-04-01 00:00:00.000000 | 10
2025-05-01 00:00:00.000000 | 100
```

**Truth:** Not a number claim for ClickHouse.

**Why this verdict:** The claim is correct for ClickHouse, and documented.

**Docs:**

- [`docs-mintlify/reference/data-modeling/measures.mdx:466`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/measures.mdx#L466): omitting the date range works on a list of sources, with Postgres and with no ClickHouse.

**Issues:**

- none found


### other_agg

**Another aggregation of a measure**

- **Claims:** docs/query-compilation.md: `Measure aggregation type doesn't match`
- **Verdict:** `documented-behaviour`
- **Claim text:** Docs here: `max` of a count fails with `Measure aggregation type doesn't match`.
- **Command:** `python3 run.py other-aggregation-of-measure`

**Output:**

```text
# min_of_count
$ psql> SELECT MIN(count) x FROM orders
[ch-off, ch-on, pg-off, pg-on]
psql:<stdin>:1: ERROR:  Rewrite Error: Measure aggregation type doesn't match. The aggregation type for 'count' is 'COUNT()' but 'MIN()' was provided. Please check logs for additional information
QUERY: SELECT MIN(count) AS x FROM orders
# max_of_sum
$ psql> SELECT MAX(revenue) x FROM orders
[ch-off, ch-on, pg-off, pg-on]
x
400
# avg_of_avg
$ psql> SELECT AVG(avg_amount) x FROM orders
[ch-off, ch-on, pg-off, pg-on]
x
233.33333333333334
```

**Truth:** Not a number claim.

**Why this verdict:** The claim is correct. `MAX` of a `sum` measure does not fail, and gives the largest row value.

**Code:**

- [`rust/cubesql/cubesql/src/compile/rewrite/rules/members.rs:2868`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cubesql/cubesql/src/compile/rewrite/rules/members.rs#L2868)

**Docs:**

- [`docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx:70`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L70): the same error text.

**Issues:**

- [#8062](https://github.com/cube-js/cube/issues/8062) (open): the same behaviour.


### cda

**count_distinct_approx and a rollup on ClickHouse**

- **Claims:** docs/query-compilation.md: count_distinct_approx
- **Verdict:** `documented-behaviour`
- **Claim text:** Docs here: a rollup cannot store it on ClickHouse, so these queries read the table.
- **Command:** `python3 run.py count-distinct-approx-rollup`

**Output:**

```text
# rest_exact_rollup_shape
$ POST /cubejs-api/v1/load {"measures": ["orders.distinct_approx"], "dimensions": ["orders.status"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month"}], "order": {"orders.c ...
[ch-off, ch-on]
HTTP 400 ERROR Distributed approximate distinct count is not supported by this DB
SQL ERROR: {"type":"UserError","error":"Distributed approximate distinct count is not supported by this DB","stack":"Error: Distributed approximate distinct count is not supported by this DB\n    at C ...
[pg-off, pg-on]
HTTP 400 ERROR Error: function hll_hash_any(text) does not exist
# rest_no_rollup
$ POST /cubejs-api/v1/load {"measures": ["plain.distinct_approx"]}
[ch-off, ch-on]
HTTP 200 [{"plain.distinct_approx": "2"}]
[pg-off, pg-on]
HTTP 400 ERROR Error: function hll_hash_any(text) does not exist
```

**Truth:** 2.

**Why this verdict:** The claim is correct. The text here is not exact: a rollup that lists this measure makes the query fail, and it does not read the table.

**Code:**

- [`packages/cubejs-schema-compiler/src/adapter/BaseQuery.ts:4292`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/adapter/BaseQuery.ts#L4292): the error.

**Docs:**

- [`docs-mintlify/admin/connect-to-data/data-sources/clickhouse.mdx:51`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/admin/connect-to-data/data-sources/clickhouse.mdx#L51): it cannot be used in pre-aggregations on ClickHouse.

**Issues:**

- none found


### nonadditive

**avg and count_distinct on a rollup**

- **Claims:** docs/query-compilation.md: measures that are not additive
- **Verdict:** `documented-behaviour`
- **Claim text:** Docs here: a rollup with other dimensions than the query does not serve them.
- **Command:** `python3 run.py non-additive-rollup`

**Output:**

```text
# rest_avg_other_dims
$ POST /cubejs-api/v1/load {"measures": ["orders.avg_amount"]}
[ch-on]
HTTP 200 [{"orders.avg_amount": "233.33333333333334"}]
[pg-on]
HTTP 200 [{"orders.avg_amount": "233.3333333333333333"}]
# rest_sum_other_dims
$ POST /cubejs-api/v1/load {"measures": ["orders.revenue"]}
[ch-on, pg-on]
HTTP 200 [{"orders.revenue": "700"}]
# truth_avg
$ clickhouse> SELECT avg(amount) a FROM repro.orders
[ch-on]
a
233.33333333333334
[pg-on]
a
233.3333333333333333
```

**Truth:** 233.33.

**Why this verdict:** The claim is correct: the `avg` query reads the table and gives the truth.

**Docs:**

- [`docs-mintlify/docs/pre-aggregations/matching-pre-aggregations.mdx`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/docs/pre-aggregations/matching-pre-aggregations.mdx): additive measures and matching.

**Issues:**

- none found


### meta_view

**A view has no joins, and a primary key is not public**

- **Claims:** docs/semantic-sync.md: two limits
- **Verdict:** `documented-behaviour`
- **Claim text:** Docs here: a view has no joins in `/v1/meta`. A primary key is not public unless the model says so.
- **Command:** `python3 run.py meta-view-joins-primary-key`

**Output:**

```text
# meta_extended_view
[ch-on]
{"name": "order_users", "type": "view", "joins": [], "public": true, "measures": [{"name": "order_users.count", "type": "number", "public": true, "isVisible": true, "aggType": "count"}], "dimensions": [{"name": "order_users.id", "type": "number", "public": true, "isVisible": true, "primaryKey": false}, {"name": "order_users.user ...
# meta_extended_cube
[ch-on]
{"name": "orders", "type": "cube", "joins": [{"name": "users", "relationship": "belongsTo", "sql": "`${CUBE}.user_id = ${users.id}`"}], "public": true, "connectedComponent": 1, "measures": [{"name": "orders.count", "type": "number", "public": true, "isVisible": true, "aggType": "count"}], "dimensions": [{"name": "orders.id", "ty ...
# meta_users
[ch-on]
{"name": "users", "type": "cube", "public": true, "connectedComponent": 1, "measures": [{"name": "users.count", "type": "number", "public": true, "isVisible": true, "aggType": "count"}], "dimensions": [{"name": "users.id", "type": "number", "public": false, "isVisible": false, "primaryKey": true}, {"name": "users.vip", "type": " ...
```

**Truth:** Not a number claim.

**Why this verdict:** Both are correct. A view has `joins: []` in the extended metadata.

**Docs:**

- [`docs-mintlify/reference/data-modeling/dimensions.mdx:226`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/dimensions.mdx#L226): `public`. The error `primaryKey fields are public: false by default` says the same.

**Issues:**

- none found


### week

**A week starts on Monday**

- **Claims:** docs/query-compilation.md: week section
- **Verdict:** `documented-behaviour`
- **Claim text:** Docs here: Cube weeks start on Monday.
- **Command:** `python3 run.py week-starts-monday`

**Output:**

```text
# sql_week
$ psql> SELECT DATE_TRUNC('week',created_at) w, MEASURE(revenue) r FROM orders GROUP BY 1 ORDER BY 1
[ch-off, ch-on, pg-off, pg-on]
w | r
2025-05-26 00:00:00.000000 | 1
2025-06-02 00:00:00.000000 | 10
```

**Truth:** 2025-06-01 is a Sunday, and it belongs to the week of 2025-05-26.

**Why this verdict:** The claim is correct.

**Code:**

- [`packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts:94`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L94): `toMonday`.

**Docs:**

- [`docs-mintlify/reference/data-modeling/dimensions.mdx:1063`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/dimensions.mdx#L1063): `week` starts on Monday.

**Issues:**

- [#4971](https://github.com/cube-js/cube/issues/4971) (closed)
- [#177](https://github.com/cube-js/cube/issues/177) (closed)


### jwt_dev

**Dev mode accepts each JWT**

- **Claims:** docs/security.md: dev mode
- **Verdict:** `documented-behaviour`
- **Claim text:** Docs here: dev mode of Cube accepts each JWT, also one with a wrong signature.
- **Command:** `python3 run.py dev-mode-any-jwt`

**Output:**

```text
# wrong_jwt
$ GET /cubejs-api/v1/meta token=wrong
[ch-on]
HTTP 200 {"cubes":[{"name":"orders","type":"cube","title":"Orders","isVisible":true,"public":true,"measures":[{"name":"orders.coun ...
# no_token
$ GET /cubejs-api/v1/meta token=none
[ch-on]
HTTP 200 {"cubes":[{"name":"orders","type":"cube","title":"Orders","isVisible":true,"public":true,"measures":[{"name":"orders.coun ...
```

**Truth:** Not a number claim.

**Why this verdict:** The claim is correct and documented.

**Code:**

- [`packages/cubejs-api-gateway/src/gateway.ts:3164`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-api-gateway/src/gateway.ts#L3164): it throws only with `enforceSecurityChecks`.

**Docs:**

- `docs-mintlify/snippets/dev-mode-warning.mdx`: dev mode is an authentication bypass.

**Issues:**

- none found


### jwt_log

**Cube logs a refused JWT**

- **Claims:** docs/security.md: the JWT in the log
- **Verdict:** `unclear-intent`
- **Claim text:** Docs here: Cube writes the JWT of a request that it refuses to its own log.
- **Command:** `python3 run.py prod-mode-jwt-logged`

**Output:**

```text
# wrong_jwt
$ GET /cubejs-api/v1/meta token=wrong
[ch-on]
HTTP 403 {"error":"Invalid token"}
# log
$ docker compose logs cube | grep -E 'invalid signature'
[ch-on]
{"level":"error","message":"invalid signature","url":"/cubejs-api/v1/meta","token":"<JWT>","error":"JsonWebTokenError: invalid signature","protocol":"http"}
matching lines: 1
```

**Truth:** The repro masks the token in this output. The log has the full token.

**Why this verdict:** The claim is correct. The docs do not mention it, so the intent is not clear. A refused token has a bad signature, so the risk is small, but a token with another fault can be real.

**Code:**

- [`packages/cubejs-api-gateway/src/gateway.ts:3318`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-api-gateway/src/gateway.ts#L3318): `token` is in the log entry for an auth error.

**Issues:**

- none found. Close: #9515


### N1

**New: a rolling measure on Postgres labels a month bucket with the filter day**

- **Claims:** new finding, from B6
- **Verdict:** `cube-bug`
- **Claim text:** No claim in this repository. The run for B6 found it.
- **Command:** `python3 run.py B6-rolling-no-bucket`

**Output:**

```text
# sql_filter_inside_bucket
$ psql> SELECT DATE_TRUNC('month',created_at) m, MEASURE(revenue_trailing_30d) r FROM orders WHERE created_at >= '2025-04-15' AND created_at < '2025-06-01' GROUP BY 1 ORDER BY 1
[ch-on]
m | r
2025-04-01 00:00:00.000000 | 100
2025-05-01 00:00:00.000000 | 1000
[pg-on]
m | r
2025-04-15 00:00:00.000000 | 1100
2025-05-15 00:00:00.000000 | 
# rest_filter_inside_bucket
$ POST /cubejs-api/v1/load {"measures": ["orders.revenue_trailing_30d"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month", "dateRange": ["2025-04-15", "2025-05-31"]}]}
[ch-on]
HTTP 200 [{"orders.created_at.month": "2025-04-01T00:00:00.000", "orders.revenue_trailing_30d": "100", "orders.created_at": "2025-04-01T00:00:00.000"}, {"orders.created_at.month": "2025-05-01T00:00:00.000", "orders.revenue_trailing_30d": "1000", "orders.created_at": "2025-05-01T00:00:00.000"}]
SQL:  WITH 
time_series AS (  SELECT parseDateTimeBestEffort(dates.f) date_from, parseDateTimeBestEffort(dates.t) date_to 
  FROM (
      select '2025-04-01T00:00:00.000' f, '2025-04-30T23:59:59.999' t 
   UNION ALL
      select '2025-05-01T00:00:00.000' f, '2025-05-31T23:59:59.999' t 
  ) AS dates),
cte_0 AS (  SELECT toDateTime64(toStartOfDay(toTimeZone(toDateTime64(`orders`.created_at, 0), 'UTC'), 'UTC'), 0, 'UTC') `orders__created_at_day`, sum(`orders`.amount) `orders__revenue_trailing_30d` 
  FROM  repro.orders  AS `orders`
  WHERE (toTimeZone(toDateTime64(`orders`.created_at, 0), 'UTC') >= parseDateTimeBestEffort(?) AND toTimeZone(toDateTime64(`orders`.created_at, 0), 'UTC') <= parseDateTimeBestEffort(?))
  GROUP BY 1
  ORDER BY  1  ASC),
cte_1 AS (  SELECT `time_series`.`date_from` `orders__created_at_month`, sum(`rolling_source`.`orders__revenue_trailing_30d`) `orders__revenue_trailing_30d` 
  FROM  time_series  AS `time_series`
  LEFT JOIN  cte_0  AS `rolling_source` ON `rolling_source`.`orders__created_at_day` > subDate(`time_series`.`date_to`, INTERVAL 30 DAY) AND `rolling_source`.`orders__created_at_day` <= `time_series`.`date_to`
  GROUP BY 1
  ORDER BY  1  ASC)
SELECT `fk_aggregate`.`orders__created_at_month` `orders__created_at_month`, `fk_aggregate`.`orders__revenue_trailing_30d` `orders__revenue_trailing_30d` 
FROM  cte_1  AS `fk_aggregate`
ORDER BY  1  ASC
LIMIT 10000
PARAMS: ["2025-03-02T00:00:00.000", "2025-05-31T23:59:59.999"]
[pg-on]
HTTP 200 [{"orders.created_at.month": "2025-04-15T00:00:00.000", "orders.revenue_trailing_30d": "1100", "orders.created_at": "2025-04-15T00:00:00.000"}, {"orders.created_at.month": "2025-05-15T00:00:00.000", "orders.revenue_trailing_30d": null, "orders.created_at": "2025-05-15T00:00:00.000"}]
SQL:  WITH 
time_series AS (  SELECT "d"::timestamptz AS "date_from",
  ("d"::timestamptz + interval '1 month') - interval '1 millisecond' AS "date_to" 
  FROM generate_series('2025-04-15T00:00:00.000'::timestamp, '2025-05-31T23:59:59.999':: timestamp, '1 month'::interval) d ),
cte_0 AS (  SELECT date_trunc('day', ("orders".created_at::timestamptz AT TIME ZONE 'UTC')) "orders__created_at_day", sum("orders".amount) "orders__revenue_trailing_30d" 
  FROM  repro.orders  AS "orders"
  WHERE (("orders".created_at::timestamptz AT TIME ZONE 'UTC') >= $1::timestamptz AND ("orders".created_at::timestamptz AT TIME ZONE 'UTC') <= $2::timestamptz)
  GROUP BY 1
  ORDER BY  1  ASC),
cte_1 AS (  SELECT "time_series"."date_from" "orders__created_at_month", sum("rolling_source"."orders__revenue_trailing_30d") "orders__revenue_trailing_30d" 
  FROM  time_series  AS "time_series"
  LEFT JOIN  cte_0  AS "rolling_source" ON "rolling_source"."orders__created_at_day" > "time_series"."date_to" - interval '30 days' AND "rolling_source"."orders__created_at_day" <= "time_series"."date_to"
  GROUP BY 1
  ORDER BY  1  ASC)
SELECT "fk_aggregate"."orders__created_at_month" "orders__created_at_month", "fk_aggregate"."orders__revenue_trailing_30d" "orders__revenue_trailing_30d" 
FROM  cte_1  AS "fk_aggregate"
ORDER BY  1  ASC
LIMIT 10000
PARAMS: ["2025-03-02T00:00:00.000", "2025-06-30T23:59:58.999"]
```

**Truth:** A `month` bucket starts on the first day. The rolling value at the end of April is 100, and of May is 1000.

**Why this verdict:** This contradicts the meaning of a month bucket, and ClickHouse gives other buckets with the same query. It affects only sources with generated time series, on Tesseract.

**Code:**

- [`packages/cubejs-schema-compiler/src/adapter/PostgresQuery.ts:106`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/adapter/PostgresQuery.ts#L106): `generate_series` starts at the first day of the date range.
- [`rust/cube/cubesqlplanner/cubesqlplanner/src/physical_plan/time_series.rs:121`](https://github.com/cube-js/cube/blob/v1.8.15/rust/cube/cubesqlplanner/cubesqlplanner/src/physical_plan/time_series.rs#L121): the generated series gets the raw start.

**Docs:**

- [`docs-mintlify/reference/data-modeling/measures.mdx:466`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/measures.mdx#L466): Postgres generates the series in SQL.

**Issues:**

- none found. Close: #12023

**Fix layer and test:** `time_series.rs`: truncate the start to the granularity before `generate_series`. Test idea: a rolling measure by month from a date inside a month on Postgres.

**Subclass or package:** Not for ClickHouse, which has no generated series.


<details><summary>Issue draft: Tesseract: a rolling measure by month starts its buckets at the filter day on Postgres</summary>

**Title:** Tesseract: a rolling measure by month starts its buckets at the filter day on Postgres

**Describe the bug**

With a date range that starts inside a month, the `month` buckets of a rolling measure start on that day.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: postgres:17-alpine
    environment: { POSTGRES_DB: repro, POSTGRES_USER: repro, POSTGRES_PASSWORD: repro }
    healthcheck: { test: [CMD-SHELL, "pg_isready -U repro -d repro"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: postgres
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "5432"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: orders
    sql_table: repro.orders
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: status, sql: status, type: string }
      - { name: amount_dim, sql: amount, type: number }
      - { name: created_at, sql: created_at, type: time }
    measures:
      - { name: count, type: count }
      - { name: revenue, sql: amount, type: sum }
      - { name: revenue_trailing_30d, sql: amount, type: sum, rolling_window: { trailing: 30 day } }
```

`seed.sql`:

```sql
CREATE TABLE repro.orders (id int PRIMARY KEY, status text, amount int, created_at timestamp);
INSERT INTO repro.orders VALUES
(1, 'a', 1, '2025-01-10 00:00:00'),
(2, 'a', 10, '2025-03-20 00:00:00'),
(3, 'a', 100, '2025-04-20 00:00:00'),
(4, 'a', 1000, '2025-05-10 00:00:00');
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
(echo 'CREATE SCHEMA repro;'; cat seed.sql) | docker compose exec -T -e PGPASSWORD=repro db psql -U repro -d repro -q
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
curl -s -X POST http://127.0.0.1:4000/cubejs-api/v1/load -H 'Content-Type: application/json' -d '{"query": {"measures": ["orders.revenue_trailing_30d"], "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month", "dateRange": ["2025-04-15", "2025-05-31"]}]}}'
# Run it again if the reply is {"error":"Continue wait"}.
```

**Expected behavior**

Buckets `2025-04-01` and `2025-05-01`, as on ClickHouse.

**Actual behavior**

Buckets `2025-04-15` and `2025-05-15`.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

The SQL has `generate_series('2025-04-15T00:00:00.000'::timestamp, ...)`. The legacy planner gives `2025-04-01` and `2025-05-01`.

</details>


### N2

**New: the legacy planner on ClickHouse gives a wrong rolling number**

- **Claims:** new finding, from B6
- **Verdict:** `cube-clickhouse-bug`
- **Claim text:** No claim in this repository. The run for B6 found it. The demo uses Tesseract, so the demo does not have this.
- **Command:** `python3 run.py B6-rolling-no-bucket`, `python3 run.py rolling-unbounded`

**Output:**

```text
# sql_whole_buckets
$ psql> SELECT DATE_TRUNC('month',created_at) m, MEASURE(revenue_trailing_30d) r FROM orders WHERE created_at >= '2025-04-01' AND created_at < '2025-06-01' GROUP BY 1 ORDER BY 1
[ch-off]
m | r
2025-04-01 00:00:00.000000 | 1100
2025-05-01 00:00:00.000000 | 1100
[ch-on, pg-off, pg-on]
m | r
2025-04-01 00:00:00.000000 | 100
2025-05-01 00:00:00.000000 | 1000
```
```text
# two_months_100y
$ psql> SELECT DATE_TRUNC('month',created_at) m, MEASURE(cumulative_100y) c FROM orders WHERE created_at >= '2025-04-01' AND created_at < '2025-06-01' GROUP BY 1 ORDER BY 1
[ch-off]
m | c
2025-04-01 00:00:00.000000 | 5
2025-05-01 00:00:00.000000 | 5
[ch-on, pg-off, pg-on]
m | c
2025-04-01 00:00:00.000000 | 2
2025-05-01 00:00:00.000000 | 3
```

**Truth:** `truth_30d_to_apr30` is 100, and `truth_30d_to_may31` is 1000.

**Why this verdict:** The number is wrong with no error. Tesseract and Postgres are correct. The SQL is in the results of `rest_filter_inside_bucket`. The legacy planner is deprecated, and `CUBEJS_TESSERACT_SQL_PLANNER=false` shows a warning, so the priority is low.

**Code:**

- [`packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts:274`](https://github.com/cube-js/cube/blob/v1.8.15/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L274): `seriesSql` writes two independent `arrayJoin` calls, so `date_from` and `date_to` make a cross product, and each bucket also joins the wrong end.

**Docs:**

- [`docs-mintlify/reference/data-modeling/measures.mdx:459`](https://github.com/cube-js/cube/blob/v1.8.15/docs-mintlify/reference/data-modeling/measures.mdx#L459): the rolling window is defined per bucket.

**Issues:**

- none found

**Fix layer and test:** `ClickHouseQuery.seriesSql`: join the pairs with `arrayJoin(arrayZip(...))`. Test idea: a rolling measure with two buckets on ClickHouse on the legacy planner.

**Subclass or package:** Possible by an override of `seriesSql`. I did not test it.


<details><summary>Issue draft: ClickHouse legacy planner: a rolling measure has a cross product of the time series</summary>

**Title:** ClickHouse legacy planner: a rolling measure has a cross product of the time series

**Describe the bug**

`seriesSql` has two independent `arrayJoin` calls, so each `date_from` pairs with each `date_to`.

**To Reproduce**

1. Create these three files in an empty directory.

`docker-compose.yml`:

```yaml
name: cube-repro
services:
  db:
    image: clickhouse/clickhouse-server:26.10.3-alpine
    environment: { CLICKHOUSE_DB: repro, CLICKHOUSE_USER: repro, CLICKHOUSE_PASSWORD: repro, CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: "1" }
    healthcheck: { test: [CMD-SHELL, "clickhouse-client -u repro --password repro -q 'SELECT 1'"], interval: 2s, retries: 40 }
  cube:
    image: cubejs/cube:v1.8.15
    depends_on: { db: { condition: service_healthy } }
    environment:
      CUBEJS_DEV_MODE: "true"
      CUBEJS_API_SECRET: repro-dev-only-0123456789abcdef0123456789
      CUBEJS_DB_TYPE: clickhouse
      CUBEJS_DB_HOST: db
      CUBEJS_DB_PORT: "8123"
      CUBEJS_DB_NAME: repro
      CUBEJS_DB_USER: repro
      CUBEJS_DB_PASS: repro
      CUBEJS_PG_SQL_PORT: "15432"
      CUBEJS_SQL_USER: repro
      CUBEJS_SQL_PASSWORD: repro
      CUBEJS_TELEMETRY: "false"
      CUBEJS_TESSERACT_SQL_PLANNER: "false"
    volumes: ["./model:/cube/conf/model:ro"]
    ports: ["127.0.0.1:4000:4000", "127.0.0.1:15432:15432"]
```

`model/model.yml`:

```yaml
cubes:
  - name: orders
    sql_table: repro.orders
    dimensions:
      - { name: id, sql: id, type: number, primary_key: true, public: true }
      - { name: status, sql: status, type: string }
      - { name: amount_dim, sql: amount, type: number }
      - { name: created_at, sql: created_at, type: time }
    measures:
      - { name: count, type: count }
      - { name: revenue, sql: amount, type: sum }
      - { name: revenue_trailing_30d, sql: amount, type: sum, rolling_window: { trailing: 30 day } }
```

`seed.sql`:

```sql
CREATE TABLE repro.orders (id UInt32, status String, amount Int32, created_at DateTime('UTC')) ENGINE = MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES
(1, 'a', 1, '2025-01-10 00:00:00'),
(2, 'a', 10, '2025-03-20 00:00:00'),
(3, 'a', 100, '2025-04-20 00:00:00'),
(4, 'a', 1000, '2025-05-10 00:00:00');
```

2. Start the stack and load the data:

```sh
docker compose up -d --wait db
docker compose exec -T db clickhouse-client -u repro --password repro --multiquery < seed.sql
docker compose up -d cube
until curl -sf http://127.0.0.1:4000/readyz >/dev/null; do sleep 1; done
```

3. Run the query:

```sh
docker run --rm --network host -e PGPASSWORD=repro postgres:17-alpine psql -h 127.0.0.1 -p 15432 -U repro -d repro -c "SELECT DATE_TRUNC('month',created_at) m, MEASURE(revenue_trailing_30d) r FROM orders WHERE created_at >= '2025-04-01' AND created_at < '2025-06-01' GROUP BY 1 ORDER BY 1"
```

**Expected behavior**

`2025-04-01, 100` and `2025-05-01, 1000`, as with Tesseract. Set `CUBEJS_TESSERACT_SQL_PLANNER: "false"` for this repro.

**Actual behavior**

`2025-04-01, 1100` and `2025-05-01, 1100`.

**Version:** v1.8.15, the newest tag on 2026-10-30.

**Additional context**

This needs the legacy planner.

</details>


## Entries with no claim about Cube

These entries in `conformance/features.yaml` have a non-pass status and make no claim that Cube has a defect or a limit, so I did not run them.

| Entry | Reason |
| --- | --- |
| `measure.format.abbr`, `measure.format.accounting`, `dimension.format.abbr` | A limit of Metabase number formats. |
| `dimension.opt.order` | A limit of Metabase fields. |
| `cube.calendar_granularity_sql`, `context.filter_group`, `context.compile_context` | Not modelled in the demo yet. |
| `cube.data_source` | The demo stack has one data source. |
| `cube.refresh_key` | The check does not prove a refresh. It names no defect in Cube. |
| `mb.other.drill_through_ui` | The suite has no browser check. |
| `mcp.*`, 9 entries | Metabase MCP tools and channels. |
| `semantic.drill_members_native` | A limit of Metabase. |
| `compile.xray.*`, 4 entries | The checks do not compare each card. |

Review entries M4, m7, m8, m9, m10 and B8 name the driver, Metabase or the docs of this repository as the layer. `raw_rolling_column` in `minor-m4-rolling-max` shows the value per row that m8 reports.

## Upstream status

These are the links for the fixes that [cube-audit.md](cube-audit.md) supports. The state is from 2026-10-31.

| Defect | Link | State |
| --- | --- | --- |
| B1 | [#12532](https://github.com/cube-js/cube/pull/12532) | Open |
| datetime64 | [#12533](https://github.com/cube-js/cube/pull/12533) | Open. A filter on milliseconds also needs #12532. |
| interval | [#12534](https://github.com/cube-js/cube/pull/12534) | Open |
| M1, M2 | [#12535](https://github.com/cube-js/cube/pull/12535) | Open |
| N1 | [#12536](https://github.com/cube-js/cube/pull/12536) | Open |
| geo | [#12537](https://github.com/cube-js/cube/pull/12537) | Open |
| B2 | [Issue #12538](https://github.com/cube-js/cube/issues/12538) | Open. It changes Power BI results, so it asks the maintainers which behaviour they want. |
| propagate | [#12502](https://github.com/cube-js/cube/pull/12502) | Open, by a maintainer. The audit cases pass on it, so there is no second pull request. |
| gran, m5, N2, big | None | Ready, not sent. The limit is 6 open pull requests. |
| extract, m4 | None | Not sent. The audit says `intended` and `misuse`. |
| B4, unbounded, m3 | None | No patch. |
