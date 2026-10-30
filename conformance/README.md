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
| `--db-name NAME` | Use another database to test the runner. The result says nothing about the driver. Use it with `--no-write`. |

| Environment variable | Default |
| --- | --- |
| `METABASE_ADMIN_PASSWORD` | Required. |
| `METABASE_ADMIN_EMAIL` | `admin@example.com` |
| `METABASE_URL` | `http://127.0.0.1:3300` |
| `CUBE_REST_URL` | `http://127.0.0.1:4400` |

If Metabase has no admin user, the runner creates one with these credentials.

## Result

The runner prints one line per feature and then `coverage: N/M pass`. `M` counts all entries.

| Status | Meaning |
| --- | --- |
| `todo` | The check did not run yet. |
| `pass` | The check ran and the value is equal to the SQL truth. |
| `fail` | The check ran and a value, a member or the Cube database is missing or wrong. |
| `blocked` | The entry has no check. The `reason` tells what is necessary. |
| `unsupported` | The feature has no meaning in Metabase. The `reason` tells why. |

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
