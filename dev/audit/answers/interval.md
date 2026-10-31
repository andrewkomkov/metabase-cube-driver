title: x + INTERVAL fails on ClickHouse when the query needs SQL generation
verdict: confirmed
rank: 13
severity: An error
impact: `ts + INTERVAL` with an `ORDER BY`, a measure or a filter on the expression fails on the main target. With no `ORDER BY` it works. The `ORDER BY` form worked before `v1.7.27`.
findings: cube-clickhouse-bug
differs: The verdict stays, with a new fact: it is in part a regression. Raw rows with no `ORDER BY` work, so the findings overstate that part. The nested form fails too, unlike `extract`.

---

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. The [function table](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/reference.mdx#L481) lists `DATE_ADD` as supported. The docs describe a [failure for an expression that a data source cannot push down](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L162), but with another error text. Here the text is an internal `template not found`. The nested form that the docs give as the alternative also fails (`outer_over_scan`). So the documented way out does not work, which is the difference from [extract](#extract).
2. **Is the expected value right?** Yes. I derived the times by hand, including the leap day and the month end. Direct SQL on both databases agrees, and Postgres through Cube returns them.
3. **Is it misuse?** No. It is plain SQL. `raw_plus_day_no_order` shows that Cube accepts it, through post-processing.
4. **Master?** It still happens. [`service.rs`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubesql/cubesql/src/transport/service.rs#L951) needs a template, and [`ClickHouseQuery.sqlTemplates`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L316) has none. Both files are identical in `v1.8.15` and master. I found no pull request for it. `raw_plus_day` works on `v1.7.26` on both planners and fails on `v1.7.27`. That tag has [#11559](https://github.com/cube-js/cube/pull/11559), which prefers pushdown over post-processing, so the post-processing path that used to work is no longer taken. A `LIMIT` does not bring it back. `date_add_function` fails on each tag that I ran.
5. **Does a real user reach it?** Yes. "Each order date plus 30 days, sorted" is plain SQL. The result is a clear error with no wrong number. An interval on a constant works (`filter_constant_control`), so a relative date filter is not affected.
