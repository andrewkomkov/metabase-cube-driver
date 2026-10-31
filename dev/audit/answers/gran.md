title: A custom granularity in months puts rows in the wrong bin
verdict: confirmed
rank: 3
severity: Wrong number with no error
impact: A fiscal quarter with `offset` on a rollup by month gives wrong quarters for each row before the origin. On Postgres with no rollup, month intervals drift, and an old origin moves rows by more than one bin.
findings: cube-bug
differs: The verdict stays, with a second cause. On Postgres, a query with no rollup is also wrong. The workaround in the findings, an origin before all rows, gives wrong bins on Postgres with no rollup.

---

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. The [docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/dimensions.mdx#L1108) say that the intervals shift so that one of them matches the origin. A row belongs to the interval that holds it. The Cube Store test [`date_bin`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubestore/cubestore-sql-tests/src/tests.rs#L7482) expects correct bins for times before the origin, but only when the month count is a multiple of the interval. So a time before the origin is a supported input, and the test misses this case. [#10085](https://github.com/cube-js/cube/issues/10085) was closed for inactivity and is about the default origin, not this.
2. **Is the expected value right?** Yes. I derived the bins by hand from the origin and the interval. ClickHouse with no rollup gives the same bins in `offset_no_rollup`, and so does a rollup with an old origin. The `7 days` cases show that only intervals in months fail.
3. **Is it misuse?** No. `offset` and `origin` are the docs form. The rollup hits by the normal rules, as the `usedPreAggregations` field shows.
4. **Master?** It still happens. [`calc_intervals`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubestore/cubestore/src/queryplanner/udfs.rs#L217) divides toward zero, so -1 month over 3 months gives 0 and the origin itself. [`PostgresQuery.dateBin`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/PostgresQuery.ts#L41) divides seconds by the seconds of the interval: `3 months` counts as 90 days, so the error grows with distance. Both files are identical in `v1.8.15` and master. [PR #11432](https://github.com/cube-js/cube/pull/11432) is open, and it is about single-unit intervals, which is a different path. `v1.6.76` and `v1.0.0` give the same wrong result.
5. **Does a real user reach it?** Yes. "Revenue by fiscal quarter" with the documented model. With `offset`, the origin is in the current year, so all earlier years fall before it. In `offset_rollup`, January joins the February quarter. `recent_origin_one_row_before` moves a row from the bin of November to the bin of February. A number is wrong with no error. On Postgres, `old_origin_no_rollup` puts a February row in the bin of May.
