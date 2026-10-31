title: Two sub_query dimensions with the same SQL share one subquery
verdict: confirmed
rank: 7
severity: Wrong number with no error
impact: On Tesseract, the default, "all orders" next to "orders that match the filter" shows the same number twice. The first column decides which.
findings: cube-bug
differs: The verdict stays.

---

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. The [docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/dimensions.mdx#L787) say that the option passes filters to the subquery of that dimension. Swapping two columns must not change a value, and `plain_then_propagated` and `propagated_then_plain` differ. The legacy planner gives the correct result on both databases.
2. **Is the expected value right?** Yes. Customer 1 has 3 purchases, 2 of them paid. Direct SQL gives both counts. `other_sql` gets the correct result with SQL that differs only by `+ 0`, which has the same meaning.
3. **Is it misuse?** No. The model is the docs form twice. `different_measures` works, and so do `two_plain_control` and `two_propagated_control`, where sharing is harmless.
4. **Master?** It still happens. [`builder.rs`](https://github.com/cube-js/cube/blob/78ecad294/rust/cube/cubesqlplanner/cubesqlplanner/src/physical_plan_builder/builder.rs#L213) finds the column by the measure, so the first subquery wins. The file is identical in `v1.8.15` and master. [PR #12502](https://github.com/cube-js/cube/pull/12502) is open and rewrites this path. It removes this lookup and it fixes a related case ("Measure read from the wrong subquery"). It does not name this case and I did not build it, so it can fix this, but that is not proved. With Tesseract on, `v1.5.0`, `v1.6.76` and `v1.7.0` also fail. So a default install has this since `v1.7.0`, when Tesseract became the default.
5. **Does a real user reach it?** Yes, but it needs a specific model: two dimensions on one measure, with one propagated. "Lifetime orders and orders in the selected status, per customer" is the plain use of the option. A number is wrong with no error.
