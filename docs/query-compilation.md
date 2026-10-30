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

A subquery with raw rows has no limit, because Cube reads the outer filter correctly there. Its measure columns are NULL unless a later stage aggregates them. The driver then selects the measure, so `sum(revenue)` through a model is the measure. So raw rows of a table and raw rows of a model show the same values.

### Fan-out

A join does not change a measure. Over a fan-out from `orders` to `payments`, `SUM(quantity_dim)` is the total quantity of the orders, as a measure, and each order counts once. `COUNT(*)` counts the rows of the join. So in the demo, `COUNT(*)` is 10000 and the sum is 12500, where a plain SQL join gives 25000. Use a measure of a cube to count that cube.

### Custom granularities

In native SQL, use `DATE_TRUNC('half_day', created_at)`. In the query builder, add a dimension to the model with `sql: "{created_at.half_day}"`, and select **Don't bin** for it. Metabase selects **Day** by default.

## What the driver stops

These queries fail with a message that tells what to do. The message includes the text of Cube.

| Query | Reason |
| --- | --- |
| Join on columns that no join of the model compares | Cube ignores the `ON` columns and would use the model join. |
| Join of a question, or with a filter inside the join | Cube has `__cubeJoinField` only on a whole cube. |
| `median`, `percentile` | The Cube templates for ClickHouse have no `PERCENTILE_CONT`. |
| `datetime-diff` in months, quarters, years | Cube has no form for whole months. Its `DATEDIFF` counts boundaries. |
| `datetime-add` in an aggregated query | Cube cannot generate SQL for an interval on ClickHouse. It works on raw rows. |
| `EXTRACT` in native SQL next to a measure that is not additive | Cube cannot plan it. |
| Rolling window measure with no time bucket | Cube returns the total of all rows, with no error. |
| Rolling window measure with a date filter that starts or ends inside a bucket, also a filter inside the model | Cube computes the whole bucket and does not apply that part of the filter, with no error. Use whole buckets, or a smaller bucket. |
| Rolling window measure by a time bucket with no date filter | Cube fails with `Date range is required for time series`. |
| Another aggregation of a rolling window measure, such as `max` | Cube cannot compute it. |
| `trailing: unbounded` for more than one bucket | Cube writes a join that ClickHouse rejects: `Cannot determine join keys in JOIN ON expression`. One bucket works. |
| Another aggregation of a measure column that Cube rejects, such as `max` of a count | Cube fails with `Measure aggregation type doesn't match`. |
| Filter on a measure column of a table or a model | Cube reads this as a filter on the aggregate, so it does not filter rows. In a model it has no effect. |
| Breakout on a measure or a segment column, also of a model | Cube ignores the grouping and returns one total row. |
| Two columns of a view that come from one member through different join paths, in one query | Cube joins a cube once in a query, so both columns show one value. `aliasMember` in `/v1/meta` shows these columns. Raw rows show the first column and NULL in the others. |
| Comparison with `=` or `!=` of a time that has a fraction of a second | Cube drops the fraction. |
| `OR` of two segments | Cube fails with `Can't use OR operator with segments`. |
| A segment of another cube | Cube fails with `No field named`. Use a view that has it. |

These checks read the stages of the query, so they work on the query builder, on a model and a question by column name, and on the MCP tool `execute_query`, also with `source-card`.

### Native SQL

The driver does not change native SQL. It makes a cheap scan of the text, and it stops these shapes:

