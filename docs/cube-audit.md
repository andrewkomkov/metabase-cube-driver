# Cube audit

This audit tries to refute the 18 defects with the verdict `cube-bug` or `cube-clickhouse-bug` in [cube-findings.md](cube-findings.md). The default position for each defect is that it is not a bug. The cases and the data here are new. They do not reuse the cases of the findings.

## How this was checked

- **Versions:** Cube `v1.8.15`, ClickHouse `26.8.36-alpine`, Postgres `17-alpine`. Some cases also ran on ClickHouse `25.3-alpine` and on older Cube tags.
- **Configs:** each case ran on ClickHouse and Postgres, with `CUBEJS_TESSERACT_SQL_PLANNER` on and off, unless the table says otherwise.
- **Query shape:** the shape comes from `EXPLAIN` of each SQL API case. `regular` is a plan with only a `CubeScan`. `post-processing` is a plan with other nodes over a `CubeScan`. `pushdown` is a plan with `CubeScanWrappedSql`. `not-planned` means that `EXPLAIN` failed too. `no EXPLAIN` is a case with parameters and no literal form to explain. For a REST case, the table says if the response has `usedPreAggregations`.
- **Expected values:** 302 cases, written before the runs from a small table that you can read in `dev/audit/cases/`. Each amount is a power of 10 where this helps, so that a sum names its rows. Where direct SQL makes sense, the table also shows its result. ClickHouse output is not the truth when its semantics differ: for example, `sum` of no rows is 0 on ClickHouse and NULL in the SQL standard.
- **Corrected cases:** the runs found errors in some of the first expected values, and in some model definitions. I corrected these before the final run, and the case text gives the reason. In the tables, `diff` means that the run gives another value than the derived one.
- **Master:** on 2026-10-30, `cube-js/cube` master is [`78ecad294`](https://github.com/cube-js/cube/commit/78ecad294), 20 commits after `v1.8.15`. I did not build or run master. I read it. Of the 13 source files that the defects name, 12 are identical in `v1.8.15` and master. The change in the 13th, `BaseQuery.ts`, does not touch a path here. So a result on `v1.8.15` applies to master for these paths. Code links go to `78ecad294`.
- **Issues and pull requests:** the links are to `cube-js/cube`. A pull request that only adds a failing test says that the Cube team knows about the defect. It does not fix it.
- **Stack:** compose project `mcd-audit` on `nettop.local`, ports 4600 and 4601 on the loopback interface, `mem_limit: 1g` per service. One set ran at a time. I removed the project, its volumes and the images that it pulled after the runs.

## Limits

- I did not build or run master, or the open pull requests. A statement about master comes from the code.
- Postgres is the only other database. A note about other data sources comes from the code.
- A rollup hit is visible for REST only. For the SQL API, a case on a cube with a rollup does not prove a hit.
- `v1.8.7` has no image on Docker Hub, so a start at `v1.8.7` and at `v1.8.8` look the same.
- "I found no pull request" means no match in my searches. It is not proof.

## Run

```sh
cd dev/audit
python3 audit.py B1                        # all configs of one defect, writes results/
python3 audit.py all                       # all defects
python3 audit.py B1 --config ch-on --case plain_gt_lte
python3 audit.py N1 --only-tags            # the older Cube tags of this defect
python3 audit.py --down                    # removes the stack, its volumes and the pulled images
python3 render.py                          # writes this file from cases/, results/ and answers/
```

Run `audit.py` on a host with Docker. `render.py` needs only Python. The tables below come from `dev/audit/results/`.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `confirmed` | It survived all five questions. |
| `confirmed-but-narrow` | The defect is real, but its scope is smaller than the findings say, or it is known and documented, or it needs a rare setting. |
| `intended` | A Cube doc, test or maintainer comment says that this is the behaviour. |
| `misuse` | The query or the model is outside the documented use. |
| `already-fixed` | Master or a merged change fixes it. |
| `not-reproduced` | My cases do not show it. |

## Result

| Verdict | Defects |
| --- | --- |
| `confirmed` | 12 |
| `confirmed-but-narrow` | 4 |
| `intended` | 1 |
| `misuse` | 1 |
| `already-fixed` | 0 |
| `not-reproduced` | 0 |

### Defects by real user impact

| Rank | Defect | Verdict | Result for a user | Impact |
| --- | --- | --- | --- | --- |
| 1 | [B1](#b1) | `confirmed` | Wrong number with no error | On the main target, `d > X AND d <= Y` on a Date column adds the whole day X. On a DateTime column, it adds the rows in the second of X. |
| 2 | [B2](#b2) | `confirmed` | Wrong number with no error | Each database and planner. `m <= '2025-05-01'` on a monthly subquery keeps May with only the rows of the first day, 100 in place of 111100. |
| 3 | [gran](#gran) | `confirmed` | Wrong number with no error | A fiscal quarter with `offset` on a rollup by month gives wrong quarters for each row before the origin. On Postgres with no rollup, month intervals drift, and an old origin moves rows by more than one bin. |
| 4 | [N1](#n1) | `confirmed` | Wrong number with no error | With the default planner on Postgres, a rolling measure by month with a range that starts inside a month gets shifted buckets. The labels and the values are both wrong. A relative range such as the last 90 days does this. |
| 5 | [B4](#b4) | `confirmed` | Wrong number with no error | A filter on a measure column over a subquery with no GROUP BY keeps all rows or removes all rows. Each database and planner. A nested question or a model with a measure column can write this form. |
| 6 | [datetime64](#datetime64) | `confirmed` | Wrong number with no error | On event tables, a filter inside one second misses rows, and rows in one second show the same time. The order of rows in one second is lost. |
| 7 | [propagate](#propagate) | `confirmed` | Wrong number with no error | On Tesseract, the default, "all orders" next to "orders that match the filter" shows the same number twice. The first column decides which. |
| 8 | [unbounded](#unbounded) | `confirmed` | An error | A running total by month or by day fails on the main target, with more than one bucket. Tesseract with one bucket works. A far bound such as `100 year` works on ClickHouse 26.8 with Tesseract. |
| 9 | [M1](#m1) | `confirmed` | An error | A one-sided date filter with a bound parameter fails on a wrapped query. A client cannot avoid it by stating the type, only with a cast in the SQL text or with a literal. |
| 10 | [m3](#m3) | `confirmed` | Wrong value with no error, NULL in place of 0 | A dashboard filter that matches nothing shows an empty value in place of 0, on a rollup in Cube Store or in Postgres. |
| 11 | [big](#big) | `confirmed-but-narrow` | Wrong number with no error, in the low digits | Only values above 2^53, or decimals with more than about 15 significant digits. The SQL API has this on each database by design of its type. REST has it on ClickHouse: integers on 26.8 but not on 25.3, and decimals on both. |
| 12 | [geo](#geo) | `confirmed` | An error | Each query that selects the member fails, on each database and planner. `/v1/meta` offers the member, so an agent can pick it. Two number members in the view avoid it. |
| 13 | [interval](#interval) | `confirmed` | An error | `ts + INTERVAL` with an `ORDER BY`, a measure or a filter on the expression fails on the main target. With no `ORDER BY` it works. The `ORDER BY` form worked before `v1.7.27`. |
| 14 | [N2](#n2) | `confirmed-but-narrow` | Wrong number with no error | Only with the legacy planner: a Cube before `v1.7.0` by default, or a later Cube with the planner set to false. A default install of a current tag is correct. |
| 15 | [m5](#m5) | `confirmed-but-narrow` | An error | A model with `original_sql` on ClickHouse fails at query time. The docs warn about it, and a column alias in the cube `sql` avoids it. |
| 16 | [M2](#m2) | `confirmed-but-narrow` | An error | This is M1 in one more query form. One fix closes both. It adds no separate work. |
| 17 | [m4](#m4) | `misuse` | Cosmetic, an error with a poor message | The query has no defined meaning. It fails. Only the text of the error is poor. |
| 18 | [extract](#extract) | `intended` | An error, with a documented form that works | The docs describe this error and give the nested form as the alternative. Only a non-additive measure by a part such as day of week has no other SQL API form. |

### Verdicts that differ from docs/cube-findings.md

`docs/cube-findings.md` has `cube-bug` or `cube-clickhouse-bug` for all 18. Each verdict other than `confirmed` differs in strength. The table gives the reason.

| Defect | Findings | Audit | Reason |
| --- | --- | --- | --- |
| [B1](#b1) | `cube-clickhouse-bug` | `confirmed` | The verdict stays. The scope is wider than the findings: a Date column adds a full day, and equal bounds return rows. REST is correct, and a rollup in Cube Store is correct. |
| [B2](#b2) | `cube-bug` | `confirmed` | The verdict stays. A bound on the first of a month also fails with `<=` and `>`, so a mid-month bound is not necessary. |
| [B4](#b4) | `cube-bug` | `confirmed` | The verdict stays. The breakout in the findings, one group with the total, did not reproduce in my data on `v1.8.15`: each row keeps its own value. The filter is the defect. |
| [M1](#m1) | `cube-bug` | `confirmed` | The verdict stays. Stating the parameter type does not help: each OID fails. A filter with two bounds, `=`, `IN` and a `DATE_TRUNC` filter work. The start differs by query form, `v1.7.27` and `v1.8.7` or `v1.8.8`. |
| [M2](#m2) | `cube-bug` | `confirmed-but-narrow` | It is not a separate defect. The cause, the code path and the workaround are those of M1. |
| [m3](#m3) | `cube-bug` | `confirmed` | The verdict stays. A rollup stored in ClickHouse gives 0, because ClickHouse `sum` of no rows is 0. `COUNT(*)` through pushdown reads the source and gives 0. |
| [m4](#m4) | `cube-bug` | `misuse` | The query is outside the documented rules, and a failure is an acceptable result for it. The documented error is also not what a plain sum gets, so the expected error in the findings does not hold. |
| [m5](#m5) | `cube-clickhouse-bug` | `confirmed-but-narrow` | The cause is in the core and not in the ClickHouse dialect: an index also fails on Postgres and in Cube Store. The docs state this limit and link an open issue. ClickHouse has less room only because it needs an index. |
| [unbounded](#unbounded) | `cube-clickhouse-bug` | `confirmed` | The verdict stays. On ClickHouse 25.3, a far bound fails too, so the workaround depends on the ClickHouse version. A rollup in Cube Store gives the correct result. |
| [geo](#geo) | `cube-bug` | `confirmed` | The verdict stays. It also fails on `v1.0.0`, so it is old. |
| [gran](#gran) | `cube-bug` | `confirmed` | The verdict stays, with a second cause. On Postgres, a query with no rollup is also wrong. The workaround in the findings, an origin before all rows, gives wrong bins on Postgres with no rollup. |
| [big](#big) | `cube-bug` | `confirmed-but-narrow` | The reach is small, and the SQL API part is a design choice of one column type. The REST part for integers depends on the ClickHouse version, which the findings do not say. |
| [propagate](#propagate) | `cube-bug` | `confirmed` | The verdict stays. |
| [datetime64](#datetime64) | `cube-clickhouse-bug` | `confirmed` | The verdict stays. A measure of type `time` keeps milliseconds, so the loss is in the dimension path only. |
| [interval](#interval) | `cube-clickhouse-bug` | `confirmed` | The verdict stays, with a new fact: it is in part a regression. Raw rows with no `ORDER BY` work, so the findings overstate that part. The nested form fails too, unlike `extract`. |
| [extract](#extract) | `cube-clickhouse-bug` | `intended` | The docs describe this exact failure, for a data source that cannot push an expression down. The nested form that they recommend works on ClickHouse. It is a missing feature of the dialect and it gives no wrong number. |
| [N1](#n1) | `cube-bug` | `confirmed` | The verdict stays. The values are wrong as well as the labels, a week query loses a bucket, and a start on a month end drifts. It is not new with `v1.7.0`: with Tesseract on, `v1.3.0` has it too. |
| [N2](#n2) | `cube-clickhouse-bug` | `confirmed-but-narrow` | The number is wrong, but a default install of a current Cube does not reach it. The legacy planner gives a warning when a user selects it. |

## Defects

### B1

**A strict lower time bound in the SQL API includes the bound on ClickHouse**

- **Verdict:** `confirmed`
- **Result for a user:** Wrong number with no error
- **Impact:** On the main target, `d > X AND d <= Y` on a Date column adds the whole day X. On a DateTime column, it adds the rows in the second of X.
- **Findings verdict:** `cube-clickhouse-bug`
- **Run:** `python3 audit.py B1`. 25 cases, 100 runs: 70 match the expected value, 30 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| plain_gt_lte | SQL API: `SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE ts > '2024-02-29 00:00:00' AND ts <= '2024-03-01 00:00:00'` | `3, 11100`. Rows 3, 4, 5 are after X and not after Y: 100+1000+10000. Direct SQL: `3, 11100`. | `4, 11110` | regular | off, on | ClickHouse | diff |
| | | | `3, 11100` | regular | off, on | Postgres | match |
| gt_only | SQL API: `SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE ts > '2024-02-29 00:00:00'` | `4, 111100`. Rows 3 to 6. Direct SQL: `4, 111100`. | `4, 111100` | regular | off, on | ClickHouse, Postgres | match |
| gt_and_lt | SQL API: `SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE ts > '2024-02-29 00:00:00' AND ts < '2024-03-01 00:00:00'` | `2, 1100`. Rows 3, 4. Row 2 is on the lower bound, row 5 on the upper. Direct SQL: `2, 1100`. | `3, 1110` | regular | off, on | ClickHouse | diff |
| | | | `2, 1100` | regular | off, on | Postgres | match |
| one_second_below | SQL API: `SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE ts > '2024-02-28 23:59:59' AND ts <= '2024-02-29 00:00:00'` | `1, 10`. Row 2 only. Row 1 is on the strict bound. Direct SQL: `1, 10`. | `2, 11` | regular | off, on | ClickHouse | diff |
| | | | `1, 10` | regular | off, on | Postgres | match |
| equal_bounds_empty | SQL API: `SELECT MEASURE(n) n FROM visits WHERE ts > '2024-02-29 12:00:00' AND ts <= '2024-02-29 12:00:00'` | `0`. No time is both after and not after one value. | `1` | regular | off, on | ClickHouse | diff |
| | | | `0` | regular | off, on | Postgres | match |
| fraction_in_bound | SQL API: `SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE ts > '2024-02-29 00:00:00.500' AND ts <= '2024-03-01 00:00:00'` | `3, 11100`. Row 2 at 00:00:00 is before .500. Rows 3, 4, 5 qualify. | `4, 11110` | regular | off, on | ClickHouse | diff |
| | | | `3, 11100` | regular | off, on | Postgres | match |
| date_col_daily | SQL API: `SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE d > '2024-02-28' AND d <= '2024-02-29'` | `3, 1110`. Date column. Only the leap day is after 02-28 and not after 02-29: rows 2, 3, 4. Direct SQL: `3, 1110`. | `4, 1111` | regular | off, on | ClickHouse | diff |
| | | | `3, 1110` | regular | off, on | Postgres | match |
| date_col_month_end | SQL API: `SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE d > '2024-02-29' AND d <= '2024-03-01'` | `2, 110000`. March 1 only: rows 5, 6. Direct SQL: `2, 110000`. | `5, 111110` | regular | off, on | ClickHouse | diff |
| | | | `2, 110000` | regular | off, on | Postgres | match |
| datetime64_col | SQL API: `SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE ts64 > '2024-02-29 00:00:00.000' AND ts64 <= '2024-03-01 00:00:00.000'` | `3, 11100`. DateTime64(3): rows 3, 4, 5. Row 2 is on the bound, row 6 is 1 ms after the upper bound. Direct SQL: `3, 11100`. | `4, 11110` | regular | off, on | ClickHouse | diff |
| | | | `3, 11100` | regular | off, on | Postgres | match |
| nullable_col | SQL API: `SELECT MEASURE(n) n, MEASURE(total) t FROM visits WHERE ts_null > '2024-02-29 00:00:00' AND ts_null <= '2024-03-01 00:00:00'` | `2, 11000`. Row 3 is NULL, so only rows 4, 5. Direct SQL: `2, 11000`. | `3, 11010` | regular | off, on | ClickHouse | diff |
| | | | `2, 11000` | regular | off, on | Postgres | match |
| not_lte_form | SQL API: `SELECT MEASURE(n) n FROM visits WHERE NOT (ts <= '2024-02-29 00:00:00') AND ts <= '2024-03-01 00:00:00'` | `3`. NOT (ts <= X) is ts > X. | `3` | pushdown | off, on | ClickHouse, Postgres | match |
| between_control | SQL API: `SELECT MEASURE(n) n FROM visits WHERE ts BETWEEN '2024-02-29 00:00:00' AND '2024-03-01 00:00:00'` | `4`. BETWEEN is inclusive on both sides: rows 2 to 5. | `4` | regular | off, on | ClickHouse, Postgres | match |
| gte_control | SQL API: `SELECT MEASURE(n) n FROM visits WHERE ts >= '2024-02-29 00:00:01' AND ts <= '2024-03-01 00:00:00'` | `3`. The next second, with >=. The same rows as plain_gt_lte. | `3` | regular | off, on | ClickHouse, Postgres | match |
| typed_literal | SQL API: `SELECT MEASURE(n) n FROM visits WHERE ts > TIMESTAMP '2024-02-29 00:00:00' AND ts <= TIMESTAMP '2024-03-01 00:00:00'` | `3`. The same as plain_gt_lte, with typed literals. | `4` | regular | off, on | ClickHouse | diff |
| | | | `3` | regular | off, on | Postgres | match |
| by_day_bucket | SQL API: `SELECT DATE_TRUNC('day', ts) b, MEASURE(total) t FROM visits WHERE ts > '2024-02-29 00:00:00' AND ts <= '2024-03-01 00:00:00' GROUP BY 1 ORDER BY 1` | `2024-02-29 00:00:00, 1100; 2024-03-01 00:00:00, 10000`. Rows 3, 4 fall on the leap day and row 5 on March 1. | `2024-02-29 00:00:00, 1110; 2024-03-01 00:00:00, 10000` | regular | off, on | ClickHouse | diff |
| | | | `2024-02-29 00:00:00, 1100; 2024-03-01 00:00:00, 10000` | regular | off, on | Postgres | match |
| raw_rows | SQL API: `SELECT id FROM visits WHERE ts > '2024-02-29 00:00:00' AND ts <= '2024-03-01 00:00:00' ORDER BY 1` | `3; 4; 5`. Ungrouped rows. | `2; 3; 4; 5` | regular | off, on | ClickHouse | diff |
| | | | `3; 4; 5` | regular | off, on | Postgres | match |
| outer_filter_postproc | SQL API: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM visits) q WHERE ts > '2024-02-29 00:00:00' AND ts <= '2024-03-01 00:00:00'` | `11100`. The same rows, with an aggregate over a subquery. | `11110` | pushdown | off, on | ClickHouse | diff |
| | | | `11100` | pushdown | off, on | Postgres | match |
| bind_cast | SQL API, Parse and Bind, no OID: `SELECT MEASURE(n) n FROM visits WHERE ts > $1::timestamp AND ts <= $2::timestamp`, params `["2024-02-29 00:00:00", "2024-03-01 00:00:00"]` | `3`. The same as plain_gt_lte, with parameters. | `4` | no EXPLAIN | off, on | ClickHouse | diff |
| | | | `3` | no EXPLAIN | off, on | Postgres | match |
| sql_time_zone | SQL API: `SET TIME ZONE 'America/New_York'; SELECT MEASURE(n) n FROM visits WHERE ts > '2024-02-28 19:00:00' AND ts <= '2024-02-29 19:00:00'` | `3`. New York is UTC-5 in February, so the bounds are X and Y. | `4` | regular | off, on | ClickHouse | diff |
| | | | `3` | regular | off, on | Postgres | match |
| rest_after_before | REST: `{"measures": ["visits.n", "visits.total"], "filters": [{"member": "visits.ts", "operator": "afterDate", "values": ["2024-02-29 00:00:00"]}, {"member": "visits.ts", "operator": "beforeOrOnDate", "values": ["2024-03-01 00:00:00"]}]}` | `3, 11100`. The same condition as plain_gt_lte, in REST. The docs define afterDate with >. | `3, 11100` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| rest_after_only | REST: `{"measures": ["visits.n"], "filters": [{"member": "visits.ts", "operator": "afterDate", "values": ["2024-02-29 00:00:00"]}]}` | `4`. Rows 3 to 6. | `4` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| rest_date_range | REST: `{"measures": ["visits.n"], "timeDimensions": [{"dimension": "visits.ts", "dateRange": ["2024-02-29 00:00:00", "2024-03-01 00:00:00"]}]}` | `4`. A date range is inclusive: rows 2 to 5. | `4` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| rest_tz | REST: `{"measures": ["visits.n"], "timezone": "America/New_York", "filters": [{"member": "visits.ts", "operator": "afterDate", "values": ["2024-02-28T19:00:00"]}, {"member": "visits.ts", "operator": "beforeOrOnDate", "values": ["2024-02-29T19:00:00"]}]}` | `3`. New York is UTC-5 in February, so the bounds are X and Y. | `3` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| rollup_rest | REST: `{"measures": ["visits_r.n"], "timeDimensions": [{"dimension": "visits_r.ts", "dateRange": ["2024-02-29T00:00:00.001", "2024-03-01T00:00:00.000"]}]}` | `3`. A rollup with one row per second, a range from X plus 1 ms: rows 3, 4, 5. | `3` | REST, rollup hit | off, on | ClickHouse, Postgres | match |
| rollup_sql | SQL API: `SELECT MEASURE(n) n FROM visits_r WHERE ts > '2024-02-29 00:00:00' AND ts <= '2024-03-01 00:00:00'` | `3`. The same range through the SQL API, on the cube with a rollup. | `3` | regular | off, on | ClickHouse, Postgres | match |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.0.0 | ch-off | plain_gt_lte | `4, 11110` | diff |
| v1.0.0 | ch-off | date_col_daily | `4, 1111` | diff |
| v1.0.0 | ch-off | rest_after_before | `3, 11100` | match |
| v1.6.76 | ch-off | plain_gt_lte | `4, 11110` | diff |
| v1.6.76 | ch-off | date_col_daily | `4, 1111` | diff |
| v1.6.76 | ch-off | rest_after_before | `3, 11100` | match |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. [#11585](https://github.com/cube-js/cube/issues/11585): a maintainer says that `afterDate` is strict on purpose, so the expected value is right. [#10326](https://github.com/cube-js/cube/issues/10326) is open with the same cause. [PR #12084](https://github.com/cube-js/cube/pull/12084) adds a failing test for it, and calls it a bug. It is a draft with tests only.
2. **Is the expected value right?** Yes. `plain_gt_lte` is the rows after X and not after Y. Direct SQL on both databases gives 3 and 11100. Postgres through Cube gives the same. ClickHouse through Cube gives 4 and 11110.
3. **Is it misuse?** No. The docs say that [time filters become date ranges](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L386). `>` and `<=` are the plain SQL form. The next second with `>=` (`gte_control`) avoids it, but this only works on whole seconds, and it does not work for a `fraction_in_bound` value.
4. **Master?** It still happens. The SQL API sends X plus 1 ms, as `EXPLAIN` shows, then [`dateTimeCast`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L174) uses `parseDateTimeBestEffort`, which drops the milliseconds. The file is identical in `v1.8.15` and master. No open pull request changes this line. It also happens on `v1.6.76` and `v1.0.0`, so it is not a regression.
5. **Does a real user reach it?** Yes. "Sales after 28 February up to 29 February" on a daily table gives one extra day. The result is a wrong number with no error. `date_col_month_end` gives 111110 in place of 110000. REST `afterDate` gives the correct value, because it does not add 1 ms, so only SQL API clients have this.

### B2

**An outer filter on a DATE_TRUNC alias changes the rows inside the bucket**

- **Verdict:** `confirmed`
- **Result for a user:** Wrong number with no error
- **Impact:** Each database and planner. `m <= '2025-05-01'` on a monthly subquery keeps May with only the rows of the first day, 100 in place of 111100.
- **Findings verdict:** `cube-bug`
- **Run:** `python3 audit.py B2`. 25 cases, 100 runs: 32 match the expected value, 68 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ge_mid_month | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m >= '2025-05-15' ORDER BY 1` | `2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. The bucket label of May is 05-01, before 05-15, so May leaves. The June and July buckets stay whole. Direct SQL: `2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. | `2025-05-01 00:00:00, 110000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | regular | off, on | ClickHouse, Postgres | diff |
| ge_first_of_month | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m >= '2025-05-01' ORDER BY 1` | `2025-05-01 00:00:00, 111100; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. A bound on the bucket start. May stays with all of rows 3 to 6. Direct SQL: `2025-05-01 00:00:00, 111100; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. | `2025-05-01 00:00:00, 111100; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | regular | off, on | ClickHouse, Postgres | match |
| ge_one_second_after | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m >= '2025-05-01 00:00:01' ORDER BY 1` | `2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. One second after the May label, so May leaves. | `2025-05-01 00:00:00, 111000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | regular | off, on | ClickHouse, Postgres | diff |
| ge_one_second_before | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m >= '2025-04-30 23:59:59' ORDER BY 1` | `2025-05-01 00:00:00, 111100; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. The April label 04-01 is before the bound. The value 10 of April must not appear. | `2025-04-01 00:00:00, 10; 2025-05-01 00:00:00, 111100; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | regular | off, on | ClickHouse, Postgres | diff |
| gt_first_of_month | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m > '2025-05-01' ORDER BY 1` | `2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. Strict: the May label equals the bound, so May leaves. Direct SQL: `2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. | `2025-05-01 00:00:00, 111000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | regular | off, on | ClickHouse, Postgres | diff |
| lt_mid_month | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m < '2025-05-15' ORDER BY 1` | `2025-03-01 00:00:00, 1; 2025-04-01 00:00:00, 10; 2025-05-01 00:00:00, 111100`. The May label is before 05-15, so May stays, whole: rows 3 to 6. Direct SQL: `2025-03-01 00:00:00, 1; 2025-04-01 00:00:00, 10; 2025-05-01 00:00:00, 111100`. | `2025-03-01 00:00:00, 1; 2025-04-01 00:00:00, 10; 2025-05-01 00:00:00, 1100` | regular | off, on | ClickHouse, Postgres | diff |
| le_first_of_month | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m <= '2025-05-01' ORDER BY 1` | `2025-03-01 00:00:00, 1; 2025-04-01 00:00:00, 10; 2025-05-01 00:00:00, 111100`. The May label equals the bound. May stays, whole. Direct SQL: `2025-03-01 00:00:00, 1; 2025-04-01 00:00:00, 10; 2025-05-01 00:00:00, 111100`. | `2025-03-01 00:00:00, 1; 2025-04-01 00:00:00, 10; 2025-05-01 00:00:00, 100` | regular | off, on | ClickHouse, Postgres | diff |
| lt_first_of_month | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m < '2025-05-01' ORDER BY 1` | `2025-03-01 00:00:00, 1; 2025-04-01 00:00:00, 10`. Before the May label: March and April. | `2025-03-01 00:00:00, 1; 2025-04-01 00:00:00, 10` | regular | off, on | ClickHouse, Postgres | match |
| eq_mid_month | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m = '2025-05-15' ORDER BY 1` | `no rows`. No bucket label is 05-15. | `no rows` | pushdown | off, on | ClickHouse, Postgres | match |
| eq_first_of_month | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m = '2025-05-01' ORDER BY 1` | `2025-05-01 00:00:00, 111100`. The May bucket, whole. | `2025-05-01 00:00:00, 111100` | regular | off, on | ClickHouse, Postgres | match |
| between_mid_months | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m BETWEEN '2025-04-15' AND '2025-06-15' ORDER BY 1` | `2025-05-01 00:00:00, 111100; 2025-06-01 00:00:00, 1000000`. The labels 05-01 and 06-01 are inside. Each bucket stays whole. Direct SQL: `2025-05-01 00:00:00, 111100; 2025-06-01 00:00:00, 1000000`. | `2025-04-01 00:00:00, 10; 2025-05-01 00:00:00, 111100; 2025-06-01 00:00:00, 1000000` | regular | off, on | ClickHouse, Postgres | diff |
| empty_far_future | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m >= '2030-01-01' ORDER BY 1` | `no rows`. No rows. | `no rows` | regular | off, on | ClickHouse, Postgres | match |
| outer_sum | SQL API: `SELECT SUM(rev) x FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m >= '2025-05-15'` | `11000000`. June plus July. | `11110000` | post-processing | off, on | ClickHouse, Postgres | diff |
| typed_literal | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m >= TIMESTAMP '2025-05-15 00:00:00' ORDER BY 1` | `2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. The same as ge_mid_month. | `2025-05-01 00:00:00, 110000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | regular | off, on | ClickHouse, Postgres | diff |
| cte_form | SQL API: `WITH s AS (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) SELECT m, rev FROM s WHERE m >= '2025-05-15' ORDER BY 1` | `2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. A CTE has the meaning of the subquery. | `2025-05-01 00:00:00, 110000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | regular | off, on | ClickHouse, Postgres | diff |
| alias_as_column | SQL API: `SELECT ts, rev FROM (SELECT DATE_TRUNC('month', ts) AS ts, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE ts >= '2025-05-15' ORDER BY 1` | `2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. The alias has the name of the column. The outer ts is the bucket. | `2025-05-01 00:00:00, 110000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | regular | off, on | ClickHouse, Postgres | diff |
| year_bucket | SQL API: `SELECT y, rev FROM (SELECT DATE_TRUNC('year', ts) AS y, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE y >= '2025-03-01' ORDER BY 1` | `no rows`. The only label is 2025-01-01, before 03-01. | `2025-01-01 00:00:00, 11111111` | regular | off, on | ClickHouse, Postgres | diff |
| week_bucket | SQL API: `SELECT w, rev FROM (SELECT DATE_TRUNC('week', ts) AS w, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE w >= '2025-05-13' ORDER BY 1` | `2025-05-26 00:00:00, 1100000; 2025-07-14 00:00:00, 10000000`. Weeks start on Monday. 05-14 and 05-15 are in the week of 05-12, before the bound. 05-31 and 06-01 are in the week of 05-26. | `2025-05-12 00:00:00, 11000; 2025-05-26 00:00:00, 1100000; 2025-07-14 00:00:00, 10000000` | regular | off, on | ClickHouse, Postgres | diff |
| date_column | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', d) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m >= '2025-05-15' ORDER BY 1` | `2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. The same as ge_mid_month, on a Date column. | `2025-05-01 00:00:00, 110000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | regular | off, on | ClickHouse, Postgres | diff |
| bind_cast | SQL API, Parse and Bind, no OID: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m >= $1::timestamp ORDER BY 1`, params `["2025-05-15 00:00:00"]` | `2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. The same as ge_mid_month, with a parameter. | `2025-05-01 00:00:00, 110000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | no EXPLAIN | off, on | ClickHouse, Postgres | diff |
| sql_time_zone | SQL API: `SET TIME ZONE 'America/New_York'; SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales GROUP BY 1) s WHERE m >= '2025-05-15' ORDER BY 1` | `2025-07-01 00:00:00, 10000000`. In New York, UTC-4 in May: row 7 at 06-01 00:00 UTC falls in May, so no June bucket. The May label is before the bound. Only July is left. | `2025-05-01 00:00:00, 1110000; 2025-07-01 00:00:00, 10000000` | regular | off, on | ClickHouse, Postgres | diff |
| rollup_miss_or_hit | SQL API: `SELECT m, rev FROM (SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales_r GROUP BY 1) s WHERE m >= '2025-05-15' ORDER BY 1` | `2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. The same as ge_mid_month, on a cube with a rollup by month. | `2025-05-01 00:00:00, 110000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | regular | off, on | ClickHouse, Postgres | diff |
| direct_where_control | SQL API: `SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales WHERE DATE_TRUNC('month', ts) >= '2025-05-15' GROUP BY 1 ORDER BY 1` | `2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. The same condition with no subquery. | `2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | regular | off, on | ClickHouse, Postgres | match |
| row_filter_control | SQL API: `SELECT DATE_TRUNC('month', ts) AS m, MEASURE(rev) AS rev FROM sales WHERE ts >= '2025-05-15' GROUP BY 1 ORDER BY 1` | `2025-05-01 00:00:00, 110000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. A filter on rows, not on buckets, so May keeps rows 5 and 6. This is the docs form of a partial bucket. | `2025-05-01 00:00:00, 110000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | regular | off, on | ClickHouse, Postgres | match |
| rest_date_range_control | REST: `{"measures": ["sales.rev"], "timeDimensions": [{"dimension": "sales.ts", "granularity": "month", "dateRange": ["2025-05-15", "2025-07-31"]}]}` | `2025-05-01 00:00:00, 110000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000`. REST has a date range on rows, the same as row_filter_control. | `2025-05-01 00:00:00, 110000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | REST, no rollup | off, on | ClickHouse, Postgres | match |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v0.35.58 | pg-off | ge_mid_month | `2025-05-01 00:00:00, 110000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | diff |
| v0.35.58 | pg-off | lt_mid_month | `2025-03-01 00:00:00, 1; 2025-04-01 00:00:00, 10; 2025-05-01 00:00:00, 1100` | diff |
| v1.0.0 | pg-off | ge_mid_month | `2025-05-01 00:00:00, 110000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | diff |
| v1.0.0 | pg-off | lt_mid_month | `2025-03-01 00:00:00, 1; 2025-04-01 00:00:00, 10; 2025-05-01 00:00:00, 1100` | diff |
| v1.6.76 | pg-off | ge_mid_month | `2025-05-01 00:00:00, 110000; 2025-06-01 00:00:00, 1000000; 2025-07-01 00:00:00, 10000000` | diff |
| v1.6.76 | pg-off | lt_mid_month | `2025-03-01 00:00:00, 1; 2025-04-01 00:00:00, 10; 2025-05-01 00:00:00, 1100` | diff |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. The test [`test_thoughtspot_filter_date_trunc_column_with_literal`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubesql/cubesql/src/compile/mod.rs#L13230) pins the correct meaning of the direct form: `>= 2020-03-25` on a month becomes `afterOrOnDate 2020-04-01`. [`test_filter_in_outer_query_over_date_trunced_column`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubesql/cubesql/src/compile/mod.rs#L17044) pins the outer form, only for `=`. The code has the comment [`TODO: all other operators need special granularity handlers like Eq`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs#L3812). So the Cube team knows that the work is not complete. The partial bucket is the documented result only for a filter on rows (`row_filter_control`, `rest_date_range_control`).
2. **Is the expected value right?** Yes. SQL evaluates the subquery first, so the outer filter sees bucket labels. Direct SQL on Postgres and on ClickHouse gives the expected rows in `ge_mid_month`, `lt_mid_month`, `le_first_of_month`.
3. **Is it misuse?** No. The docs recommend [nested queries](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L172) and a [CTE](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L196). `cte_form` fails in the same way. `direct_where_control` gives the correct result.
4. **Master?** It still happens. [`filters.rs`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs#L242) is identical in `v1.8.15` and master. [#8344](https://github.com/cube-js/cube/issues/8344) closed the `=` form in `v0.35.58`, and the other operators fail on that tag too. I found no open pull request for it.
5. **Does a real user reach it?** Yes. "Monthly revenue up to May" over a saved monthly question gives a May value that looks correct and is too small. A rollup does not change this. The result is a wrong number with no error.

### B4

**An outer filter on a measure column of an ungrouped subquery compares the total**

- **Verdict:** `confirmed`
- **Result for a user:** Wrong number with no error
- **Impact:** A filter on a measure column over a subquery with no GROUP BY keeps all rows or removes all rows. Each database and planner. A nested question or a model with a measure column can write this form.
- **Findings verdict:** `cube-bug`
- **Run:** `python3 audit.py B4`. 20 cases, 80 runs: 40 match the expected value, 40 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| row_values_control | SQL API: `SELECT id, rev FROM items ORDER BY 1` | `1, 100; 2, 200; 3, 300; 4, 0; 5, -50; 6, NULL`. A measure column with no GROUP BY. Each row has its own amount, so a per-row reading of the column is what Cube itself returns. | `1, 100; 2, 200; 3, 300; 4, 0; 5, -50; 6, NULL` | regular | off, on | ClickHouse, Postgres | match |
| gt_between | SQL API: `SELECT SUM(amt_dim) s, COUNT(*) c FROM (SELECT id, amt_dim, rev FROM items) q WHERE rev > 150` | `500, 2`. Rows 2 and 3 are above 150. Direct SQL: `500, 2`. | `550, 6` | pushdown | off, on | ClickHouse, Postgres | diff |
| gt_exact_bound | SQL API: `SELECT SUM(amt_dim) s, COUNT(*) c FROM (SELECT id, amt_dim, rev FROM items) q WHERE rev > 200` | `300, 1`. Row 2 is on the strict bound. Only row 3. | `550, 6` | pushdown | off, on | ClickHouse, Postgres | diff |
| ge_exact_bound | SQL API: `SELECT SUM(amt_dim) s, COUNT(*) c FROM (SELECT id, amt_dim, rev FROM items) q WHERE rev >= 200` | `500, 2`. Rows 2 and 3. | `550, 6` | pushdown | off, on | ClickHouse, Postgres | diff |
| above_each_row_below_total | SQL API: `SELECT SUM(amt_dim) s, COUNT(*) c FROM (SELECT id, amt_dim, rev FROM items) q WHERE rev > 400` | `NULL, 0`. No row is above 400. The total of all rows, 550, is. SUM of no rows is NULL, COUNT is 0. Direct SQL: `\N, 0`. | `550, 6` | pushdown | off, on | ClickHouse, Postgres | diff |
| above_total | SQL API: `SELECT SUM(amt_dim) s, COUNT(*) c FROM (SELECT id, amt_dim, rev FROM items) q WHERE rev > 1000` | `NULL, 0`. Nothing is above 1000. An aggregate with no GROUP BY gives one row. | `no rows` | pushdown | off, on | ClickHouse, Postgres | diff |
| zero_value | SQL API: `SELECT SUM(amt_dim) s, COUNT(*) c FROM (SELECT id, amt_dim, rev FROM items) q WHERE rev = 0` | `0, 1`. Row 4 only. | `no rows` | pushdown | off, on | ClickHouse, Postgres | diff |
| negative_value | SQL API: `SELECT SUM(amt_dim) s, COUNT(*) c FROM (SELECT id, amt_dim, rev FROM items) q WHERE rev < 0` | `-50, 1`. Row 5 only. | `no rows` | pushdown | off, on | ClickHouse, Postgres | diff |
| null_value | SQL API: `SELECT COUNT(*) c FROM (SELECT id, amt_dim, rev FROM items) q WHERE rev IS NULL` | `1`. Row 6 only. | `no rows` | pushdown | off, on | ClickHouse, Postgres | diff |
| breakout | SQL API: `SELECT rev, COUNT(*) c FROM (SELECT id, amt_dim, rev FROM items) q GROUP BY 1 ORDER BY 1 NULLS LAST` | `-50, 1; 0, 1; 100, 1; 200, 1; 300, 1; NULL, 1`. Six rows with six values. | `-50, 1; 0, 1; 100, 1; 200, 1; 300, 1; NULL, 1` | pushdown | off, on | ClickHouse, Postgres | match |
| breakout_plain_order | SQL API: `SELECT rev, COUNT(*) c FROM (SELECT id, amt_dim, rev FROM items) q WHERE rev IS NOT NULL GROUP BY 1 ORDER BY 1` | `-50, 1; 0, 1; 100, 1; 200, 1; 300, 1`. The same breakout with a filter on the column and a plain ORDER BY. Rows with an amount. | `-50, 1; 0, 1; 100, 1; 200, 1; 300, 1` | pushdown | off, on | ClickHouse, Postgres | match |
| breakout_no_filter_plain | SQL API: `SELECT rev, COUNT(*) c FROM (SELECT id, amt_dim, rev FROM items) q GROUP BY 1 ORDER BY 1` | `-50, 1; 0, 1; 100, 1; 200, 1; 300, 1; NULL, 1`. Six rows with six values. NULL sorts last in ascending order, as in Postgres. | `-50, 1; 0, 1; 100, 1; 200, 1; 300, 1; NULL, 1` | pushdown | off, on | ClickHouse, Postgres | match |
| breakout_cube_with_time_dim | SQL API: `SELECT rev, COUNT(*) c FROM (SELECT id, amt_dim, rev FROM items_t) q GROUP BY 1 ORDER BY 1` | `-50, 1; 0, 1; 100, 1; 200, 1; 300, 1; NULL, 1`. The findings report this form as one row with the total. This cube has a time dimension, as the findings cube has. The docs give six rows. | `-50, 1; 0, 1; 100, 1; 200, 1; 300, 1; NULL, 1` | pushdown | off, on | ClickHouse, Postgres | match |
| count_measure | SQL API: `SELECT SUM(amt_dim) s, COUNT(*) c FROM (SELECT id, amt_dim, n FROM items) q WHERE n > 0` | `550, 6`. Per row, a count measure is 1. SUM ignores the NULL. | `550, 6` | pushdown | off, on | ClickHouse, Postgres | match |
| avg_measure | SQL API: `SELECT SUM(amt_dim) s, COUNT(*) c FROM (SELECT id, amt_dim, avg_amt FROM items) q WHERE avg_amt > 150` | `500, 2`. Per row, the avg is the amount. | `no rows` | pushdown | off, on | ClickHouse, Postgres | diff |
| raw_rows_refused | SQL API: `SELECT id, amt_dim, rev FROM (SELECT id, amt_dim, rev FROM items) q WHERE rev > 150 ORDER BY 1` | `2, 200, 200; 3, 300, 300`. The same filter with no outer aggregate: rows 2 and 3. | error: `Database Execution Error: Measure filters aren't allowed in ungrouped query` | regular | off, on | ClickHouse, Postgres | diff |
| grouped_inner_control | SQL API: `SELECT SUM(amt_dim) s, COUNT(*) c FROM (SELECT id, amt_dim, MEASURE(rev) AS rev FROM items GROUP BY 1, 2) q WHERE rev > 150` | `500, 2`. MEASURE and GROUP BY by the primary key: one row per id. The docs form. | `500, 2` | post-processing | off, on | ClickHouse, Postgres | match |
| direct_having_control | SQL API: `SELECT cat, MEASURE(n) c FROM items GROUP BY 1 HAVING MEASURE(rev) > 250 ORDER BY 1` | `a, 2`. Totals per category: a 300, b 250, c NULL. Only a is above 250. | `a, 2` | regular | off, on | ClickHouse, Postgres | match |
| direct_where_measure | SQL API: `SELECT cat, MEASURE(n) c FROM items WHERE rev > 250 GROUP BY 1 ORDER BY 1` | `a, 2`. WHERE on a measure in a grouped query. The REST filter on a measure is HAVING, so the same result as direct_having_control. | `a, 2` | regular | off, on | ClickHouse, Postgres | match |
| rest_ungrouped_measure_filter | REST: `{"dimensions": ["items.id"], "measures": ["items.rev"], "ungrouped": true, "filters": [{"member": "items.rev", "operator": "gt", "values": ["150"]}]}` | error: `Measure filters aren't allowed`. REST refuses this combination, so an error is the behaviour that Cube has chosen for it. | error: `HTTP 400 Measure filters aren't allowed in ungrouped query` | rest | off, on | ClickHouse, Postgres | match |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.0.0 | pg-off | gt_between | `550, 6` | diff |
| v1.0.0 | pg-off | above_each_row_below_total | `550, 6` | diff |
| v1.6.76 | pg-off | gt_between | `550, 6` | diff |
| v1.6.76 | pg-off | above_each_row_below_total | `550, 6` | diff |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. The [docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L349) say that an ungrouped query renders measures as if they were grouped by the primary key. So each row has its own value, and `row_values_control` shows this. A filter on a measure in a grouped query is a `HAVING` by design (`direct_having_control`), but no doc extends this to a subquery with no group. Cube itself refuses the same combination in two places: `rest_ungrouped_measure_filter` and `raw_rows_refused` both give `Measure filters aren't allowed in ungrouped query`. So only the path with an outer aggregate lets it through.
2. **Is the expected value right?** Yes. Direct SQL gives 500 and 2 for `amt > 150`. `above_each_row_below_total` shows the mechanism: no row is above 400, but the total of 550 is, so Cube returns all 6 rows. `above_total` returns no row at all, and an aggregate with no `GROUP BY` must return one.
3. **Is it misuse?** Partly. The form that works is `MEASURE(rev)` with `GROUP BY` by the primary key (`grouped_inner_control`). Nothing in the docs warns against the other form, and it returns a number in place of the error that the sibling forms give.
4. **Master?** It still happens. [`filters.rs`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubesql/cubesql/src/compile/rewrite/rules/filters.rs#L242) is identical in `v1.8.15` and master. `v1.6.76` and `v1.0.0` give the same number. I found no issue or pull request that names it.
5. **Does a real user reach it?** Yes. "Sum of amounts for orders with revenue above 150", over a saved question or model that lists the measure column. The number is wrong with no error. A driver or a client can avoid it by writing the inner `GROUP BY`.

### M1

**A date parameter fails on a query that needs SQL generation**

- **Verdict:** `confirmed`
- **Result for a user:** An error
- **Impact:** A one-sided date filter with a bound parameter fails on a wrapped query. A client cannot avoid it by stating the type, only with a cast in the SQL text or with a literal.
- **Findings verdict:** `cube-bug`
- **Run:** `python3 audit.py M1`. 26 cases, 104 runs: 48 match the expected value, 56 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| no_oid | SQL API, Parse and Bind, no OID: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1`, params `["2025-05-10 00:00:00"]` | `1400`. Rows 2, 3, 4 are on or after the bound: 200+400+800. Postgres infers the parameter type from the column. This is what psql \bind, asyncpg and psycopg3 send. Direct SQL: `1400`. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder'` | pushdown | off, on | ClickHouse, Postgres | diff |
| oid_unspecified | SQL API, Parse and Bind, OID 0: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1`, params `["2025-05-10 00:00:00"]` | `1400`. Rows 2, 3, 4 are on or after the bound: 200+400+800. OID 0 is what pgjdbc sends for setTimestamp. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder'` | pushdown | off, on | ClickHouse, Postgres | diff |
| oid_timestamp | SQL API, Parse and Bind, OID 1114: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1`, params `["2025-05-10 00:00:00"]` | `1400`. Rows 2, 3, 4 are on or after the bound: 200+400+800. The client states the type timestamp. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder'` | pushdown | off, on | ClickHouse, Postgres | diff |
| oid_timestamptz | SQL API, Parse and Bind, OID 1184: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1`, params `["2025-05-10 00:00:00+00"]` | `1400`. Rows 2, 3, 4 are on or after the bound: 200+400+800. The client states timestamptz. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder'` | pushdown | off, on | ClickHouse, Postgres | diff |
| oid_date | SQL API, Parse and Bind, OID 1082: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1`, params `["2025-05-10"]` | `1400`. Rows 2, 3, 4 are on or after the bound: 200+400+800. The client states date. Midnight is the bound. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder'` | pushdown | off, on | ClickHouse, Postgres | diff |
| oid_varchar | SQL API, Parse and Bind, OID 1043: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1`, params `["2025-05-10 00:00:00"]` | `1400`. Rows 2, 3, 4 are on or after the bound: 200+400+800. OID 1043 is what pgjdbc sends for setString. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder'` | pushdown | off, on | ClickHouse, Postgres | diff |
| iso_text | SQL API, Parse and Bind, no OID: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1`, params `["2025-05-10T00:00:00.000Z"]` | `1400`. Rows 2, 3, 4 are on or after the bound: 200+400+800. An ISO text value. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder'` | pushdown | off, on | ClickHouse, Postgres | diff |
| lt_bound | SQL API, Parse and Bind, no OID: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts < $1`, params `["2025-05-10 00:00:00"]` | `100`. Only row 1 is before the bound. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts < 'replaced_placeholder'` | no EXPLAIN | off, on | ClickHouse, Postgres | diff |
| two_params | SQL API, Parse and Bind, no OID: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1 AND ts <= $2`, params `["2025-05-10 00:00:00", "2025-05-10 00:00:01"]` | `600`. Rows 2 and 3. | `600` | no EXPLAIN | off, on | ClickHouse, Postgres | match |
| eq_param | SQL API, Parse and Bind, no OID: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts = $1`, params `["2025-05-10 00:00:00"]` | `200`. Row 2. | `200` | no EXPLAIN | off, on | ClickHouse, Postgres | match |
| in_params | SQL API, Parse and Bind, no OID: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts IN ($1, $2)`, params `["2025-05-10 00:00:00", "2025-06-01 12:00:00"]` | `1000`. Rows 2 and 4. | `1000` | no EXPLAIN | off, on | ClickHouse, Postgres | match |
| null_param | SQL API, Parse and Bind, no OID: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1`, params `[null]` | `NULL`. ts >= NULL is never true. SUM of no rows is NULL. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder'` | pushdown | off, on | ClickHouse, Postgres | diff |
| date_column | SQL API, Parse and Bind, no OID: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, d FROM ev) q WHERE d >= $1`, params `["2025-05-10"]` | `1400`. Rows 2, 3, 4 on the Date column. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, d FROM ev) q WHERE d >= 'replaced_placeholder'` | no EXPLAIN | off, on | ClickHouse, Postgres | diff |
| grouped_wrapper | SQL API, Parse and Bind, no OID: `SELECT DATE_TRUNC('month', ts) m, SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1 GROUP BY 1 ORDER BY 1`, params `["2025-05-10 00:00:00"]` | `2025-05-01 00:00:00, 600; 2025-06-01 00:00:00, 800`. Rows 2, 3 in May, row 4 in June. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT DATE_TRUNC('month', ts) AS m, SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= ...` | no EXPLAIN | off, on | ClickHouse, Postgres | diff |
| trunc_in_filter | SQL API, Parse and Bind, no OID: `SELECT MEASURE(n) c FROM ev WHERE DATE_TRUNC('day', ts) >= $1`, params `["2025-05-10 00:00:00"]` | `3`. Days on or after 05-10: rows 2, 3, 4. | `3` | no EXPLAIN | off, on | ClickHouse, Postgres | match |
| issue_12459_form | SQL API, Parse and Bind, no OID: `SELECT DATE_TRUNC('month', ts) m, label, SUM(total) * 0.5 x FROM ev WHERE ts >= $1 GROUP BY 1, 2 ORDER BY 1, 2`, params `["2025-05-10 00:00:00"]` | `2025-05-01 00:00:00, a, 100; 2025-05-01 00:00:00, b, 200; 2025-06-01 00:00:00, b, 400`. The form of #12459: arithmetic on a measure and an ORDER BY. Half of 200, 400, 800. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT DATE_TRUNC('month', ts) AS m, label, SUM(total) * 0.5 AS x FROM ev WHERE ts >= 'replaced_placeholde ...` | no EXPLAIN | off, on | ClickHouse, Postgres | diff |
| issue_12459_form_no_order | SQL API, Parse and Bind, no OID: `SELECT DATE_TRUNC('month', ts) m, label, SUM(total) * 0.5 x FROM ev WHERE ts >= $1 GROUP BY 1, 2`, params `["2025-05-10 00:00:00"]` | `2025-05-01 00:00:00, a, 100; 2025-05-01 00:00:00, b, 200; 2025-06-01 00:00:00, b, 400`. The same with no ORDER BY. #12459 says that this form works. | `2025-05-01 00:00:00, a, 100; 2025-06-01 00:00:00, b, 400; 2025-05-01 00:00:00, b, 200` | no EXPLAIN | off, on | ClickHouse | match |
| | | | `2025-05-01 00:00:00, a, 100; 2025-05-01 00:00:00, b, 200; 2025-06-01 00:00:00, b, 400` | no EXPLAIN | off, on | Postgres | match |
| no_oid_limit | SQL API, Parse and Bind, no OID: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1 LIMIT 10`, params `["2025-05-10 00:00:00"]` | `1400`. Rows 2, 3, 4 are on or after the bound: 200+400+800. With a LIMIT. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder' LIMIT  ...` | no EXPLAIN | off, on | ClickHouse, Postgres | diff |
| issue_12459_form_limit | SQL API, Parse and Bind, no OID: `SELECT DATE_TRUNC('month', ts) m, label, SUM(total) * 0.5 x FROM ev WHERE ts >= $1 GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 10`, params `["2025-05-10 00:00:00"]` | `2025-05-01 00:00:00, a, 100; 2025-05-01 00:00:00, b, 200; 2025-06-01 00:00:00, b, 400`. The form of #12459 with a LIMIT. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT DATE_TRUNC('month', ts) AS m, label, SUM(total) * 0.5 AS x FROM ev WHERE ts >= 'replaced_placeholde ...` | no EXPLAIN | off, on | ClickHouse, Postgres | diff |
| cast_control | SQL API, Parse and Bind, no OID: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1::timestamp`, params `["2025-05-10 00:00:00"]` | `1400`. Rows 2, 3, 4 are on or after the bound: 200+400+800. | `1400` | no EXPLAIN | off, on | ClickHouse, Postgres | match |
| cast_fn_control | SQL API, Parse and Bind, no OID: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= CAST($1 AS timestamp)`, params `["2025-05-10 00:00:00"]` | `1400`. Rows 2, 3, 4 are on or after the bound: 200+400+800. | `1400` | no EXPLAIN | off, on | ClickHouse, Postgres | match |
| cast_date_control | SQL API, Parse and Bind, no OID: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= $1::date`, params `["2025-05-10"]` | `1400`. Rows 2, 3, 4 are on or after the bound: 200+400+800. | `1400` | no EXPLAIN | off, on | ClickHouse, Postgres | match |
| literal_control | SQL API: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= TIMESTAMP '2025-05-10 00:00:00'` | `1400`. Rows 2, 3, 4 are on or after the bound: 200+400+800. | `1400` | pushdown | off, on | ClickHouse, Postgres | match |
| direct_scan_control | SQL API, Parse and Bind, no OID: `SELECT MEASURE(n) c FROM ev WHERE ts >= $1`, params `["2025-05-10 00:00:00"]` | `3`. No outer aggregate, so no wrapper: rows 2, 3, 4. | `3` | no EXPLAIN | off, on | ClickHouse, Postgres | match |
| text_param_control | SQL API, Parse and Bind, no OID: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim, label FROM ev) q WHERE label = $1`, params `["a"]` | `300`. A text parameter in the same shape: rows 1 and 2. | `300` | no EXPLAIN | off, on | ClickHouse, Postgres | match |
| number_param_control | SQL API, Parse and Bind, no OID: `SELECT SUM(amt_dim) s FROM (SELECT id, amt_dim FROM ev) q WHERE amt_dim > $1`, params `[300]` | `1200`. A number parameter in the same shape: rows 3 and 4. | `1200` | no EXPLAIN | off, on | ClickHouse, Postgres | match |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.7.26 | pg-off | no_oid | `1400` | match |
| v1.7.26 | pg-off | oid_timestamp | `1400` | match |
| v1.7.26 | pg-off | two_params | `600` | match |
| v1.7.26 | pg-off | cast_control | `1400` | match |
| v1.7.26 | pg-on | no_oid | `1400` | match |
| v1.7.26 | pg-on | oid_timestamp | `1400` | match |
| v1.7.26 | pg-on | two_params | `600` | match |
| v1.7.26 | pg-on | issue_12459_form | `2025-05-01 00:00:00, a, 100; 2025-05-01 00:00:00, b, 200; 2025-06-01 00:00:00, b, 400` | match |
| v1.7.26 | pg-on | cast_control | `1400` | match |
| v1.7.27 | pg-off | no_oid | `1400` | match |
| v1.7.27 | pg-off | oid_timestamp | `1400` | match |
| v1.7.27 | pg-off | two_params | `600` | match |
| v1.7.27 | pg-off | cast_control | `1400` | match |
| v1.7.27 | pg-on | no_oid | `1400` | match |
| v1.7.27 | pg-on | oid_timestamp | `1400` | match |
| v1.7.27 | pg-on | two_params | `600` | match |
| v1.7.27 | pg-on | issue_12459_form | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT DATE_TRUNC('month', ts) AS m, label, SUM(total) * 0.5 AS x FROM ev WHERE ts >= 'replaced_placeholde ...` | diff |
| v1.7.27 | pg-on | cast_control | `1400` | match |
| v1.7.46 | pg-on | no_oid | `1400` | match |
| v1.8.4 | pg-on | no_oid | `1400` | match |
| v1.8.6 | pg-on | no_oid | `1400` | match |
| v1.8.8 | pg-on | no_oid | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder'` | diff |
| v1.8.9 | pg-on | no_oid | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder'` | diff |
| v1.8.14 | pg-off | no_oid | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder'` | diff |
| v1.8.14 | pg-off | oid_timestamp | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder'` | diff |
| v1.8.14 | pg-off | two_params | `600` | match |
| v1.8.14 | pg-off | cast_control | `1400` | match |
| v1.8.14 | pg-on | no_oid | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder'` | diff |
| v1.8.14 | pg-on | oid_timestamp | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT SUM(amt_dim) AS s FROM (SELECT id, amt_dim, ts FROM ev) q WHERE ts >= 'replaced_placeholder'` | diff |
| v1.8.14 | pg-on | two_params | `600` | match |
| v1.8.14 | pg-on | issue_12459_form | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT DATE_TRUNC('month', ts) AS m, label, SUM(total) * 0.5 AS x FROM ev WHERE ts >= 'replaced_placeholde ...` | diff |
| v1.8.14 | pg-on | cast_control | `1400` | match |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. The SQL API takes the [Postgres protocol](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/index.mdx), and this error names an internal placeholder. [#12459](https://github.com/cube-js/cube/issues/12459) is open with the same error, and it has no comment from a maintainer yet.
2. **Is the expected value right?** Yes. Direct SQL gives 1400. Postgres infers the type of `$1` from the column, so the same text with a parameter is valid on Postgres itself. `literal_control` and `cast_control` give 1400 through Cube. `null_param` also fails, where SQL gives NULL.
3. **Is it misuse?** No. A date parameter with no cast is what pgjdbc, psql `\bind`, asyncpg and psycopg3 can send. `oid_timestamp`, `oid_timestamptz`, `oid_date`, `oid_varchar` and `oid_unspecified` all fail in the same way.
4. **Master?** It still happens. At Parse, [each placeholder becomes the text `replaced_placeholder`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubesql/cubesql/src/sql/statement.rs#L826), and [the planner cannot parse this text as a date](https://github.com/cube-js/cube/blob/78ecad294/rust/cube/cubesqlplanner/cubesqlplanner/src/planner/time_dimension/date_time_helper.rs#L41). `statement.rs` is identical in `v1.8.15` and master. I found no pull request for it. It is a regression, with two starts. The form of #12459 works on `v1.7.26` and fails on `v1.7.27`, which agrees with the issue. That tag has [#11559](https://github.com/cube-js/cube/pull/11559), which prefers pushdown. My form with a sum over a subquery works on `v1.8.6` and fails on `v1.8.8`. `v1.8.7` has no image on Docker Hub. I did not find the commit for this second start.
5. **Does a real user reach it?** Yes. "Revenue by month after this date", from a tool that uses bound parameters. #12459 reports it from Metabase dashboards. The result is an error, so no number is wrong. The driver in this repository already sends a literal ([docs/query-compilation.md](query-compilation.md)).

### M2

**A week number query with a date parameter fails**

- **Verdict:** `confirmed-but-narrow`
- **Result for a user:** An error
- **Impact:** This is M1 in one more query form. One fix closes both. It adds no separate work.
- **Findings verdict:** `cube-bug`
- **Run:** `python3 audit.py M2`. 11 cases, 44 runs: 20 match the expected value, 24 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| untyped | SQL API, Parse and Bind, no OID: `SELECT w, c FROM (SELECT CAST(FLOOR(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT) AS w, MEASURE(n) c FROM wk WHERE ts >= $1 GROUP BY 1) q ORDER BY 1`, params `["2025-01-05 00:00:00"]` | `1252, 2; 1253, 1`. Weeks start on Sunday 2001-01-07 here. 01-05 and 01-06 share a week, 01-12 starts the next. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT w, c FROM (SELECT CAST(floor(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT) ...` | pushdown | off, on | ClickHouse, Postgres | diff |
| oid_timestamp | SQL API, Parse and Bind, OID 1114: `SELECT w, c FROM (SELECT CAST(FLOOR(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT) AS w, MEASURE(n) c FROM wk WHERE ts >= $1 GROUP BY 1) q ORDER BY 1`, params `["2025-01-05 00:00:00"]` | `1252, 2; 1253, 1`. Weeks start on Sunday 2001-01-07 here. 01-05 and 01-06 share a week, 01-12 starts the next. The client states the type. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT w, c FROM (SELECT CAST(floor(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT) ...` | pushdown | off, on | ClickHouse, Postgres | diff |
| oid_varchar | SQL API, Parse and Bind, OID 1043: `SELECT w, c FROM (SELECT CAST(FLOOR(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT) AS w, MEASURE(n) c FROM wk WHERE ts >= $1 GROUP BY 1) q ORDER BY 1`, params `["2025-01-05 00:00:00"]` | `1252, 2; 1253, 1`. Weeks start on Sunday 2001-01-07 here. 01-05 and 01-06 share a week, 01-12 starts the next. The type of pgjdbc setString. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT w, c FROM (SELECT CAST(floor(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT) ...` | pushdown | off, on | ClickHouse, Postgres | diff |
| one_second_before | SQL API, Parse and Bind, no OID: `SELECT w, c FROM (SELECT CAST(FLOOR(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT) AS w, MEASURE(n) c FROM wk WHERE ts >= $1 GROUP BY 1) q ORDER BY 1`, params `["2025-01-04 23:59:59"]` | `1251, 1; 1252, 2; 1253, 1`. Row 1 is on the bound, in the week before. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT w, c FROM (SELECT CAST(floor(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT) ...` | pushdown | off, on | ClickHouse, Postgres | diff |
| empty_result | SQL API, Parse and Bind, no OID: `SELECT w, c FROM (SELECT CAST(FLOOR(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT) AS w, MEASURE(n) c FROM wk WHERE ts >= $1 GROUP BY 1) q ORDER BY 1`, params `["2030-01-01 00:00:00"]` | `no rows`. No rows. | error: `Internal Error: Can't parse date: 'replaced_placeholder' QUERY: SELECT w, c FROM (SELECT CAST(floor(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT) ...` | pushdown | off, on | ClickHouse, Postgres | diff |
| in_inner_and_diff | SQL API, Parse and Bind, no OID: `SELECT w, c FROM (SELECT CAST(FLOOR(DATEDIFF('day', $1, ts) / 7.0) AS BIGINT) AS w, MEASURE(n) c FROM wk GROUP BY 1) q ORDER BY 1`, params `["2025-01-05 00:00:00"]` | `-1, 1; 0, 2; 1, 1`. The parameter is the origin: 01-05 is week 0. Row 1 is one second before it, so week -1. | error: `Database Execution Error: Query failed: Error: A value of illegal type was provided as 2nd argument 'startdate' to function 'dateDiff'. Expected: Date[32] or DateTime[64] ...` | no EXPLAIN | off, on | ClickHouse | diff |
| | | | error: `Database Execution Error: function date_trunc(unknown, unknown) is not unique` | no EXPLAIN | off, on | Postgres | diff |
| cast_control | SQL API, Parse and Bind, no OID: `SELECT w, c FROM (SELECT CAST(FLOOR(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT) AS w, MEASURE(n) c FROM wk WHERE ts >= $1::timestamp GROUP BY 1) q ORDER BY 1`, params `["2025-01-05 00:00:00"]` | `1252, 2; 1253, 1`. Weeks start on Sunday 2001-01-07 here. 01-05 and 01-06 share a week, 01-12 starts the next. | `1252, 2; 1253, 1` | no EXPLAIN | off, on | ClickHouse, Postgres | match |
| literal_control | SQL API: `SELECT w, c FROM (SELECT CAST(FLOOR(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT) AS w, MEASURE(n) c FROM wk WHERE ts >= TIMESTAMP '2025-01-05 00:00:00' GROUP BY 1) q ORDER BY 1` | `1252, 2; 1253, 1`. Weeks start on Sunday 2001-01-07 here. 01-05 and 01-06 share a week, 01-12 starts the next. | `1252, 2; 1253, 1` | pushdown | off, on | ClickHouse, Postgres | match |
| no_param_control | SQL API: `SELECT CAST(FLOOR(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT) AS w, MEASURE(n) c FROM wk GROUP BY 1 ORDER BY 1` | `1251, 1; 1252, 2; 1253, 1`. All rows. | `1251, 1; 1252, 2; 1253, 1` | pushdown | off, on | ClickHouse, Postgres | match |
| text_param_control | SQL API, Parse and Bind, no OID: `SELECT w, c FROM (SELECT CAST(FLOOR(DATEDIFF('day', TIMESTAMP '2001-01-07 00:00:00', ts) / 7.0) AS BIGINT) AS w, MEASURE(n) c FROM wk WHERE label = $1 GROUP BY 1) q ORDER BY 1`, params `["b"]` | `1252, 1; 1253, 1`. A text parameter in the same shape: rows 3, 4. | `1252, 1; 1253, 1` | no EXPLAIN | off, on | ClickHouse, Postgres | match |
| date_trunc_week_param | SQL API, Parse and Bind, no OID: `SELECT DATE_TRUNC('week', ts) w, MEASURE(n) c FROM wk WHERE ts >= $1 GROUP BY 1 ORDER BY 1`, params `["2025-01-05 00:00:00"]` | `2024-12-30 00:00:00, 1; 2025-01-06 00:00:00, 2`. Weeks start on Monday in DATE_TRUNC. Row 2 falls in the week of 12-30, rows 3, 4 in the week of 01-06. No wrapper is needed. | `2024-12-30 00:00:00, 1; 2025-01-06 00:00:00, 2` | no EXPLAIN | off, on | ClickHouse, Postgres | match |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.7.26 | pg-on | untyped | `1252, 2; 1253, 1` | match |
| v1.7.26 | pg-on | cast_control | `1252, 2; 1253, 1` | match |
| v1.7.27 | pg-on | untyped | `1252, 2; 1253, 1` | match |
| v1.7.27 | pg-on | cast_control | `1252, 2; 1253, 1` | match |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No, as for [M1](#m1). It has the same error and it is covered by [#12459](https://github.com/cube-js/cube/issues/12459).
2. **Is the expected value right?** Yes. I derived the week numbers from date arithmetic in `cases/M2.py`, with no database. `literal_control` and `cast_control` return them.
3. **Is it misuse?** No. Only the parameter with no cast fails, on each OID that I tried. `date_trunc_week_param` shows that a bucket by week with the same parameter works, so the plain week chart is not affected. `in_inner_and_diff` puts the parameter inside `DATEDIFF`, and it fails with other errors. A database cannot infer that type either, so I do not count this case against Cube.
4. **Master?** It still happens, by the same code as M1. `v1.7.26` and `v1.7.27` return the correct result for this form, so it began later. I expect the second start of M1, but I did not bisect this form.
5. **Does a real user reach it?** Rarely: it needs a week number with `DATEDIFF`, a one-sided filter and a bound parameter. The result is an error.

### m3

**A count of no rows is NULL when a rollup serves it**

- **Verdict:** `confirmed`
- **Result for a user:** Wrong value with no error, NULL in place of 0
- **Impact:** A dashboard filter that matches nothing shows an empty value in place of 0, on a rollup in Cube Store or in Postgres.
- **Findings verdict:** `cube-bug`
- **Run:** `python3 audit.py m3`. 17 cases, 68 runs: 40 match the expected value, 28 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| plain_no_rollup_sql | SQL API: `SELECT MEASURE(n) c FROM o WHERE st = 'nope'` | `0`. A count of no rows is 0 in SQL: SQL:2016 part 2, 10.9, and both databases. Direct SQL: `0`. | `0` | regular | off, on | ClickHouse, Postgres | match |
| plain_no_rollup_rest | REST: `{"measures": ["o.n"], "filters": [{"member": "o.st", "operator": "equals", "values": ["nope"]}]}` | `0`. A count of no rows is 0 in SQL: SQL:2016 part 2, 10.9, and both databases. Direct SQL: `0`. | `0` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| ext_rollup_sql | SQL API: `SELECT MEASURE(n) c FROM or_ext WHERE st = 'nope'` | `0`. A count of no rows is 0 in SQL: SQL:2016 part 2, 10.9, and both databases. Direct SQL: `0`. | `NULL` | regular | off, on | ClickHouse, Postgres | diff |
| ext_rollup_count_star | SQL API: `SELECT COUNT(*) c FROM or_ext WHERE st = 'nope'` | `0`. A count of no rows is 0 in SQL: SQL:2016 part 2, 10.9, and both databases. | `0` | pushdown | off, on | ClickHouse, Postgres | match |
| ext_rollup_rest | REST: `{"measures": ["or_ext.n"], "filters": [{"member": "or_ext.st", "operator": "equals", "values": ["nope"]}]}` | `0`. A count of no rows is 0 in SQL: SQL:2016 part 2, 10.9, and both databases. Direct SQL: `0`. | `NULL` | REST, rollup hit | off, on | ClickHouse, Postgres | diff |
| db_rollup_rest | REST: `{"measures": ["or_db.n"], "filters": [{"member": "or_db.st", "operator": "equals", "values": ["nope"]}]}` | `0`. A count of no rows is 0 in SQL: SQL:2016 part 2, 10.9, and both databases. The rollup is in the database, not in Cube Store. | `0` | REST, rollup hit | off, on | ClickHouse | match |
| | | | `NULL` | REST, rollup hit | off, on | Postgres | diff |
| db_rollup_sql | SQL API: `SELECT MEASURE(n) c FROM or_db WHERE st = 'nope'` | `0`. A count of no rows is 0 in SQL: SQL:2016 part 2, 10.9, and both databases. The rollup is in the database. | `0` | regular | off, on | ClickHouse | match |
| | | | `NULL` | regular | off, on | Postgres | diff |
| ext_rollup_one_match | REST: `{"measures": ["or_ext.n"], "filters": [{"member": "or_ext.st", "operator": "equals", "values": ["b"]}]}` | `1`. Row 3. A value with rows is correct. | `1` | REST, rollup hit | off, on | ClickHouse, Postgres | match |
| ext_rollup_group_by | REST: `{"measures": ["or_ext.n"], "dimensions": ["or_ext.st"], "filters": [{"member": "or_ext.st", "operator": "equals", "values": ["nope"]}]}` | `no rows`. GROUP BY of no rows gives no rows, not a 0. | `no rows` | REST, rollup hit | off, on | ClickHouse, Postgres | match |
| ext_rollup_date_range_empty | REST: `{"measures": ["or_ext.n"], "timeDimensions": [{"dimension": "or_ext.ts", "dateRange": ["2030-01-01", "2030-01-31"]}]}` | `0`. A count of no rows is 0 in SQL: SQL:2016 part 2, 10.9, and both databases. The date range has no rows. | `NULL` | REST, rollup hit | off, on | ClickHouse, Postgres | diff |
| ext_rollup_null_filter | REST: `{"measures": ["or_ext.n"], "filters": [{"member": "or_ext.st", "operator": "notSet"}]}` | `0`. A count of no rows is 0 in SQL: SQL:2016 part 2, 10.9, and both databases. No row has a NULL status. | `NULL` | REST, rollup hit | off, on | ClickHouse, Postgres | diff |
| ext_rollup_coalesce | SQL API: `SELECT COALESCE(MEASURE(n), 0) c FROM or_ext WHERE st = 'nope'` | `0`. The value with a COALESCE, the form that a client can write. | `0` | post-processing | off, on | ClickHouse, Postgres | match |
| ext_rollup_sum_is_null | REST: `{"measures": ["or_ext.total"], "filters": [{"member": "or_ext.st", "operator": "equals", "values": ["nope"]}]}` | `NULL`. SUM of no rows is NULL in SQL. This is correct, so the rule of m3 is only for a count. Direct SQL: `\N`. | `NULL` | REST, rollup hit | off, on | ClickHouse, Postgres | match |
| ext_rollup_miss_dim | REST: `{"measures": ["or_ext.n"], "filters": [{"member": "or_ext.id", "operator": "equals", "values": ["99"]}]}` | `0`. A count of no rows is 0 in SQL: SQL:2016 part 2, 10.9, and both databases. A filter on id, which is not in the rollup, so the rollup misses. | `0` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| ext_rollup_miss_distinct | REST: `{"measures": ["or_ext.dist"], "filters": [{"member": "or_ext.st", "operator": "equals", "values": ["nope"]}]}` | `0`. count_distinct is not in the rollup, so it reads the source. COUNT(DISTINCT) of no rows is 0. | `0` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| empty_table_rollup | REST: `{"measures": ["oe_ext.n"]}` | `0`. A count of no rows is 0 in SQL: SQL:2016 part 2, 10.9, and both databases. The table has no rows at all. | `NULL` | REST, rollup hit | off, on | ClickHouse, Postgres | diff |
| empty_table_rollup_sql | SQL API: `SELECT MEASURE(n) c FROM oe_ext` | `0`. A count of no rows is 0 in SQL: SQL:2016 part 2, 10.9, and both databases. The table has no rows at all. | `NULL` | regular | off, on | ClickHouse, Postgres | diff |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.0.0 | pg-off | ext_rollup_rest | `NULL` | diff |
| v1.6.76 | pg-off | ext_rollup_rest | `NULL` | diff |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. I found no doc or test that gives NULL for a count. The result changes when a rollup hits, and a rollup must not change a result. The [docs on measures](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/measures.mdx) define `count` as a row count.
2. **Is the expected value right?** Yes for a count: SQL `COUNT` of no rows is 0, and direct SQL on both databases gives 0. I do not treat `sum` the same way: `ext_rollup_sum_is_null` shows NULL, which is the SQL standard. ClickHouse gives 0 for `sum` of no rows, so ClickHouse output is no proof here.
3. **Is it misuse?** No. The model is a plain rollup with an index. `ext_rollup_coalesce` shows that a client can add `COALESCE`.
4. **Master?** It still happens. [`aggregateOnGroupedColumn`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/BaseQuery.ts#L4273) writes `sum` for `count`. The change in this file from `v1.8.15` to master does not touch this line. It also happens on `v1.6.76` and `v1.0.0`. I found no pull request for it.
5. **Does a real user reach it?** Yes. "Orders with status X" on a cube with a rollup, when X has no rows. REST returns `null`. A client that divides by the count, or a chart, gets an empty value. The number is wrong by type more than by amount.

### m4

**Raw errors from MAX, MIN or AVG of a rolling measure**

- **Verdict:** `misuse`
- **Result for a user:** Cosmetic, an error with a poor message
- **Impact:** The query has no defined meaning. It fails. Only the text of the error is poor.
- **Findings verdict:** `cube-bug`
- **Run:** `python3 audit.py m4`. 11 cases, 44 runs: 21 match the expected value, 23 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| max_of_regular_sum | SQL API: `SELECT MAX(rev) x FROM rt` | error: `Measure aggregation type doesn't match`. MAX of a sum measure. The docs give this error when an aggregate does not match the measure type. This is the control: the error that the docs promise. | `11` | pushdown | off, on | ClickHouse, Postgres | diff |
| max_of_rolling_sum | SQL API: `SELECT MAX(rev_30d) x FROM rt` | error: `Measure aggregation type doesn't match`. MAX of a rolling sum. The docs give this error when an aggregate does not match the measure type. | error: `Database Execution Error: Query failed: Error: Unknown expression or function identifier 'amt' in scope SELECT max(amt) AS max_rt_rev_30d_ FROM (SELECT sum(rt_rev_30d_cum ...` | pushdown | off | ClickHouse | diff |
| | | | error: `Database Execution Error: Query failed: Error: Unknown expression or function identifier 'rt.amt' in scope WITH cte_0 AS (SELECT max(rt.amt) AS rt__rev_30d FROM audit.rt  ...` | pushdown | on | ClickHouse | diff |
| | | | error: `Database Execution Error: column "amt" does not exist` | pushdown | off | Postgres | diff |
| | | | error: `Database Execution Error: missing FROM-clause entry for table "rt"` | pushdown | on | Postgres | diff |
| min_of_rolling_sum | SQL API: `SELECT MIN(rev_30d) x FROM rt` | error: `Measure aggregation type doesn't match`. MIN of a rolling sum. The docs give this error when an aggregate does not match the measure type. | error: `Database Execution Error: Query failed: Error: Unknown expression or function identifier 'amt' in scope SELECT min(amt) AS min_rt_rev_30d_ FROM (SELECT sum(rt_rev_30d_cum ...` | pushdown | off | ClickHouse | diff |
| | | | error: `Database Execution Error: Query failed: Error: Unknown expression or function identifier 'rt.amt' in scope WITH cte_0 AS (SELECT min(rt.amt) AS rt__rev_30d FROM audit.rt  ...` | pushdown | on | ClickHouse | diff |
| | | | error: `Database Execution Error: column "amt" does not exist` | pushdown | off | Postgres | diff |
| | | | error: `Database Execution Error: missing FROM-clause entry for table "rt"` | pushdown | on | Postgres | diff |
| avg_of_rolling_sum | SQL API: `SELECT AVG(rev_30d) x FROM rt` | error: `Measure aggregation type doesn't match`. AVG of a rolling sum. The docs give this error when an aggregate does not match the measure type. | error: `Database Execution Error: Query failed: Error: Unknown expression or function identifier 'amt' in scope SELECT avg(amt) AS avg_rt_rev_30d_ FROM (SELECT sum(rt_rev_30d_cum ...` | pushdown | off | ClickHouse | diff |
| | | | error: `Database Execution Error: Query failed: Error: Unknown expression or function identifier 'rt.amt' in scope WITH cte_0 AS (SELECT avg(rt.amt) AS rt__rev_30d FROM audit.rt  ...` | pushdown | on | ClickHouse | diff |
| | | | error: `Database Execution Error: column "amt" does not exist` | pushdown | off | Postgres | diff |
| | | | error: `Database Execution Error: missing FROM-clause entry for table "rt"` | pushdown | on | Postgres | diff |
| max_of_rolling_by_bucket | SQL API: `SELECT DATE_TRUNC('month', ts) m, MAX(rev_30d) x FROM rt WHERE ts >= '2025-01-01' AND ts < '2025-03-01' GROUP BY 1 ORDER BY 1` | error: `Measure aggregation type doesn't match`. The same with a bucket and a range. The docs give this error when an aggregate does not match the measure type. | error: `Database Execution Error: Query failed: Error: Unknown expression or function identifier 'amt' in scope SELECT q_0.datetrunc_utf8__ AS datetrunc_utf8__, max(amt) AS max_r ...` | pushdown | off | ClickHouse | diff |
| | | | error: `Internal Error: Date range is required for time series QUERY: SELECT DATE_TRUNC('month', ts) AS m, MAX(rev_30d) AS x FROM rt WHERE ts >= '2025-01-01' AND ts < '2025-03-01 ...` | not-planned | on | ClickHouse | diff |
| | | | error: `Database Execution Error: column "amt" does not exist` | pushdown | off | Postgres | diff |
| | | | error: `Database Execution Error: missing FROM-clause entry for table "rt"` | pushdown | on | Postgres | diff |
| sum_of_rolling_sum_control | SQL API: `SELECT SUM(rev_30d) x FROM rt` | `21`. SUM matches the type. With no bucket, Cube gives the sum of all rows: 5+7+11-2. | `21` | regular | off, on | ClickHouse, Postgres | match |
| max_of_rolling_max_control | SQL API: `SELECT MAX(top_30d) x FROM rt` | `11`. MAX matches the type. The largest amount is 11. | `11` | regular | off, on | ClickHouse, Postgres | match |
| max_of_max_control | SQL API: `SELECT MAX(top) x FROM rt` | `11`. A plain max measure. | `11` | regular | off, on | ClickHouse, Postgres | match |
| measure_by_bucket_control | SQL API: `SELECT DATE_TRUNC('month', ts) m, MEASURE(rev_30d) x FROM rt WHERE ts >= '2025-01-01' AND ts < '2025-03-01' GROUP BY 1 ORDER BY 1` | `2025-01-01 00:00:00, 12; 2025-02-01 00:00:00, 9`. The docs form. 30 days to 01-31: rows 1, 2. 30 days to 02-28: rows 3, 4. | `2025-01-01 00:00:00, 21; 2025-02-01 00:00:00, 21` | regular | off | ClickHouse | diff |
| | | | `2025-01-01 00:00:00, 12; 2025-02-01 00:00:00, 9` | regular | see database | ClickHouse (on), Postgres (off, on) | match |
| max_over_subquery_control | SQL API: `SELECT MAX(x) y FROM (SELECT DATE_TRUNC('month', ts) m, MEASURE(rev_30d) x FROM rt WHERE ts >= '2025-01-01' AND ts < '2025-03-01' GROUP BY 1) q` | `12`. What a person means by this: the largest monthly value, 12 against 9. | `21` | post-processing | off | ClickHouse | diff |
| | | | `12` | post-processing | see database | ClickHouse (on), Postgres (off, on) | match |
| rest_control | REST: `{"measures": ["rt.rev_30d"], "timeDimensions": [{"dimension": "rt.ts", "granularity": "month", "dateRange": ["2025-01-01", "2025-02-28"]}]}` | `2025-01-01 00:00:00, 12; 2025-02-01 00:00:00, 9`. REST has no outer aggregate. | `2025-01-01 00:00:00, 21; 2025-02-01 00:00:00, 21` | REST, no rollup | off | ClickHouse | diff |
| | | | `2025-01-01 00:00:00, 12; 2025-02-01 00:00:00, 9` | REST, no rollup | see database | ClickHouse (on), Postgres (off, on) | match |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.6.76 | pg-off | max_of_regular_sum | `11` | diff |
| v1.6.76 | pg-off | max_of_rolling_sum | error: `Database Execution Error: column "amt" does not exist` | diff |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** Partly. The docs say that an aggregate must [match the measure type](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L46), and that a mismatch [gives an error](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L70). So the query is not supported. The text of the error differs.
2. **Is the expected value right?** The findings expect the documented error. `max_of_regular_sum` shows that a plain sum measure does not get this error: it gives 11, the largest row, through pushdown. So the documented error is not a safe expectation. A rolling window with no bucket has no row value to take the largest of. No correct number exists.
3. **Is it misuse?** Yes. The form that the docs recommend is `MEASURE()` (`measure_by_bucket_control`). The form for the largest monthly value is `MAX` over a subquery (`max_over_subquery_control`). Both give the correct number on Tesseract. A matching aggregate (`max_of_rolling_max_control`) also works.
4. **Master?** The behaviour is the same. The rewrite rules have no change for this between `v1.8.15` and master. `v1.6.76` gives the same error.
5. **Does a real user reach it?** Rarely by hand. An agent that picks `MAX` on any column can. It gets an error such as `Unknown expression or function identifier`, not a wrong number. `max_of_rolling_by_bucket` also gives other error texts on each config. The cost is a poor message, so a report to Cube has a low priority.

### m5

**An original_sql pre-aggregation with an index fails**

- **Verdict:** `confirmed-but-narrow`
- **Result for a user:** An error
- **Impact:** A model with `original_sql` on ClickHouse fails at query time. The docs warn about it, and a column alias in the cube `sql` avoids it.
- **Findings verdict:** `cube-clickhouse-bug`
- **Run:** `python3 audit.py m5`. 10 cases, 40 runs: 16 match the expected value, 24 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| db_no_index | REST: `{"measures": ["c_db_noidx.rev"], "dimensions": ["c_db_noidx.st"], "order": {"c_db_noidx.st": "asc"}}` | ch: error `without indexes`, `a, 30; b, 40`. Rows 1, 2 are a, row 3 is b. ClickHouse: the docs state that a pre-aggregation needs an index, so the refusal is correct there. Direct SQL: `a, 30; b, 40`. | error: `HTTP 400 ClickHouse doesn't support pre-aggregations without indexes` | rest | off, on | ClickHouse | match |
| | | | `a, 30; b, 40` | REST, rollup hit | off, on | Postgres | match |
| db_index | REST: `{"measures": ["c_db_idx.rev"], "dimensions": ["c_db_idx.st"], "order": {"c_db_idx.st": "asc"}}` | `a, 30; b, 40`. Rows 1, 2 are a, row 3 is b. The index that the docs ask for. Direct SQL: `a, 30; b, 40`. | error: `HTTP 400 Error: Command failed: Error: Missing columns: 'c_db_idx__st' while processing: '(c_db_idx__st)', required columns: 'c_db_idx__st', available columns: '_block_nu ...` | rest | off, on | ClickHouse | diff |
| | | | error: `HTTP 400 Error: column "c_db_idx__st" does not exist` | rest | off, on | Postgres | diff |
| db_index_sql | SQL API: `SELECT st, MEASURE(rev) r FROM c_db_idx GROUP BY 1 ORDER BY 1` | `a, 30; b, 40`. Rows 1, 2 are a, row 3 is b. Through the SQL API. | error: `Database Execution Error: Command failed: Error: Missing columns: 'c_db_idx__st' while processing: '(c_db_idx__st)', required columns: 'c_db_idx__st', available columns:  ...` | regular | off, on | ClickHouse | diff |
| | | | `a, 30; b, 40` | regular | off, on | Postgres | match |
| ext_no_index | REST: `{"measures": ["c_ext_noidx.rev"], "dimensions": ["c_ext_noidx.st"], "order": {"c_ext_noidx.st": "asc"}}` | `a, 30; b, 40`. Rows 1, 2 are a, row 3 is b. In Cube Store, so the ClickHouse rule on indexes does not apply. | error: `HTTP 400 ClickHouse doesn't support pre-aggregations without indexes` | rest | off, on | ClickHouse | diff |
| | | | `a, 30; b, 40` | REST, rollup hit | off, on | Postgres | match |
| ext_index | REST: `{"measures": ["c_ext_idx.rev"], "dimensions": ["c_ext_idx.st"], "order": {"c_ext_idx.st": "asc"}}` | `a, 30; b, 40`. Rows 1, 2 are a, row 3 is b. In Cube Store, with an index. | error: `HTTP 400 Error: Error during create table dev_pre_aggregations.c_ext_idx_base_b54euei3_kttw5y5z_1lea64p: Column 'c_ext_idx__st' in index 'c_ext_idx_base_st_i_b54euei3_ktt ...` | rest | off | ClickHouse | diff |
| | | | error: `HTTP 400 Error: Error during create table dev_pre_aggregations.c_ext_idx_base_nxmzbxek_kttw5y5z_1lea64i: Column 'c_ext_idx__st' in index 'c_ext_idx_base_st_i_nxmzbxek_ktt ...` | rest | on | ClickHouse | diff |
| | | | error: `HTTP 400 Error: Error during create table dev_pre_aggregations.c_ext_idx_base_rltqyn54_mvax5jfc_1lea656: Column 'c_ext_idx__st' in index 'c_ext_idx_base_st_i_rltqyn54_mva ...` | rest | off | Postgres | diff |
| | | | error: `HTTP 400 Error: Error during create table dev_pre_aggregations.c_ext_idx_base_rltqyn54_mvax5jfc_1lea64v: Column 'c_ext_idx__st' in index 'c_ext_idx_base_st_i_rltqyn54_mva ...` | rest | on | Postgres | diff |
| ext_index_sql | SQL API: `SELECT st, MEASURE(rev) r FROM c_ext_idx GROUP BY 1 ORDER BY 1` | `a, 30; b, 40`. Rows 1, 2 are a, row 3 is b. In Cube Store, through the SQL API. | error: `Database Execution Error: Error during create table dev_pre_aggregations.c_ext_idx_base_b54euei3_kttw5y5z_1lea64p: Column 'c_ext_idx__st' in index 'c_ext_idx_base_st_i_b5 ...` | regular | off | ClickHouse | diff |
| | | | error: `Database Execution Error: Error during create table dev_pre_aggregations.c_ext_idx_base_nxmzbxek_kttw5y5z_1lea64i: Column 'c_ext_idx__st' in index 'c_ext_idx_base_st_i_nx ...` | regular | on | ClickHouse | diff |
| | | | error: `Database Execution Error: Error during create table dev_pre_aggregations.c_ext_idx_base_rltqyn54_mvax5jfc_1lea656: Column 'c_ext_idx__st' in index 'c_ext_idx_base_st_i_rl ...` | regular | off | Postgres | diff |
| | | | error: `Database Execution Error: Error during create table dev_pre_aggregations.c_ext_idx_base_rltqyn54_mvax5jfc_1lea64v: Column 'c_ext_idx__st' in index 'c_ext_idx_base_st_i_rl ...` | regular | on | Postgres | diff |
| db_rollup_over | REST: `{"measures": ["c_db_roll.rev"], "dimensions": ["c_db_roll.st"], "order": {"c_db_roll.st": "asc"}}` | `a, 30; b, 40`. Rows 1, 2 are a, row 3 is b. The docs form: a rollup with use_original_sql_pre_aggregations. | error: `HTTP 400 Error: Command failed: Error: Missing columns: 'c_db_roll__st' while processing: '(c_db_roll__st)', required columns: 'c_db_roll__st', available columns: '_block ...` | rest | off, on | ClickHouse | diff |
| | | | error: `HTTP 400 Error: column "c_db_roll__st" does not exist` | rest | off, on | Postgres | diff |
| ext_rollup_over | REST: `{"measures": ["c_ext_roll.rev"], "dimensions": ["c_ext_roll.st"], "order": {"c_ext_roll.st": "asc"}}` | `a, 30; b, 40`. Rows 1, 2 are a, row 3 is b. The same, with the original_sql table in Cube Store. | error: `HTTP 400 ClickHouse doesn't support pre-aggregations without indexes` | rest | off, on | ClickHouse | diff |
| | | | error: `HTTP 400 Error: relation "dev_pre_aggregations.c_ext_roll_base" does not exist` | rest | off, on | Postgres | diff |
| plain_rollup_control | REST: `{"measures": ["c_plain_roll.rev"], "dimensions": ["c_plain_roll.st"], "order": {"c_plain_roll.st": "asc"}}` | `a, 30; b, 40`. Rows 1, 2 are a, row 3 is b. A rollup with no original_sql. | `a, 30; b, 40` | REST, rollup hit | off, on | ClickHouse, Postgres | match |
| alias_column_workaround | REST: `{"measures": ["c_aliased.rev"], "dimensions": ["c_aliased.st"], "order": {"c_aliased.st": "asc"}}` | `a, 30; b, 40`. Rows 1, 2 are a, row 3 is b. The source column has the name of the member alias, so the index column exists. | `a, 30; b, 40` | REST, rollup hit | off, on | ClickHouse, Postgres | match |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.0.0 | ch-off | db_index | error: `HTTP 500 TypeError: Cannot read properties of undefined (reading 'apply')` | diff |
| v1.6.76 | ch-off | db_index | error: `HTTP 400 Error: Query failed: Error: Missing columns: 'c_db_idx__st' while processing: '(c_db_idx__st)', required columns: 'c_db_idx__st', available columns: '_block_numb ...` | diff |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** It is a known limit, not a design. The [docs note](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/pre-aggregations.mdx#L1834) says that indexes do not work in some cases with `original_sql`, and links [#7420](https://github.com/cube-js/cube/issues/7420). That issue is open, and a maintainer reproduced it. [PR #12021](https://github.com/cube-js/cube/pull/12021) adds a failing test for it. The [ClickHouse docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/admin/connect-to-data/data-sources/clickhouse.mdx#L48) ask for an index, so `db_no_index` is a correct refusal.
2. **Is the expected value right?** Yes: a 30, b 40, from direct SQL. `plain_rollup_control` gives it.
3. **Is it misuse?** Partly. The [docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/pre-aggregations.mdx#L149) say to use `original_sql` only for a complex cube `sql`. They also say that `external: true` is not supported in general. So `ext_index` and `ext_rollup_over` test a form that the docs discourage. `db_rollup_over` is the docs form, and it fails on both databases.
4. **Master?** It still happens. [`evaluateIndexColumns`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/BaseQuery.ts#L4694) and [`preAggregationLoadSql`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L286) do not change between `v1.8.15` and master. No open pull request fixes it. `v1.6.76` has the same error. `v1.0.0` fails with a different error, which is my model on that older compiler.
5. **Does a real user reach it?** Only with an expensive cube `sql` on ClickHouse. The query fails with `Missing columns`, so no wrong number can follow. `alias_column_workaround` works with no change to Cube: name the source column as the member alias. A plain rollup does the job in most models.

### unbounded

**A rolling window with a time series fails on ClickHouse**

- **Verdict:** `confirmed`
- **Result for a user:** An error
- **Impact:** A running total by month or by day fails on the main target, with more than one bucket. Tesseract with one bucket works. A far bound such as `100 year` works on ClickHouse 26.8 with Tesseract.
- **Findings verdict:** `cube-clickhouse-bug`
- **Run:** `python3 audit.py unbounded`. 14 cases, 84 runs: 43 match the expected value, 41 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| two_months_sql | SQL API: `SELECT DATE_TRUNC('month', ts) m, MEASURE(cum) c FROM u WHERE ts >= '2025-04-01' AND ts < '2025-06-01' GROUP BY 1 ORDER BY 1` | `2025-04-01 00:00:00, 111; 2025-05-01 00:00:00, 1111`. Everything up to 04-30: rows 1, 2, 3. Up to 05-31: also row 4. Direct SQL: `1111`. | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__tableN.u__ts_month, __tableN.date_to). ; query id: <id>` | regular | off, on | ClickHouse | diff |
| | | | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression [Predcates: () Residual filter: ((greaterOrEquals(_CAST(__tableN.date_to,  ...` | regular | off, on | ClickHouse 25.3-alpine | diff |
| | | | `2025-04-01 00:00:00, 111; 2025-05-01 00:00:00, 1111` | regular | off, on | Postgres | match |
| two_months_rest | REST: `{"measures": ["u.cum"], "timeDimensions": [{"dimension": "u.ts", "granularity": "month", "dateRange": ["2025-04-01", "2025-05-31"]}]}` | `2025-04-01 00:00:00, 111; 2025-05-01 00:00:00, 1111`. Everything up to 04-30: rows 1, 2, 3. Up to 05-31: also row 4. | error: `HTTP 400 Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__tableN.u__ts_month, __tableN.date_to). ; query id: <id>` | rest | off, on | ClickHouse | diff |
| | | | error: `HTTP 400 Error: Query failed: Error: Cannot determine join keys in JOIN ON expression [Predcates: () Residual filter: ((greaterOrEquals(_CAST(__tableN.date_to, 'DateTime6 ...` | rest | off, on | ClickHouse 25.3-alpine | diff |
| | | | `2025-04-01 00:00:00, 111; 2025-05-01 00:00:00, 1111` | REST, no rollup | off, on | Postgres | match |
| one_month_sql | SQL API: `SELECT DATE_TRUNC('month', ts) m, MEASURE(cum) c FROM u WHERE ts >= '2025-04-01' AND ts < '2025-05-01' GROUP BY 1 ORDER BY 1` | `2025-04-01 00:00:00, 111`. One bucket: rows 1, 2, 3. | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__tableN.u__ts_month, __tableN.date_to). ; query id: <id>` | regular | off | ClickHouse | diff |
| | | | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression [Predcates: () Residual filter: ((greaterOrEquals(_CAST(__tableN.date_to,  ...` | regular | off, on | ClickHouse 25.3-alpine | diff |
| | | | `2025-04-01 00:00:00, 111` | regular | see database | ClickHouse (on), Postgres (off, on) | match |
| two_days | SQL API: `SELECT DATE_TRUNC('day', ts) m, MEASURE(cum) c FROM u WHERE ts >= '2025-04-01' AND ts < '2025-04-03' GROUP BY 1 ORDER BY 1` | `2025-04-01 00:00:00, 111; 2025-04-02 00:00:00, 111`. Rows 1, 2, 3 on both days. | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__tableN.u__ts_day, __tableN.date_to). ; query id: <id>` | regular | off, on | ClickHouse | diff |
| | | | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression [Predcates: () Residual filter: ((greaterOrEquals(_CAST(__tableN.date_to,  ...` | regular | off, on | ClickHouse 25.3-alpine | diff |
| | | | `2025-04-01 00:00:00, 111; 2025-04-02 00:00:00, 111` | regular | off, on | Postgres | match |
| one_day_on_row | SQL API: `SELECT DATE_TRUNC('day', ts) m, MEASURE(cum) c FROM u WHERE ts >= '2025-03-31' AND ts < '2025-04-01' GROUP BY 1 ORDER BY 1` | `2025-03-31 00:00:00, 11`. A bucket that ends after row 2, at 23:59:59. | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__tableN.u__ts_day, __tableN.date_to). ; query id: <id>` | regular | off | ClickHouse | diff |
| | | | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression [Predcates: () Residual filter: ((greaterOrEquals(_CAST(__tableN.date_to,  ...` | regular | off, on | ClickHouse 25.3-alpine | diff |
| | | | `2025-03-31 00:00:00, 11` | regular | see database | ClickHouse (on), Postgres (off, on) | match |
| before_all_rows | SQL API: `SELECT DATE_TRUNC('month', ts) m, MEASURE(cum) c FROM u WHERE ts >= '2025-01-01' AND ts < '2025-03-01' GROUP BY 1 ORDER BY 1` | `2025-01-01 00:00:00, NULL; 2025-02-01 00:00:00, NULL`. No rows are before these buckets. A SUM of no rows is NULL. | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__tableN.u__ts_month, __tableN.date_to). ; query id: <id>` | regular | off, on | ClickHouse | diff |
| | | | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression [Predcates: () Residual filter: ((greaterOrEquals(_CAST(__tableN.date_to,  ...` | regular | off, on | ClickHouse 25.3-alpine | diff |
| | | | `2025-01-01 00:00:00, NULL; 2025-02-01 00:00:00, NULL` | regular | off, on | Postgres | match |
| after_all_rows | SQL API: `SELECT DATE_TRUNC('month', ts) m, MEASURE(cum) c FROM u WHERE ts >= '2025-08-01' AND ts < '2025-10-01' GROUP BY 1 ORDER BY 1` | `2025-08-01 00:00:00, 11111; 2025-09-01 00:00:00, 11111`. All rows, with no new row. The buckets stay. | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__tableN.u__ts_month, __tableN.date_to). ; query id: <id>` | regular | off, on | ClickHouse | diff |
| | | | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression [Predcates: () Residual filter: ((greaterOrEquals(_CAST(__tableN.date_to,  ...` | regular | off, on | ClickHouse 25.3-alpine | diff |
| | | | `2025-08-01 00:00:00, 11111; 2025-09-01 00:00:00, 11111` | regular | off, on | Postgres | match |
| no_bucket | SQL API: `SELECT MEASURE(cum) c FROM u WHERE ts >= '2025-04-01' AND ts < '2025-06-01'` | `1111`. With a range and no bucket: the window ends at the end of the range. Rows 1 to 4. | `1111` | regular | off, on | ClickHouse, ClickHouse 25.3-alpine, Postgres | match |
| far_bound_workaround | SQL API: `SELECT DATE_TRUNC('month', ts) m, MEASURE(cum_100y) c FROM u WHERE ts >= '2025-04-01' AND ts < '2025-06-01' GROUP BY 1 ORDER BY 1` | `2025-04-01 00:00:00, 111; 2025-05-01 00:00:00, 1111`. A far bound in place of unbounded. Everything up to 04-30: rows 1, 2, 3. Up to 05-31: also row 4. | `2025-04-01 00:00:00, 1222; 2025-05-01 00:00:00, 1222` | regular | off | ClickHouse | diff |
| | | | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression [Predcates: () Residual filter: ((less(_CAST(subDate(__tableN.date_to, _CA ...` | regular | off, on | ClickHouse 25.3-alpine | diff |
| | | | `2025-04-01 00:00:00, 111; 2025-05-01 00:00:00, 1111` | regular | see database | ClickHouse (on), Postgres (off, on) | match |
| rollup_hit | REST: `{"measures": ["ur.cum"], "timeDimensions": [{"dimension": "ur.ts", "granularity": "month", "dateRange": ["2025-04-01", "2025-05-31"]}]}` | `2025-04-01 00:00:00, 111; 2025-05-01 00:00:00, 1111`. The query runs in Cube Store if the rollup hits. Everything up to 04-30: rows 1, 2, 3. Up to 05-31: also row 4. | `2025-04-01 00:00:00, 111; 2025-05-01 00:00:00, 1111` | REST, rollup hit | off, on | ClickHouse, ClickHouse 25.3-alpine, Postgres | match |
| db_only_inequality_join | Database only: `SELECT d, count() FROM (SELECT toDateTime('2025-04-30 23:59:59','UTC') d UNION ALL SELECT toDateTime('2025-05-31 23:59:59','UTC')) t LEFT JOIN audit.ut o ON o.ts <= t.d GROUP BY 1 ORDER BY 1` | `2025-04-30 23:59:59, 3; 2025-05-31 23:59:59, 4`. The same join with no Cube: 3 rows up to 04-30, 4 up to 05-31. | error: `Received exception from server (version 26.8.36): Code: 403. DB::Exception: Received from localhost:9000. DB::Exception: Cannot determine join keys in JOIN ON expression  ...` | database | off, on | ClickHouse | diff |
| | | | error: `Received exception from server (version 25.3.14): Code: 403. DB::Exception: Received from localhost:9000. DB::Exception: Cannot determine join keys in JOIN ON expression  ...` | database | off, on | ClickHouse 25.3-alpine | diff |
| | | | `2025-04-30 23:59:59, 3; 2025-05-31 23:59:59, 4` | database | off, on | Postgres | match |
| db_only_inequality_old_analyzer | Database only: `SELECT d, count() FROM (SELECT toDateTime('2025-04-30 23:59:59','UTC') d UNION ALL SELECT toDateTime('2025-05-31 23:59:59','UTC')) t LEFT JOIN audit.ut o ON o.ts <= t.d GROUP BY 1 ORDER BY 1 SETTINGS enable_analyzer = 0` | `2025-04-30 23:59:59, 3; 2025-05-31 23:59:59, 4`. The same join with the old ClickHouse analyzer. | error: `Received exception from server (version 26.8.36): Code: 403. DB::Exception: Received from localhost:9000. DB::Exception: Unsupported JOIN ON conditions. Unexpected 'ts <= ...` | database | off, on | ClickHouse | diff |
| | | | error: `Received exception from server (version 25.3.14): Code: 403. DB::Exception: Received from localhost:9000. DB::Exception: Unsupported JOIN ON conditions. Unexpected 'ts <= ...` | database | off, on | ClickHouse 25.3-alpine | diff |
| | | | `2025-04-30 23:59:59, 3; 2025-05-31 23:59:59, 4` | database | off, on | Postgres | match |
| db_only_inequality_join_condition_setting | Database only: `SELECT d, count() FROM (SELECT toDateTime('2025-04-30 23:59:59','UTC') d UNION ALL SELECT toDateTime('2025-05-31 23:59:59','UTC')) t LEFT JOIN audit.ut o ON o.ts <= t.d GROUP BY 1 ORDER BY 1 SETTINGS allow_experimental_join_condition = 1` | `2025-04-30 23:59:59, 3; 2025-05-31 23:59:59, 4`. The same join with the setting for join conditions. | error: `Received exception from server (version 26.8.36): Code: 403. DB::Exception: Received from localhost:9000. DB::Exception: Cannot determine join keys in JOIN ON expression  ...` | database | off, on | ClickHouse | diff |
| | | | error: `Received exception from server (version 25.3.14): Code: 403. DB::Exception: Received from localhost:9000. DB::Exception: Cannot determine join keys in JOIN ON expression  ...` | database | off, on | ClickHouse 25.3-alpine | diff |
| | | | `2025-04-30 23:59:59, 3; 2025-05-31 23:59:59, 4` | database | off, on | Postgres | match |
| db_only_cross_join | Database only: `SELECT d, countIf(o.ts <= t.d) FROM (SELECT toDateTime('2025-04-30 23:59:59','UTC') d UNION ALL SELECT toDateTime('2025-05-31 23:59:59','UTC')) t CROSS JOIN audit.ut o GROUP BY 1 ORDER BY 1` | `2025-04-30 23:59:59, 3; 2025-05-31 23:59:59, 4`. A cross join with the condition after it, the form a fix can write. | `2025-04-30 23:59:59, 3; 2025-05-31 23:59:59, 4` | database | off, on | ClickHouse, ClickHouse 25.3-alpine, Postgres | match |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.0.0 | ch-off | two_months_sql | error: `Arrow error: Compute error: Error: Code: 403. DB::Exception: Cannot determine join keys in JOIN ON expression lessOrEquals(__tableN.u__ts_month, __tableN.date_to). (INVAL ...` | diff |
| v1.0.0 | ch-off | one_month_sql | error: `Arrow error: Compute error: Error: Code: 403. DB::Exception: Cannot determine join keys in JOIN ON expression lessOrEquals(__tableN.u__ts_month, __tableN.date_to). (INVAL ...` | diff |
| v1.6.76 | ch-off | two_months_sql | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__tableN.u__ts_month, __tableN.date_to). ; query id: <id>` | diff |
| v1.6.76 | ch-off | one_month_sql | error: `Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__tableN.u__ts_month, __tableN.date_to). ; query id: <id>` | diff |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. The [docs on rolling windows](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/measures.mdx#L459) name no limit for ClickHouse. [#9600](https://github.com/cube-js/cube/issues/9600) is open with reports from several users. The Cube repository has the ClickHouse test of this feature [skipped](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/test/integration/clickhouse/clickhouse-graph-builder.test.ts#L379), so nothing tests it.
2. **Is the expected value right?** Yes. A running total to the end of April is rows 1, 2, 3: 111. To the end of May: 1111. Postgres gives this through Cube. `db_only_cross_join` gives the counts directly on ClickHouse. The database itself refuses the join that Cube writes (`db_only_inequality_join`). `enable_analyzer = 0` and `allow_experimental_join_condition = 1` do not help. So ClickHouse behaves as it says, and the fault is the SQL that Cube sends to it.
3. **Is it misuse?** No. `trailing: unbounded` is the docs form. `running_total` is no longer a type since `v1.7.0` ([#11044](https://github.com/cube-js/cube/pull/11044)), so a model cannot use it instead.
4. **Master?** It still happens. [`multi_stage_rolling_window.rs`](https://github.com/cube-js/cube/blob/78ecad294/rust/cube/cubesqlplanner/cubesqlplanner/src/physical_plan_builder/processors/multi_stage_rolling_window.rs#L53) is identical in `v1.8.15` and master. No open pull request changes the join for ClickHouse. It also fails on `v1.6.76` and `v1.0.0` with ClickHouse 26.8.
5. **Does a real user reach it?** Yes. "Cumulative revenue by month for this year" is a common chart. The result is an error, not a wrong number. A rollup avoids it, as `rollup_hit` shows.

### geo

**A geo dimension in a view fails**

- **Verdict:** `confirmed`
- **Result for a user:** An error
- **Impact:** Each query that selects the member fails, on each database and planner. `/v1/meta` offers the member, so an agent can pick it. Two number members in the view avoid it.
- **Findings verdict:** `cube-bug`
- **Run:** `python3 audit.py geo`. 13 cases, 52 runs: 24 match the expected value, 28 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cube_rest_control | REST: `{"dimensions": ["places.id", "places.loc"], "order": {"places.id": "asc"}}` | `1, 10.5,20.5; 2, -30.25,0`. The dimension is the latitude and the longitude with a comma, as the cube itself returns it. Row 1: 10.5 and 20.5. Row 2: -30.25 and 0. Direct SQL: `1, 10.5, 20.5; 2, -30.25, 0`. | `1, 10.5,20.5; 2, -30.25,0` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| cube_sql_control | SQL API: `SELECT id, loc FROM places ORDER BY 1` | `1, 10.5,20.5; 2, -30.25,0`. The dimension is the latitude and the longitude with a comma, as the cube itself returns it. | `1, 10.5,20.5; 2, -30.25,0` | regular | off, on | ClickHouse, Postgres | match |
| view_list_rest | REST: `{"dimensions": ["v_list.id", "v_list.loc"], "order": {"v_list.id": "asc"}}` | `1, 10.5,20.5; 2, -30.25,0`. The dimension is the latitude and the longitude with a comma, as the cube itself returns it. The docs have no exclusion of geo in views. | error: `HTTP 500 TypeError: Cannot read properties of undefined (reading 'sql')` | rest | off, on | ClickHouse, Postgres | diff |
| view_star_rest | REST: `{"dimensions": ["v_star.id", "v_star.loc"], "order": {"v_star.id": "asc"}}` | `1, 10.5,20.5; 2, -30.25,0`. The dimension is the latitude and the longitude with a comma, as the cube itself returns it. With includes: *. | error: `HTTP 500 TypeError: Cannot read properties of undefined (reading 'sql')` | rest | off, on | ClickHouse, Postgres | diff |
| view_alias_rest | REST: `{"dimensions": ["v_alias.id", "v_alias.where_at"], "order": {"v_alias.id": "asc"}}` | `1, 10.5,20.5; 2, -30.25,0`. The dimension is the latitude and the longitude with a comma, as the cube itself returns it. With an alias. | error: `HTTP 500 TypeError: Cannot read properties of undefined (reading 'sql')` | rest | off, on | ClickHouse, Postgres | diff |
| view_prefix_rest | REST: `{"dimensions": ["v_prefix.places_id", "v_prefix.places_loc"], "order": {"v_prefix.places_id": "asc"}}` | `1, 10.5,20.5; 2, -30.25,0`. The dimension is the latitude and the longitude with a comma, as the cube itself returns it. With a prefix. | error: `HTTP 500 TypeError: Cannot read properties of undefined (reading 'sql')` | rest | off, on | ClickHouse, Postgres | diff |
| view_list_sql | SQL API: `SELECT id, loc FROM v_list ORDER BY 1` | `1, 10.5,20.5; 2, -30.25,0`. The dimension is the latitude and the longitude with a comma, as the cube itself returns it. Through the SQL API. | error: `Database Execution Error: TypeError: Cannot read properties of undefined (reading 'sql')` | regular | off, on | ClickHouse, Postgres | diff |
| view_geo_only | REST: `{"dimensions": ["v_list.loc"], "order": {"v_list.loc": "asc"}}` | `-30.25,0; 10.5,20.5`. The dimension is the latitude and the longitude with a comma, as the cube itself returns it. No other member. | error: `HTTP 500 TypeError: Cannot read properties of undefined (reading 'sql')` | rest | off, on | ClickHouse, Postgres | diff |
| view_geo_and_measure | REST: `{"dimensions": ["v_list.loc"], "measures": ["v_list.n"], "order": {"v_list.loc": "asc"}}` | `-30.25,0, 1; 10.5,20.5, 1`. The dimension is the latitude and the longitude with a comma, as the cube itself returns it. With a count per place. | error: `HTTP 500 TypeError: Cannot read properties of undefined (reading 'sql')` | rest | off, on | ClickHouse, Postgres | diff |
| view_measure_only_control | REST: `{"measures": ["v_list.n"]}` | `2`. The view has a geo member, but this query does not use it. | `2` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| view_id_only_control | REST: `{"dimensions": ["v_list.id"], "order": {"v_list.id": "asc"}}` | `1; 2`. The same. | `1; 2` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| view_lat_lon_workaround | REST: `{"dimensions": ["v_nogeo.id", "v_nogeo.lat", "v_nogeo.lon"], "order": {"v_nogeo.id": "asc"}}` | `1, 10.5, 20.5; 2, -30.25, 0`. The two numbers as separate members of a view. | `1, 10.5, 20.5; 2, -30.25, 0` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| meta_lists_member | REST: `GET /v1/meta`, geo members of `v_list` | `v_list.loc, geo`. /v1/meta offers the member, so a client can pick it. | `v_list.loc, geo` | meta | off, on | ClickHouse, Postgres | match |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.0.0 | pg-off | cube_rest_control | `1, 10.5,20.5; 2, -30.25,0` | match |
| v1.0.0 | pg-off | view_list_rest | error: `HTTP 500 TypeError: Cannot read properties of undefined (reading 'sql')` | diff |
| v1.6.76 | pg-off | cube_rest_control | `1, 10.5,20.5; 2, -30.25,0` | match |
| v1.6.76 | pg-off | view_list_rest | error: `HTTP 500 TypeError: Cannot read properties of undefined (reading 'sql')` | diff |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. The [view docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/view.mdx) have no exclusion for geo. [`geo`](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/dimensions.mdx#L960) is a documented type. `meta_lists_member` shows that Cube offers the member. A `TypeError` from an undefined value is not a designed result. I found no test of geo through a view.
2. **Is the expected value right?** Yes. The same member of the cube returns `10.5,20.5` (`cube_rest_control`), so a view must return it too.
3. **Is it misuse?** No. Views are the documented public interface. `includes` as a list, `*`, an alias and a prefix all fail.
4. **Master?** It still happens. A [view dimension](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/compiler/CubeSymbols.ts#L1200) copies `sql` and other fields, with no `latitude` and `longitude`. The [`geo` branch](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/BaseQuery.ts#L3725) then reads `symbol.latitude.sql`. `CubeSymbols.ts` is identical in `v1.8.15` and master. I found no pull request for it.
5. **Does a real user reach it?** Yes, in a model that has a geo dimension and exposes views. "Show the location of each store" returns HTTP 500. It is an error, not a wrong number. `view_lat_lon_workaround` works.

### gran

**A custom granularity in months puts rows in the wrong bin**

- **Verdict:** `confirmed`
- **Result for a user:** Wrong number with no error
- **Impact:** A fiscal quarter with `offset` on a rollup by month gives wrong quarters for each row before the origin. On Postgres with no rollup, month intervals drift, and an old origin moves rows by more than one bin.
- **Findings verdict:** `cube-bug`
- **Run:** `python3 audit.py gran`. 18 cases, 72 runs: 49 match the expected value, 23 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| offset_no_rollup | REST: `{"measures": ["fn.rev"], "timeDimensions": [{"dimension": "fn.ts", "granularity": "fq_offset"}], "order": {"fn.ts": "asc"}}` | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000011; 2025-02-01 00:00:00, 1100; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000`. 3-month bins on February, May, August, November. 02-29 goes to Feb 2024. 11-01, 11-15, 01-31 go to Nov 2024. 02-01 and 04-30 go to Feb 2025. No rollup. | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000011; 2025-02-01 00:00:00, 1100; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000` | REST, no rollup | off, on | ClickHouse | match |
| | | | `2024-02-01 00:00:00, 1000000; 2024-08-01 00:00:00, 10000000; 2024-11-01 00:00:00, 111; 2025-02-01 00:00:00, 11000; 2025-08-01 00:00:00, 100000` | REST, no rollup | off, on | Postgres | diff |
| offset_rollup | REST: `{"measures": ["ff.rev"], "timeDimensions": [{"dimension": "ff.ts", "granularity": "fq_offset"}], "order": {"ff.ts": "asc"}}` | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000011; 2025-02-01 00:00:00, 1100; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000`. 3-month bins on February, May, August, November. 02-29 goes to Feb 2024. 11-01, 11-15, 01-31 go to Nov 2024. 02-01 and 04-30 go to Feb 2025. A rollup by month. | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000001; 2025-02-01 00:00:00, 110; 2025-05-01 00:00:00, 11000; 2025-08-01 00:00:00, 100000` | REST, rollup hit | off, on | ClickHouse, Postgres | diff |
| recent_origin_no_rollup | REST: `{"measures": ["fn.rev"], "timeDimensions": [{"dimension": "fn.ts", "granularity": "fq_recent"}], "order": {"fn.ts": "asc"}}` | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000011; 2025-02-01 00:00:00, 1100; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000`. 3-month bins on February, May, August, November. 02-29 goes to Feb 2024. 11-01, 11-15, 01-31 go to Nov 2024. 02-01 and 04-30 go to Feb 2025. No rollup. | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000011; 2025-02-01 00:00:00, 1100; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000` | REST, no rollup | off, on | ClickHouse | match |
| | | | `2024-02-01 00:00:00, 1000000; 2024-08-01 00:00:00, 10000000; 2024-11-01 00:00:00, 11; 2025-02-01 00:00:00, 11100; 2025-08-01 00:00:00, 100000` | REST, no rollup | off, on | Postgres | diff |
| recent_origin_rollup | REST: `{"measures": ["ff.rev"], "timeDimensions": [{"dimension": "ff.ts", "granularity": "fq_recent"}], "order": {"ff.ts": "asc"}}` | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000011; 2025-02-01 00:00:00, 1100; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000`. 3-month bins on February, May, August, November. 02-29 goes to Feb 2024. 11-01, 11-15, 01-31 go to Nov 2024. 02-01 and 04-30 go to Feb 2025. The origin is after rows 7, 8, 1, 2. | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000001; 2025-02-01 00:00:00, 1110; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000` | REST, rollup hit | off, on | ClickHouse, Postgres | diff |
| old_origin_no_rollup | REST: `{"measures": ["fn.rev"], "timeDimensions": [{"dimension": "fn.ts", "granularity": "fq_old"}], "order": {"fn.ts": "asc"}}` | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000011; 2025-02-01 00:00:00, 1100; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000`. 3-month bins on February, May, August, November. 02-29 goes to Feb 2024. 11-01, 11-15, 01-31 go to Nov 2024. 02-01 and 04-30 go to Feb 2025. No rollup. The origin 2000-02-01 is 25 years before the rows. | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000011; 2025-02-01 00:00:00, 1100; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000` | REST, no rollup | off, on | ClickHouse | match |
| | | | `2024-05-01 00:00:00, 1000000; 2025-02-01 00:00:00, 10000001; 2025-05-01 00:00:00, 110; 2025-08-01 00:00:00, 11000; 2025-11-01 00:00:00, 100000` | REST, no rollup | off, on | Postgres | diff |
| recent_origin_near_rows_no_rollup | REST: `{"measures": ["fn.rev"], "timeDimensions": [{"dimension": "fn.ts", "granularity": "fq_recent", "dateRange": ["2025-02-01", "2025-07-31"]}], "order": {"fn.ts": "asc"}}` | `2025-02-01 00:00:00, 1100; 2025-05-01 00:00:00, 10000`. Rows on and after the origin, no rollup. | `2025-02-01 00:00:00, 1100; 2025-05-01 00:00:00, 10000` | REST, no rollup | off, on | ClickHouse | match |
| | | | `2025-02-01 00:00:00, 11100` | REST, no rollup | off, on | Postgres | diff |
| recent_origin_one_interval_before_no_rollup | REST: `{"measures": ["fn.rev"], "timeDimensions": [{"dimension": "fn.ts", "granularity": "fq_recent", "dateRange": ["2024-11-01", "2024-11-30"]}], "order": {"fn.ts": "asc"}}` | `2024-11-01 00:00:00, 10000001`. Row 8 is exactly one calendar interval before the origin, 92 days. Row 1 is in the same bin. No rollup. | `2024-11-01 00:00:00, 10000001` | REST, no rollup | off, on | ClickHouse | match |
| | | | `2024-08-01 00:00:00, 10000000; 2024-11-01 00:00:00, 1` | REST, no rollup | off, on | Postgres | diff |
| old_origin_rollup | REST: `{"measures": ["ff.rev"], "timeDimensions": [{"dimension": "ff.ts", "granularity": "fq_old"}], "order": {"ff.ts": "asc"}}` | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000011; 2025-02-01 00:00:00, 1100; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000`. 3-month bins on February, May, August, November. 02-29 goes to Feb 2024. 11-01, 11-15, 01-31 go to Nov 2024. 02-01 and 04-30 go to Feb 2025. The origin 2000-02-01 is before all rows. It gives the same bins. | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000011; 2025-02-01 00:00:00, 1100; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000` | REST, rollup hit | off, on | ClickHouse, Postgres | match |
| recent_origin_after_origin_only | REST: `{"measures": ["ff.rev"], "timeDimensions": [{"dimension": "ff.ts", "granularity": "fq_recent", "dateRange": ["2025-02-01", "2025-07-31"]}], "order": {"ff.ts": "asc"}}` | `2025-02-01 00:00:00, 1100; 2025-05-01 00:00:00, 10000`. A range from the origin on: no row is before it. | `2025-02-01 00:00:00, 1100; 2025-05-01 00:00:00, 10000` | REST, rollup hit | off, on | ClickHouse, Postgres | match |
| recent_origin_one_row_before | REST: `{"measures": ["ff.rev"], "timeDimensions": [{"dimension": "ff.ts", "granularity": "fq_recent", "dateRange": ["2025-01-01", "2025-01-31"]}], "order": {"ff.ts": "asc"}}` | `2024-11-01 00:00:00, 10`. Only row 2, one second before the origin. Its bin ends at the origin. | `2025-02-01 00:00:00, 10` | REST, rollup hit | off, on | ClickHouse, Postgres | diff |
| recent_origin_on_bin_start | REST: `{"measures": ["ff.rev"], "timeDimensions": [{"dimension": "ff.ts", "granularity": "fq_recent", "dateRange": ["2024-11-01", "2024-11-30"]}], "order": {"ff.ts": "asc"}}` | `2024-11-01 00:00:00, 10000001`. Row 8 is exactly one interval before the origin, and row 1 is in the same bin. | `2024-11-01 00:00:00, 10000001` | REST, rollup hit | off, on | ClickHouse, Postgres | match |
| leap_day | REST: `{"measures": ["ff.rev"], "timeDimensions": [{"dimension": "ff.ts", "granularity": "fq_recent", "dateRange": ["2024-02-01", "2024-02-29"]}], "order": {"ff.ts": "asc"}}` | `2024-02-01 00:00:00, 1000000`. 02-29 is in the bin of Feb 2024, four intervals before the origin. | `2024-02-01 00:00:00, 1000000` | REST, rollup hit | off, on | ClickHouse, Postgres | match |
| month_interval_no_rollup | REST: `{"measures": ["fn.rev"], "timeDimensions": [{"dimension": "fn.ts", "granularity": "mon_recent"}], "order": {"fn.ts": "asc"}}` | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000001; 2025-01-01 00:00:00, 10; 2025-02-01 00:00:00, 100; 2025-04-01 00:00:00, 1000; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000`. Bins of one month: each row goes to the first of its month. | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000001; 2025-01-01 00:00:00, 10; 2025-02-01 00:00:00, 100; 2025-04-01 00:00:00, 1000; 2025-05-01 00:00:00, 10000; 202 ...` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| month_interval_rollup | REST: `{"measures": ["ff.rev"], "timeDimensions": [{"dimension": "ff.ts", "granularity": "mon_recent"}], "order": {"ff.ts": "asc"}}` | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000001; 2025-01-01 00:00:00, 10; 2025-02-01 00:00:00, 100; 2025-04-01 00:00:00, 1000; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000`. The same on the rollup. With an interval of one month, no division is left over. | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000001; 2025-01-01 00:00:00, 10; 2025-02-01 00:00:00, 100; 2025-04-01 00:00:00, 1000; 2025-05-01 00:00:00, 10000; 202 ...` | REST, rollup hit | off, on | ClickHouse, Postgres | match |
| days_no_rollup | REST: `{"measures": ["fn.rev"], "timeDimensions": [{"dimension": "fn.ts", "granularity": "wk_recent"}], "order": {"fn.ts": "asc"}}` | `2024-02-26 00:00:00, 1000000; 2024-10-28 00:00:00, 10000000; 2024-11-11 00:00:00, 1; 2025-01-27 00:00:00, 110; 2025-04-28 00:00:00, 11000; 2025-08-11 00:00:00, 100000`. 7-day bins on Mondays, because the origin is a Monday. Each row goes to the Monday on or before it. | `2024-02-26 00:00:00, 1000000; 2024-10-28 00:00:00, 10000000; 2024-11-11 00:00:00, 1; 2025-01-27 00:00:00, 110; 2025-04-28 00:00:00, 11000; 2025-08-11 00:00:00, 100000` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| days_rollup_origin_inside | REST: `{"measures": ["ff.rev"], "timeDimensions": [{"dimension": "ff.ts", "granularity": "wk_recent"}], "order": {"ff.ts": "asc"}}` | `2024-02-26 00:00:00, 1000000; 2024-10-28 00:00:00, 10000000; 2024-11-11 00:00:00, 1; 2025-01-27 00:00:00, 110; 2025-04-28 00:00:00, 11000; 2025-08-11 00:00:00, 100000`. 7-day bins on Mondays, because the origin is a Monday. Each row goes to the Monday on or before it. The rollup by day. Some rows are before the origin. | `2024-02-26 00:00:00, 1000000; 2024-10-28 00:00:00, 10000000; 2024-11-11 00:00:00, 1; 2025-01-27 00:00:00, 110; 2025-04-28 00:00:00, 11000; 2025-08-11 00:00:00, 100000` | REST, rollup hit | off, on | ClickHouse, Postgres | match |
| days_rollup_origin_after | REST: `{"measures": ["ff.rev"], "timeDimensions": [{"dimension": "ff.ts", "granularity": "wk_future"}], "order": {"ff.ts": "asc"}}` | `2024-02-26 00:00:00, 1000000; 2024-10-28 00:00:00, 10000000; 2024-11-11 00:00:00, 1; 2025-01-27 00:00:00, 110; 2025-04-28 00:00:00, 11000; 2025-08-11 00:00:00, 100000`. 7-day bins on Mondays, because the origin is a Monday. Each row goes to the Monday on or before it. 2025-12-29 is a Monday too, so the same bins, with all rows before the origin. | `2024-02-26 00:00:00, 1000000; 2024-10-28 00:00:00, 10000000; 2024-11-11 00:00:00, 1; 2025-01-27 00:00:00, 110; 2025-04-28 00:00:00, 11000; 2025-08-11 00:00:00, 100000` | REST, rollup hit | off, on | ClickHouse, Postgres | match |
| rest_time_zone | REST: `{"measures": ["ff.rev"], "timezone": "America/New_York", "timeDimensions": [{"dimension": "ff.ts", "granularity": "fq_old", "dateRange": ["2025-01-01", "2025-01-31"]}]}` | `2024-11-01 00:00:00, 110`. Another time zone with an old origin, the control. In New York, row 2 at 01-31 23:59:59 UTC and row 3 at 02-01 00:00 UTC are both in January. | `2024-11-01 00:00:00, 110` | REST, no rollup | off | ClickHouse | match |
| | | | `2024-11-01 00:00:00, 110` | REST, rollup hit | on | ClickHouse, Postgres | match |
| | | | `2025-05-01 00:00:00, 110` | REST, no rollup | off | Postgres | diff |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.0.0 | pg-off | recent_origin_no_rollup | `2024-02-01 00:00:00, 1000000; 2024-08-01 00:00:00, 10000000; 2024-11-01 00:00:00, 11; 2025-02-01 00:00:00, 11100; 2025-08-01 00:00:00, 100000` | diff |
| v1.0.0 | pg-off | recent_origin_rollup | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000001; 2025-02-01 00:00:00, 1110; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000` | diff |
| v1.6.76 | pg-off | recent_origin_no_rollup | `2024-02-01 00:00:00, 1000000; 2024-08-01 00:00:00, 10000000; 2024-11-01 00:00:00, 11; 2025-02-01 00:00:00, 11100; 2025-08-01 00:00:00, 100000` | diff |
| v1.6.76 | pg-off | recent_origin_rollup | `2024-02-01 00:00:00, 1000000; 2024-11-01 00:00:00, 10000001; 2025-02-01 00:00:00, 1110; 2025-05-01 00:00:00, 10000; 2025-08-01 00:00:00, 100000` | diff |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. The [docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/dimensions.mdx#L1108) say that the intervals shift so that one of them matches the origin. A row belongs to the interval that holds it. The Cube Store test [`date_bin`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubestore/cubestore-sql-tests/src/tests.rs#L7482) expects correct bins for times before the origin, but only when the month count is a multiple of the interval. So a time before the origin is a supported input, and the test misses this case. [#10085](https://github.com/cube-js/cube/issues/10085) was closed for inactivity and is about the default origin, not this.
2. **Is the expected value right?** Yes. I derived the bins by hand from the origin and the interval. ClickHouse with no rollup gives the same bins in `offset_no_rollup`, and so does a rollup with an old origin. The `7 days` cases show that only intervals in months fail.
3. **Is it misuse?** No. `offset` and `origin` are the docs form. The rollup hits by the normal rules, as the `usedPreAggregations` field shows.
4. **Master?** It still happens. [`calc_intervals`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubestore/cubestore/src/queryplanner/udfs.rs#L217) divides toward zero, so -1 month over 3 months gives 0 and the origin itself. [`PostgresQuery.dateBin`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/PostgresQuery.ts#L41) divides seconds by the seconds of the interval: `3 months` counts as 90 days, so the error grows with distance. Both files are identical in `v1.8.15` and master. [PR #11432](https://github.com/cube-js/cube/pull/11432) is open, and it is about single-unit intervals, which is a different path. `v1.6.76` and `v1.0.0` give the same wrong result.
5. **Does a real user reach it?** Yes. "Revenue by fiscal quarter" with the documented model. With `offset`, the origin is in the current year, so all earlier years fall before it. In `offset_rollup`, January joins the February quarter. `recent_origin_one_row_before` moves a row from the bin of November to the bin of February. A number is wrong with no error. On Postgres, `old_origin_no_rollup` puts a February row in the bin of May.

### big

**Numbers above 2^53 lose digits**

- **Verdict:** `confirmed-but-narrow`
- **Result for a user:** Wrong number with no error, in the low digits
- **Impact:** Only values above 2^53, or decimals with more than about 15 significant digits. The SQL API has this on each database by design of its type. REST has it on ClickHouse: integers on 26.8 but not on 25.3, and decimals on both.
- **Findings verdict:** `cube-bug`
- **Run:** `python3 audit.py big`. 18 cases, 90 runs: 49 match the expected value, 41 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| sql_2p53_control | SQL API: `SELECT u FROM bn WHERE id = 1` | `9007199254740992`. 2^53 fits a double. | `9007199254740992` | regular | see database | ClickHouse (off, on), ClickHouse 25.3-alpine (on), Postgres (off, on) | match |
| sql_2p53_plus1 | SQL API: `SELECT u FROM bn WHERE id = 2` | `9007199254740993`. 2^53+1. The row holds this value. Read it from the table. Direct SQL: `9007199254740993`. | `9007199254740992` | regular | see database | ClickHouse (off, on), ClickHouse 25.3-alpine (on), Postgres (off, on) | diff |
| sql_uint64_max | SQL API: `SELECT u FROM bn WHERE id = 3` | `18446744073709551615`. UInt64 max in the table. Direct SQL: `18446744073709551615`. | `18446744073709552000` | regular | see database | ClickHouse (off, on), ClickHouse 25.3-alpine (on), Postgres (off, on) | diff |
| sql_negative | SQL API: `SELECT i FROM bn WHERE id = 2` | `-9007199254740993`. Int64, negative. Direct SQL: `-9007199254740993`. | `-9007199254740992` | regular | see database | ClickHouse (off, on), ClickHouse 25.3-alpine (on), Postgres (off, on) | diff |
| sql_decimal | SQL API: `SELECT dc FROM bn WHERE id = 2` | `3000000000000.00000003`. Decimal(38,8): 21 significant digits. Direct SQL: `3000000000000.00000003`. | `3000000000000` | regular | see database | ClickHouse (off, on), ClickHouse 25.3-alpine (on), Postgres (off, on) | diff |
| sql_small_decimal_control | SQL API: `SELECT dc FROM bn WHERE id = 3` | `0.00000001`. A small decimal fits a double closely enough to print the same. | `0.00000001` | regular | see database | ClickHouse (off, on), ClickHouse 25.3-alpine (on), Postgres (off, on) | match |
| sql_measure_sum | SQL API: `SELECT MEASURE(sum_i) x FROM bn WHERE id = 2` | `-9007199254740993`. A sum over one row. | `-9007199254740992` | regular | see database | ClickHouse (off, on), ClickHouse 25.3-alpine (on), Postgres (off, on) | diff |
| sql_expression_pushdown | SQL API: `SELECT u + 0 x FROM bn WHERE id = 2` | `9007199254740993`. An expression on the column. Cube can push it down. | `9007199254740992` | post-processing | see database | ClickHouse (off, on), ClickHouse 25.3-alpine (on), Postgres (off, on) | diff |
| sql_float_control | SQL API: `SELECT f FROM bn WHERE id = 3` | `9007199254740992`. A Float64 cannot hold 2^53+1. Rounding is correct here, so this is the control. The table has 9007199254740992. Direct SQL: `9007199254740992`. | `9007199254740992` | regular | see database | ClickHouse (off, on), ClickHouse 25.3-alpine (on), Postgres (off, on) | match |
| sql_text_workaround | SQL API: `SELECT u_text FROM bn WHERE id = 2` | `9007199254740993`. The docs have no number type wider than double. A string member keeps each digit. | `9007199254740993` | regular | see database | ClickHouse (off, on), ClickHouse 25.3-alpine (on), Postgres (off, on) | match |
| sql_text_decimal_workaround | SQL API: `SELECT dc_text FROM bn WHERE id = 2` | `3000000000000.00000003`. The same for a decimal. | `3000000000000.00000003` | regular | see database | ClickHouse (off, on), ClickHouse 25.3-alpine (on), Postgres (off, on) | match |
| rest_2p53_plus1 | REST: `{"dimensions": ["bn.u"], "filters": [{"member": "bn.id", "operator": "equals", "values": ["2"]}]}` | `9007199254740993`. REST returns numbers as strings, so it can keep each digit. | `9007199254740992` | REST, no rollup | off, on | ClickHouse | diff |
| | | | `9007199254740993` | REST, no rollup | see database | ClickHouse 25.3-alpine (on), Postgres (off, on) | match |
| rest_uint64_max | REST: `{"dimensions": ["bn.u"], "filters": [{"member": "bn.id", "operator": "equals", "values": ["3"]}]}` | `18446744073709551615`. The same for UInt64 max. | `18446744073709552000` | REST, no rollup | off, on | ClickHouse | diff |
| | | | `18446744073709551615` | REST, no rollup | see database | ClickHouse 25.3-alpine (on), Postgres (off, on) | match |
| rest_negative | REST: `{"dimensions": ["bn.i"], "filters": [{"member": "bn.id", "operator": "equals", "values": ["2"]}]}` | `-9007199254740993`. The same for Int64. | `-9007199254740992` | REST, no rollup | off, on | ClickHouse | diff |
| | | | `-9007199254740993` | REST, no rollup | see database | ClickHouse 25.3-alpine (on), Postgres (off, on) | match |
| rest_decimal | REST: `{"dimensions": ["bn.dc"], "filters": [{"member": "bn.id", "operator": "equals", "values": ["2"]}]}` | `3000000000000.00000003`. The same for a Decimal. | `3000000000000` | REST, no rollup | see database | ClickHouse (off, on), ClickHouse 25.3-alpine (on) | diff |
| | | | `3000000000000.00000003` | REST, no rollup | off, on | Postgres | match |
| rest_measure_sum | REST: `{"measures": ["bn.sum_i"], "filters": [{"member": "bn.id", "operator": "equals", "values": ["2"]}]}` | `-9007199254740993`. A sum over one row. | `-9007199254740992` | REST, no rollup | off, on | ClickHouse | diff |
| | | | `-9007199254740993` | REST, no rollup | see database | ClickHouse 25.3-alpine (on), Postgres (off, on) | match |
| rest_small_control | REST: `{"measures": ["bn.sum_small"]}` | `6`. 1+2+3. | `6` | REST, no rollup | see database | ClickHouse (off, on), ClickHouse 25.3-alpine (on), Postgres (off, on) | match |
| sql_small_control | SQL API: `SELECT MEASURE(sum_small) x FROM bn` | `6`. 1+2+3. | `6` | regular | see database | ClickHouse (off, on), ClickHouse 25.3-alpine (on), Postgres (off, on) | match |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** Partly. The SQL API maps each [`number` member to `Double`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubesql/cubesql/src/transport/ext.rs#L104) on purpose, so the wire type is honest. No doc tells the user about the loss. REST is different: the [`castNumerics` recipe](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/recipes/core-data-api/cast-numerics.mdx) says that the REST API keeps big numbers as strings, and only the client option loses digits. A maintainer closed the same REST symptom on BigQuery [as a fix](https://github.com/cube-js/cube/issues/9136). So REST on ClickHouse is a bug. [#8741](https://github.com/cube-js/cube/issues/8741) is open on this topic.
2. **Is the expected value right?** Yes. Direct SQL returns `9007199254740993`. `sql_float_control` shows the correct case for rounding: a Float64 holds 2^53, so rounding is right there and my check does not call it a defect.
3. **Is it misuse?** Partly. A large id as `type: number` is a weak model. `sql_text_workaround` keeps each digit with a string member. Nothing in the docs asks for that.
4. **Master?** It still happens. [`ext.rs`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubesql/cubesql/src/transport/ext.rs#L104) and [`ClickHouseDriver.ts`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-clickhouse-driver/src/ClickHouseDriver.ts#L354) are identical in `v1.8.15` and master. [PR #11794](https://github.com/cube-js/cube/pull/11794) is open and is about the type parser. I did not check that it changes this result. The ClickHouse 25.3 run returns integers with all digits through REST, and 26.8 does not. I read this as a change of the default for quoting 64-bit integers in JSON, and I did not test the setting.
5. **Does a real user reach it?** Rarely with amounts: a sum must pass 9 quadrillion. More often with UInt64 ids and hashes in `type: number`, and with long decimals. "Show the id of this event" gives a neighbouring id with no error. A join key taken from this value finds the wrong row.

### propagate

**Two sub_query dimensions with the same SQL share one subquery**

- **Verdict:** `confirmed`
- **Result for a user:** Wrong number with no error
- **Impact:** On Tesseract, the default, "all orders" next to "orders that match the filter" shows the same number twice. The first column decides which.
- **Findings verdict:** `cube-bug`
- **Run:** `python3 audit.py propagate`. 15 cases, 60 runs: 48 match the expected value, 12 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| propagated_alone | REST: `{"dimensions": ["customers.id", "customers.filt_n"], "order": {"customers.id": "asc"}, "filters": [{"member": "purchases.st", "operator": "equals", "values": ["paid"]}]}` | `1, 2`. Customer 1 has 2 paid purchases, no one else has any. Direct SQL: `1, 2`. | `1, 2` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| plain_alone | REST: `{"dimensions": ["customers.id", "customers.all_n"], "order": {"customers.id": "asc"}, "filters": [{"member": "purchases.st", "operator": "equals", "values": ["paid"]}]}` | `1, 3`. Customer 1 passes the filter. The dimension with no propagation counts all 3 of the purchases. Direct SQL: `3`. | `1, 3` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| plain_then_propagated | REST: `{"dimensions": ["customers.id", "customers.all_n", "customers.filt_n"], "order": {"customers.id": "asc"}, "filters": [{"member": "purchases.st", "operator": "equals", "values": ["paid"]}]}` | `1, 3, 2`. All 3, of which 2 are paid. Each dimension keeps its meaning next to the other. | `1, 3, 2` | REST, no rollup | off | ClickHouse, Postgres | match |
| | | | `1, 3, 3` | REST, no rollup | on | ClickHouse, Postgres | diff |
| propagated_then_plain | REST: `{"dimensions": ["customers.id", "customers.filt_n", "customers.all_n"], "order": {"customers.id": "asc"}, "filters": [{"member": "purchases.st", "operator": "equals", "values": ["paid"]}]}` | `1, 2, 3`. The same with the columns swapped. Column order must not change a value. | `1, 2, 3` | REST, no rollup | off | ClickHouse, Postgres | match |
| | | | `1, 2, 2` | REST, no rollup | on | ClickHouse, Postgres | diff |
| sums_same_sql | REST: `{"dimensions": ["customers.id", "customers.all_total", "customers.filt_total"], "order": {"customers.id": "asc"}, "filters": [{"member": "purchases.st", "operator": "equals", "values": ["paid"]}]}` | `1, 70, 30`. All: 10+20+40. Paid: 10+20. The same pair with sum. | `1, 70, 30` | REST, no rollup | off | ClickHouse, Postgres | match |
| | | | `1, 70, 70` | REST, no rollup | on | ClickHouse, Postgres | diff |
| other_sql | REST: `{"dimensions": ["customers.id", "customers.all_n", "customers.filt_other"], "order": {"customers.id": "asc"}, "filters": [{"member": "purchases.st", "operator": "equals", "values": ["paid"]}]}` | `1, 3, 2`. SQL that differs by + 0, with the same meaning. | `1, 3, 2` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| different_measures | REST: `{"dimensions": ["customers.id", "customers.all_total", "customers.filt_n"], "order": {"customers.id": "asc"}, "filters": [{"member": "purchases.st", "operator": "equals", "values": ["paid"]}]}` | `1, 70, 2`. Different measures: no shared SQL. | `1, 70, 2` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| two_propagated_control | REST: `{"dimensions": ["customers.id", "customers.filt_n", "customers.filt_n2"], "order": {"customers.id": "asc"}, "filters": [{"member": "purchases.st", "operator": "equals", "values": ["paid"]}]}` | `1, 2, 2`. Both with propagation, so sharing a subquery gives the right number. | `1, 2, 2` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| two_plain_control | REST: `{"dimensions": ["customers.id", "customers.all_n", "customers.all_n2"], "order": {"customers.id": "asc"}, "filters": [{"member": "purchases.st", "operator": "equals", "values": ["paid"]}]}` | `1, 3, 3`. Both with no propagation. | `1, 3, 3` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| other_status | REST: `{"dimensions": ["customers.id", "customers.all_n", "customers.filt_n"], "order": {"customers.id": "asc"}, "filters": [{"member": "purchases.st", "operator": "equals", "values": ["open"]}]}` | `1, 3, 1; 2, 1, 1`. Open: customer 1 has 1 of 3, customer 2 has 1 of 1. | `1, 3, 1; 2, 1, 1` | REST, no rollup | off | ClickHouse, Postgres | match |
| | | | `1, 3, 3; 2, 1, 1` | REST, no rollup | on | ClickHouse, Postgres | diff |
| no_match | REST: `{"dimensions": ["customers.id", "customers.all_n", "customers.filt_n"], "order": {"customers.id": "asc"}, "filters": [{"member": "purchases.st", "operator": "equals", "values": ["nope"]}]}` | `no rows`. No purchase has this status, so no customer passes. | `no rows` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| no_filter_control | REST: `{"dimensions": ["customers.id", "customers.all_n", "customers.filt_n"], "order": {"customers.id": "asc"}}` | `1, 3, 3; 2, 1, 1; 3, 0, 0`. With no filter, both are the same. Customer 3 has zero purchases. The subquery left-joins from customers, so the count is 0. | `1, 3, 3; 2, 1, 1; 3, 0, 0` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| sql_view_together | SQL API: `SELECT id, all_n, filt_n FROM cv WHERE st = 'paid' GROUP BY 1, 2, 3 ORDER BY 1` | `1, 3, 2`. The matrix claim: in one SQL API query. | `1, 3, 2` | regular | off | ClickHouse, Postgres | match |
| | | | `1, 3, 3` | regular | on | ClickHouse, Postgres | diff |
| sql_view_swapped | SQL API: `SELECT id, filt_n, all_n FROM cv WHERE st = 'paid' GROUP BY 1, 2, 3 ORDER BY 1` | `1, 2, 3`. The same with the columns swapped. | `1, 2, 3` | regular | off | ClickHouse, Postgres | match |
| | | | `1, 2, 2` | regular | on | ClickHouse, Postgres | diff |
| sql_view_other_sql | SQL API: `SELECT id, all_n, filt_other FROM cv WHERE st = 'paid' GROUP BY 1, 2, 3 ORDER BY 1` | `1, 3, 2`. SQL that differs by + 0. | `1, 3, 2` | regular | off, on | ClickHouse, Postgres | match |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.5.0 | pg-on | plain_then_propagated | `1, 3, 3` | diff |
| v1.5.0 | pg-on | other_sql | `1, 3, 2` | match |
| v1.6.76 | pg-on | plain_then_propagated | `1, 3, 3` | diff |
| v1.6.76 | pg-on | other_sql | `1, 3, 2` | match |
| v1.7.0 | pg-on | plain_then_propagated | `1, 3, 3` | diff |
| v1.7.0 | pg-on | other_sql | `1, 3, 2` | match |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. The [docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/dimensions.mdx#L787) say that the option passes filters to the subquery of that dimension. Swapping two columns must not change a value, and `plain_then_propagated` and `propagated_then_plain` differ. The legacy planner gives the correct result on both databases.
2. **Is the expected value right?** Yes. Customer 1 has 3 purchases, 2 of them paid. Direct SQL gives both counts. `other_sql` gets the correct result with SQL that differs only by `+ 0`, which has the same meaning.
3. **Is it misuse?** No. The model is the docs form twice. `different_measures` works, and so do `two_plain_control` and `two_propagated_control`, where sharing is harmless.
4. **Master?** It still happens. [`builder.rs`](https://github.com/cube-js/cube/blob/78ecad294/rust/cube/cubesqlplanner/cubesqlplanner/src/physical_plan_builder/builder.rs#L213) finds the column by the measure, so the first subquery wins. The file is identical in `v1.8.15` and master. [PR #12502](https://github.com/cube-js/cube/pull/12502) is open and rewrites this path. It removes this lookup and it fixes a related case ("Measure read from the wrong subquery"). It does not name this case and I did not build it, so it can fix this, but that is not proved. With Tesseract on, `v1.5.0`, `v1.6.76` and `v1.7.0` also fail. So a default install has this since `v1.7.0`, when Tesseract became the default.
5. **Does a real user reach it?** Yes, but it needs a specific model: two dimensions on one measure, with one propagated. "Lifetime orders and orders in the selected status, per customer" is the plain use of the option. A number is wrong with no error.

### datetime64

**A time dimension on ClickHouse loses milliseconds**

- **Verdict:** `confirmed`
- **Result for a user:** Wrong number with no error
- **Impact:** On event tables, a filter inside one second misses rows, and rows in one second show the same time. The order of rows in one second is lost.
- **Findings verdict:** `cube-clickhouse-bug`
- **Run:** `python3 audit.py datetime64`. 19 cases, 76 runs: 58 match the expected value, 18 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| raw_sql | SQL API: `SELECT id, ts FROM e WHERE id IN (1,2,3) ORDER BY 1` | `1, 2025-01-01 10:00:00.007; 2, 2025-01-01 10:00:00.5; 3, 2025-01-01 10:00:00.999`. The column holds milliseconds. Direct SQL: `1, 2025-01-01 10:00:00.007; 2, 2025-01-01 10:00:00.500; 3, 2025-01-01 10:00:00.999`. | `1, 2025-01-01 10:00:00; 2, 2025-01-01 10:00:00; 3, 2025-01-01 10:00:00` | regular | off, on | ClickHouse | diff |
| | | | `1, 2025-01-01 10:00:00.007; 2, 2025-01-01 10:00:00.5; 3, 2025-01-01 10:00:00.999` | regular | off, on | Postgres | match |
| raw_rest | REST: `{"dimensions": ["e.id", "e.ts"], "order": {"e.id": "asc"}, "filters": [{"member": "e.id", "operator": "equals", "values": ["1", "2", "3"]}]}` | `1, 2025-01-01 10:00:00.007; 2, 2025-01-01 10:00:00.5; 3, 2025-01-01 10:00:00.999`. REST time values have the form .SSS, so 3 digits exist. | `1, 2025-01-01 10:00:00; 2, 2025-01-01 10:00:00; 3, 2025-01-01 10:00:00` | REST, no rollup | off, on | ClickHouse | diff |
| | | | `1, 2025-01-01 10:00:00.007; 2, 2025-01-01 10:00:00.5; 3, 2025-01-01 10:00:00.999` | REST, no rollup | off, on | Postgres | match |
| micro_rest | REST: `{"dimensions": ["e.id", "e.us"], "order": {"e.id": "asc"}, "filters": [{"member": "e.id", "operator": "equals", "values": ["1"]}]}` | `1, 2025-01-01 10:00:00.007`. REST has 3 digits, so .007123 can read as .007. Milliseconds stay. | `1, 2025-01-01 10:00:00` | REST, no rollup | off, on | ClickHouse | diff |
| | | | `1, 2025-01-01 10:00:00.007` | REST, no rollup | off, on | Postgres | match |
| filter_ms_window_sql | SQL API: `SELECT MEASURE(total) t FROM e WHERE ts >= '2025-01-01 10:00:00.500' AND ts < '2025-01-01 10:00:00.501'` | `10`. A 1 ms window around row 2. Direct SQL: `10`. | `0` | regular | off, on | ClickHouse | diff |
| | | | `10` | regular | off, on | Postgres | match |
| filter_ms_window_rest | REST: `{"measures": ["e.total"], "timeDimensions": [{"dimension": "e.ts", "dateRange": ["2025-01-01T10:00:00.500", "2025-01-01T10:00:00.500"]}]}` | `10`. The docs say that a range includes both ends. Row 2 is on both. | `0` | REST, no rollup | off, on | ClickHouse | diff |
| | | | `10` | REST, no rollup | off, on | Postgres | match |
| filter_gt_last_ms | SQL API: `SELECT MEASURE(total) t FROM e WHERE ts > '2025-01-01 10:00:00.999' AND ts <= '2025-01-01 10:00:01.000'` | `1000`. Row 3 is on the strict bound. Row 4 is on the upper bound. | `1000` | regular | off, on | ClickHouse, Postgres | match |
| filter_le_ms | SQL API: `SELECT MEASURE(total) t FROM e WHERE ts >= '2025-01-01 10:00:00.000' AND ts <= '2025-01-01 10:00:00.007'` | `1`. Row 1 is on the upper bound. Row 2 is after it. | `0` | regular | off, on | ClickHouse | diff |
| | | | `1` | regular | off, on | Postgres | match |
| filter_year_end | SQL API: `SELECT MEASURE(n) c FROM e WHERE ts <= '2024-12-31 23:59:59.998'` | `0`. Row 5 is 1 ms after the bound, so no row. Direct SQL: `0`. | `0` | regular | off, on | ClickHouse, Postgres | match |
| filter_on_ms_upper | SQL API: `SELECT MEASURE(n) c FROM e WHERE ts <= '2024-12-31 23:59:59.999'` | `1`. Row 5 is on the bound. Direct SQL: `1`. | `0` | regular | off, on | ClickHouse | diff |
| | | | `1` | regular | off, on | Postgres | match |
| midnight_day_filter | SQL API: `SELECT MEASURE(total) t FROM e WHERE ts >= '2025-01-01 00:00:00' AND ts < '2025-01-02 00:00:00'` | `1111`. A day range: rows 1 to 4. Row 5 is 1 ms before it, row 6 on the open end. Whole second bounds. Direct SQL: `1111`. | `1111` | regular | off, on | ClickHouse, Postgres | match |
| bucket_second_control | SQL API: `SELECT DATE_TRUNC('second', ts) s, MEASURE(total) t FROM e WHERE id IN (1,2,3,4) GROUP BY 1 ORDER BY 1` | `2025-01-01 10:00:00, 111; 2025-01-01 10:00:01, 1000`. Truncation to a second is what the query asks. | `2025-01-01 10:00:00, 111; 2025-01-01 10:00:01, 1000` | regular | off, on | ClickHouse, Postgres | match |
| bucket_day_control | SQL API: `SELECT DATE_TRUNC('day', ts) s, MEASURE(total) t FROM e GROUP BY 1 ORDER BY 1` | `2024-12-31 00:00:00, 10000; 2025-01-01 00:00:00, 1111; 2025-01-02 00:00:00, 100000`. Milliseconds do not move a row across midnight. | `2024-12-31 00:00:00, 10000; 2025-01-01 00:00:00, 1111; 2025-01-02 00:00:00, 100000` | regular | off, on | ClickHouse, Postgres | match |
| order_by_ms | SQL API: `SELECT id, ts_text FROM e WHERE id IN (1,2,3) ORDER BY ts_text DESC` | `3, 2025-01-01 10:00:00.999; 2, 2025-01-01 10:00:00.5; 1, 2025-01-01 10:00:00.007`. The order of the text form, a control. Postgres prints .500 as .5. | `3, 2025-01-01 10:00:00.999; 2, 2025-01-01 10:00:00.5; 1, 2025-01-01 10:00:00.007` | regular | off, on | ClickHouse, Postgres | match |
| order_by_time_selected | SQL API: `SELECT id, ts FROM e WHERE id IN (1,2,3) ORDER BY ts DESC` | `3, 2025-01-01 10:00:00.999; 2, 2025-01-01 10:00:00.5; 1, 2025-01-01 10:00:00.007`. The latest row first, with the milliseconds. | `1, 2025-01-01 10:00:00; 2, 2025-01-01 10:00:00; 3, 2025-01-01 10:00:00` | regular | off, on | ClickHouse | diff |
| | | | `3, 2025-01-01 10:00:00.999; 2, 2025-01-01 10:00:00.5; 1, 2025-01-01 10:00:00.007` | regular | off, on | Postgres | match |
| max_time_measure_rest | REST: `{"measures": ["e.last_time"], "filters": [{"member": "e.id", "operator": "equals", "values": ["1", "2", "3"]}]}` | `2025-01-01 10:00:00.999`. A measure of type time, as the docs ask for a time value. | `2025-01-01 10:00:00.999` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| max_time_measure_sql | SQL API: `SELECT MEASURE(last_time) x FROM e WHERE id IN (1,2,3)` | `2025-01-01 10:00:00.999`. The same through the SQL API. | `2025-01-01 10:00:00.999` | regular | off, on | ClickHouse, Postgres | match |
| max_measure_rest | REST: `{"measures": ["e.last_at"], "filters": [{"member": "e.id", "operator": "equals", "values": ["1", "2", "3"]}]}` | `2025-01-01 10:00:00.999`. The largest of the three. | `2025-01-01 10:00:00.999` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| time_zone_sql | SQL API: `SET TIME ZONE 'America/New_York'; SELECT id, ts FROM e WHERE id = 1` | `1, 2025-01-01 05:00:00.007`. UTC-5 in January. | `1, 2025-01-01 05:00:00` | regular | off, on | ClickHouse | diff |
| | | | `1, 2025-01-01 05:00:00.007` | regular | off, on | Postgres | match |
| text_workaround | SQL API: `SELECT ts_text FROM e WHERE id = 1` | `2025-01-01 10:00:00.007`. A string member with the same value. | `2025-01-01 10:00:00.007` | regular | off, on | ClickHouse, Postgres | match |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.0.0 | ch-off | raw_rest | `1, 2025-01-01 10:00:00; 2, 2025-01-01 10:00:00; 3, 2025-01-01 10:00:00` | diff |
| v1.0.0 | ch-off | filter_ms_window_rest | `0` | diff |
| v1.6.76 | ch-off | raw_rest | `1, 2025-01-01 10:00:00; 2, 2025-01-01 10:00:00; 3, 2025-01-01 10:00:00` | diff |
| v1.6.76 | ch-off | filter_ms_window_rest | `0` | diff |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. REST time values have the form `YYYY-MM-DDTHH:mm:ss.SSS`, as the [query format docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/rest-api/query-format.mdx#L641) say. [#9051](https://github.com/cube-js/cube/issues/9051) is open with the exact case. [PR #12046](https://github.com/cube-js/cube/pull/12046) and [PR #12519](https://github.com/cube-js/cube/pull/12519) add failing tests for it.
2. **Is the expected value right?** Yes. Direct SQL on ClickHouse returns `.007`. `filter_ms_window_sql` gives 10 by direct SQL. Cube gives 0 on ClickHouse.
3. **Is it misuse?** No. `DateTime64(3)` with `type: time` is the plain model. `text_workaround` keeps the value as a string, but a string is not a time for filters and buckets.
4. **Master?** It still happens. [`convertTz`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L91) writes `toDateTime64(field, 0)`. The file is identical in `v1.8.15` and master. No open pull request fixes it. `v1.6.76` and `v1.0.0` behave the same.
5. **Does a real user reach it?** Yes, on logs and events, where ClickHouse is common. "Requests in this one second" is a rare chart, but `filter_on_ms_upper` matters more: a bound taken from a row value, `.999`, misses that row and gives 0. A filter on whole seconds and a bucket by second or more are correct (`midnight_day_filter`, `bucket_second_control`). So most dashboards do not see it. The number is wrong with no error when a query does reach it.

### interval

**x + INTERVAL fails on ClickHouse when the query needs SQL generation**

- **Verdict:** `confirmed`
- **Result for a user:** An error
- **Impact:** `ts + INTERVAL` with an `ORDER BY`, a measure or a filter on the expression fails on the main target. With no `ORDER BY` it works. The `ORDER BY` form worked before `v1.7.27`.
- **Findings verdict:** `cube-clickhouse-bug`
- **Run:** `python3 audit.py interval`. 20 cases, 120 runs: 62 match the expected value, 58 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| raw_plus_day | SQL API: `SELECT id, ts + INTERVAL '1 day' x FROM it ORDER BY 1` | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59`. Each time plus one day. 2024 is a leap year, so 02-29 goes to 03-01. Direct SQL: `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59`. | error: `Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/i ...` | not-planned | off, on | ClickHouse | diff |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT id, ts + INTERVAL '1 day' AS x FROM it  ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| | | | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59` | pushdown | off, on | Postgres | match |
| raw_plus_day_no_order | SQL API: `SELECT id, ts + INTERVAL '1 day' x FROM it` | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59`. The same with no ORDER BY. | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59` | post-processing | off, on | ClickHouse, Postgres | match |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT id, ts + INTERVAL '1 day' AS x FROM it` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| raw_plus_day_where_order | SQL API: `SELECT id, ts + INTERVAL '1 day' x FROM it WHERE id = 2 ORDER BY 1` | `2, 2024-03-01 00:00:00`. One row, with a filter and an ORDER BY on id. | error: `Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/i ...` | not-planned | off, on | ClickHouse | diff |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT id, ts + INTERVAL '1 day' AS x FROM it  ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| | | | `2, 2024-03-01 00:00:00` | pushdown | off, on | Postgres | match |
| raw_plus_day_where | SQL API: `SELECT id, ts + INTERVAL '1 day' x FROM it WHERE id = 2` | `2, 2024-03-01 00:00:00`. One row, with a filter and no ORDER BY. | `2, 2024-03-01 00:00:00` | post-processing | off, on | ClickHouse, Postgres | match |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT id, ts + INTERVAL '1 day' AS x FROM it  ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| raw_plus_day_order_time | SQL API: `SELECT id, ts + INTERVAL '1 day' x FROM it ORDER BY 2` | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59`. ORDER BY the expression. | error: `Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/i ...` | not-planned | off, on | ClickHouse | diff |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT id, ts + INTERVAL '1 day' AS x FROM it  ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| | | | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59` | pushdown | off, on | Postgres | match |
| aggregated_no_order | SQL API: `SELECT ts + INTERVAL '1 day' x, MEASURE(n) c FROM it GROUP BY 1` | `2024-02-01 10:00:00, 1; 2024-03-01 00:00:00, 1; 2025-03-10 01:30:00, 1; 2026-01-01 23:59:59, 1`. The same with a measure and no ORDER BY. | error: `Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/i ...` | not-planned | off, on | ClickHouse | diff |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT ts + INTERVAL '1 day' AS x, MEASURE(n)  ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| | | | `2024-02-01 10:00:00, 1; 2025-03-10 01:30:00, 1; 2024-03-01 00:00:00, 1; 2026-01-01 23:59:59, 1` | pushdown | off, on | Postgres | match |
| raw_plus_day_order_limit | SQL API: `SELECT id, ts + INTERVAL '1 day' x FROM it ORDER BY 1 LIMIT 10` | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59`. ORDER BY and LIMIT. PR #11559 prefers pushdown only over post-processing with no limit. | error: `Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/i ...` | not-planned | off, on | ClickHouse | diff |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT id, ts + INTERVAL '1 day' AS x FROM it  ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| | | | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59` | pushdown | off, on | Postgres | match |
| aggregated_order_limit | SQL API: `SELECT ts + INTERVAL '1 day' x, MEASURE(n) c FROM it GROUP BY 1 ORDER BY 1 LIMIT 10` | `2024-02-01 10:00:00, 1; 2024-03-01 00:00:00, 1; 2025-03-10 01:30:00, 1; 2026-01-01 23:59:59, 1`. The same with a measure. | error: `Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/i ...` | not-planned | off, on | ClickHouse | diff |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT ts + INTERVAL '1 day' AS x, MEASURE(n)  ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| | | | `2024-02-01 10:00:00, 1; 2024-03-01 00:00:00, 1; 2025-03-10 01:30:00, 1; 2026-01-01 23:59:59, 1` | pushdown | off, on | Postgres | match |
| raw_minus_day | SQL API: `SELECT id, ts - INTERVAL '1 day' x FROM it WHERE id = 2` | `2, 2024-02-28 00:00:00`. The leap day minus one day. | `2, 2024-02-28 00:00:00` | post-processing | off, on | ClickHouse, Postgres | match |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT id, ts - INTERVAL '1 day' AS x FROM it  ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| raw_plus_month | SQL API: `SELECT id, ts + INTERVAL '1 month' x FROM it ORDER BY 1` | `1, 2024-02-29 10:00:00; 2, 2024-03-29 00:00:00; 3, 2025-04-09 01:30:00; 4, 2026-01-31 23:59:59`. Month end clamps: 01-31 goes to 02-29, as in the SQL standard and Postgres. | error: `Internal Error: Can't generate SQL for interval: Unsupported Error: Interval template generation is not supported QUERY: SELECT id, ts + INTERVAL '1 month' AS x FROM it O ...` | not-planned | off, on | ClickHouse | diff |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT id, ts + INTERVAL '1 month' AS x FROM i ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| | | | `1, 2024-02-29 10:00:00; 2, 2024-03-29 00:00:00; 3, 2025-04-09 01:30:00; 4, 2026-01-31 23:59:59` | pushdown | off, on | Postgres | match |
| raw_two_units | SQL API: `SELECT id, ts + INTERVAL '1 day 2 hours' x FROM it WHERE id = 3` | `3, 2025-03-10 03:30:00`. Two units in one interval. | `3, 2025-03-10 03:30:00` | post-processing | off, on | ClickHouse, Postgres | match |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT id, ts + INTERVAL '1 day 2 hours' AS x  ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| date_column | SQL API: `SELECT id, d + INTERVAL '1 day' x FROM it WHERE id = 1` | `1, 2024-02-01 00:00:00`. A Date column. | `1, 2024-02-01 00:00:00` | post-processing | off, on | ClickHouse, Postgres | match |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT id, d + INTERVAL '1 day' AS x FROM it W ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| aggregated | SQL API: `SELECT ts + INTERVAL '1 day' x, MEASURE(n) c FROM it GROUP BY 1 ORDER BY 1` | `2024-02-01 10:00:00, 1; 2024-03-01 00:00:00, 1; 2025-03-10 01:30:00, 1; 2026-01-01 23:59:59, 1`. The same with a measure. | error: `Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/i ...` | not-planned | off, on | ClickHouse | diff |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT ts + INTERVAL '1 day' AS x, MEASURE(n)  ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| | | | `2024-02-01 10:00:00, 1; 2024-03-01 00:00:00, 1; 2025-03-10 01:30:00, 1; 2026-01-01 23:59:59, 1` | pushdown | off, on | Postgres | match |
| bucket_plus | SQL API: `SELECT DATE_TRUNC('month', ts) + INTERVAL '1 month' x, MEASURE(n) c FROM it GROUP BY 1 ORDER BY 1` | `2024-02-01 00:00:00, 1; 2024-03-01 00:00:00, 1; 2025-04-01 00:00:00, 1; 2026-01-01 00:00:00, 1`. The next month of each bucket. | error: `Internal Error: Can't generate SQL for interval: Unsupported Error: Interval template generation is not supported QUERY: SELECT DATE_TRUNC('month', ts) + INTERVAL '1 mont ...` | not-planned | off, on | ClickHouse | diff |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT DATE_TRUNC('month', ts) + INTERVAL '1 m ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| | | | `2024-02-01 00:00:00, 1; 2024-03-01 00:00:00, 1; 2025-04-01 00:00:00, 1; 2026-01-01 00:00:00, 1` | pushdown | off, on | Postgres | match |
| filter_column_plus | SQL API: `SELECT MEASURE(n) c FROM it WHERE ts + INTERVAL '1 day' >= TIMESTAMP '2025-12-31 00:00:00'` | `1`. Only row 4. | error: `Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/i ...` | not-planned | off, on | ClickHouse | diff |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT MEASURE(n) AS c FROM it WHERE ts + INTE ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| | | | `1` | pushdown | off, on | Postgres | match |
| date_add_function | SQL API: `SELECT id, DATE_ADD(ts, INTERVAL '1 day') x FROM it ORDER BY 1` | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59`. The function form that the docs list. | error: `Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/i ...` | not-planned | off, on | ClickHouse | diff |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT id, DATE_ADD(ts, INTERVAL '1 day') AS x ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| | | | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59` | pushdown | off, on | Postgres | match |
| filter_constant_control | SQL API: `SELECT MEASURE(n) c FROM it WHERE ts >= TIMESTAMP '2025-01-02 00:00:00' - INTERVAL '1 day'` | `2`. An interval on a constant: rows 3, 4. A relative date filter has this form. | `2` | regular | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres (off, on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | match |
| outer_over_scan | SQL API: `SELECT m + INTERVAL '1 day' x, c FROM (SELECT DATE_TRUNC('year', ts) m, MEASURE(n) c FROM it GROUP BY 1) q ORDER BY 1` | `2024-01-02 00:00:00, 2; 2025-01-02 00:00:00, 2`. The interval on the outer query of a grouped subquery. | error: `Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/i ...` | not-planned | off, on | ClickHouse | diff |
| | | | `2024-01-02 00:00:00, 2; 2025-01-02 00:00:00, 2` | post-processing | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | match |
| | | | `2024-01-02 00:00:00, 2; 2025-01-02 00:00:00, 2` | pushdown | off, on | Postgres | match |
| time_zone_dst | SQL API: `SET TIME ZONE 'America/New_York'; SELECT id, ts + INTERVAL '1 day' x FROM it WHERE id = 3` | `3, 2025-03-09 20:30:00`. Row 3 at 01:30 UTC on 03-09 is 20:30 on 03-08 in New York, before the clock change at 02:00 local on 03-09. Plus one local day is 20:30 on 03-09. | `3, 2025-03-09 20:30:00` | post-processing | off, on | ClickHouse, Postgres | match |
| | | | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT id, ts + INTERVAL '1 day' AS x FROM it  ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| no_interval_control | SQL API: `SELECT id, ts x FROM it WHERE id = 1` | `1, 2024-01-31 10:00:00`. The same query with no interval. | `1, 2024-01-31 10:00:00` | regular | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres (off, on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | match |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.0.0 | ch-off | raw_plus_day | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59` | match |
| v1.0.0 | ch-off | date_add_function | error: `Error during rewrite: Can't detect Cube query and it may be not supported yet. Please check logs for additional information. QUERY: SELECT id, DATE_ADD(ts, INTERVAL '1 da ...` | diff |
| v1.6.76 | ch-off | raw_plus_day | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59` | match |
| v1.6.76 | ch-off | date_add_function | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT id, DATE_ADD(ts, INTERVAL '1 day') AS x ...` | diff |
| v1.7.15 | ch-off | raw_plus_day | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59` | match |
| v1.7.23 | ch-off | raw_plus_day | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59` | match |
| v1.7.25 | ch-off | raw_plus_day | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59` | match |
| v1.7.26 | ch-off | raw_plus_day | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59` | match |
| v1.7.26 | ch-on | raw_plus_day | `1, 2024-02-01 10:00:00; 2, 2024-03-01 00:00:00; 3, 2025-03-10 01:30:00; 4, 2026-01-01 23:59:59` | match |
| v1.7.26 | ch-on | date_add_function | error: `Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/i ...` | diff |
| v1.7.27 | ch-off | raw_plus_day | error: `Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/i ...` | diff |
| v1.7.32 | ch-off | raw_plus_day | error: `Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_date_part template: template not found: template "expressions/i ...` | diff |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. The [function table](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/reference.mdx#L481) lists `DATE_ADD` as supported. The docs describe a [failure for an expression that a data source cannot push down](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L162), but with another error text. Here the text is an internal `template not found`. The nested form that the docs give as the alternative also fails (`outer_over_scan`). So the documented way out does not work, which is the difference from [extract](#extract).
2. **Is the expected value right?** Yes. I derived the times by hand, including the leap day and the month end. Direct SQL on both databases agrees, and Postgres through Cube returns them.
3. **Is it misuse?** No. It is plain SQL. `raw_plus_day_no_order` shows that Cube accepts it, through post-processing.
4. **Master?** It still happens. [`service.rs`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubesql/cubesql/src/transport/service.rs#L951) needs a template, and [`ClickHouseQuery.sqlTemplates`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L316) has none. Both files are identical in `v1.8.15` and master. I found no pull request for it. `raw_plus_day` works on `v1.7.26` on both planners and fails on `v1.7.27`. That tag has [#11559](https://github.com/cube-js/cube/pull/11559), which prefers pushdown over post-processing, so the post-processing path that used to work is no longer taken. A `LIMIT` does not bring it back. `date_add_function` fails on each tag that I ran.
5. **Does a real user reach it?** Yes. "Each order date plus 30 days, sorted" is plain SQL. The result is a clear error with no wrong number. An interval on a constant works (`filter_constant_control`), so a relative date filter is not affected.

### extract

**EXTRACT next to a measure fails on ClickHouse**

- **Verdict:** `intended`
- **Result for a user:** An error, with a documented form that works
- **Impact:** The docs describe this error and give the nested form as the alternative. Only a non-additive measure by a part such as day of week has no other SQL API form.
- **Findings verdict:** `cube-clickhouse-bug`
- **Run:** `python3 audit.py extract`. 17 cases, 102 runs: 52 match the expected value, 50 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| month_sum | SQL API: `SELECT EXTRACT(MONTH FROM ts) p, MEASURE(total) v FROM xt GROUP BY 1 ORDER BY 1` | `1, 11; 2, 5; 3, 7; 12, 13`. January 11, February 5, March 7, December 13. Direct SQL: `1, 11; 2, 5; 3, 7; 12, 13`. | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT EXTRACT(MONTH FROM ts) AS p, MEASURE(to ...` | not-planned | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | diff |
| | | | `1, 11; 2, 5; 3, 7; 12, 13` | pushdown | off, on | Postgres | match |
| month_count | SQL API: `SELECT EXTRACT(MONTH FROM ts) p, MEASURE(n) v FROM xt GROUP BY 1 ORDER BY 1` | `1, 1; 2, 1; 3, 1; 12, 1`. One row per month. | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT EXTRACT(MONTH FROM ts) AS p, MEASURE(n) ...` | not-planned | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | diff |
| | | | `1, 1; 2, 1; 3, 1; 12, 1` | pushdown | off, on | Postgres | match |
| month_avg | SQL API: `SELECT EXTRACT(MONTH FROM ts) p, MEASURE(avg_a) v FROM xt GROUP BY 1 ORDER BY 1` | `1, 11; 2, 5; 3, 7; 12, 13`. One row per month, so the avg is the amount. | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT EXTRACT(MONTH FROM ts) AS p, MEASURE(av ...` | not-planned | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | diff |
| | | | `1, 11; 2, 5; 3, 7; 12, 13` | pushdown | off, on | Postgres | match |
| year | SQL API: `SELECT EXTRACT(YEAR FROM ts) p, MEASURE(total) v FROM xt GROUP BY 1 ORDER BY 1` | `2024, 12; 2025, 24`. 5+7 and 11+13. | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT EXTRACT(YEAR FROM ts) AS p, MEASURE(tot ...` | not-planned | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | diff |
| | | | `2024, 12; 2025, 24` | pushdown | off, on | Postgres | match |
| day | SQL API: `SELECT EXTRACT(DAY FROM ts) p, MEASURE(total) v FROM xt GROUP BY 1 ORDER BY 1` | `1, 11; 3, 7; 29, 5; 31, 13`. Leap day 29, month end 31. | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT EXTRACT(DAY FROM ts) AS p, MEASURE(tota ...` | not-planned | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | diff |
| | | | `1, 11; 3, 7; 29, 5; 31, 13` | pushdown | off, on | Postgres | match |
| hour | SQL API: `SELECT EXTRACT(HOUR FROM ts) p, MEASURE(total) v FROM xt GROUP BY 1 ORDER BY 1` | `0, 18; 23, 18`. Midnight rows 2, 3 and rows 1, 4 at hour 23. | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT EXTRACT(HOUR FROM ts) AS p, MEASURE(tot ...` | not-planned | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | diff |
| | | | `0, 18; 23, 18` | pushdown | off, on | Postgres | match |
| dow | SQL API: `SELECT EXTRACT(DOW FROM ts) p, MEASURE(total) v FROM xt GROUP BY 1 ORDER BY 1` | `0, 7; 3, 24; 4, 5`. Postgres DOW has Sunday as 0. 03-03 is a Sunday, 2025-01-01 and 12-31 are Wednesdays, 02-29 is a Thursday. | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT EXTRACT(DOW FROM ts) AS p, MEASURE(tota ...` | not-planned | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | diff |
| | | | `0, 7; 3, 24; 4, 5` | pushdown | off, on | Postgres | match |
| dow_avg_no_nested_form | SQL API: `SELECT EXTRACT(DOW FROM ts) p, MEASURE(avg_a) v FROM xt GROUP BY 1 ORDER BY 1` | `0, 7; 3, 12; 4, 5`. An avg by day of week. Wednesday: (11+13)/2. The docs say that a non-additive measure cannot use the nested form, so this query has no other SQL API form. | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT EXTRACT(DOW FROM ts) AS p, MEASURE(avg_ ...` | not-planned | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | diff |
| | | | `0, 7; 3, 12; 4, 5` | pushdown | off, on | Postgres | match |
| nested_avg_wrong_by_docs | SQL API: `SELECT EXTRACT(DOW FROM d) p, AVG(v) a FROM (SELECT DATE_TRUNC('day', ts) d, MEASURE(avg_a) v FROM xt GROUP BY 1) q GROUP BY 1 ORDER BY 1` | `0, 7; 3, 12; 4, 5`. The nested form with a day inside. Each day has one row here, so an average of averages happens to be correct. The docs warn against it in general. | `0, 7; 3, 12; 4, 5` | post-processing | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | match |
| | | | `0, 7; 3, 12; 4, 5` | pushdown | off, on | Postgres | match |
| quarter | SQL API: `SELECT EXTRACT(QUARTER FROM ts) p, MEASURE(total) v FROM xt GROUP BY 1 ORDER BY 1` | `1, 23; 4, 13`. 5+7+11 in Q1, 13 in Q4. | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT EXTRACT(QUARTER FROM ts) AS p, MEASURE( ...` | not-planned | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | diff |
| | | | `1, 23; 4, 13` | pushdown | off, on | Postgres | match |
| date_part_fn | SQL API: `SELECT date_part('month', ts) p, MEASURE(total) v FROM xt GROUP BY 1 ORDER BY 1` | `1, 11; 2, 5; 3, 7; 12, 13`. The function form. | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT date_part('month', ts) AS p, MEASURE(to ...` | not-planned | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | diff |
| | | | `1, 11; 2, 5; 3, 7; 12, 13` | pushdown | off, on | Postgres | match |
| raw_rows | SQL API: `SELECT id, EXTRACT(MONTH FROM ts) p FROM xt ORDER BY 1` | `1, 2; 2, 3; 3, 1; 4, 12`. No measure. | `1, 2; 2, 3; 3, 1; 4, 12` | post-processing | off, on | ClickHouse | match |
| | | | error: `Rewrite Error: Unexpected panic. Reason: Unexpected expression node: AggregateAggrExpr([]). Please check logs for additional information QUERY: SELECT id, EXTRACT(MONTH F ...` | not-planned | on | ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false`, Postgres, `CUBESQL_SQL_PUSH_DOWN=false` | diff |
| | | | `1, 2; 2, 3; 3, 1; 4, 12` | pushdown | off, on | Postgres | match |
| in_filter | SQL API: `SELECT MEASURE(total) v FROM xt WHERE EXTRACT(YEAR FROM ts) = 2024` | `12`. Rows 1, 2. | `12` | regular | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres (off, on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | match |
| date_column | SQL API: `SELECT EXTRACT(MONTH FROM d) p, MEASURE(total) v FROM xt GROUP BY 1 ORDER BY 1` | `1, 11; 2, 5; 3, 7; 12, 13`. A Date column. | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT EXTRACT(MONTH FROM d) AS p, MEASURE(tot ...` | not-planned | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | diff |
| | | | `1, 11; 2, 5; 3, 7; 12, 13` | pushdown | off, on | Postgres | match |
| outer_over_scan | SQL API: `SELECT EXTRACT(YEAR FROM m) y, SUM(v) s FROM (SELECT DATE_TRUNC('month', ts) m, MEASURE(total) v FROM xt GROUP BY 1) q GROUP BY 1 ORDER BY 1` | `2024, 12; 2025, 24`. The expression is on the outer query. A grouped inner query gives it month rows. | `2024, 12; 2025, 24` | post-processing | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | match |
| | | | `2024, 12; 2025, 24` | pushdown | off, on | Postgres | match |
| time_zone | SQL API: `SET TIME ZONE 'America/New_York'; SELECT EXTRACT(MONTH FROM ts) p, MEASURE(total) v FROM xt GROUP BY 1 ORDER BY 1` | `2, 5; 3, 7; 12, 24`. In New York, UTC-5: 2025-01-01 00:00 UTC is in December 2024. The month is 12 for rows 3 and 4. | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT EXTRACT(MONTH FROM ts) AS p, MEASURE(to ...` | not-planned | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | diff |
| | | | `2, 5; 3, 7; 12, 24` | pushdown | off, on | Postgres | match |
| trunc_control | SQL API: `SELECT DATE_TRUNC('year', ts) p, MEASURE(total) v FROM xt GROUP BY 1 ORDER BY 1` | `2024-01-01 00:00:00, 12; 2025-01-01 00:00:00, 24`. DATE_TRUNC with the same data. | `2024-01-01 00:00:00, 12; 2025-01-01 00:00:00, 24` | regular | see database | ClickHouse (off, on), ClickHouse, `CUBESQL_SQL_PUSH_DOWN=false` (on), Postgres (off, on), Postgres, `CUBESQL_SQL_PUSH_DOWN=false` (on) | match |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.0.0 | ch-off | month_sum | error: `Error during rewrite: Can't detect Cube query and it may be not supported yet. Please check logs for additional information. QUERY: SELECT EXTRACT(MONTH FROM ts) AS p, ME ...` | diff |
| v1.0.0 | ch-off | raw_rows | `1, 2; 2, 3; 3, 1; 4, 12` | match |
| v1.6.76 | ch-off | month_sum | error: `Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional information QUERY: SELECT EXTRACT(MONTH FROM ts) AS p, MEASURE(to ...` | diff |
| v1.6.76 | ch-off | raw_rows | `1, 2; 2, 3; 3, 1; 4, 12` | match |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** Yes. The [docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L162) use EXTRACT as their example. They say that such a query fails with `Can't detect Cube query and it may be not supported yet` if the data source does not support pushing down an expression. They then give the [nested form](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L172). One doc points the other way: the [function table](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/reference.mdx#L484) lists EXTRACT with inner projections, with no note about the data source.
2. **Is the expected value right?** Yes, for the value: direct SQL on both databases gives the months and sums. The query failing is the documented result, so the expected value in my table is stricter than the docs.
3. **Is it misuse?** Partly. The docs form is `outer_over_scan`, which gives the correct result on ClickHouse. `raw_rows` and `in_filter` also work.
4. **Master?** The behaviour is the same. The [rule](https://github.com/cube-js/cube/blob/78ecad294/rust/cubesql/cubesql/src/compile/rewrite/rules/wrapper/extract.rs#L76) needs the template `expressions/extract`, and [`ClickHouseQuery.sqlTemplates`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L316) has none. The file is identical in `v1.8.15` and master. `v1.6.76` and `v1.0.0` behave the same. [#8313](https://github.com/cube-js/cube/issues/8313) is closed for Postgres, where the template exists.
5. **Does a real user reach it?** Yes, and it is a clear error, not a wrong number. "Revenue by month of year" works when written in the nested form. The gap that is left is `dow_avg_no_nested_form`: the docs say that a non-additive measure needs pushdown, and pushdown refuses EXTRACT here. A template in the dialect closes this gap. That is a feature request for Cube.

### N1

**A rolling measure on Postgres starts its buckets at the filter date**

- **Verdict:** `confirmed`
- **Result for a user:** Wrong number with no error
- **Impact:** With the default planner on Postgres, a rolling measure by month with a range that starts inside a month gets shifted buckets. The labels and the values are both wrong. A relative range such as the last 90 days does this.
- **Findings verdict:** `cube-bug`
- **Run:** `python3 audit.py N1`. 13 cases, 52 runs: 34 match the expected value, 18 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| month_mid_start_rest | REST: `{"measures": ["nt.r30"], "timeDimensions": [{"dimension": "nt.ts", "granularity": "month", "dateRange": ["2025-04-15", "2025-05-31"]}], "order": {"nt.ts": "asc"}}` | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000`. A month bucket starts on the first, as DATE_TRUNC and the non-rolling control do. 30 days up to 04-30: rows 2, 3. 30 days up to 05-31: rows 4, 5. | `2025-04-01 00:00:00, 11110; 2025-05-01 00:00:00, 11110` | REST, no rollup | off | ClickHouse | diff |
| | | | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000` | REST, no rollup | see database | ClickHouse (on), Postgres (off) | match |
| | | | `2025-04-15 00:00:00, 1100; 2025-05-15 00:00:00, 110000` | REST, no rollup | on | Postgres | diff |
| month_first_start_control | REST: `{"measures": ["nt.r30"], "timeDimensions": [{"dimension": "nt.ts", "granularity": "month", "dateRange": ["2025-04-01", "2025-05-31"]}], "order": {"nt.ts": "asc"}}` | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000`. A month bucket starts on the first, as DATE_TRUNC and the non-rolling control do. 30 days up to 04-30: rows 2, 3. 30 days up to 05-31: rows 4, 5. The range starts on the first. | `2025-04-01 00:00:00, 11110; 2025-05-01 00:00:00, 11110` | REST, no rollup | off | ClickHouse | diff |
| | | | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000` | REST, no rollup | see database | ClickHouse (on), Postgres (off, on) | match |
| month_second_day | REST: `{"measures": ["nt.r30"], "timeDimensions": [{"dimension": "nt.ts", "granularity": "month", "dateRange": ["2025-04-02", "2025-05-31"]}], "order": {"nt.ts": "asc"}}` | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000`. A month bucket starts on the first, as DATE_TRUNC and the non-rolling control do. 30 days up to 04-30: rows 2, 3. 30 days up to 05-31: rows 4, 5. One day after the first. | `2025-04-01 00:00:00, 11110; 2025-05-01 00:00:00, 11110` | REST, no rollup | off | ClickHouse | diff |
| | | | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000` | REST, no rollup | see database | ClickHouse (on), Postgres (off) | match |
| | | | `2025-04-02 00:00:00, 110; 2025-05-02 00:00:00, 11000` | REST, no rollup | on | Postgres | diff |
| month_mid_start_sql | SQL API: `SELECT DATE_TRUNC('month', ts) m, MEASURE(r30) r FROM nt WHERE ts >= '2025-04-15' AND ts < '2025-06-01' GROUP BY 1 ORDER BY 1` | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000`. A month bucket starts on the first, as DATE_TRUNC and the non-rolling control do. 30 days up to 04-30: rows 2, 3. 30 days up to 05-31: rows 4, 5. | `2025-04-01 00:00:00, 11110; 2025-05-01 00:00:00, 11110` | regular | off | ClickHouse | diff |
| | | | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000` | regular | see database | ClickHouse (on), Postgres (off) | match |
| | | | `2025-04-15 00:00:00, 1100; 2025-05-15 00:00:00, 110000` | regular | on | Postgres | diff |
| month_end_start | REST: `{"measures": ["nt.r30"], "timeDimensions": [{"dimension": "nt.ts", "granularity": "month", "dateRange": ["2025-01-31", "2025-03-31"]}], "order": {"nt.ts": "asc"}}` | `2025-01-01 00:00:00, NULL; 2025-02-01 00:00:00, NULL; 2025-03-01 00:00:00, 1`. Starts on a month end, which a step of a month can move. 3 buckets. Only row 1 is in 30 days to 03-31. | `2025-01-01 00:00:00, 1; 2025-02-01 00:00:00, 1; 2025-03-01 00:00:00, 1` | REST, no rollup | off | ClickHouse | diff |
| | | | `2025-01-01 00:00:00, NULL; 2025-02-01 00:00:00, NULL; 2025-03-01 00:00:00, 1` | REST, no rollup | see database | ClickHouse (on), Postgres (off) | match |
| | | | `2025-01-31 00:00:00, NULL; 2025-02-28 00:00:00, 1; 2025-03-28 00:00:00, 110` | REST, no rollup | on | Postgres | diff |
| leap_day_start | REST: `{"measures": ["nt.r30"], "timeDimensions": [{"dimension": "nt.ts", "granularity": "month", "dateRange": ["2024-02-29", "2024-03-31"]}], "order": {"nt.ts": "asc"}}` | `2024-02-01 00:00:00, NULL; 2024-03-01 00:00:00, NULL`. Starts on a leap day. 2 buckets, no rows. | `2024-02-01 00:00:00, NULL; 2024-03-01 00:00:00, NULL` | REST, no rollup | see database | ClickHouse (off, on), Postgres (off) | match |
| | | | `2024-02-29 00:00:00, NULL; 2024-03-29 00:00:00, NULL` | REST, no rollup | on | Postgres | diff |
| week_mid_start | REST: `{"measures": ["nt.r30"], "timeDimensions": [{"dimension": "nt.ts", "granularity": "week", "dateRange": ["2025-05-07", "2025-05-20"]}], "order": {"nt.ts": "asc"}}` | `2025-05-05 00:00:00, 1100; 2025-05-12 00:00:00, 1100; 2025-05-19 00:00:00, 11000`. Weeks start on Monday. To 05-11: rows 3, 4. To 05-18: rows 3, 4. To 05-25: rows 4, 5. | `2025-05-05 00:00:00, 13200; 2025-05-12 00:00:00, 13200; 2025-05-19 00:00:00, 13200` | REST, no rollup | off | ClickHouse | diff |
| | | | `2025-05-05 00:00:00, 1100; 2025-05-12 00:00:00, 1100; 2025-05-19 00:00:00, 11000` | REST, no rollup | see database | ClickHouse (on), Postgres (off) | match |
| | | | `2025-05-07 00:00:00, 1100; 2025-05-14 00:00:00, 1000` | REST, no rollup | on | Postgres | diff |
| year_mid_start | REST: `{"measures": ["nt.r30"], "timeDimensions": [{"dimension": "nt.ts", "granularity": "year", "dateRange": ["2025-03-15", "2025-06-30"]}], "order": {"nt.ts": "asc"}}` | `2025-01-01 00:00:00, NULL`. The year bucket ends on 12-31, with no row in the 30 days before. | `2025-01-01 00:00:00, NULL` | REST, no rollup | see database | ClickHouse (off, on), Postgres (off) | match |
| | | | `2025-03-15 00:00:00, NULL` | REST, no rollup | on | Postgres | diff |
| day_control | REST: `{"measures": ["nt.r30"], "timeDimensions": [{"dimension": "nt.ts", "granularity": "day", "dateRange": ["2025-05-05", "2025-05-06"]}], "order": {"nt.ts": "asc"}}` | `2025-05-05 00:00:00, 1110; 2025-05-06 00:00:00, 1110`. A day has no inside start. 30 days to the end of 05-05 starts after 04-05: rows 2, 3, 4. | `2025-05-05 00:00:00, 2220; 2025-05-06 00:00:00, 2220` | REST, no rollup | off | ClickHouse | diff |
| | | | `2025-05-05 00:00:00, 1110; 2025-05-06 00:00:00, 1110` | REST, no rollup | see database | ClickHouse (on), Postgres (off, on) | match |
| unbounded_mid_start | REST: `{"measures": ["nt.cum"], "timeDimensions": [{"dimension": "nt.ts", "granularity": "month", "dateRange": ["2025-04-15", "2025-05-31"]}], "order": {"nt.ts": "asc"}}` | `2025-04-01 00:00:00, 111; 2025-05-01 00:00:00, 11111`. The same bucket rule with an unbounded window. | error: `HTTP 400 Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(__tableN.nt__ts_month, __tableN.date_to). ; query id: <id>` | rest | off, on | ClickHouse | diff |
| | | | `2025-04-01 00:00:00, 111; 2025-05-01 00:00:00, 11111` | REST, no rollup | off | Postgres | match |
| | | | `2025-04-15 00:00:00, 11111; 2025-05-15 00:00:00, 111111` | REST, no rollup | on | Postgres | diff |
| non_rolling_control | REST: `{"measures": ["nt.rev"], "timeDimensions": [{"dimension": "nt.ts", "granularity": "month", "dateRange": ["2025-04-15", "2025-05-31"]}], "order": {"nt.ts": "asc"}}` | `2025-04-01 00:00:00, 100; 2025-05-01 00:00:00, 11000`. A plain sum with the same range: the label stays on the first, and the rows are filtered. | `2025-04-01 00:00:00, 100; 2025-05-01 00:00:00, 11000` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| rollup_mid_start | REST: `{"measures": ["nr.r30"], "timeDimensions": [{"dimension": "nr.ts", "granularity": "month", "dateRange": ["2025-04-15", "2025-05-31"]}], "order": {"nr.ts": "asc"}}` | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000`. A month bucket starts on the first, as DATE_TRUNC and the non-rolling control do. 30 days up to 04-30: rows 2, 3. 30 days up to 05-31: rows 4, 5. A rollup by day. | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000` | REST, rollup hit | off, on | ClickHouse, Postgres | match |
| time_zone_mid_start | REST: `{"measures": ["nt.r30"], "timezone": "America/New_York", "timeDimensions": [{"dimension": "nt.ts", "granularity": "month", "dateRange": ["2025-04-15", "2025-04-30"]}]}` | `2025-04-01 00:00:00, 110`. The label in New York time is the first of April. Rows 2, 3 are in the 30 days before. | `2025-04-01 00:00:00, 110` | REST, no rollup | see database | ClickHouse (off, on), Postgres (off) | match |
| | | | `2025-04-15 00:00:00, 1100` | REST, no rollup | on | Postgres | diff |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.3.0 | pg-on | month_mid_start_rest | `2025-04-15 00:00:00, 1100; 2025-05-15 00:00:00, 110000` | diff |
| v1.3.0 | pg-on | month_first_start_control | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000` | match |
| v1.5.0 | pg-on | month_mid_start_rest | `2025-04-15 00:00:00, 1100; 2025-05-15 00:00:00, 110000` | diff |
| v1.5.0 | pg-on | month_first_start_control | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000` | match |
| v1.6.52 | pg-on | month_mid_start_rest | `2025-04-15 00:00:00, 1100; 2025-05-15 00:00:00, 110000` | diff |
| v1.6.52 | pg-on | month_first_start_control | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000` | match |
| v1.6.53 | pg-on | month_mid_start_rest | `2025-04-15 00:00:00, 1100; 2025-05-15 00:00:00, 110000` | diff |
| v1.6.53 | pg-on | month_first_start_control | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000` | match |
| v1.7.0 | pg-on | month_mid_start_rest | `2025-04-15 00:00:00, 1100; 2025-05-15 00:00:00, 110000` | diff |
| v1.7.0 | pg-on | month_first_start_control | `2025-04-01 00:00:00, 110; 2025-05-01 00:00:00, 11000` | match |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. Cube itself labels a month with its first day in each other path: `non_rolling_control`, the legacy planner, ClickHouse with Tesseract and a rollup in Cube Store. The Tesseract tests for this path, [`db_time_series.rs`](https://github.com/cube-js/cube/blob/78ecad294/rust/cube/cubesqlplanner/cubesqlplanner/src/tests/integration/rolling_window/db_time_series.rs#L13), use `granularity: day` only, where the start is always on a bucket, so they do not pin this. [#12023](https://github.com/cube-js/cube/issues/12023) is another shift on this planner, with no date range.
2. **Is the expected value right?** Yes. I derived it by hand: 30 days up to the end of April are rows 2 and 3, 110. Cube gives 110 on `pg-off`, `ch-on` and on the rollup. On `pg-on` it gives 1100 with the label 04-15. That number is right for 30 days up to 05-14, which is not what a month bucket means.
3. **Is it misuse?** No. The [docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/measures.mdx#L459) ask for a date range with a rolling window. `month_first_start_control` shows that it works from the first of a month. A range inside a month is a normal input.
4. **Master?** It still happens. [`time_series.rs`](https://github.com/cube-js/cube/blob/78ecad294/rust/cube/cubesqlplanner/cubesqlplanner/src/physical_plan/time_series.rs#L110) passes the raw start, and [`generate_series`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/PostgresQuery.ts#L106) counts from it. Both files are identical in `v1.8.15` and master. I found no pull request for it. With Tesseract on, it happens on `v1.3.0`, `v1.5.0`, `v1.6.52`, `v1.6.53` and `v1.7.0`. So it is not from the native series of [#10980](https://github.com/cube-js/cube/pull/10980). A default install has it since `v1.7.0`, when Tesseract became the default. Other data sources with `generated_time_series_select` use the same path, but I ran only Postgres.
5. **Does a real user reach it?** Yes. "30-day revenue by month, for the last 90 days" on Postgres. A chart shows months that start on the 15th, with values for other windows. `week_mid_start` returns 2 buckets in place of 3. `month_end_start` gives 01-31, 02-28, 03-28. The number is wrong with no error. ClickHouse does not have this.

### N2

**The legacy planner on ClickHouse gives a wrong rolling number**

- **Verdict:** `confirmed-but-narrow`
- **Result for a user:** Wrong number with no error
- **Impact:** Only with the legacy planner: a Cube before `v1.7.0` by default, or a later Cube with the planner set to false. A default install of a current tag is correct.
- **Findings verdict:** `cube-clickhouse-bug`
- **Run:** `python3 audit.py N2`. 10 cases, 41 runs: 34 match the expected value, 7 differ.

#### Cases

| Case | Input | Expected and how derived | Actual | Query shape | Planner | Database | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| two_months_rest | REST: `{"measures": ["lt.r30"], "timeDimensions": [{"dimension": "lt.ts", "granularity": "month", "dateRange": ["2025-04-01", "2025-05-31"]}], "order": {"lt.ts": "asc"}}` | `2025-04-01 00:00:00, 11; 2025-05-01 00:00:00, 1100`. 30 days to 04-30: rows 1, 2. 30 days to 05-31: rows 3, 4. Direct SQL: `1100`. | `2025-04-01 00:00:00, 11; 2025-05-01 00:00:00, 1100` | REST, no rollup | see database | ClickHouse (def, on), Postgres (off, on) | match |
| | | | `2025-04-01 00:00:00, 1111; 2025-05-01 00:00:00, 1111` | REST, no rollup | off | ClickHouse | diff |
| two_months_sql | SQL API: `SELECT DATE_TRUNC('month', ts) m, MEASURE(r30) r FROM lt WHERE ts >= '2025-04-01' AND ts < '2025-06-01' GROUP BY 1 ORDER BY 1` | `2025-04-01 00:00:00, 11; 2025-05-01 00:00:00, 1100`. 30 days to 04-30: rows 1, 2. 30 days to 05-31: rows 3, 4. | `2025-04-01 00:00:00, 1111; 2025-05-01 00:00:00, 1111` | regular | off | ClickHouse | diff |
| | | | `2025-04-01 00:00:00, 11; 2025-05-01 00:00:00, 1100` | regular | see database | ClickHouse (on), Postgres (off, on) | match |
| three_months | REST: `{"measures": ["lt.r30"], "timeDimensions": [{"dimension": "lt.ts", "granularity": "month", "dateRange": ["2025-04-01", "2025-06-30"]}], "order": {"lt.ts": "asc"}}` | `2025-04-01 00:00:00, 11; 2025-05-01 00:00:00, 1100; 2025-06-01 00:00:00, 10000`. 30 days to 04-30: rows 1, 2. 30 days to 05-31: rows 3, 4. 30 days to 06-30: row 5. | `2025-04-01 00:00:00, 11111; 2025-05-01 00:00:00, 11111; 2025-06-01 00:00:00, 11111` | REST, no rollup | off | ClickHouse | diff |
| | | | `2025-04-01 00:00:00, 11; 2025-05-01 00:00:00, 1100; 2025-06-01 00:00:00, 10000` | REST, no rollup | see database | ClickHouse (on), Postgres (off, on) | match |
| one_month_control | REST: `{"measures": ["lt.r30"], "timeDimensions": [{"dimension": "lt.ts", "granularity": "month", "dateRange": ["2025-04-01", "2025-04-30"]}], "order": {"lt.ts": "asc"}}` | `2025-04-01 00:00:00, 11`. One bucket: rows 1, 2. | `2025-04-01 00:00:00, 11` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| two_days | REST: `{"measures": ["lt.r30"], "timeDimensions": [{"dimension": "lt.ts", "granularity": "day", "dateRange": ["2025-04-24", "2025-04-25"]}], "order": {"lt.ts": "asc"}}` | `2025-04-24 00:00:00, 1; 2025-04-25 00:00:00, 11`. To the end of 04-24: row 1. To the end of 04-25: rows 1, 2. | `2025-04-24 00:00:00, 12; 2025-04-25 00:00:00, 12` | REST, no rollup | off | ClickHouse | diff |
| | | | `2025-04-24 00:00:00, 1; 2025-04-25 00:00:00, 11` | REST, no rollup | see database | ClickHouse (on), Postgres (off, on) | match |
| two_weeks | REST: `{"measures": ["lt.r30"], "timeDimensions": [{"dimension": "lt.ts", "granularity": "week", "dateRange": ["2025-05-05", "2025-05-18"]}], "order": {"lt.ts": "asc"}}` | `2025-05-05 00:00:00, 110; 2025-05-12 00:00:00, 110`. 30 days to 05-11 starts after 04-11: rows 2, 3. 30 days to 05-18 starts after 04-18: rows 2, 3. | `2025-05-05 00:00:00, 220; 2025-05-12 00:00:00, 220` | REST, no rollup | off | ClickHouse | diff |
| | | | `2025-05-05 00:00:00, 110; 2025-05-12 00:00:00, 110` | REST, no rollup | see database | ClickHouse (on), Postgres (off, on) | match |
| count_two_months | REST: `{"measures": ["lt.c30"], "timeDimensions": [{"dimension": "lt.ts", "granularity": "month", "dateRange": ["2025-04-01", "2025-05-31"]}], "order": {"lt.ts": "asc"}}` | `2025-04-01 00:00:00, 2; 2025-05-01 00:00:00, 2`. Two rows in each window. | `2025-04-01 00:00:00, 4; 2025-05-01 00:00:00, 4` | REST, no rollup | off | ClickHouse | diff |
| | | | `2025-04-01 00:00:00, 2; 2025-05-01 00:00:00, 2` | REST, no rollup | see database | ClickHouse (on), Postgres (off, on) | match |
| far_bound_two_months | REST: `{"measures": ["lt.r100y"], "timeDimensions": [{"dimension": "lt.ts", "granularity": "month", "dateRange": ["2025-04-01", "2025-05-31"]}], "order": {"lt.ts": "asc"}}` | `2025-04-01 00:00:00, 11; 2025-05-01 00:00:00, 1111`. A far bound: all rows up to the end of each bucket. | `2025-04-01 00:00:00, 1122; 2025-05-01 00:00:00, 1122` | REST, no rollup | off | ClickHouse | diff |
| | | | `2025-04-01 00:00:00, 11; 2025-05-01 00:00:00, 1111` | REST, no rollup | see database | ClickHouse (on), Postgres (off, on) | match |
| plain_sum_control | REST: `{"measures": ["lt.rev"], "timeDimensions": [{"dimension": "lt.ts", "granularity": "month", "dateRange": ["2025-04-01", "2025-05-31"]}], "order": {"lt.ts": "asc"}}` | `2025-04-01 00:00:00, 11; 2025-05-01 00:00:00, 1100`. No window, so no series. | `2025-04-01 00:00:00, 11; 2025-05-01 00:00:00, 1100` | REST, no rollup | off, on | ClickHouse, Postgres | match |
| rollup_two_months | REST: `{"measures": ["lr.r30"], "timeDimensions": [{"dimension": "lr.ts", "granularity": "month", "dateRange": ["2025-04-01", "2025-05-31"]}], "order": {"lr.ts": "asc"}}` | `2025-04-01 00:00:00, 11; 2025-05-01 00:00:00, 1100`. 30 days to 04-30: rows 1, 2. 30 days to 05-31: rows 3, 4. A rollup by day, so Cube Store runs the window. | `2025-04-01 00:00:00, 11; 2025-05-01 00:00:00, 1100` | REST, rollup hit | off, on | ClickHouse, Postgres | match |

#### Older Cube tags

| Cube tag | Config | Case | Actual | Result |
| --- | --- | --- | --- | --- |
| v1.0.0 | ch-def | two_months_rest | `2025-04-01 00:00:00, 1111; 2025-05-01 00:00:00, 1111` | diff |
| v1.0.0 | ch-off | two_months_rest | `2025-04-01 00:00:00, 1111; 2025-05-01 00:00:00, 1111` | diff |
| v1.6.76 | ch-def | two_months_rest | `2025-04-01 00:00:00, 1111; 2025-05-01 00:00:00, 1111` | diff |
| v1.6.76 | ch-off | two_months_rest | `2025-04-01 00:00:00, 1111; 2025-05-01 00:00:00, 1111` | diff |
| v1.7.0 | ch-def | two_months_rest | `2025-04-01 00:00:00, 11; 2025-05-01 00:00:00, 1100` | match |
| v1.7.0 | ch-off | two_months_rest | `2025-04-01 00:00:00, 1111; 2025-05-01 00:00:00, 1111` | diff |

#### Refutation

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. The [docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/measures.mdx#L459) define the window per bucket. The legacy planner is on its way out: [`nativeSqlPlanner`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-backend-shared/src/env.ts#L528) defaults to true, and it warns when a user sets it to false. The ClickHouse test of this feature is [skipped](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/test/integration/clickhouse/clickhouse-graph-builder.test.ts#L379).
2. **Is the expected value right?** Yes. I derived it by hand, and three other configs give it. On `ch-off`, the buckets all get the sum of each bucket window: `two_months_rest` gives 1111 twice, in place of 11 and 1100. `count_two_months` gives 4 and 4, in place of 2 and 2.
3. **Is it misuse?** No, if a user runs that planner. Setting it to false is a supported option. One bucket (`one_month_control`) and a rollup in Cube Store (`rollup_two_months`) are correct.
4. **Master?** It still happens with the flag. [`seriesSql`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L274) writes two independent `arrayJoin` calls, so `date_from` and `date_to` make a cross product. The file is identical in `v1.8.15` and master. I found no pull request for it. With the default planner, `v1.0.0` and `v1.6.76` are wrong, and `v1.7.0` and `v1.8.15` are correct. GitHub marks `v1.6.76` as the latest release, so a user on that tag has it by default.
5. **Does a real user reach it?** Only on a Cube before `v1.7.0`, or with the flag. "30-day revenue by month" on ClickHouse shows one repeated number. It is wrong with no error, and a repeated value is easy to see.

## Pre-aggregation forms

The `preagg.*` checks in `conformance/features.yaml` test each form in [the reference of Cube](https://github.com/cube-js/cube/blob/master/docs-mintlify/reference/data-modeling/pre-aggregations.mdx) on ClickHouse, with the catalog in `dev/cube/model/metrics.yml`. Each check names the exact set of rollups and reads `usedPreAggregations`. A check fails if a query that must hit reads ClickHouse, or if it reads another rollup. Each check compares the number with direct SQL on ClickHouse, on a hit and on a miss.

These results come from that work.

### rollup-no-index

**A rollup with no index stops on ClickHouse.** The smallest form in the docs has no `indexes`. `ClickHouseQuery.preAggregationLoadSql` stops each query that matches it, also when the rollup goes to Cube Store: `ClickHouse doesn't support pre-aggregations without indexes`. On stock Cube, each run of the refresh worker stops with this error too, so each other rollup goes stale. The dialect builds it with `ORDER BY tuple()`. See `preagg.option.no_index`.

### original-sql-rollup

**A rollup with `use_original_sql_pre_aggregations` stops on ClickHouse.** This corrects `db_rollup_over` in [m5](#m5). `PreAggregationLoader` replaces the name of the `original_sql` table only when it loads a table in the source database. The load into Cube Store streams the SQL with the name that has no version, so ClickHouse answers `Unknown table expression identifier`. The dialect replaces the name before the load. See `preagg.option.use_original_sql_same_grain_hit`.

### rollup-range-zone

**A partitioned rollup in a time zone leaves out rows, with no error.** Cube finds the build range with `select max(convertTz(x))`, and it reads the result as UTC. ClickHouse gives the result in the zone of the query. So in New York, the end of the range is 5 hours early, and the last partition does not have the rows of those hours. In the demo data, December 2025 gave 23166, and the truth is 23313. East of UTC, the start of the range moves and the first rows go. The rollup must have no `build_range_end`. The dialect gives the range in UTC. See `preagg.time_zone.partitioned_hit`.

### rollup-approx-worker

**A rollup of `count_distinct_approx` stops the refresh worker.** `BaseQuery.hllInit` stops with `Distributed approximate distinct count is not supported by this DB`. A query on the measure stops with this text, and the driver shows a clear message. Each run of the refresh worker also stops with `Refresh Scheduler Error`. With this one rollup in the model, a rollup with `refresh_key: { every: 30 second }` in another cube did not change in 200 seconds. With no such rollup, the same rollup changed in 30 seconds. So one such rollup makes each other rollup stale, with no error for a person who reads them. ClickHouse has no sketch that Cube Store can read, so the dialect cannot fix this. Do not put this measure type in a rollup on ClickHouse. See `preagg.type.count_distinct_approx_stops`.

### rollup-zone-range-driver

**A date filter under a report time zone did not read a rollup.** This was a defect of the driver. The driver wrote each bound in a form that Cube does not merge, because Cube reads a bound that is not in a date range as UTC. The number was right, and each query read ClickHouse. Now two bounds on a column with a rollup stay a date range in the zone. A third bound, one bound alone, and a bound inside `OR` or `NOT` keep the old form. See `preagg.metabase.time_zone_day_hit`.

### Limits that stay

| Form | Result | Entry |
| --- | --- | --- |
| A query out of `build_range_start` and `build_range_end` | The rows of the range only, with no error. The docs warn about this. Use `rollup_lambda`, or a range over all rows. | `preagg.build_range.whole_table_reads_only_the_range` |
| `allow_non_strict_date_range_match: true` on a rollup | A range inside a bucket reads only the buckets that start in the range, with no error. The docs call this a slight discrepancy. Leave it out, and keep the environment variable `false`. | `preagg.option.non_strict_inside_a_bucket_reads_whole_buckets` |
| `rollup_lambda` of two rollups with no overlap | Cube reads a rollup only up to the start of its last partition. The rows of that partition are not in the result if the next rollup does not hold them, with no error. | `preagg.type.rollup_lambda_gap_loses_the_last_partition` |
| `rollup_lambda` with source data, over the end of the batch | The truth or `Stream query failed: aborted`, see [#12285](https://github.com/cube-js/cube/issues/12285). | `preagg.type.rollup_lambda_over_the_batch_with_source` |
| `scheduled_refresh: false` | The rollup keeps its first rows until a job of `/v1/pre-aggregations/jobs` runs. This is the documented behaviour. | `preagg.refresh.scheduled_refresh_false_stays_old_until_a_job` |
| `incremental: true` | A partition out of `update_window` keeps its old rows. With no `update_window`, only the last partition refreshes. This is the documented behaviour. | `preagg.refresh.incremental_keeps_a_partition_out_of_the_window` |
