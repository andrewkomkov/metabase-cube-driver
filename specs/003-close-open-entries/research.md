# Research: Close the open entries

Each section gives the decision for one entry or one group of entries, and the evidence.

## mb.agg.median, mb.agg.percentile

- Decision: add `PERCENTILE_CONT` to the dialect, and let the driver ask Cube if it has it.
- Evidence: stock Cube removes the function for ClickHouse (`ClickHouseQuery.ts`,
  `delete templates.functions.PERCENTILECONT`). With the template
  `quantileExactInclusive({{ args[0] }})`, the SQL API gives 56 and 117 for the median and the
  90th percentile of `orders.amount_dim`. ClickHouse gives the same numbers.
- `quantileExactInclusive` uses the linear form of `PERCENTILE_CONT`. `quantile` is an estimate.
- The driver asks `/v1/sql?format=sql`, which runs nothing. Stock Cube answers HTTP 500 there.

## dimension.type.geo.view

- Decision: fix it in the dialect.
- Evidence: a view copies the type `geo` of a dimension and not its `latitude` and `longitude`
  (`CubeSymbols.ts`, `generateIncludeMembers`). The planner then reads `symbol.latitude.sql`.
  With the type `string` on the view member, the planner reads the reference to the dimension
  of the cube. `/v1/meta` keeps the type `geo` through a patch of `CubeToMetaTransformer`.
- On the scratch Cube, `customer_locations.location` gives `42.36,-71.06` with 50 customers.

## The number formats abbr and accounting

- Decision: the limit is in Metabase. The status stays `partial`.
- Evidence: `formatNumber` has the options `compact` and `negativeInParentheses`
  (`frontend/src/metabase/utils/formatting/numbers.tsx`). No column setting sets them:
  `getComputedSettings` in `frontend/src/metabase/viz-core/lib/settings.ts` computes only the
  settings that `viz-core/lib/settings/column.ts` defines, and it drops each other key of the
  field settings.
