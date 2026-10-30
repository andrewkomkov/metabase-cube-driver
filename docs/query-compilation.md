# How queries reach Cube

The driver writes SQL for the Cube SQL API. Cube runs that SQL in one of three ways. `EXPLAIN` on the SQL API shows which way.

| Way | `EXPLAIN` shows | A rollup can serve it | Row limit |
| --- | --- | --- | --- |
| Regular | Only a `CubeScan` | Yes | The result has at most `CUBEJS_DB_QUERY_LIMIT` rows |
| Post-processing | A plan above a `CubeScan` | Yes | Cube reads at most `CUBEJS_DB_QUERY_LIMIT` rows from the `CubeScan` |
| SQL push-down | `CubeScanWrapper` or `WrappedSelect` | Rarely | No limit |

## Required Cube settings

Set these two values on a Cube that Metabase reads. Without them, Cube returns wrong numbers with no error.

| Setting | Value | Reason |
| --- | --- | --- |
| `CUBEJS_PRE_AGGREGATIONS_ALLOW_NON_STRICT_DATE_RANGE_MATCH` | `false` | The default `true` lets a rollup of months answer a filter on one day. In the demo, that filter gave NULL where the truth was 14. |
| `CUBEJS_DB_QUERY_LIMIT` | Above the largest group count of a post-processing query | The default is 50000. Post-processing then computes from 50000 rows with no error. In the demo, the count of 100000 customers gave 50000. |

`CUBESQL_STREAM_MODE=true` also removes the limit, but then each query that a rollup serves fails with `Streaming queries to Cube Store aren't supported`.

## What the driver writes

