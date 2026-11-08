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

Run Cube with the Tesseract planner, the default since Cube v1.7.0. With `CUBEJS_TESSERACT_SQL_PLANNER=false`, the legacy planner drops the `default_filters` of a view: in the demo, the count of `paid_sales` gave 5000 where its filter keeps 2000. `/v1/meta` does not show which views have `default_filters`, so on the legacy planner the driver stops each query on a view, with a message. The driver sees the planner from the SQL that Cube writes. See `legacy.view_default_filters`.

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
| A number with a fraction in a filter on a column | `x * CAST('1.0' AS double precision) < 12.50001`. Cube sends a plain number as text, and ClickHouse cuts text to the scale of a `Decimal` column, so `x < 12.50001` reads as `x < 12.5`. On an integer column it stops with `Cannot convert string`. | Push-down |
| `=` with 8 values or more on a number or a text column, or on a time | `x IN (a, b, ...)`, because a list of `OR` stops at about 100 values. With a number that has a fraction, `x * CAST('1.0' AS double precision) IN (...)`, for the reason above. See [Long lists](#long-lists). | Regular, or push-down with a fraction |
| `=` with 8 whole days or more on a time column | `DATE_TRUNC('day', x) IN (a, b, ...)` | Push-down |
| `!=` or NOT of `=` with 8 values or more | `(x NOT IN (...) OR x IS NULL)` for `!=`, `(x NOT IN (...) AND x IS NOT NULL)` for NOT, and `(CAST(x AS date) NOT IN (...) AND x IS NOT NULL)` for whole days | Regular or push-down |
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
| `x > t` | `(x > t OR x IS NULL) AND x IS NOT NULL` | Cube does not merge this into a date range, so it reads `t` as it is. Such a filter makes stock Cube compare a `DateTime64` at whole seconds, see [Known wrong numbers](#known-wrong-numbers). |
| `x < t` | The same form. With a rollup on this column, or with a rolling window or a time shift, it stays `x < t`. | A rollup, a rolling window and a time shift need a date range. |
| `x >= t`, `x <= t`, `between` | As they are | The range is exact at a whole second. |
| A bound with a fraction, `x = t.5` | The driver stops the query | Cube drops the fraction, and this would move the bound on a `DateTime64`. |

With a report time zone that is not UTC, Cube reads the two bounds of a date range in that zone, and each other bound as UTC. One bound, a bound next to `OR` or `NOT`, and a bound of a filter on an expression are "other". So on each Cube, the driver writes each bound on a column with `(x >= t OR x IS NULL) AND x IS NOT NULL` and `t` in UTC. Cube never merges this form into a range. A time shift needs a date range, so with a time shift the two bounds stay in the zone. A rollup needs a date range too, and so does a cube whose SQL reads the range as a value. So two bounds on such a column stay in the zone. This applies when these two bounds are the only filters on that column, at the top level of the filter. A filter on a time of a stage with an aggregation stops, see [What the driver stops](#what-the-driver-stops).

### Stages

Cube pushes a filter of an outer stage below the aggregation of a subquery. A filter on a saved question then changes the rows of the question: a month becomes a part of that month, and a share of the total becomes 1. A limit on the subquery stops this, so the driver adds one to each stage with an aggregation that has a next stage, and to a native question with an aggregation. `CUBEJS_DB_QUERY_LIMIT` still applies to the rows of that subquery.

A subquery with raw rows has no limit, because Cube reads the outer filter correctly there. Its measure columns are NULL unless a later stage uses them, so raw rows of a table and raw rows of a model show the same values. If a later stage aggregates a measure column, the driver selects the measure, so `sum(revenue)` through a model is the measure.

If a later stage filters or groups by a measure column, and the model has the primary key of the cube, the driver writes `MEASURE(x)` and `GROUP BY` of the dimensions in the subquery. Each group is then one row, so `revenue > 150` filters rows, and a breakout on `revenue` has one group for each value. With no primary key in the model, a group is more than one row, so the driver stops the query.

### Two stages

Cube cannot plan some first stages, and it can plan the same query as two stages. The driver asks Cube with `EXPLAIN` for a stage that can fail to plan, and it asks at most once a minute for one SQL text. If Cube has no plan, or the stage needs it, the driver writes an inner stage with the plain members and `GROUP BY`, and an outer stage that computes the rest. Cube writes both stages as one SQL query of the database, so `CUBEJS_DB_QUERY_LIMIT` does not cut the inner stage, and it applies `default_filters`.

| Stage | Why one stage fails |
| --- | --- |
| A view with `default_filters`, with a part of a date, a filter that needs push-down, or `distinct` next to a measure | Cube has no push-down for such a view. It fails with `Can't detect Cube query`, or with `was used with the aggregate function`. |
| `count`, `sum`, `avg`, `cum-sum`, `cum-count`, `count-where`, `sum-where` or `share` on a column of a joined cube, through a many to one join, also a custom column on it, a column of a view from another cube, and a later stage over raw rows | Cube reads each row of the joined cube once. `count(products.price)` gave 29 where the truth is 5000. The outer stage adds up the row count of the first cube, so the number is the number of a SQL join. A later stage over raw rows first joins the first stage. |
| A custom column that reads one column twice, such as `case` with two branches, next to `COUNT(*)` or a `SUM` | Cube fails with `Schema contains duplicate qualified field name`. |

The outer stage needs the rows of each group, so these limits apply. If one does not hold, the query keeps its message:

- A measure must add up over groups: `sum`, `count`, `min` or `max`, with no rolling window and with no other measure in its SQL. A distinct count or an average stops.
- A column that the outer stage folds must come from the cube of each measure, or from a many to one join of it, and it cannot be a `switch` dimension. Else a row of the cube is in two groups.
- No column can read a member of a cube, as a `sub_query` dimension does, because Cube changes its value with the other columns of the query.
- A row count needs a measure of type `count` with no filter on the first cube, and no row with a NULL key.
- Under a report time zone, a filter on a time stays in the inner stage. If Cube cannot plan it there, the query stops. See the row on a time filter below.

### Fan-out

A join does not change a measure. A measure of a cube counts each row of that cube once, also next to a one to many join. So `orders.count` over `orders` and `payments` is 5000. Use a measure of a cube to count that cube.

An aggregation of a dimension or a count of rows has the number of a SQL join. Cube protects a dimension from fan-out, so `SUM(users.id)` over `users` and `orders` reads each user once: 20100, where a SQL join gives 498050. The driver writes [two stages](#two-stages) for this form: the inner stage groups by the dimension and has `COUNT(*)`, which is a real count of the rows of the join, and the outer stage adds up `value * rows`. So over `orders` and `payments`, `COUNT(*)` is 10000 and `SUM(quantity_dim)` is 25000. `stddev`, `var`, `median` and `percentile` have no form from groups, so they stop.

Native SQL has no stage that the driver can write, so such an aggregation stops with a message. Write the two stages in the text: `SELECT SUM(id * c) FROM (SELECT u.id, COUNT(*) c FROM users u CROSS JOIN orders o WHERE u.__cubeJoinField = o.__cubeJoinField GROUP BY 1 LIMIT 2147483647) t`.

A dimension of a view that comes from a joined cube has the same protection, so `SUM(users_id)` over `paid_sales` would read each user once. In native SQL it stops, and the message gives the exact form with a count measure of the view: `SELECT SUM(users_id * n), SUM(n) FROM (SELECT users_id, MEASURE(count) AS n FROM paid_sales GROUP BY 1) t` gives 199140 and 2000, as ClickHouse does. A `WHERE` goes inside the subquery. The query builder writes this form itself.

A view with a one to many join from the cube where it starts has no one set of rows, because Cube joins only the cubes of the columns of a query. On such a view, a count of rows and an aggregation of a dimension stop with a message. A measure of the view runs.

### Custom granularities

In native SQL, use `DATE_TRUNC('half_day', created_at)`. In the query builder, add a dimension to the model with `sql: "{created_at.half_day}"`, and select **Don't bin** for it. Metabase selects **Day** by default.

## What the driver stops

These queries fail with a message that tells what to do. The message includes the text of Cube.

| Query | Reason |
| --- | --- |
| Join on columns that no join of the model compares | The driver writes `__cubeJoinField`, so Cube would use the model join. Cube refuses `JOIN ... ON a = b` on other columns in native SQL with `Can't detect Cube query`, and `CROSS JOIN ... WHERE o.other_id = u.id` applies the model join and then the filter. |
| An aggregation of a dimension of a joined cube, or next to a one to many join, that depends on how often a row counts, when [two stages](#two-stages) cannot give it: `stddev`, `var`, `median` and `percentile` always, and `sum`, `avg`, `count`, `cum-sum`, `cum-count`, `count-where`, `sum-where` or `share` next to a measure that does not add up or on a `sub_query` dimension | Cube reads each row of the joined cube once. `SUM(products.price)` by a column of `orders` in one stage gives the total of the products, for each group. Add a measure, or start from the cube of the dimension. `min`, `max` and `distinct` do not depend on this, and they run. |
| Join of a question, or with a filter inside the join | A subquery that lists its columns has no `__cubeJoinField`. A subquery with `SELECT *` has it, but a filter inside it applies to the whole query, so it has no use here. |
| A right or a full join | Metabase asks a driver for this feature, and Cube joins are left joins. |
| A count of rows or an aggregation of a dimension on a view with a one to many join from the cube where it starts | See [Fan-out](#fan-out). |
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
| Rolling window measure with a report time zone that is not UTC, on stock Cube | Stock Cube reads the bounds of each window in UTC and adds the wrong months together, also on its REST API. The dialect gives the truth. See `zone.*.rolling`. |
| A time shift measure with a report time zone that is not UTC, on stock Cube | Stock Cube adds the interval in UTC, so next to a clock change a row moves to another day. The dialect gives the truth. |
| Rolling window measure by a week that starts on another day than Monday, with no custom granularity on the time dimension | The week is a number, so Cube has no time series. Add a granularity with `interval: 1 week` and the offset of that day. |
| Rolling window measure on stock Cube with `CUBEJS_TESSERACT_SQL_PLANNER=false` | Each bucket gets one wrong number. `/v1/sql` shows the planner. The dialect gives the truth. |
| Stock Cube: a measure with `type: time`, with a report time zone that is not UTC, unless its SQL reads `SQL_UTILS.convertTz` | Stock Cube prints the clock of the zone of the column, and it does not show that zone. [The dialect](https://github.com/andrewkomkov/metabase-cube-driver/tree/main/cube-clickhouse) gives each such measure in the report time zone, on each column type. |
| A measure with `type: count` and no SQL of its own, on a cube where rows have a NULL primary key | Cube writes `count(pk)`, so a row with a NULL key does not count. This form keeps a left join from counting a row with no match, so the driver cannot write `COUNT(*)` in its place. The count of rows of Metabase gives the truth. The driver finds this with one query for each cube and minute. |
| A filter or a breakout on a measure column of a model, when a group of the dimensions of the model is more than one row, for example with a NULL key | The driver reads the value of a row with `GROUP BY`, so rows would join. The driver compares the rows and the groups for this shape only, and a model on a unique key keeps its value. |
| `distinct` of a time column, with a report time zone that repeats an hour now or since 1970 | Cube counts the clock, so two rows in the repeated hour count once. A zone with no repeated hour, such as `Asia/Kathmandu`, works. |
| `count-where`, `sum-where` or `share` on a column of a joined cube, also a column of a view that comes from another cube than the first dimension of the view | Cube adds each row of that cube once, so the number would be wrong. |
| A filter on a time column of a question with an aggregation, with a report time zone that is not UTC | The database reads the filter value as UTC, and a rollup reads it in the zone. So the filter can pick another bucket, and the driver cannot see which one Cube reads. A filter inside the question has no such limit. |
| A relative date before the year 1900 | ClickHouse clips such a time with no error, so the filter compares with another date. |
| A time shift measure with a date range that starts or ends inside a bucket, with a report time zone that is not UTC | Cube moves rows of other periods into the bucket. |
| A time shift measure with one date bound | Cube reads a time shift only with a date range, so it gives periods outside the filter. |
| A time shift measure with `x > t` on stock Cube | A date range on stock Cube has no form for this bound that is right for a `DateTime64`. Use `>=` with the next second, or the dialect. |
| A cube whose SQL reads a filter as a value, as in `v(date_from = ${FILTER_PARAMS.c.t.filter((from, to) => from)})`, when the filter on that dimension is not a lower and an upper bound on a time, or one equal value on another type. In native SQL the lower bound is `>=`, because `>` has a form that is not a date range | Cube writes `1 = 1` in the place of the value, and the database reads it as a value. So the result is empty or wrong, or the database fails with an unclear message. `/v1/sql` shows this form. A cube that reads the filter as a condition, as in `WHERE ${FILTER_PARAMS.c.t.filter('t')}`, has no limit. With a report time zone, a text filter, a part of a date or an expression next to the bounds also stops here, because Cube then reads no date range. |
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
| `rollup_lambda` on ClickHouse, when the range reads source rows, with no report time zone | Cube fails with `Stream query failed: aborted`. Inside the range of the rollup it works. See `preagg.rollup_lambda`. |
| `OR` of two segments | Cube fails with `Can't use OR operator with segments`. |
| A segment of another cube | Cube fails with `No field named`. Use a view that has it. |

These checks read the stages of the query, so they work on the query builder, on a model and a question by column name, and on the MCP tool `execute_query`, also with `source-card`. A later stage can name a column by its output, as `products__via__product_id__price`, or by the name of its field, as `price`. Metabase core finds both, and the checks read both, so both give one number.

### Rollups that hold a part of the rows

Cube answers from a rollup with no error in four cases where the rollup does not give each row of the query once: the query is out of the build range, the rollups of a `rollup_lambda` have a gap, the rollup has `allow_non_strict_date_range_match: true` and a bound of the query is inside a bucket, or a `rollup_lambda` joins a rollup to its source under a report time zone. In the last case stock Cube reads a row twice or not at all: `lambda_sales` by month in New York gives 319894 and in Kolkata 319607, and the truth is 319790. Cube reads a rollup of a lambda up to the start of its last partition, so a gap comes when the next rollup does not hold that partition. A build range that ends inside a partition holds rows only up to that end. If only the database can tell an end of a build range, the driver gives the first or the last partition no credit.

`/v1/meta` names these rollups, so a query on another cube adds no request. For a query on such a cube or a view on it, the driver asks Cube what it would read: `EXPLAIN` gives the request, `/v1/sql` the rollups, and `/v1/load` with one row the partitions. A bound that the query does not have is the first or the last time of the source. Each answer is at most one minute old.

If the rollups do not hold each row, the driver adds a filter that holds for each row, as `(id IS NULL) OR (id IS NOT NULL)`, on a column that the rollup does not hold. If the rollup holds each public dimension, the filter is on its time column, which also makes Cube read the source. Cube then reads the source, so the number is right and the query is slower. A query that the rollup holds keeps the rollup. Only two cases stop with a message: a table with each column in the rollup, and native SQL, which has no stage for the filter. A filter on a column that the rollup does not hold, or a date filter in the range, lets native SQL run. The REST API of Cube has no driver in front of it, so it still gives the rows of the rollup.

### Native SQL

Native SQL is fail-closed by grammar. The SQL API of Cube reads much of SQL with another meaning, and it gives a number with no error. A list of bad spellings does not end, so the driver has a list of good forms.

1. A template variable, a field filter and a snippet become literals in the text.
2. The driver parses the text with a recursive-descent parser over its lexer (`src/metabase/driver/cube/sqltext.clj`).
3. `src/metabase/driver/cube/grammar.clj` walks the tree, and it resolves each column in the sources of its `SELECT`. Each node must match an entry of the allow-list, on the types of the data model. Any other node stops the query. The message starts with `Native SQL on Cube:`, it names the first such construct, and it gives the supported form when one exists.
4. The driver writes each form that Cube reads otherwise, in `src/metabase/driver/cube/correct.clj`.

A saved native question as a stage reads the same path. With no data model from Cube, native SQL stops.

#### The allow-list

<!-- allow-list:start -->
| Construct | Form | Proof |
| --- | --- | --- |
| `agg/count` | COUNT(*), COUNT(x), COUNT(DISTINCT x) | 632 |
| `agg/distinct` | MIN and MAX with DISTINCT | 48 |
| `agg/filter` | FILTER (WHERE p) on an aggregation of a column | 60 |
| `agg/measure` | MEASURE(x) of a measure | 482 |
| `agg/min-max` | MIN, MAX on each type | 183 |
| `agg/number` | SUM, AVG, STDDEV, STDDEV_SAMP and STDDEV_POP, also on an expression | 368 |
| `agg/of-measure` | The aggregation of a measure column that holds that measure, such as SUM(revenue) for a sum | 14 |
| `agg/window` | ROW_NUMBER, RANK, DENSE_RANK, LAG, LEAD and an aggregation OVER a window of the groups, or of the rows of a subquery with GROUP BY, with no frame | 42 |
| `clause/group` | GROUP BY a position, an alias, a column or an expression | 857 |
| `clause/having` | HAVING on an aggregation, a group or an alias | 94 |
| `clause/where` | WHERE | 1144 |
| `fn/date-trunc` | DATE_TRUNC with a unit in a literal, on a time | 211 |
| `fn/datediff` | DATEDIFF with a unit in a literal, on two times | 19 |
| `fn/null` | COALESCE, NULLIF | 111 |
| `fn/number` | ABS, ROUND, FLOOR, CEIL, CEILING | 174 |
| `fn/text` | LOWER, UPPER, SUBSTRING, SUBSTR, LEFT, RIGHT, CONCAT, LENGTH, STRPOS | 81 |
| `from/cross-model` | CROSS JOIN or a comma of two cubes, with a.__cubeJoinField = b.__cubeJoinField in WHERE, or with MEASURE() | 38 |
| `from/join-model` | JOIN or LEFT JOIN of two cubes on the join of the data model, as __cubeJoinField or as the keys of that join | 125 |
| `from/subquery` | FROM a subquery, with a filter, a star or an aggregation over it | 173 |
| `param/value` | A template variable or a field filter, as its value | 121 |
| `pred/between` | BETWEEN and NOT BETWEEN on numbers and on times | 105 |
| `pred/boolean-column` | A boolean column as a condition | 27 |
| `pred/compare-boolean` | = <> on a boolean, with TRUE, FALSE, 1 or 0 | 14 |
| `pred/compare-null` | = and <> with NULL, which keep no row | 27 |
| `pred/compare-number` | = <> != < <= > >= on numbers, with exact values | 645 |
| `pred/compare-text` | = <> != < <= > >= on text | 331 |
| `pred/compare-time` | = <> != < <= > >= on a time | 232 |
| `pred/constant` | A comparison of two literals, as the 1 = 1 of a field filter with no value | 38 |
| `pred/in-list` | IN and NOT IN with a list of literals, also NULL | 126 |
| `pred/in-subquery` | IN with a subquery of one column, and NOT IN with one that has column IS NOT NULL in its WHERE | 29 |
| `pred/is-boolean` | IS [NOT] TRUE and IS [NOT] FALSE on a boolean column | 26 |
| `pred/is-distinct` | IS [NOT] DISTINCT FROM a literal | 28 |
| `pred/is-null` | IS NULL and IS NOT NULL | 108 |
| `pred/join-field` | a.__cubeJoinField = b.__cubeJoinField | 125 |
| `pred/like` | LIKE, ILIKE, NOT LIKE and NOT ILIKE, with a literal pattern | 284 |
| `pred/logic` | AND, OR, NOT and parentheses. NOT, <>, NOT IN, NOT LIKE, NOT BETWEEN, IS TRUE and IS DISTINCT FROM only at the top of WHERE, HAVING or FILTER | 519 |
| `query/distinct` | SELECT DISTINCT | 40 |
| `query/limit` | LIMIT or FETCH FIRST with a whole number | 268 |
| `query/no-from` | SELECT of literals, with no FROM | 32 |
| `query/offset` | OFFSET with a whole number | 62 |
| `query/order` | ORDER BY a position, an alias, a column or an expression, ASC or DESC, NULLS LAST | 316 |
| `query/select` | SELECT ... FROM one table, a view or a subquery | 1679 |
| `query/union` | UNION and UNION ALL at the top of a query | 33 |
| `query/with` | WITH of queries that are not recursive | 71 |
| `select/alias` | An alias of a column, also with the name of a column | 858 |
| `select/column` | A dimension, by its name, with or without its table | 964 |
| `select/segment` | A segment in WHERE | 43 |
| `select/star` | * or t.*: the public dimensions, written out by the driver | 57 |
| `value/arithmetic` | + - * % and unary minus on numbers | 165 |
| `value/case` | CASE WHEN ... THEN ... ELSE ... END, and CASE x WHEN | 151 |
| `value/cast-date` | CAST(x AS date), x::date or DATE(x) of a time: DATE_TRUNC('day', x) | 20 |
| `value/cast-number` | CAST(x AS numeric, decimal, double precision, real or float), and x::numeric | 196 |
| `value/cast-text` | CAST(x AS text or varchar) of a text | 31 |
| `value/cast-time` | CAST(x AS timestamp) of a time column, which is that column | 50 |
| `value/concat` | \|\| on text | 29 |
| `value/division` | / on numbers: a real division, and NULL for a divisor of 0 | 73 |
| `value/literal` | A number, a text, NULL, TRUE or FALSE | 1645 |
| `value/time-interval` | A time + or - INTERVAL 'n unit', with the dialect and with no report time zone | 25 |
| `value/time-literal` | A time as a text, as TIMESTAMP 'x', as DATE 'x' or as a cast of a text | 248 |
<!-- allow-list:end -->

`Proof` counts the queries with this construct in the generator seeds of the last measured run. Each query had a truth from direct ClickHouse SQL, on Cube with the dialect, stock Cube and Cube in production mode. See `dev/fuzz/README.md`.

The acceptance test is `dev/fuzz/analyst.py`: 30 queries of an analyst on the demo model, written before the grammar. The check `native.grammar.analyst_queries` runs it.

#### What the driver writes

The driver corrects the meaning of NULL only at the top of `WHERE`, `HAVING` and `FILTER`, where a row counts only if its condition is true: `NOT` moves down by De Morgan, and `<>`, `NOT IN`, `NOT LIKE`, `IS NOT TRUE` and `IS DISTINCT FROM` get their form for NULL there. In each other place these forms stop. Other corrections, such as of numbers, times and text, hold in each place.

| Construct | What Cube does with no driver | What the driver does |
| --- | --- | --- |
| `*` or `t.*` | Reads each measure, and each private member in dev mode. | The public dimensions, written out. No measure, no `switch` dimension and no second path of one member. |
| Any time literal | A date with no time at the end of a date range is the end of that day. | `TIMESTAMP 'x'`, with the meaning of SQL. A fraction of a second stops on stock Cube. |
| `x BETWEEN a AND b` on a time | Reads the end as above. | `x >= a AND x <= b`. |
| `x = t` on a time | Next to `DATE_TRUNC`, reads the whole bucket. | `x >= t AND x <= t`. |
| `x > t`, `x < t` on stock Cube | Moves the bound, see [Date bounds](#date-bounds). | `((x > t OR x IS NULL) AND x IS NOT NULL)`. `x < t` stays on a column with a rollup, a rolling window or a view. |
| A time under a report time zone | Reads a literal in the zone only in a date range, and as UTC in other forms. | A lower and an upper bound at the top of `WHERE` stay a date range. Each other bound in `WHERE` has the guarded form, with the literal in UTC. A time in `HAVING`, `CASE`, `FILTER`, `IN`, `DATE_TRUNC` in a filter and a filter over a subquery stop. |
| `LIKE`, `ILIKE` | Ignores case on ClickHouse, and reads `_` as a character. | `(x \|\| '') LIKE 'p'`, which the database reads. A pattern of `'%' \|\| 'x' \|\| '%'` is one literal. |
| A function of a column next to a literal, such as `LEFT(x, 1) IN ('a')` or `STRPOS(x, 'a') > 0` | Reads some of them as a member filter with another meaning. | A text with `\|\| ''` and a number with the exact factor, so the database compares them. |
| A subquery with an aggregation | Pushes an outer filter below the aggregation. | `LIMIT 2147483647` on the subquery. |
| A column with no alias next to an expression | Stops with `Schema contains duplicate qualified field name`. | `x AS x`. |
| `CAST(NULL AS t)` | Grows the rewrite graph next to an expression. Twenty restart Cube. | `NULL`. |
| `CAST(x AS numeric)` of a column | Writes it with no Nullable, and a scale that ClickHouse can stop on. | `x`. |
| `CAST(x AS date)` or `DATE(x)` of a time | Is not on the list of functions for each path. | `DATE_TRUNC('day', x)`. |
| `a JOIN b ON` the keys of a join of the model | Stops with `Can't detect Cube query`. | `a JOIN b ON a.__cubeJoinField = b.__cubeJoinField`. |
| `a JOIN b` | Reads a left join, so rows with no match stay: 10 where the truth is 6. | Adds `b.key IS NOT NULL` to `WHERE`. If this key is private, the query stops. |
| `a CROSS JOIN b WHERE a.__cubeJoinField = b.__cubeJoinField`, also a comma | Next to an expression in `WHERE`, gives no rows: 0 where the truth is 2408. | `a LEFT JOIN b ON ...`, as the docs of Cube read this form. |
| `FETCH FIRST n ROWS ONLY`, `U&'x'` | | `LIMIT n`, `'x'`. |
| `COUNT(*) FILTER (WHERE p)`, each aggregation, also before `OVER` | Drops `FILTER`, so 5000 where the truth is 2000. | `COUNT(CASE WHEN p THEN 1 END)`, and the same for the others |
| `MEASURE(x) FILTER`, `SUM(measure) FILTER`, FILTER on another function | Drops `FILTER`. | Stops. CASE cannot go inside a measure. |
| `x <> v`, `x NOT IN (...)`, `NOT (x = v)`, `x NOT LIKE p`, in `WHERE` and `HAVING`, on a column or a column of a subquery | Keeps NULL: 4000 where the truth is 2000. | `NOT` moves down to each comparison by De Morgan, and each `<>` gets `AND x IS NOT NULL`. In `WHERE` and `HAVING` only true counts, so this holds for three-valued logic, also under a second `NOT`. Inside `CASE`, in `SELECT` and on an expression, the database reads the comparison, and it is right as it is. |
| `x NOT IN (a, NULL)` | Gives rows. | No row, as SQL. |
| `x NOT IN (SELECT y ...)`, also `NOT (x IN (SELECT y ...))`, where `y` can be NULL | Reads NULL as a value, as ClickHouse does: 265 where SQL gives no row. | Stops, unless the subquery has `y IS NOT NULL` at the top of its `WHERE`. |
| `NOT (x LIKE p)`, `NOT (x NOT ILIKE p)` in `FILTER` | No plan: `Rewrite Error: Column #CASE WHEN NOT ... not found`. | `FILTER (WHERE p)` gets the corrections of `WHERE`, also under a second `NOT`: `x NOT LIKE p AND x IS NOT NULL`, or `x LIKE p`. `CASE WHEN` keeps only true, as `FILTER` does, so these forms are exact there. See `native.not_like_in_select` and `native.not_like_three_values`. |
| `NOT`, `<>`, `!=`, `NOT IN`, `NOT LIKE`, `NOT ILIKE`, `NOT BETWEEN`, `IS [NOT] TRUE`, `IS [NOT] FALSE` and `IS [NOT] DISTINCT FROM` inside `CASE`, as a value in `SELECT`, in a function, or in `GROUP BY` or `ORDER BY` | Cube reads some of them with another meaning for NULL, and a value with NULL shows here: `NOT (NOT (n_str LIKE 's1%'))` in `CASE` gave 400 rows where the truth is 150. | Stops, with a message that names the form. Write the condition in `WHERE`, `HAVING` or `FILTER`, such as `COUNT(*) FILTER (WHERE x NOT LIKE 'a%')`, or use `=`, `LIKE`, `IN`, `BETWEEN` or `IS NULL` there. See `native.not_like_three_values`. |
| A native question with no `LIMIT` or `FETCH` at the top, or with a larger one | On Cube Store, a result of about a million rows is too large for one message: Cube retries with no end, and Metabase stops the query after 300 s with no message. | At most `n` rows at the top, where `n` is the number of rows that Metabase keeps: 2000 for a question, the row limit of a download for a download. A query with no `LIMIT` gets `LIMIT n`, also after an `OFFSET`, and a larger `LIMIT` or `FETCH` becomes `n`. Metabase drops the other rows anyway, with `ORDER BY` these are the first rows in that order, and an aggregation inside the query still reads each row. See `native.row_limit`. |
| A download of more rows than one message of Cube Store holds, with dev mode off | Cube retries with no end, and the download stops after its time-out with no message. | Before a query that can give more than 100000 rows, on a Cube with dev mode off, the driver counts the rows of the query and measures 100 of them. Cube Store takes one message of at most 64 MiB, the default of `CUBESTORE_TRANSPORT_MAX_MESSAGE_SIZE`, and Cube keeps each value as text with its key `cube__member`. So three integer columns of 1048575 rows made 93 MB, and Cube Store logged `Websocket message too large`. The estimate gives each value its key, the longest alias of the cubes that the query names, and at least 10 characters. If the result would be larger than 90% of 64 MiB, the query stops with the number of rows that fit. The estimate is high by about a third for narrow rows, so it can stop a download that would fit. `make production` checks this, see `run_large_results` in `dev/review/production.py`. |
| A number with a fraction in a comparison, `IN` or `BETWEEN`: `x = 44.001`, `(x) = 44.001`, `CAST(x AS numeric) = 44.001`, `x::numeric`, `x = '44.001'`, `x = CAST('44.001' AS numeric)`, `x = 44.0 + 0.001`, a column of a subquery, a parameter, in `WHERE` and `HAVING` | Sends the number as text, and ClickHouse cuts it to the scale of a `Decimal`: 83 where the truth is 0. On an integer column it stops. | `x * CAST('1.0' AS double precision) = 44.001`. A column in an expression makes Cube write the number as it is, so the database compares two doubles. A rollup does not serve this filter. Text compares as a number only with a column of numbers. |
| `a / b` | Has three meanings: `Decimal / x` keeps the scale in the database, 160.00 / 7 is 22.85. Above a CubeScan, integers divide with no fraction, `COUNT(*) / 7` is 714. In the database, `quantity / 2` is 1.5. | A real division on each path, as on ClickHouse: `a * CAST('1.0' AS double precision) / b`. To cut the fraction, write `FLOOR` or `TRUNC`. |
| `a / b` where `b` can be 0 | The database gives an error for a `Decimal`, and infinity for a double, which a filter reads as a number. | `b` is `NULLIF(b * CAST('1.0' AS double precision), 0.0)`, so such a row is NULL, as Metabase writes `/` in the query builder. A literal 0 as the divisor stops. |
| `DATE_TRUNC(u, x) NOT IN (a, b)`, also `NOT (DATE_TRUNC(u, x) IN (a, b))` | Reads `notInDateRange`, and it joins the ranges with OR, so two buckets apart exclude no row. | Each value has its own `<>`. |
| `SUM(DISTINCT x)`, `AVG(DISTINCT x)`, `STDDEV(DISTINCT x)` | Drops `DISTINCT`: 12500 where the truth is 10, and an average of 4.49 where the truth is 4.5. | Stops. `COUNT`, `MIN` and `MAX` with `DISTINCT` are right. |
| `GROUP BY ROLLUP(x)`, `CUBE(x)` | Reads a plain `GROUP BY`, so the rows of the totals are not in the result. | Stops. Write the groups and the total with `UNION ALL`. |
| `GROUP BY GROUPING SETS (...)` | Cannot plan it. | Stops with a message that names it. |
| `ORDER BY x NULLS FIRST` | Drops `NULLS FIRST`. With `LIMIT`, the result has other rows. | Stops. `NULLS LAST` holds. With no `NULLS`, NULL is last in both directions, as on ClickHouse. SQL leaves this open, and Postgres has NULL first for `DESC`. |
| `FROM a, b` or `a CROSS JOIN b` with no `__cubeJoinField`, also two subqueries or two `WITH` queries | Ignores the cross product, and reads the sources through the model: 5000 where the truth is 1000000, and a count of 199 where the truth is 200. | Stops. `MEASURE()` of two cubes with `CROSS JOIN` is the form of the Cube docs for a join of the model, and `MEASURE()` is not SQL, so it runs. |
| A join of a cube to itself through `__cubeJoinField` | Joins the cube once. | Stops. |
| An aggregation of a dimension next to a one to many join, `SUM(u.id)` | See [Fan-out](#fan-out). | Stops. |
| Two aggregations with one name and no alias in a `UNION`, `SELECT COUNT(*) FROM a UNION ALL SELECT COUNT(*) FROM a` | Gives NULL in each row. | The first branch gets an alias. |
| `COUNT(*)` on no rows, on a cube with a rollup | NULL. | `COALESCE(x, 0)` around a count in each `SELECT` with no grouping, also in a branch of a `UNION` and over a subquery. |
| A window function or `DISTINCT ON` over raw rows of a cube with a rolling window measure | Fails, because it reads each measure of the cube. | Stops with a message that names it. The same query runs on a cube with no such measure, and a window function over groups runs on each cube. |
| A subquery of raw rows over a join or a view, with an aggregation outside it, also a saved native question as a source: `SELECT SUM(id) FROM (SELECT u.id FROM users u CROSS JOIN orders o WHERE ...) t` | Reads both as one query, so each row of `users` counts once: 20100 where the truth is 498050. | `LIMIT 2147483647` on the subquery. `CUBEJS_DB_QUERY_LIMIT` applies to its rows. |
| `x IS NOT TRUE`, `x IS NOT FALSE` in SQL that the database reads | Writes it with no NULL: 429 where the truth is 571. | `(x = 0 OR x IS NULL)`, and the same for the others. |
| `x = TRUE`, `x`, `NOT x` in `WHERE`, on a boolean dimension with its own SQL | Sends the text `'true'`, and ClickHouse stops on it. | `x = 1`, `x = 0`. |
| `x IS DISTINCT FROM v AND y` | Writes it with no parentheses, as `x IS DISTINCT FROM (v AND y)`. | `(x <> v OR x IS NULL)`, with a literal. |
| `CAST(x AS numeric)` on a column with NULL, a cast of a literal in a list | Stops on the type in the database. | In a comparison with a number, the cast goes away, because the meaning is the same. |
| `x - y` on two times | Fails on the type of the result. | Stops with a message. Use `DATEDIFF`. |

#### What stops, with the reason

- `IS TRUE` on an expression, `= ANY`, `~`, `SIMILAR TO`, `EXISTS`, a subquery as a value, a function of literals such as `ABS(44.001)`, `IS DISTINCT FROM` a column, `LIKE` with a pattern from a column and `ESCAPE`. Each of these gave a wrong number in some spelling, or Cube has no plan for it. Write the form in the message.
- `EXTRACT`, an interval, `AT TIME ZONE`, a window frame, a window over raw rows, `DISTINCT ON`, `UNION` in a subquery, a join of a subquery, and a right or a full join. Cube has no plan for these on each path. Use `DATE_TRUNC`, or a dimension or a measure in the data model.
- A rolling window measure with no `DATE_TRUNC` on a time column, or with a filter inside a bucket, with `OR` or `NOT`, or with a custom granularity as the bucket.
- A time shift measure with no time bucket and no date filter. A custom granularity that a rollup reads wrong, see the table above. A cube whose SQL reads a filter as a value, with another filter on that dimension.
- Two columns from one member through different join paths. A raw select of a `switch` dimension.
- `VARIANCE`, `VAR_SAMP` and `VAR_POP`. The SQL API of Cube has no implementation of them, and it stops with `variance is not implemented`. `STDDEV` runs.
- `SUM`, `AVG`, `COUNT` or `STDDEV` of a dimension of a joined cube. See [Fan-out](#fan-out).
- A measure column outside `MEASURE()`, or in an aggregation that does not hold it. A column that is in `SELECT` and not in `GROUP BY`. A segment outside `WHERE`.
- A private member by name, also through an alias with its name. See [the security docs](security.md).

#### The proof

`make fuzz-native` runs `dev/fuzz/native_grammar.py` on the three paths. The generator follows the allow-list, and it writes each query in two texts from one tree, with no driver code. It fails on a wrong number, a raw error, a mutation that ran, and a refusal of a query on the list with no report time zone. A text and its print from libpg_query, the parser of PostgreSQL, must have one answer from the grammar. The workflow `fuzz` runs a new seed each night.

### Long lists

The planner of Cube rewrites a graph of the expression, and it stops at about 10000 nodes (`AST node limit`). Each condition joined with OR or AND adds to the graph, and a value of an `IN` list adds less. In a run on stock Cube with 2 GiB:

| Form | Values | Result |
| --- | --- | --- |
| `x IN (...)` | 2000 | Runs, in 12 to 19 s. 1000 takes 3 s. |
| `x IN (...)` | 4000 | Stops on the limit of the planner after 29 s. |
| `x IN (...)` | 8000 | Stops on the limit of the planner after 105 s. |
| `x = a OR x = b` | 50 | Stops on the limit of the planner after 5 s. |
| `x = a OR x = b` | 1500 | Stops after 26 s, with 300 MB more in the container. |
| `x = a OR x = b` | 2000 | Cube restarts. The client sees a broken connection. |

So a filter on one column with many values must not be a chain:

- The query builder writes `IN` or `NOT IN` from 8 values, for numbers, text, times and whole days, with `=`, `!=` and NOT. Metabase core writes such a filter as a chain, so a list of 1000 values with `!=` or on a time was 2000 conditions or more, and it restarted Cube. With a report time zone, a list of times stays a chain, because Cube reads a literal in another way in `IN`.
- Native `x = a OR x = b ...` and `x <> a AND x <> b ...` from 8 values on one column become `IN` and `NOT IN`.
- A list above 2000 values and a text with more than 500 conditions stop with a message before the query reaches Cube.

A filter from another form, such as `contains` with many values, stays a chain and has the limit of the planner.

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

`preagg.latency.big_orders_hit_is_faster_than_a_miss` compares a hit and a miss on the REST API of Cube, with no cached result. It proves each one from `usedPreAggregations`. One run gave these medians of five:

| Query on `big_orders` | Rollup | Time |
| --- | --- | --- |
| Revenue by status and month | `daily`, a hit | 56 ms |
| Revenue by customer and year | none, a miss that reads 10 million rows | 2441 ms |

## Limits of Cube that the driver cannot change

- A number above 2^53 stops with a clear message, because the Cube SQL API has the type Double, and the ClickHouse driver of Cube sends JSON numbers. `SELECT ui64` read 9007199254740996 where the truth is 9007199254740995. A long decimal below 2^53 fits a Double, so the driver cannot see it. A dimension with `sql: toString(column)` keeps the digits of both.
- The driver sends `SET TIME ZONE` with the report time zone of Metabase, so Cube makes each bucket in that zone. See [Date bounds](#date-bounds) for a time literal. With no report time zone it is UTC. A rollup serves only the zones in its `scheduled_refresh_timezones`, so a query in another zone reads the source. `DATEDIFF` in the week number form compares with a literal, so it is tested on the zones of the check `mb.report_timezone` only.
- On stock Cube, a `DateTime64` loses its milliseconds in a result, so raw rows show whole seconds. Buckets are right. See `clickhouse.datetime64_millis`.
- A rolling window with `trailing: unbounded` for more than one bucket fails on ClickHouse before 26.10, see above. One bucket works.
- The repeated hour of a clock change gives two rows with one label, such as `01:00:00-04:00`, where Metabase on Postgres labels the second row `-05:00`. Each number and the total are right. The SQL API gives a time with no offset, so the two rows have one local time, and the driver cannot tell which row is the first: a sort of the rows is not a promise of Cube, and a row can exist in only one of the two hours. A rollup by hour joins the two rows into one.
- A masked measure with no group gives one row for each source row, all with the mask. A group or a `COUNT(*)` next to it gives one row. Cube reads the SQL of the mask as a value, and `/v1/meta` does not show that a measure has a mask.
- `count_distinct_approx` is `uniq()` on ClickHouse. A rollup cannot store it on ClickHouse. If a rollup holds this measure, each query on the measure stops, and each run of the refresh worker stops, so each other rollup goes stale. Do not put it in a rollup. See [the audit](cube-audit.md#rollup-approx-worker).
- A left join starts at the cube that the data model starts at. `COUNT(*)` on a left join from `products` to `orders` stops with a clear message, from `Can't find join path`, if the model has only the join from `orders` to `products`. A measure of `orders` works, and it has no row for a product with no orders.
- `avg` and `count_distinct` measures are not additive. A rollup with other dimensions than the query does not serve them.
- `DATEDIFF` compares with a time literal in the week number form. The literal is in the time zone of the ClickHouse server, and the demo server has UTC. A server in another zone can move the start of a week.
- Forms with `DATEDIFF` were tested on ClickHouse only.

## Known wrong numbers

The driver cannot stop these. Each number comes with no error. [Cube findings](cube-findings.md) and [Cube audit](cube-audit.md) have the verdict and a clean repro for each. `conformance/features.yaml` records each one, with its reason.

| Query | What you get | Entry |
| --- | --- | --- |
| Stock Cube: a partitioned rollup with no `build_range_end`, with a report time zone that is not UTC | West of UTC, the rows of the last hours are not in the result. The dialect gives the truth. | `preagg.time_zone.partitioned_hit` |
| A long decimal, such as `Decimal(38, 8)`, below 2^53 | It loses digits, because the Cube SQL API has the type Double. Its value fits a Double, so the driver cannot see this. A dimension with `sql: toString(column)` keeps the digits. | `compile.ch.decimal_above_double` |
| Stock Cube: `x >= a AND x < b` on a `DateTime64`, on a column with a rollup, from native SQL or the query builder | The range ends at the last whole second before `b`, so the rows of that second are out. Cube does not show the type of a column. [The ClickHouse dialect](https://github.com/andrewkomkov/metabase-cube-driver/tree/main/cube-clickhouse) gives the truth. | `native.bound.datetime64_last_second_limit` |
| Stock Cube: native `x > t` on a `DateTime64`, with `t` at a whole second | The rows later in the second of `t` are not in the result, and a `<=` on the same column next to it keeps the rows of the second of its bound. Once a filter on the column is not a date range, Cube compares the column at whole seconds. A date range starts 1 ms after `t`, and stock Cube drops that fraction, so it keeps a row of a `DateTime` at `t`. One form must hold for each type, and Cube does not show the type of a column. `<=`, `>=` and `BETWEEN` alone give the truth. [The ClickHouse dialect](https://github.com/andrewkomkov/metabase-cube-driver/tree/main/cube-clickhouse) gives the truth. | `native.bound.datetime64_upper_and_strict_lower` |
| Stock Cube with a report time zone that is not UTC: a `Date` or `Date32` column | Cube reads it as a time at midnight UTC, so a row can move to another day, in a bucket and in a filter. Cube does not show the type of a column. A dimension with `meta: { date_only: true }` stops. The dialect reads it as midnight in the zone. | `grid.compact.report_zone` |
| A filter on a time with an offset in its literal, as `2025-06-15T11:00:00Z`, with a report time zone that is not UTC | Metabase core drops the offset of a literal on a `type/DateTime` column before the driver gets the query, so the literal is a local time. Base type `type/DateTimeWithLocalTZ` keeps the offset, but Metabase core then writes the end of a month filter with the offset of its start, so a filter on March gets one hour of April in New York. Postgres with `timestamptz` shows the same. | `grid.compact.report_zone` |
| Stock Cube, or a ClickHouse user with `readonly`: `sum`, `min` or `max` of no rows | 0, and 1970-01-01 for a time, where SQL and Metabase on Postgres give `NULL`. An expression of it in native SQL reads that 0, so `MAX(id - 1)` of no rows is -1. Cube sends no setting for this, and the driver cannot tell no rows from a total of 0. [The ClickHouse dialect](https://github.com/andrewkomkov/metabase-cube-driver/tree/main/cube-clickhouse) gives `NULL`. | `fuzz.sample` |
| `ROUND(x, n)` of an aggregation whose value has a 5 in the digit after `n`, such as `ROUND(AVG(ROUND(n_dec, 1)), 2)` of 125.025 | Cube rounds above the database, on the Double of the value, and the Double of 125.025 is below the half, so the result is 125.02 where SQL on a decimal gives 125.03. The result is one unit of its last digit off. Metabase on Postgres with a `double precision` column gives the same. A measure with `ROUND` in its SQL rounds in the database. | `fuzz.sample` |
| Stock Cube: a rollup with a text dimension that has an empty text | Cube Store reads an empty value of its CSV as `NULL`. So the group of the empty text joins the group of `NULL`, and a filter on the empty text finds no rows. The result does not show which rows were empty. [The dialect](https://github.com/andrewkomkov/metabase-cube-driver/tree/main/cube-clickhouse) gives the truth. | `preagg.empty_text_stays_apart_from_null` |
| Stock Cube: a raw `DateTime64` value | It shows whole seconds. Buckets are right. The dialect keeps the milliseconds. | `clickhouse.datetime64_millis` |
| A value of a time in the repeated hour of a clock change, under a report time zone: a measure with `type: time`, or a raw time column | The value reads 1 hour early, with the offset of the first pass: `01:00:00-04:00` where the truth is `01:00:00-05:00` in New York. The SQL API gives a time with no offset, so the driver cannot tell the two passes apart. | `zone.time_measure_repeated_hour_limit` |
| Postgres, not the main target: a rolling window with a range that starts inside a month (N1), a custom granularity in months with no rollup | Shifted buckets. See [Cube audit](cube-audit.md). | None. The suite runs on ClickHouse. |
