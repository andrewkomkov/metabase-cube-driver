# ClickHouse dialect for stock Cube

This directory has one file, `index.js`. It holds the ClickHouse fixes that wait for a change in Cube, as a subclass of `ClickHouseQuery`. You keep the `cubejs/cube` image. You do not need a fork.

## Install

Copy this directory next to your `cube.js`, in `/cube/conf`. If you have no `cube.js`, this creates it:

```sh
echo "module.exports = require('./cube-clickhouse');" > cube.js
```

If you have a `cube.js`, add one line to its export:

```js
module.exports = { ...require('./cube-clickhouse'), /* your options */ };
```

Then restart Cube. A cube on another data source keeps its own dialect.

## What it fixes

| Defect | Cube pull request | Result with the dialect |
| --- | --- | --- |
| B1 | [#12532](https://github.com/cube-js/cube/pull/12532) | `x > a AND x <= b` leaves out the row at `a`. |
| datetime64 | [#12533](https://github.com/cube-js/cube/pull/12533) | A `DateTime64` keeps its milliseconds in a result and in a filter. |
| interval | [#12534](https://github.com/cube-js/cube/pull/12534) | `x + INTERVAL '1 day'` works. |
| m5 | none yet | An `original_sql` pre-aggregation builds. Its indexes have no effect. |
| N2 | none yet | A rolling measure is right on the legacy planner. |

Each override in `index.js` has the link. Remove an override when Cube ships its fix. The driver asks Cube how it writes a date range, so it needs no setting for this.

## Test

`make conformance` runs the suite on stock Cube and with this dialect, and prints a coverage line for each. CI does the same. It tests the versions in the [compatibility table](https://andrewkomkov.github.io/metabase-cube-driver/compatibility/).
