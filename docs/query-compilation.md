# How queries reach Cube

The driver writes SQL for the Cube SQL API. Cube runs that SQL in one of three ways. `EXPLAIN` on the SQL API shows which way.

| Way | `EXPLAIN` shows | A rollup can serve it | Row limit |
| --- | --- | --- | --- |
| Regular | Only a `CubeScan` | Yes | No limit for the result of the group |
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
| Offset, n below 0 | `CASE WHEN ROW_NUMBER() OVER w > n THEN LAG(x, n) OVER w END`, because ClickHouse `LAG` gives 0 on the first row | Push-down |

### Week and the first day of the week

Cube weeks start on Monday, and Cube cannot add an interval to a time on ClickHouse, see [metabase/metabase#42255](https://github.com/metabase/metabase/issues/42255). To make weeks from another day fast, add a custom granularity to the time dimension. A rollup of days then serves the query:

```yaml
granularities:
  - { name: week_from_sunday, interval: 1 week, offset: -1 day }
```

Without it, the driver writes a week number and the database reads all rows. All measure types work in both forms.

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

## Limits of Cube that the driver cannot change

- A number above 2^53 or with more than 15 digits loses digits, because Cube returns numbers through JavaScript. `SELECT ui64` gave 9007199254740996 where the truth is 9007199254740995. A dimension with `sql: toString(column)` keeps the digits.
- All times are in UTC. The Cube SQL API has no time zone for a query, and `SET TIME ZONE` has no effect. The report time zone of Metabase does not move a bucket.
- A custom granularity with an offset in months is wrong on a measure that a rollup of months serves, see `granularity.custom.offset` in `conformance/features.yaml`.
- A rolling window with `trailing: unbounded` fails on ClickHouse: `Cannot determine join keys in JOIN ON expression`.
- `count_distinct_approx` is `uniq()` on ClickHouse. A rollup cannot store it on ClickHouse, so these queries read the table.
- A left join starts at the cube that the data model starts at. A left join from `products` to `orders` drops products with no orders if the model has only the join from `orders` to `products`.
- `avg` and `count_distinct` measures are not additive. A rollup with other dimensions than the query does not serve them.
- `DATEDIFF` compares with a time literal in the week number form. The literal is in the time zone of the ClickHouse server, and the demo server has UTC. A server in another zone can move the start of a week.
- Forms with `DATEDIFF` were tested on ClickHouse only.
