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
| Hour of day with a report time zone | The hour in the text of the time: `SUBSTR(CAST(x AS text), 12, 2)`. `DATEDIFF` counts boundaries of hours in absolute time. | Push-down |
| Week of year | `ceil(day-of-year(week(x)) / 7)`, as in Metabase core, so the number is the same as on Postgres. See [Week and the first day of the week](#week-and-the-first-day-of-the-week). | Push-down |
| `contains`, `starts-with`, `ends-with`, also `does-not-contain` | `STRPOS(x, 'a') >= 1` or `= 1`, and `chr(31)` marks the end of the text. Cube reads `LIKE` as an operator that ignores case on ClickHouse, and it reads `STRPOS(x, 'a') > 0`, `LEFT` and `RIGHT` the same. With `case-sensitive: false`, `LOWER(x) LIKE`. | Push-down |
| `column = time` | `column >= time AND column <= time`. Next to a breakout on the same column, Cube reads `column = time` as the whole bucket. | Regular |
| A bound on a time with a report time zone | The form of [Date bounds](#date-bounds) that Cube does not merge, with the literal in UTC. | Push-down |
| `NOT (column > time)` | The opposite bound, `column <= time`. | Same as the bound |
| Filter on a part of a date | The same `DATEDIFF` in `WHERE` | Push-down |
| ISO week of year | `EXTRACT(WEEK)` | Post-processing |
| Bins | `FLOOR(x / w) * CAST('w' AS double precision)` | Push-down |
| `log` | `LN(x) / LN(10)`, because `LOG(x)` on ClickHouse has base e | Push-down |
| `a / b`, also for two measures | `a * CAST('1.0' AS double precision) / NULLIF(b * CAST('1.0' AS double precision), 0.0)`. A cast of `a` fails on ClickHouse when a measure is NULL: `Cannot convert NULL value to non-Nullable type`. A plain `1.0` is a decimal in Cube, and it cuts the digits of a `Decimal`. | Push-down |
| `datetime-diff` in seconds, minutes, hours, days, weeks | `DATEDIFF` | Push-down |
| Implicit or explicit join | `ON a.__cubeJoinField = b.__cubeJoinField` | Regular with a measure, push-down with `COUNT(*)` |
| Inner join | As above, and the joined column must not be NULL | Push-down |
| Segment | `segment = TRUE` | Regular |
| `count` or a count measure with no breakout | `COALESCE(x, 0)`, because a rollup gives NULL on no rows | Post-processing |
| Native `COUNT(...)` or `MEASURE()` of a count, in a text with one `SELECT` and no grouping | `COALESCE(x, 0)`, for the same reason | Post-processing |
| Date filter value | `TIMESTAMP '2025-03-15 05:00:00'` as text in the SQL, not a query parameter. A parameter makes post-processing fail with `Can't parse date: 'replaced_placeholder'` | Same as the query |
| `column > time`, `column < time` on stock Cube | `(column > time OR column IS NULL) AND column IS NOT NULL`, see [Date bounds](#date-bounds) | Regular |
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

The SQL API makes a date range from two bounds, and it starts the range 1 ms after the bound of `>`. The ClickHouse dialect of stock Cube writes the range with `parseDateTimeBestEffort`, which drops the fraction. So `x > a AND x <= b` includes the row at `a`, and `x >= a AND x < b` ends at the last whole second before `b`, so a `DateTime64` loses the rows of that second. Cube does not show the type of a column, so one form must be right for each type.

The driver asks Cube how it writes a date range, with `/v1/sql`. If Cube keeps the fraction, as it does with [the ClickHouse dialect](https://github.com/andrewkomkov/metabase-cube-driver/tree/main/cube-clickhouse) and on other data sources, the driver writes each filter as it is. On stock Cube:

| Filter | SQL | Reason |
| --- | --- | --- |
| `x > t` | `(x > t OR x IS NULL) AND x IS NOT NULL` | Cube does not merge this into a date range, so it reads `t` as it is. |
| `x < t` | The same form. With a rollup on this column, or with a rolling window or a time shift, it stays `x < t`. | A rollup, a rolling window and a time shift need a date range. |
| `x >= t`, `x <= t`, `between` | As they are | The range is exact at a whole second. |
| A bound with a fraction, `x = t.5` | The driver stops the query | Cube drops the fraction, and this would move the bound on a `DateTime64`. |

With a report time zone that is not UTC, Cube reads the two bounds of a date range in that zone, and each other bound as UTC. One bound, a bound next to `OR` or `NOT`, and a bound of a filter on an expression are "other". So on each Cube, the driver writes each bound on a column with `(x >= t OR x IS NULL) AND x IS NOT NULL` and `t` in UTC. Cube never merges this form into a range. A time shift needs a date range, so with a time shift the two bounds stay in the zone. A rollup needs a date range too, so two bounds on a column with a rollup stay in the zone, if the filter has no other bound on that column and no `OR` or `NOT` around them. A column of a stage with an aggregation is a time in the zone, so its literal stays in the zone too.

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
| Sum or average of a dimension of a joined cube, from the side with the foreign key | Cube adds each row of the joined cube once. `SUM(products.price)` by a column of `orders` gives the total of the products, for each group. A subquery gives the same number, so the driver stops this query. Add a measure, or start from the cube of the dimension. |
| Join of a question, or with a filter inside the join | A subquery that lists its columns has no `__cubeJoinField`. A subquery with `SELECT *` has it, but a filter inside it applies to the whole query, so it has no use here. |
| `median`, `percentile` | The Cube templates for ClickHouse have no `PERCENTILE_CONT`. |
| `datetime-diff` in months, quarters, years | Cube has no form for whole months. Its `DATEDIFF` counts boundaries. |
| `datetime-add` on stock Cube | The ClickHouse dialect of stock Cube has no template for an interval: `Can't generate SQL for interval`. The form `x + INTERVAL` fails on raw rows too. [The ClickHouse dialect](https://github.com/andrewkomkov/metabase-cube-driver/tree/main/cube-clickhouse) has the template. |
| `EXTRACT` in native SQL next to a measure | The ClickHouse dialect of Cube has no template for it, so it fails with each measure. It works on Postgres. |
| Rolling window measure with no time bucket | Cube returns the total of all rows, with no error. |
| Rolling window measure with a date filter that starts or ends inside a bucket, also a filter inside the model | Cube computes the whole bucket and does not apply that part of the filter, with no error. Use whole buckets, or a smaller bucket. |
| Rolling window measure by a time bucket with no date filter | Cube fails with `Date range is required for time series`. |
| Another aggregation of a rolling window measure, such as `max` | Cube cannot compute it. |
| Stock Cube: a case-sensitive text filter next to `x > t`, `x <= t`, `x = t` or `between` on a time | The text filter sends the query to the database, and there stock Cube compares a time in whole seconds. On a `DateTime64`, the rows of the second after `t` go to the wrong side. Cube does not show the type of a column, so the driver stops this for each time column. `>=` and `<` stay right. The dialect has no such limit. |
| `trailing: unbounded` for more than one bucket, on ClickHouse before 26.10 | Cube writes a join with only an inequality, and ClickHouse before 26.10 rejects it: `Cannot determine join keys in JOIN ON expression`. One bucket works. ClickHouse 26.10 runs this join and gives the truth. |
| Rolling window measure that is shorter than the bucket, such as month to date by a quarter | Cube can add the windows of the whole bucket, and no one number is what this means. A window of 30 days counts as a month, so it is allowed by a month. `/v1/sql` shows the window. |
| Rolling window measure in days, by a month, a quarter or a year, when a rollup serves it | Cube Store reads the wrong days for a month that has another length than the month before it, with no error. The source gives the truth, and a window in months gives the truth from the rollup. `metric.rollup.day_window_by_month_refused` shows this. |
| Rolling window with a length in months after the end of a bucket, such as `leading: 1 month, offset: end`, by a month, a quarter or a year | Cube reads whole months for each bucket except the last, and it cuts the last one at the end of its window. By a day, the number is right. |
| Rolling window measure with a report time zone that is not UTC | Cube adds the wrong months together, also on its REST API. |
| Rolling window measure on stock Cube with `CUBEJS_TESSERACT_SQL_PLANNER=false` | Each bucket gets one wrong number. `/v1/sql` shows the planner. The dialect gives the truth. |
| A time shift measure with one date bound | Cube reads a time shift only with a date range, so it gives periods outside the filter. |
| A time shift measure with `x > t` on stock Cube | A date range on stock Cube has no form for this bound that is right for a `DateTime64`. Use `>=` with the next second, or the dialect. |
| A dimension with `meta: { date_only: true }`, with a report time zone, on stock Cube | Stock Cube reads a `Date` as a time at midnight UTC. The dialect gives the truth. |
| Another aggregation of a measure column that Cube rejects, such as `max` of a count | Cube fails with `Measure aggregation type doesn't match`. |
| Filter on a measure column of a table, or of a model with no primary key, or on a rolling window measure | Cube reads this as a filter on the aggregate, so it does not filter rows. |
| Breakout on a measure or a segment column, except a measure of a model with the primary key | Cube ignores the grouping and returns one total row. |
| Two columns of a view that come from one member through different join paths, in one query | Cube joins a cube once in a query, so both columns show one value. `aliasMember` in `/v1/meta` shows these columns. Raw rows show the first column and NULL in the others. |
| A bound with a fraction of a second on stock Cube | Cube drops the fraction. |
| A measure with a time shift, with no time bucket and no date filter | Cube returns the total of all rows. `/v1/sql` shows the shift. |
| A custom granularity in months on a measure that a rollup serves, when the data starts before the origin | Cube Store puts a time before the origin in the wrong bin. An offset has its origin in the current year. |
| Two `sub_query` dimensions with equal SQL in a query with a filter | Cube reads the first subquery for both. Raw rows show `NULL` in the later column. |
| A number at or above 2^53 in a result | It has lost digits, because the SQL API has the type Double. A dimension with `sql: toString(column)` gives the full digits as text. |
| A distinct count or a measure with its own SQL, next to a breakout on an expression, or a filter or a group on a measure column | The driver reads the values of rows here, and these values do not add up to the measure. |
| `rollup_lambda` on ClickHouse, when the range reads source rows | Cube fails with `Stream query failed: aborted`. Inside the range of the rollup it works. See `preagg.rollup_lambda`. |
| `OR` of two segments | Cube fails with `Can't use OR operator with segments`. |
| A segment of another cube | Cube fails with `No field named`. Use a view that has it. |

These checks read the stages of the query, so they work on the query builder, on a model and a question by column name, and on the MCP tool `execute_query`, also with `source-card`.

### Native SQL

The driver changes native SQL in one case: it writes `COALESCE(x, 0)` around a count in a text with one `SELECT` and no grouping. It makes a cheap scan of the text, and it stops these shapes:

| Shape | Reason |
| --- | --- |
| A rolling window measure with no `DATE_TRUNC` on a time column | Cube returns the total of all rows. Its docs require a time dimension, so this has unclear intent. |
| A rolling window measure with a filter inside a bucket, with `OR` or `NOT`, or with a custom granularity as the bucket | Cube computes whole buckets, because a date range selects buckets. The result does not agree with the filter, so the driver stops it. The scan cannot show that this filter keeps whole buckets. |
| `x BETWEEN a AND 'yyyy-mm-dd'`, also `NOT BETWEEN` and a date parameter | This is documented behaviour of Cube: it pads a date with no time at the end of a date range to the end of that day, as [its REST API docs](https://github.com/cube-js/cube/blob/master/docs-mintlify/reference/core-data-apis/rest-api/query-format.mdx) describe. SQL reads it as midnight, and the SQL API docs do not warn about it. Write the time, use `DATE 'yyyy-mm-dd'`, or use `>=` and `<`. |
| `x > a AND x <= b` or `x < b` | See [Date bounds](#date-bounds). Write `>=` with the next second. |
| Two columns from one member through different join paths | See the table above. |
| A rolling window measure that is shorter than the bucket, or with a report time zone | See the table above. |
| `x = t` next to `DATE_TRUNC` on `x` | Cube returns the whole bucket. Write `x >= t AND x <= t`. |
| `x LIKE` a pattern with a wildcard and a letter, also `NOT LIKE`, `STRPOS(x, 'a') > 0`, `LEFT(x, n) =` and `RIGHT(x, n) =` | Cube reads these as an operator that ignores case on ClickHouse. Write `ILIKE`, or `STRPOS(x, 'a') >= 1` to keep the case. |
| A raw select of a `switch` dimension, also `SELECT *` | Each row comes once for each value. |
| `SELECT *` on a cube with a rolling window measure | The star reads that measure, and it needs a time bucket and a date filter. |
| `SUM(j.x)` or `AVG(j.x)` of a dimension of a joined cube | See the table above. |
| With a report time zone, each filter on a time with no lower and upper bound, or next to `OR` or `NOT`, or with `DATE_TRUNC` | Cube reads a literal in the zone only in a date range, and as UTC in other forms. |

On stock Cube, the scan also stops `x > a` next to an upper bound on the same column, and a bound with a fraction.

The scan also stops an outer filter over a subquery or a `WITH` query with an aggregation and no limit, and a filter on a measure column over a subquery of raw rows. It removes parentheses and casts around a column or a literal, reads an alias of a time column, and reads `column op literal` on either side, `BETWEEN` and `DATE_TRUNC`, outside strings and comments. With a rolling window measure, it stops each other use of the time column, such as `IN` or a function, also `DATE_TRUNC` in a filter and arithmetic on a literal. See the shapes that it does not read in [Known wrong numbers](#known-wrong-numbers).

### Two join paths in a view

Cube [resolves a diamond](https://github.com/cube-js/cube/blob/master/docs-mintlify/docs/data-modeling/joins.mdx) with one `join_path` per view. It joins a cube once in a query. A view with two paths to one cube can read each path alone, and in one query it uses one path for both. To show both in one query, give the join an `alias` for each path, as `region_sales` in `dev/cube/model/inheritance.yml` does, or make one view per path. `region_sales_one_join` next to it shows the view with no alias.

### Another aggregation of a measure column

A measure column has one clause that holds the measure: `sum` for sums and counts, `avg`, `min`, `max`, and `max` for other types. This clause compiles to `MEASURE()`. Another clause stays a SQL aggregation, and Cube computes it from the rows: `max` of `revenue` is the largest order, not the revenue. Sync hides these columns, so only the query API and MCP can name them.

### A measure on a time column

A measure with `type: min` or `type: max` on a time column is NULL on the SQL API, because `/v1/meta` types it as a number. Write it with `type: time` and `sql: min(column)`, as `first_order_at` in `dev/cube/model/orders.yml` does.

### Metrics made from other metrics

A measure of type `number` reads other measures, so Cube computes it at the grain of the query. `sum(a) / sum(b)` by a year is the sum of the year over the sum of the year. It is not an average or a sum of the ratios of the months. A rollup with `a` and `b` serves it at a coarser grain. A distinct count stays out of this: a rollup serves it only at the grain of the rollup.

| Metric | Stock form | Note |
| --- | --- | --- |
| Ratio of sums | `sql: "1.0 * {a} / NULLIF({b}, 0)"` | With no `1.0 *`, `Decimal / Int` on ClickHouse cuts the digits. |
| Percentile | `sql: "quantile(0.9)(toFloat64({CUBE}.amount))"` | With no `toFloat64`, `quantile` of a `Decimal` cuts the digits of the interpolated value. |
| Balance that adds up over accounts and not over time | `multi_stage: true`, `type: number_agg`, `sql: "argMax({total}, {day})"`, `add_group_by: [day]` | The day is a dimension. Its name must differ from `<time>_day`, because Cube uses this alias for a time dimension. Cube Store has no `argMax`, so a rollup must not have this day. A rolling window of 1 day is right by a day and by an hour, and it stops by a month. |

A custom expression `a / b` of two measures gives the same number as the Cube metric, at each grain. `metric.expression.*` compare both with the truth.

## Latency

`compile.latency.ten_million_rows` measures this on the demo stack, with 10 million rows and a date range that changes on each run, so that the result cache of Cube cannot answer. It prints the values on each run. One run gave these medians of five:

| Query | With rollup | Without rollup |
| --- | --- | --- |
| Revenue by status, through Metabase | 0.301 s | 0.321 s |
| Revenue by day and status, on the Cube SQL API | 0.240 s | 0.447 s |

ClickHouse is fast on this table, and Metabase adds most of the time to a small result. The rollup gains more when a query has more rows to scan.

## Limits of Cube that the driver cannot change

- A number above 2^53 stops with a clear message, because the Cube SQL API has the type Double, and the ClickHouse driver of Cube sends JSON numbers. `SELECT ui64` read 9007199254740996 where the truth is 9007199254740995. A long decimal below 2^53 fits a Double, so the driver cannot see it. A dimension with `sql: toString(column)` keeps the digits of both.
- The driver sends `SET TIME ZONE` with the report time zone of Metabase, so Cube makes each bucket in that zone. See [Date bounds](#date-bounds) for a time literal. With no report time zone it is UTC. A rollup serves only the zones in its `scheduled_refresh_timezones`, so a query in another zone reads the source. `DATEDIFF` in the week number form compares with a literal, so it is tested on the zones of the check `mb.report_timezone` only.
- On stock Cube, a `DateTime64` loses its milliseconds in a result, so raw rows show whole seconds. Buckets are right. See `clickhouse.datetime64_millis`.
- A rolling window with `trailing: unbounded` for more than one bucket fails on ClickHouse before 26.10, see above. One bucket works.
- `count_distinct_approx` is `uniq()` on ClickHouse. A rollup cannot store it on ClickHouse. If a rollup holds this measure, each query on the measure stops, and each run of the refresh worker stops, so each other rollup goes stale. Do not put it in a rollup. See [the audit](cube-audit.md#rollup-approx-worker).
- A left join starts at the cube that the data model starts at. `COUNT(*)` on a left join from `products` to `orders` stops with `Can't find join path` if the model has only the join from `orders` to `products`. A measure of `orders` works, and it has no row for a product with no orders.
- `avg` and `count_distinct` measures are not additive. A rollup with other dimensions than the query does not serve them.
- `DATEDIFF` compares with a time literal in the week number form. The literal is in the time zone of the ClickHouse server, and the demo server has UTC. A server in another zone can move the start of a week.
- Forms with `DATEDIFF` were tested on ClickHouse only.

## Known wrong numbers

The driver cannot stop these. Each number comes with no error. [Cube findings](cube-findings.md) and [Cube audit](cube-audit.md) have the verdict and a clean repro for each. `conformance/features.yaml` records each one, with its reason.

| Query | What you get | Entry |
| --- | --- | --- |
| A query out of the build range of a rollup | The rows of the range only. The docs of Cube warn about this. Use `rollup_lambda`, or a range over all rows. | `preagg.build_range.whole_table_reads_only_the_range` |
| A rollup with `allow_non_strict_date_range_match: true`, and a range inside a bucket | Only the buckets that start in the range. Leave the key out of the rollup. | `preagg.option.non_strict_inside_a_bucket_reads_whole_buckets` |
| `rollup_lambda` of two rollups with no overlap | The rows of the last partition of the first rollup are not in the result. Let the next rollup hold that partition. | `preagg.type.rollup_lambda_gap_loses_the_last_partition` |
| Stock Cube: a partitioned rollup with no `build_range_end`, with a report time zone that is not UTC | West of UTC, the rows of the last hours are not in the result. The dialect gives the truth. | `preagg.time_zone.partitioned_hit` |
| A long decimal, such as `Decimal(38, 8)`, below 2^53 | It loses digits, because the Cube SQL API has the type Double. Its value fits a Double, so the driver cannot see this. A dimension with `sql: toString(column)` keeps the digits. | `compile.ch.decimal_above_double` |
| Stock Cube: native `x >= a AND x < b` on a `DateTime64`, or the same filter from the query builder on a column with a rollup | The range ends at the last whole second before `b`, so the rows of that second are out. Cube does not show the type of a column. [The ClickHouse dialect](https://github.com/andrewkomkov/metabase-cube-driver/tree/main/cube-clickhouse) gives the truth. | `native.bound.datetime64_last_second_limit` |
| Stock Cube: native `NOT (x > t)` on a `DateTime64` | SQL push-down compares whole seconds, so the rows inside the second of `t` count as not after `t`. The query builder writes `x <= t`. The dialect gives the truth. | None |
| Stock Cube with a report time zone that is not UTC: a `Date` or `Date32` column | Cube reads it as a time at midnight UTC, so a row can move to another day, in a bucket and in a filter. Cube does not show the type of a column. A dimension with `meta: { date_only: true }` stops. The dialect reads it as midnight in the zone. | `grid.compact.report_zone` |
| A filter on a time with an offset in its literal, as `2025-06-15T11:00:00Z`, with a report time zone that is not UTC | Metabase core drops the offset of a literal on a `type/DateTime` column before the driver gets the query, so the literal is a local time. Base type `type/DateTimeWithLocalTZ` keeps the offset, but Metabase core then writes the end of a month filter with the offset of its start, so a filter on March gets one hour of April in New York. Postgres with `timestamptz` shows the same. | `grid.compact.report_zone` |
| Stock Cube: a raw `DateTime64` value | It shows whole seconds. Buckets are right. The dialect keeps the milliseconds. | `clickhouse.datetime64_millis` |
| Native `COUNT(*)` on no rows in a text with more than one `SELECT` or with a grouping, on a cube with a rollup | `NULL` where SQL gives 0. Only a rollup gives this, because Cube reads it with `SUM`. | `native.count_on_no_rows` |
| Native SQL in a shape that the scan does not read. The scan is not a SQL parser, and it reads the shapes in [Native SQL](#native-sql). | The same wrong numbers that the scan stops in the plain shape. | `native.bound.strict_lower_refused`, `native.subquery_filter_refused` |
| Postgres, not the main target: a rolling window with a range that starts inside a month (N1), a custom granularity in months with no rollup | Shifted buckets. See [Cube audit](cube-audit.md). | None. The suite runs on ClickHouse. |
