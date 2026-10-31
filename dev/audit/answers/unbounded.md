title: A rolling window with a time series fails on ClickHouse
verdict: confirmed
rank: 8
severity: An error
impact: A running total by month or by day fails on the main target, with more than one bucket. Tesseract with one bucket works. A far bound such as `100 year` works on ClickHouse 26.8 with Tesseract.
findings: cube-clickhouse-bug
differs: The verdict stays. On ClickHouse 25.3, a far bound fails too, so the workaround depends on the ClickHouse version. A rollup in Cube Store gives the correct result.

---

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. The [docs on rolling windows](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/measures.mdx#L459) name no limit for ClickHouse. [#9600](https://github.com/cube-js/cube/issues/9600) is open with reports from several users. The Cube repository has the ClickHouse test of this feature [skipped](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/test/integration/clickhouse/clickhouse-graph-builder.test.ts#L379), so nothing tests it.
2. **Is the expected value right?** Yes. A running total to the end of April is rows 1, 2, 3: 111. To the end of May: 1111. Postgres gives this through Cube. `db_only_cross_join` gives the counts directly on ClickHouse. The database itself refuses the join that Cube writes (`db_only_inequality_join`). `enable_analyzer = 0` and `allow_experimental_join_condition = 1` do not help. So ClickHouse behaves as it says, and the fault is the SQL that Cube sends to it.
3. **Is it misuse?** No. `trailing: unbounded` is the docs form. `running_total` is no longer a type since `v1.7.0` ([#11044](https://github.com/cube-js/cube/pull/11044)), so a model cannot use it instead.
4. **Master?** It still happens. [`multi_stage_rolling_window.rs`](https://github.com/cube-js/cube/blob/78ecad294/rust/cube/cubesqlplanner/cubesqlplanner/src/physical_plan_builder/processors/multi_stage_rolling_window.rs#L53) is identical in `v1.8.15` and master. No open pull request changes the join for ClickHouse. It also fails on `v1.6.76` and `v1.0.0` with ClickHouse 26.8.
5. **Does a real user reach it?** Yes. "Cumulative revenue by month for this year" is a common chart. The result is an error, not a wrong number. A rollup avoids it, as `rollup_hit` shows.
