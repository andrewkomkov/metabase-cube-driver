title: A geo dimension in a view fails
verdict: confirmed
rank: 12
severity: An error
impact: Each query that selects the member fails, on each database and planner. `/v1/meta` offers the member, so an agent can pick it. Two number members in the view avoid it.
findings: cube-bug
differs: The verdict stays. It also fails on `v1.0.0`, so it is old.

---

<!-- STE deviation: this text quotes SQL, member names and links in one sentence, so some sentences are long. -->

1. **Is it intended?** No. The [view docs](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/view.mdx) have no exclusion for geo. [`geo`](https://github.com/cube-js/cube/blob/78ecad294/docs-mintlify/reference/data-modeling/dimensions.mdx#L960) is a documented type. `meta_lists_member` shows that Cube offers the member. A `TypeError` from an undefined value is not a designed result. I found no test of geo through a view.
2. **Is the expected value right?** Yes. The same member of the cube returns `10.5,20.5` (`cube_rest_control`), so a view must return it too.
3. **Is it misuse?** No. Views are the documented public interface. `includes` as a list, `*`, an alias and a prefix all fail.
4. **Master?** It still happens. A [view dimension](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/compiler/CubeSymbols.ts#L1200) copies `sql` and other fields, with no `latitude` and `longitude`. The [`geo` branch](https://github.com/cube-js/cube/blob/78ecad294/packages/cubejs-schema-compiler/src/adapter/BaseQuery.ts#L3725) then reads `symbol.latitude.sql`. `CubeSymbols.ts` is identical in `v1.8.15` and master. I found no pull request for it.
5. **Does a real user reach it?** Yes, in a model that has a geo dimension and exposes views. "Show the location of each store" returns HTTP 500. It is an error, not a wrong number. `view_lat_lon_workaround` works.
