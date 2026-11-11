# Research: Close the open entries

Each section gives the decision for one entry or one group of entries, and the evidence. The
versions are Metabase `v0.65.3.6` and Cube `v1.8.20`.

## Entries that this feature closes

### mb.agg.median, mb.agg.percentile

- Decision: add `PERCENTILE_CONT` to the dialect. The driver asks Cube if it has the function.
- Stock Cube removes the function for ClickHouse: `ClickHouseQuery.ts` has
  `delete templates.functions.PERCENTILECONT`.
- The dialect writes `quantileExactInclusive(p)(toFloat64(x))`. This function has the linear
  form of `PERCENTILE_CONT`. `quantile` is an estimate. It does not accept a `Decimal`, so the
  template has `toFloat64`.
- The driver asks `/v1/sql?format=sql`, which runs nothing. Stock Cube answers HTTP 500 there,
  and then Metabase refuses the aggregation.

### dimension.type.geo.view

- Decision: fix it in the dialect. The fix in Cube is cube-js/cube#12537, not released.
- A view copies the type `geo` of a dimension and not its `latitude` and `longitude`
  (`CubeSymbols.ts`, `generateIncludeMembers`). The planner then reads `symbol.latitude.sql`.
- The dialect gives the view member the type `string`. The planner then reads the reference
  to the dimension of the cube. A patch of `CubeToMetaTransformer` keeps `geo` in `/v1/meta`.

### preagg.rollup_lambda and the two entries with source rows

- Decision: fix it in the dialect. The issue in Cube is cube-js/cube#12285, open.
- `QueryCache.csvQuery` calls `stream.pipeline` with a callback and awaits the result. The
  result is the last stream, not a promise. So `csvQuery` closes the ClickHouse client while
  the rows still come. The query fails with `Stream query failed: aborted`, or not, with the
  timing.
- On a scratch Cube with no dialect, four of six reads after three cold starts failed with
  this error. With the fix, eight of eight reads after four cold starts gave the truth.
- The dialect reads all rows first. Cube limits these rows with `maxSourceRowLimit`.

### restapi.compare_date_range

- Decision: add a check, with status `partial`. The entry had status `unsupported`.
- Cube runs one query for each range of `compareDateRange`. Metabase has no request with one
  result for each range.
- A Metabase dashboard gives one card a date filter, and each range is one run of the card.
  This works for each kind of measure.
- One question with a custom column that names the range compares the ranges in one result.
  The ranges must not overlap. The driver stops a measure that does not add up next to a custom
  column, so the check uses `sum`, `count` and `avg` measures there.

### restapi.total_query

- Decision: add a check, with status `partial`. The entry had status `unsupported`.
- Cube runs a second query for `total: true`. Metabase has no request that gives a page and
  the total together. A second stage counts the rows of the same query, with its filter and
  with no limit. The check runs it as a nested query, over a saved question, and through MCP.

### compile.xray.field_all_cards_run, compile.xray.time_field_all_cards_run

- Decision: make the check strict. Before, it accepted a card that stops with the message of a
  total through a join. Now such a card fails the check.
- On Metabase `v0.65.3.6`, each of the two x-rays has 15 cards, and each card gives rows equal
  to ClickHouse. No card adds up `users.lifetime_orders`, so the reason of the entry was old.

### dimension.opt.order

- Decision: apply the option in the driver. `/v1/meta` gives `order: desc` for a dimension, and
  Cube itself does not sort by it.
- Metabase adds an ascending order for each breakout in the middleware
  `add-implicit-clauses`, before the driver reads the query. The driver wraps this middleware
  for a Cube database and writes the order first.
- The MCP tool `execute_query` writes a total order into the query before the query processor
  (`metabase.mcp.v2.query/with-total-order`). The driver wraps this function too.

## Entries with a real limit

### The number formats abbr and accounting (7 entries)

- The limit is in Metabase. `formatNumber` has the options `compact` and
  `negativeInParentheses` (`frontend/src/metabase/utils/formatting/numbers.tsx`). No column
  setting sets them. `getComputedSettings` in `frontend/src/metabase/viz-core/lib/settings.ts`
  computes only the settings that `viz-core/lib/settings/column.ts` defines. It drops each
  other key of the field settings.

### semantic.drill_members_native

- The limit is in Metabase. The drill to the rows of a group
  (`src/metabase/lib/drill_thru/underlying_records.cljc`) removes `:fields` from the query, so
  the result has each visible field. A driver has no hook in it.

### granularity.custom.offset, dimension.opt.propagate_filters_next_to_another_sub_query

- The fixes are in Rust: Cube Store for the first, the Tesseract planner for the second
  (cube-js/cube#12502). The dialect runs in JavaScript and cannot hold them.
