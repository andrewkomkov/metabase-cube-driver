# Conformance suite

`features.yaml` is the feature matrix. `run.py` runs it against the live stack.

## Run

Run the suite on nettop, because it reads the truth with `docker compose exec`.

```sh
METABASE_ADMIN_PASSWORD=... python3 conformance/run.py
```

| Option | Effect |
| --- | --- |
| `--only REGEX` | Run only the matching feature ids. |
| `--no-write` | Do not change `features.yaml`. |
| `--json PATH` | Write the result contract to this path. The runner also writes it when checks fail. |
| `--db-name NAME` | Use another database to test the runner. The result says nothing about the driver. Use it with `--no-write`. |

| Environment variable | Default |
| --- | --- |
| `METABASE_ADMIN_PASSWORD` | Required if Metabase has an admin. |
| `METABASE_ADMIN_EMAIL` | `admin@example.com` |
| `METABASE_URL` | `http://127.0.0.1:3300` |
| `CUBE_REST_URL` | `http://127.0.0.1:4400` |
| `CUBE_SQL_HOST`, `CUBE_SQL_PORT` | `cube`, `15432`. Metabase uses this address. |
| `CUBE_REST_INTERNAL_URL` | `http://cube:4000`. Metabase uses this address. |
| `CUBE_SQL_API_HOST`, `CUBE_SQL_API_PORT` | `127.0.0.1`, `15433`. The runner uses this address for `EXPLAIN`. |

If Metabase has no admin, the runner creates one and connects the Cube database. It generates a random password if `METABASE_ADMIN_PASSWORD` is not set.

The exit code is 0 if no check failed. It is 1 if a check failed and 2 if the runner failed.

## Result

The runner prints one line per feature and then `coverage: N/M pass`. `M` counts all entries.

| Status | Meaning |
| --- | --- |
| `todo` | The check did not run yet. This status occurs only in `features.yaml`. |
| `pass` | The check ran and the value is equal to the SQL truth. |
| `fail` | The check ran and a value, a member or the Cube database is missing or wrong. |
| `blocked` | The entry has no check. The `reason` tells what is necessary. |
| `unsupported` | The feature has no meaning in Metabase. The `reason` tells why. |

## Probe model

The `semantic.*` checks for model changes write `dev/cube/model/zz_conformance_probe.yml`, sync, change the file, sync again and delete the file. They need write access to that directory and a Cube in dev mode, which reloads the model. Each run uses a new cube name, because Metabase keeps the edits of a person for a table that comes back. Inactive tables and archived objects of old runs stay in the Metabase app DB.

## Query compilation

The `compile.*` checks compare each number with ClickHouse. They also run `EXPLAIN` on the Cube SQL API and check how Cube runs the query, and they send the request from the plan to the REST API to read `usedPreAggregations`. They need the `big_orders` table, which is 10 million rows. They change the `start-of-week` setting and set it back. See `docs/query-compilation.md`.

## Truth

Each check compares a value from Metabase with direct SQL on the demo ClickHouse. The runner reads the Cube REST API for two facts only: the aggregation type of a measure, and the pre-aggregation that served a query.

## How checks find Cube members

The driver can select one of these shapes. The runner accepts both.

| Cube member | Metabase shape |
| --- | --- |
| Cube or view | A table with the same name, in the database with engine `cube`. |
| Dimension | A field with the same name. |
| Measure | A Measure entity on the table, with the name or title of the member. Or a field with the same name, which the runner aggregates with the function of the measure type. |
| Segment | A Segment entity on the table, with the name or title of the member. Or a boolean field with the same name, which the runner filters on true. |

Format checks read `settings` and `semantic_type` of the field or the result column. Checks for meta, folders, hierarchies, view groups, drill members and links look for the text in the JSON of the table metadata.