| Metabase query | SQL | Way |
| --- | --- | --- |
| Minute, hour, day, month, quarter, year | `DATE_TRUNC` | Regular |
| Week, first day Monday | `DATE_TRUNC('week', x)` | Regular |
| Week, another first day, with a custom granularity that fits | `DATE_TRUNC('name', x)` | Regular |
| Week, another first day, no such granularity | A week number from `DATEDIFF` in a subquery, and a date in the outer query | Push-down |
| Parts of a date, such as hour of day or month of year | `DATEDIFF` from the start of the period | Push-down |
| Filter on a part of a date | The same `DATEDIFF` in `WHERE` | Push-down |
| ISO week of year | `EXTRACT(WEEK)` | Post-processing |
| Bins | `FLOOR(x / w) * CAST('w' AS double precision)` | Push-down |
| `log` | `LN(x) / LN(10)`, because `LOG(x)` on ClickHouse has base e | Push-down |
| `datetime-diff` in seconds, minutes, hours, days, weeks | `DATEDIFF` | Push-down |
| Implicit or explicit join | `ON a.__cubeJoinField = b.__cubeJoinField` | Regular with a measure, push-down with `COUNT(*)` |
| Inner join | As above, and the joined column must not be NULL | Push-down |
| Segment | `segment = TRUE` | Regular |
| `count` or a count measure with no breakout | `COALESCE(x, 0)`, because a rollup gives NULL on no rows | Post-processing |
| Date filter value | `TIMESTAMP '2025-03-15 05:00:00'` as text in the SQL, not a query parameter. A parameter makes post-processing fail with `Can't parse date: 'replaced_placeholder'` | Same as the query |
| `column > time` | `column >= time + 1 second`, see [Date bounds](#date-bounds) | Regular |
| A question or model with an aggregation, and another stage on it | The inner query has `LIMIT 2147483647`, see [Stages](#stages) | Post-processing |
| Offset, n below 0 | `CASE WHEN ROW_NUMBER() OVER w > n THEN LAG(x, n) OVER w END`, because ClickHouse `LAG` gives 0 on the first row | Push-down |

### Week and the first day of the week

Cube weeks start on Monday, and Cube cannot add an interval to a time on ClickHouse, see [metabase/metabase#42255](https://github.com/metabase/metabase/issues/42255). To make weeks from another day fast, add a custom granularity to the time dimension. A rollup of days then serves the query:

```yaml
granularities:
  - { name: week_from_sunday, interval: 1 week, offset: -1 day }
```

Without it, the driver writes a week number and the database reads all rows. All measure types work in both forms, also with a date filter, and in a subquery of a saved question. A relative filter in weeks is a time literal of the first day of that week, because it has no column to group by.

### Date bounds

Cube reads a time as whole seconds, so each bound that the driver writes is a whole second.

| Filter | SQL | Reason |
| --- | --- | --- |
| `x > t` | `x >= t + 1 second` | The SQL API makes a date range from two bounds and starts it 1 ms after `t`. Cube writes that range for ClickHouse with `parseDateTimeBestEffort`, which drops the fraction, so the row at `t` stays in. |
| `x >= t.5` | `x >= t + 1 second` | The same reason. |
| `x < t.5`, `x <= t.5` | `x <= t` | A column of whole seconds has no value between `t` and `t.5`. |
| `x = t.5`, `x != t.5` | The driver stops the query | Cube would compare with `t`. |

A `DateTime64` column stays a limit of Cube, see `compile.bound.datetime64_whole_seconds_limit`: a row in the first second after a bound of `>` is out of the result.

### Stages

Cube pushes a filter of an outer stage below the aggregation of a subquery. A filter on a saved question then changes the rows of the question: a month becomes a part of that month, and a share of the total becomes 1. A limit on the subquery stops this, so the driver adds one to each stage with an aggregation that has a next stage, and to a native question with an aggregation. `CUBEJS_DB_QUERY_LIMIT` still applies to the rows of that subquery.

A subquery with raw rows has no limit, because Cube reads the outer filter correctly there. Its measure columns are NULL unless a later stage uses them, so raw rows of a table and raw rows of a model show the same values. If a later stage aggregates a measure column, the driver selects the measure, so `sum(revenue)` through a model is the measure.

If a later stage filters or groups by a measure column, and the model has the primary key of the cube, the driver writes `MEASURE(x)` and `GROUP BY` of the dimensions in the subquery. Each group is then one row, so `revenue > 150` filters rows, and a breakout on `revenue` has one group for each value. With no primary key in the model, a group is more than one row, so the driver stops the query.

### Fan-out

A join does not change a measure. Over a fan-out from `orders` to `payments`, `SUM(quantity_dim)` is the total quantity of the orders, as a measure, and each order counts once. `COUNT(*)` counts the rows of the join. So in the demo, `COUNT(*)` is 10000 and the sum is 12500, where a plain SQL join gives 25000. Use a measure of a cube to count that cube.

### Custom granularities

In native SQL, use `DATE_TRUNC('half_day', created_at)`. In the query builder, add a dimension to the model with `sql: "{created_at.half_day}"`, and select **Don't bin** for it. Metabase selects **Day** by default.

## What the driver stops

These queries fail with a message that tells what to do. The message includes the text of Cube.

| Query | Reason |
| --- | --- |
| Join on columns that no join of the model compares | The driver writes `__cubeJoinField`, so Cube would use the model join. Cube refuses `JOIN ... ON a = b` on other columns in native SQL with `Can't detect Cube query`, and `CROSS JOIN ... WHERE o.other_id = u.id` applies the model join and then the filter. |
| Join of a question, or with a filter inside the join | A subquery that lists its columns has no `__cubeJoinField`. A subquery with `SELECT *` has it, but a filter inside it applies to the whole query, so it has no use here. |
| `median`, `percentile` | The Cube templates for ClickHouse have no `PERCENTILE_CONT`. |
| `datetime-diff` in months, quarters, years | Cube has no form for whole months. Its `DATEDIFF` counts boundaries. |
| `datetime-add` | The ClickHouse dialect of Cube has no template for an interval: `Can't generate SQL for interval`. The form `x + INTERVAL` fails on raw rows too. |
| `EXTRACT` in native SQL next to a measure | The ClickHouse dialect of Cube has no template for it, so it fails with each measure. It works on Postgres. |
| Rolling window measure with no time bucket | Cube returns the total of all rows, with no error. |
| Rolling window measure with a date filter that starts or ends inside a bucket, also a filter inside the model | Cube computes the whole bucket and does not apply that part of the filter, with no error. Use whole buckets, or a smaller bucket. |
| Rolling window measure by a time bucket with no date filter | Cube fails with `Date range is required for time series`. |
| Another aggregation of a rolling window measure, such as `max` | Cube cannot compute it. |
| `trailing: unbounded` for more than one bucket | Cube writes a join that ClickHouse rejects: `Cannot determine join keys in JOIN ON expression`. One bucket works. |
| Another aggregation of a measure column that Cube rejects, such as `max` of a count | Cube fails with `Measure aggregation type doesn't match`. |
| Filter on a measure column of a table, or of a model with no primary key, or on a rolling window measure | Cube reads this as a filter on the aggregate, so it does not filter rows. |
| Breakout on a measure or a segment column, except a measure of a model with the primary key | Cube ignores the grouping and returns one total row. |
| Two columns of a view that come from one member through different join paths, in one query | Cube joins a cube once in a query, so both columns show one value. `aliasMember` in `/v1/meta` shows these columns. Raw rows show the first column and NULL in the others. |
| Comparison with `=` or `!=` of a time that has a fraction of a second | Cube drops the fraction. |
| A distinct count or a measure with its own SQL, next to a breakout on an expression, or a filter or a group on a measure column | The driver reads the values of rows here, and these values do not add up to the measure. |
| `rollup_lambda` on ClickHouse, when the range reads source rows | Cube fails with `Stream query failed: aborted`. Inside the range of the rollup it works. See `preagg.rollup_lambda`. |
| `OR` of two segments | Cube fails with `Can't use OR operator with segments`. |
| A segment of another cube | Cube fails with `No field named`. Use a view that has it. |

These checks read the stages of the query, so they work on the query builder, on a model and a question by column name, and on the MCP tool `execute_query`, also with `source-card`.

### Native SQL

The driver does not change native SQL. It makes a cheap scan of the text, and it stops these shapes:

| Shape | Reason |
| --- | --- |
| A rolling window measure with no `DATE_TRUNC` on a time column | Cube returns the total of all rows. Its docs require a time dimension, so this has unclear intent. |
| A rolling window measure with a filter inside a bucket, with `OR` or `NOT`, or with a custom granularity as the bucket | Cube computes whole buckets, because a date range selects buckets. The result does not agree with the filter, so the driver stops it. The scan cannot show that this filter keeps whole buckets. |
| `x BETWEEN a AND 'yyyy-mm-dd'`, also `NOT BETWEEN` and a date parameter | This is documented behaviour of Cube: it pads a date with no time at the end of a date range to the end of that day, as [its REST API docs](https://github.com/cube-js/cube/blob/master/docs-mintlify/reference/core-data-apis/rest-api/query-format.mdx) describe. SQL reads it as midnight, and the SQL API docs do not warn about it. Write the time, use `DATE 'yyyy-mm-dd'`, or use `>=` and `<`. |
| `x > a AND x <= b` or `x < b` | See [Date bounds](#date-bounds). Write `>=` with the next second. |
| Two columns from one member through different join paths | See the table above. |

The scan reads only `column op literal`, `BETWEEN` and `DATE_TRUNC`, outside strings and comments. See the shapes that it does not read in [Known wrong numbers](#known-wrong-numbers).

### Two join paths in a view

Cube [resolves a diamond](https://github.com/cube-js/cube/blob/master/docs-mintlify/docs/data-modeling/joins.mdx) with one `join_path` per view. It joins a cube once in a query. A view with two paths to one cube can read each path alone, and in one query it uses one path for both. To show both in one query, give the join an `alias` for each path, as `region_sales` in `dev/cube/model/inheritance.yml` does, or make one view per path. `region_sales_one_join` next to it shows the view with no alias.

### Another aggregation of a measure column

A measure column has one clause that holds the measure: `sum` for sums and counts, `avg`, `min`, `max`, and `max` for other types. This clause compiles to `MEASURE()`. Another clause stays a SQL aggregation, and Cube computes it from the rows: `max` of `revenue` is the largest order, not the revenue. Sync hides these columns, so only the query API and MCP can name them.

### A measure on a time column

A measure with `type: min` or `type: max` on a time column is NULL on the SQL API, because `/v1/meta` types it as a number. Write it with `type: time` and `sql: min(column)`, as `first_order_at` in `dev/cube/model/orders.yml` does.

## Latency

`compile.latency.ten_million_rows` measures this on the demo stack, with 10 million rows and a date range that changes on each run, so that the result cache of Cube cannot answer. It prints the values on each run. One run gave these medians of five:

| Query | With rollup | Without rollup |
| --- | --- | --- |
| Revenue by status, through Metabase | 0.301 s | 0.321 s |
| Revenue by day and status, on the Cube SQL API | 0.240 s | 0.447 s |

ClickHouse is fast on this table, and Metabase adds most of the time to a small result. The rollup gains more when a query has more rows to scan.

## Limits of Cube that the driver cannot change

- A number above 2^53 or with more than 15 digits loses digits, because the Cube SQL API has the type Double, and the ClickHouse driver of Cube sends JSON numbers. `SELECT ui64` gave 9007199254740996 where the truth is 9007199254740995. A dimension with `sql: toString(column)` keeps the digits.
- The driver sends `SET TIME ZONE` with the report time zone of Metabase, so Cube makes each bucket and reads each time literal in that zone. With no report time zone it is UTC. A rollup serves only the zones in its `scheduled_refresh_timezones`, so a query in another zone reads the source. `DATEDIFF` in the week number form compares with a literal, so it is tested on the zones of the check `mb.report_timezone` only.
- A custom granularity with an offset can give a wrong bin on a measure that a rollup serves, see `granularity.custom.offset` in `conformance/features.yaml`.
- A time shift measure with no time bucket gives the total of all rows. See [Known wrong numbers](#known-wrong-numbers).
- A `DateTime64` loses its milliseconds, because the ClickHouse dialect of Cube reads a time as whole seconds. Postgres keeps them. A filter on milliseconds finds no row. A dimension with `sql: toString(column)` keeps the text. See `clickhouse.datetime64_millis`.
- In one query, a `propagate_filters_to_sub_query` dimension next to a second `sub_query` dimension with equal SQL reads the first subquery. See `dimension.opt.propagate_filters_next_to_another_sub_query`.
- A rolling window with `trailing: unbounded` for more than one bucket fails on ClickHouse, see above. One bucket works.
- Native `COUNT(*)` on no rows gives NULL, where SQL gives 0. A query from the query builder gives 0. See `native.count_on_no_rows_limit`.
- `count_distinct_approx` is `uniq()` on ClickHouse. A rollup cannot store it on ClickHouse, so these queries read the table.
- A left join starts at the cube that the data model starts at. `COUNT(*)` on a left join from `products` to `orders` stops with `Can't find join path` if the model has only the join from `orders` to `products`. A measure of `orders` works, and it has no row for a product with no orders.
- `avg` and `count_distinct` measures are not additive. A rollup with other dimensions than the query does not serve them.
- `DATEDIFF` compares with a time literal in the week number form. The literal is in the time zone of the ClickHouse server, and the demo server has UTC. A server in another zone can move the start of a week.
- Forms with `DATEDIFF` were tested on ClickHouse only.

## Known wrong numbers

The driver cannot stop these. Each number comes with no error. Some are defects of Cube, and some have an intent that Cube does not define. [Cube findings](cube-findings.md) has the verdict and a clean repro for each. `conformance/features.yaml` records each one as `partial`, with its reason.

| Query | What you get | Entry |
| --- | --- | --- |
| A time shift measure with no time bucket | The total of all rows. This has unclear intent in Cube: a sum over all time does not change after a shift. `/v1/meta` does not tell which measure has a time shift, so the driver cannot ask for a bucket. Use a time bucket. | `measure.multi_stage.time_shift_no_bucket_limit` |
| A custom granularity with an offset, on a measure that a rollup serves, for a time before the origin | A wrong bin from Cube Store. Use an `origin` before all rows in place of an `offset`. | `granularity.custom.offset` |
| A filter on a `DateTime64` column | A row in the first second after a bound of `>` is out, and a filter on milliseconds finds no row. Cube reads whole seconds. | `compile.bound.datetime64_whole_seconds_limit`, `clickhouse.datetime64_millis` |
| A number above 2^53 | It loses digits, because the Cube SQL API has the type Double. | See the limits above. |
| A `propagate_filters_to_sub_query` dimension next to a second `sub_query` dimension with equal SQL | Both columns read the first subquery. Other SQL, such as `{orders.count} + 0`, gives the truth. | `dimension.opt.propagate_filters_next_to_another_sub_query` |
| `between` with a time part in the MCP tool `execute_query` | Metabase makes it into whole days before the driver. Use `>=` and `<=`. | `mcp.query.between_is_whole_days` |
| Native SQL in a shape that the scan does not read, such as `'2025-01-01' < created_at`, `(created_at) > x`, a rolling window measure with `created_at IN (...)`, or a filter with a function on the time column | The same wrong numbers that the scan stops in the plain shape. | `native.bound.strict_lower_refused`, `measure.rolling.native_refused`, `native.between_date_text_refused` |
