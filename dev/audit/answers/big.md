title: Numbers above 2^53 lose digits
verdict: confirmed-but-narrow
rank: 11
severity: Wrong number with no error, in the low digits
impact: Only values above 2^53, or decimals with more than about 15 significant digits. The SQL API has this on each database by design of its type. REST has it on ClickHouse: integers on 26.8 but not on 25.3, and decimals on both.
findings: cube-bug
differs: The reach is small, and the SQL API part is a design choice of one column type. The REST part for integers depends on the ClickHouse version, which the findings do not say.

---

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** Partly. The SQL API maps each [`number` member to `Double`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubesql/cubesql/src/transport/ext.rs#L104) on purpose, so the wire type is honest. No doc tells the user about the loss. REST is different: the [`castNumerics` recipe](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/recipes/core-data-api/cast-numerics.mdx) says that the REST API keeps big numbers as strings, and only the client option loses digits. A maintainer closed the same REST symptom on BigQuery [as a fix](https://github.com/cube-js/cube/issues/9136). So REST on ClickHouse is a bug. [#8741](https://github.com/cube-js/cube/issues/8741) is open on this topic.
2. **Is the expected value right?** Yes. Direct SQL returns `9007199254740993`. `sql_float_control` shows the correct case for rounding: a Float64 holds 2^53, so rounding is right there and my check does not call it a defect.
3. **Is it misuse?** Partly. A large id as `type: number` is a weak model. `sql_text_workaround` keeps each digit with a string member. Nothing in the docs asks for that.
4. **Master?** It still happens. [`ext.rs`](https://github.com/cube-js/cube/blob/78ecad294/rust/cubesql/cubesql/src/transport/ext.rs#L104) and [`ClickHouseDriver.ts`](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-clickhouse-driver/src/ClickHouseDriver.ts#L354) are identical in `v1.8.15` and master. [PR #11794](https://github.com/cube-js/cube/pull/11794) is open and is about the type parser. I did not check that it changes this result. The ClickHouse 25.3 run returns integers with all digits through REST, and 26.8 does not. I read this as a change of the default for quoting 64-bit integers in JSON, and I did not test the setting.
5. **Does a real user reach it?** Rarely with amounts: a sum must pass 9 quadrillion. More often with UInt64 ids and hashes in `type: number`, and with long decimals. "Show the id of this event" gives a neighbouring id with no error. A join key taken from this value finds the wrong row.
