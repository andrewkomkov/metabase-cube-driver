# Random query generator

`make fuzz SEED=n COUNT=n` makes random Metabase queries, runs each one through the driver, and compares each number with a truth that it writes in direct ClickHouse SQL. The same seed gives the same queries. With no seed, it uses the time.

## Parts

| File | Job |
| --- | --- |
| `catalog.py` | What the generator knows about the demo data, by hand from `dev/clickhouse` and `dev/cube/model`. |
| `gen.py` | `generate(seed, index)` gives one spec, a plain JSON value. |
| `oracle.py` | `truth(spec)` writes the ClickHouse query. It reads no driver code and no Cube output. |
| `mbql.py` | Writes a spec as a query for `/api/dataset`. |
| `fuzz.py` | Runs a seed, judges each query, reduces each failure and saves it. |
| `cases/` | The permanent cases: the smallest form of each failure that the generator found. Each run replays them first. |
| `docker-compose.fuzz.yml` | The small stack: ClickHouse with its stock profile, Cube with the dialect and one Metabase, with compose project `mcd-fuzz`. `CUBE_CLICKHOUSE_FIXES=off` gives stock Cube. |

Output goes to `dev/fuzz/out/report-<seed>.json`. `FUZZ_PROJECT` changes the compose project. `ARGS="--workers 2"` gives options to `fuzz.py`, and `ARGS="--replay"` runs only the permanent cases.

## What a query can have

Aggregations: measures of each type, `count` of rows and of a column, `sum`, `avg`, `min`, `max`, `distinct`, `stddev`, `var`, `count-where`, `sum-where`, `share`, and a ratio of two measures. Rolling windows and time shifts, also in forms that must stop.

Breakouts: each dimension type, bins, time buckets and parts of a date.

Filters: each operator on text, numbers, booleans and times. Also `and`, `or`, `not`, segments and relative dates.

Boundary values: the first and the last row, a day with a clock change, the end of a month and values out of the data. Also empty text, quotes, text that differs only in case, a number with more digits than its column, a list of 8 or more such numbers, and NULL.

Sources: cubes, views with `default_filters`, implicit joins and explicit left and inner joins. Also a table with NULL in each type, a rollup over 10 million rows, and a table with zero and NULL denominators.

Stages: a second stage on an inline query, a saved question and a model, on rows and on aggregated columns.

Settings: the report time zone and the first day of the week. These are global, so one pair holds for a block of 40 queries.

## Verdicts

| Verdict | Meaning | Fails the run |
| --- | --- | --- |
| `pass` | Each row is equal to ClickHouse. | No |
| `refused` | A clear message. The report gives the rate and the top messages, because a driver that refuses all queries passes. | No |
| `wrong` | A number with no error that differs from ClickHouse. | Yes |
| `no_refusal` | A number where only a refusal has a meaning, such as a rolling window with no bucket. | Yes |
| `raw_error` | A raw error of Cube, with no message that tells what to do. | Yes |
| `harness`, `truth_error` | The generator wrote a query that Metabase cannot read, or a truth that ClickHouse cannot run. | Yes |
| `skip` | The form has no defined truth. | No |
| `stock_known` | Only on stock Cube: a wrong number on `Date32` under a report time zone or on `DateTime64`. These are `stock-unmarked-date` and `stock-raw-datetime64` in `dev/review/known_limits.py`. | No |
| `stock_no_rows` | Only on stock Cube: `sum`, `min` or `max` of no rows gives what ClickHouse gives, 0 or 1970-01-01. The truth stays `NULL`, which is what Metabase on Postgres gives. See "Known wrong numbers" in `docs/query-compilation.md`. | No |

A failure runs a second time. If it does not come back, the report lists it under `flaky`.

## Rules of the truth

These are the rules of Metabase on Postgres and of the SQL standard, in ClickHouse text. A truth changes only if Metabase on Postgres or the SQL standard shows that it was wrong. Do not change a truth to match an output.

- An aggregate of no rows is `NULL`, except `count` and `distinct`.
- `count-where` is `SUM(CASE WHEN p THEN 1 ELSE 0.0 END)`, and `share` divides it by `COUNT(*)`. On no rows, both are `NULL`.
- `x != v`, `does-not-contain` and `NOT` of `contains`, `starts-with` or `ends-with` also keep `NULL`, as `correct-null-behaviour` in `metabase.driver.sql.query-processor` does. `NOT` of another clause has three values. Metabase core moves `NOT` inside `and` and `or` before it writes SQL, so each part has its own rule.
- A date with no time on a time column compares days, because Metabase core adds the unit `day`. A time with no offset is a time in the report time zone.
- In a filter, `!=` with a unit or a date has no `NULL`, because Metabase core writes the opposite of a range.
- In the condition of `count-where`, `sum-where` and `share`, Metabase core adds no unit, so a date is midnight of that day. Metabase on Postgres gives this number.
- The week starts on the setting `start-of-week`. The week of the year is the day of the year of the week, divided by 7 and rounded up.
- A join does not change a measure. See `docs/query-compilation.md`.

## What the generator leaves out

| Form | Reason |
| --- | --- |
| `x != n` on a part of a date | Metabase core changes the number before a driver gets the query, so `x != 5` on a month of the year becomes `x <> 1`. Metabase on Postgres writes the same SQL. The UI does not write this form. |
| `stddev` and `var` of a `Float32` | ClickHouse adds them up in single precision, so direct SQL on the column gives 2.0789 where the exact value is 2.078125. Neither the driver nor the dialect reaches this. |
| `contains` with `%` or `_` | Metabase on Postgres reads these as wildcards, and the driver reads them as text, which is what a person means. |
| `Decimal(38, 8)` and numbers above 2^53 | See `dev/review/known_limits.py`. |
| A literal with an offset | See `dev/review/known_limits.py`. |
| A measure with `min(created_at)` next to a join with users | The SQL of this demo measure names a column that both tables have. |

## CI

The workflow `fuzz` runs a new seed each night. A failure opens one issue, or adds a comment to the issue that is open. The conformance checks `fuzz.cases` and `fuzz.sample` replay the permanent cases and run a fixed seed.
