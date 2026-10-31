title: A time dimension on ClickHouse loses milliseconds
verdict: confirmed
rank: 6
severity: Wrong number with no error
impact: On event tables, a filter inside one second misses rows, and rows in one second show the same time. The order of rows in one second is lost.
findings: cube-clickhouse-bug
differs: The verdict stays. A measure of type `time` keeps milliseconds, so the loss is in the dimension path only.

---

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. REST time values have the form `YYYY-MM-DDTHH:mm:ss.SSS`, as the [query format docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/core-data-apis/rest-api/query-format.mdx#L641) say. [#9051](https://github.com/cube-js/cube/issues/9051) is open with the exact case. [PR #12046](https://github.com/cube-js/cube/pull/12046) and [PR #12519](https://github.com/cube-js/cube/pull/12519) add failing tests for it.
2. **Is the expected value right?** Yes. Direct SQL on ClickHouse returns `.007`. `filter_ms_window_sql` gives 10 by direct SQL. Cube gives 0 on ClickHouse.
3. **Is it misuse?** No. `DateTime64(3)` with `type: time` is the plain model. `text_workaround` keeps the value as a string, but a string is not a time for filters and buckets.
4. **Master?** It still happens. [`convertTz`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/ClickHouseQuery.ts#L91) writes `toDateTime64(field, 0)`. The file is identical in `v1.8.15` and master. No open pull request fixes it. `v1.6.76` and `v1.0.0` behave the same.
5. **Does a real user reach it?** Yes, on logs and events, where ClickHouse is common. "Requests in this one second" is a rare chart, but `filter_on_ms_upper` matters more: a bound taken from a row value, `.999`, misses that row and gives 0. A filter on whole seconds and a bucket by second or more are correct (`midnight_day_filter`, `bucket_second_control`). So most dashboards do not see it. The number is wrong with no error when a query does reach it.