| Shape | Reason |
| --- | --- |
| A rolling window measure with no `DATE_TRUNC` on a time column | Cube returns the total of all rows. |
| A rolling window measure with a filter inside a bucket, with `OR` or `NOT`, or with a custom granularity as the bucket | Cube computes whole buckets. The scan cannot show that this filter keeps whole buckets. |
| `x BETWEEN a AND 'yyyy-mm-dd'`, also `NOT BETWEEN` and a date parameter | Cube pads a date with no time at the end of a date range to the end of that day, which [its REST API docs](https://github.com/cube-js/cube/blob/master/docs-mintlify/reference/core-data-apis/rest-api/query-format.mdx) describe. SQL reads it as midnight. Write the time, use `DATE 'yyyy-mm-dd'`, or use `>=` and `<`. |
| `x > a AND x <= b` or `x < b` | See [Date bounds](#date-bounds). Write `>=` with the next second. |
| Two columns from one member through different join paths | See the table above. |

The scan reads only `column op literal`, `BETWEEN` and `DATE_TRUNC`, outside strings and comments. See the shapes that it does not read in [Known wrong numbers](#known-wrong-numbers).

### Two join paths in a view

Cube [resolves a diamond](https://github.com/cube-js/cube/blob/master/docs-mintlify/docs/data-modeling/joins.mdx) with one `join_path` per view. It joins a cube once in a query. A view with two paths to one cube can read each path alone, and in one query it uses one path for both. To show both in one query, give the join an `alias` for each path, as `region_sales` in `dev/cube/model/inheritance.yml` does, or make one view per path. `region_sales_one_join` next to it shows the view with no alias.

### Another aggregation of a measure column

A measure column has one clause that holds the measure: `sum` for sums and counts, `avg`, `min`, `max`, and `max` for other types. This clause compiles to `MEASURE()`. Another clause stays a SQL aggregation, and Cube computes it from the rows: `max` of `revenue` is the largest order, not the revenue. Sync hides these columns, so only the query API and MCP can name them.

## Latency

`compile.latency.ten_million_rows` measures this on the demo stack, with 10 million rows and a date range that changes on each run, so that the result cache of Cube cannot answer. It prints the values on each run. One run gave these medians of five:

| Query | With rollup | Without rollup |
| --- | --- | --- |
| Revenue by status, through Metabase | 0.301 s | 0.321 s |
| Revenue by day and status, on the Cube SQL API | 0.240 s | 0.447 s |

ClickHouse is fast on this table, and Metabase adds most of the time to a small result. The rollup gains more when a query has more rows to scan.

## Limits of Cube that the driver cannot change

- A number above 2^53 or with more than 15 digits loses digits, because Cube returns numbers through JavaScript. `SELECT ui64` gave 9007199254740996 where the truth is 9007199254740995. A dimension with `sql: toString(column)` keeps the digits.
- All times are in UTC. The Cube SQL API has no time zone for a query, and `SET TIME ZONE` has no effect. The report time zone of Metabase does not move a bucket.
- A custom granularity with an offset in months is wrong on a measure that a rollup of months serves, see `granularity.custom.offset` in `conformance/features.yaml`.
- A time shift measure with no time bucket gives the total of all rows. See [Known wrong numbers](#known-wrong-numbers).
- A `DateTime64` loses its milliseconds, because Cube reads a time as whole seconds. A filter on milliseconds finds no row. A dimension with `sql: toString(column)` keeps the text. See `clickhouse.datetime64_millis`.
- In one query, a `propagate_filters_to_sub_query` dimension next to a second `sub_query` dimension ignores the filter. See `dimension.opt.propagate_filters_next_to_another_sub_query`.
- A rolling window with `trailing: unbounded` for more than one bucket fails on ClickHouse, see above. One bucket works.
- Native `COUNT(*)` on no rows gives NULL, where SQL gives 0. A query from the query builder gives 0. See `native.count_on_no_rows_limit`.
- `count_distinct_approx` is `uniq()` on ClickHouse. A rollup cannot store it on ClickHouse, so these queries read the table.
- A left join starts at the cube that the data model starts at. `COUNT(*)` on a left join from `products` to `orders` stops with `Can't find join path` if the model has only the join from `orders` to `products`. A measure of `orders` works, and it has no row for a product with no orders.
- `avg` and `count_distinct` measures are not additive. A rollup with other dimensions than the query does not serve them.
- `DATEDIFF` compares with a time literal in the week number form. The literal is in the time zone of the ClickHouse server, and the demo server has UTC. A server in another zone can move the start of a week.
- Forms with `DATEDIFF` were tested on ClickHouse only.

## Known wrong numbers

The driver cannot stop these. Each number comes with no error. `conformance/features.yaml` records each one as `partial`, with its reason.

| Query | What you get | Entry |
| --- | --- | --- |
| A time shift measure with no time bucket | The total of all rows. `/v1/meta` does not tell which measure has a time shift. Use a time bucket. | `measure.multi_stage.time_shift_no_bucket_limit` |
| A custom granularity with an offset in months, on a measure that a rollup of months serves | The bucket of the rollup. | `granularity.custom.offset` |
| A filter on a `DateTime64` column | A row in the first second after a bound of `>` is out, and a filter on milliseconds finds no row. Cube reads whole seconds. | `compile.bound.datetime64_whole_seconds_limit`, `clickhouse.datetime64_millis` |
| A number above 2^53 | It loses digits, because Cube returns numbers through JavaScript. | See the limits above. |
| A `propagate_filters_to_sub_query` dimension next to a second `sub_query` dimension | The filter has no effect. | `dimension.opt.propagate_filters_next_to_another_sub_query` |
| Native SQL in a shape that the scan does not read, such as `'2025-01-01' < created_at`, `(created_at) > x`, a rolling window measure with `created_at IN (...)`, or a filter with a function on the time column | The same wrong numbers that the scan stops in the plain shape. | `native.bound.strict_lower_refused`, `measure.rolling.native_refused`, `native.between_date_text_refused` |
