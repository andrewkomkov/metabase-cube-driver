title: EXTRACT next to a measure fails on ClickHouse
verdict: intended
rank: 18
severity: An error, with a documented form that works
impact: The docs describe this error and give the nested form as the alternative. Only a non-additive measure by a part such as day of week has no other SQL API form.
findings: cube-clickhouse-bug
differs: The docs describe this exact failure, for a data source that cannot push an expression down. The nested form that they recommend works on ClickHouse. It is a missing feature of the dialect and it gives no wrong number.

---

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** Yes. The [docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L162) use EXTRACT as their example. They say that such a query fails with `Can't detect Cube query and it may be not supported yet` if the data source does not support pushing down an expression. They then give the [nested form](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/query-format.mdx#L172). One doc points the other way: the [function table](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/sql-api/reference.mdx#L484) lists EXTRACT with inner projections, with no note about the data source.
2. **Is the expected value right?** Yes, for the value: direct SQL on both databases gives the months and sums. The query failing is the documented result, so the expected value in my table is stricter than the docs.
3. **Is it misuse?** Partly. The docs form is `outer_over_scan`, which gives the correct result on ClickHouse. `raw_rows` and `in_filter` also work.
4. **Master?** The behaviour is the same. The [rule](https://github.com/cube-js/cube/blob/78ecad294/rust/cubesql/cubesql/src/compile/rewrite/rules/wrapper/extract.rs#L76) needs the template `expressions/extract`, and [`ClickHouseQuery.sqlTemplates`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L316) has none. The file is identical in `v1.8.15` and master. `v1.6.76` and `v1.0.0` behave the same. [#8313](https://github.com/cube-js/cube/issues/8313) is closed for Postgres, where the template exists.
5. **Does a real user reach it?** Yes, and it is a clear error, not a wrong number. "Revenue by month of year" works when written in the nested form. The gap that is left is `dow_avg_no_nested_form`: the docs say that a non-additive measure needs pushdown, and pushdown refuses EXTRACT here. A template in the dialect closes this gap. That is a feature request for Cube.
