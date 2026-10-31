# Review grid

These scripts come from [the last review](../../docs/review/0.1.0-final.md). `shapes.py` has the query shapes, and each shape has its truth in direct ClickHouse SQL.

## Run

Run this on the host of the stack:

```sh
make grid
```

The command does these steps:

1. It starts Cube with the dialect, stock Cube (`dev/docker-compose.grid.yml`) and a Postgres copy of the demo data, next to the service `metabase-conformance`.
2. It connects both Cubes and Postgres to a new Metabase.
3. It runs each part on both variants. The parts are `dataset`, `zones`, `mcp` and `mcp-zones`.
4. It prints one line of counts for each part and variant.

Output files go to `dev/review/out/`. Each line of a `.jsonl` file is one shape, with its verdict.

To run a part of the grid, send options with `ARGS`:

```sh
make grid ARGS="--part dataset --variant stock --only ^follow/ --no-stale"
```

## Counts

| Word | Meaning |
| --- | --- |
| `pass` | Each row is equal to ClickHouse. |
| `wrong` | A number with no error, and it differs from ClickHouse. |
| `no refusal` | The shape must stop, and it gave a number. |
| `refused` | A clear message from the driver. |
| `on the list of known limits` | A part of `wrong` and `no refusal`, see below. |
| `not expected` | A wrong number or a missing refusal that is not on the list, a raw error of Cube, or a failure of the runner. |

The command fails if `not expected` is not 0, or if an entry of the list has no shape that shows it.

## Known limits

`known_limits.py` is the list of shapes that stay wrong, each with its reason. The list holds only causes that the driver and the dialect cannot reach. Do not add an entry to get a pass. Do not change a truth to match an output, unless Metabase on Postgres or the SQL standard shows that the truth was wrong.

## In conformance

The `grid.compact.*` checks in `conformance/run.py` run a stable part of the same shapes with the same list, so CI catches them.

## Other scripts

`run_all.sh` runs the whole review in its own compose project. `evidence.py`, `known_wrong.py`, `security_review.py` and `plugin_effect.py` are parts of that review. `/api/dataset` has no cache strategy, so a result from there does not come from the cache. See `security.metabase_cache`.
