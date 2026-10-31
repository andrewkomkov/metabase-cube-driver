# Conformance suite

`features.yaml` is the feature matrix. `run.py` runs it against the live stack.

## Run

Run the suite on nettop, because it reads the truth with `docker compose exec`.

```sh
make conformance
```

This command runs the suite twice, on stock Cube and on Cube with [`cube-clickhouse`](../cube-clickhouse/README.md), and prints a coverage line for each. `make conformance VARIANT=on` or `VARIANT=off` runs one. At the end the demo stack has the dialect. `status` in `features.yaml` is the result with the dialect, and `status_stock` is the result on stock Cube.

This command does not read the secret store, so it needs no Touch ID and it also runs when the screen of the Mac is locked. It uses `dev/docker-compose.conformance.yml`, which adds the service `metabase-conformance`:

- Its address is `127.0.0.1:3301`. The service binds to the loopback interface only.
- Its volume is `metabase-conformance-data`. Each run starts from a new volume, the same as CI. The container stops at the end of the run, and its log stays until the next run.
- Its admin is `admin@example.com`, with a fixed throwaway password in `conformance/run_isolated.sh`. The data is fake, so the password is not a secret.
- Its plugins directory is `dev/plugins-conformance`.

The service `metabase`, its volume `metabase-data` and its admin in the secret store do not change. ClickHouse, Cube and `cube-secure` are shared, so a running `metabase` can see the probe model of a run in its own sync.

Give options to the runner with `ARGS`:

```sh
make conformance ARGS="--only dimension.type.geo --no-write"
```

To test another Metabase version with the same jar, send its tag: `METABASE_VERSION=v0.64.3 make conformance`. The status lines in `features.yaml` record the pinned version, so add `ARGS="--no-write"`.

To remove the service and its volume, run `make conformance-down`.

To run the suite on the normal Metabase, send its admin password:

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
| `CUBE_SECURE_SQL_HOST`, `CUBE_SECURE_SQL_PORT` | `cube-secure`, `15432`. Metabase uses this address for the `security.*` checks. |
| `CUBE_SECURE_REST_INTERNAL_URL` | `http://cube-secure:4000`. Metabase uses this address. |
| `METABASE_CONTAINER` | `metabase-cube-driver-metabase-1`. The runner reads its log for `security.secret.not_in_logs`. |
| `CUBE_CLICKHOUSE_FIXES` | `on`. `off` tells the runner that this stack is stock Cube. `dialect.variant` fails if this is false. |
| `METABASE_INTERNAL_URL` | `http://metabase:3000`. The browser uses this address. |
| `MAIL_URL` | `http://127.0.0.1:8025`. The runner reads the mail catcher here. |
| `BROWSER_URL` | `http://127.0.0.1:9222`. The runner drives the headless browser here. |
| `CUBE_LEGACY_REST_URL` | `http://127.0.0.1:4402`. The check of N2 uses the legacy planner here. |
| `CUBE_LEGACY_SQL_HOST`, `CUBE_LEGACY_REST_INTERNAL_URL` | `cube-legacy`, `http://cube-legacy:4000`. Metabase uses these addresses for the same check. |

If Metabase has no admin, the runner creates one and connects the Cube database. It generates a random password if `METABASE_ADMIN_PASSWORD` is not set.

The exit code is 0 if no check failed. It is 1 if a check failed and 2 if the runner failed.

## Result

The runner prints one line per feature and then `coverage: N/M pass`. `M` counts all entries.

| Status | Meaning |
| --- | --- |
| `todo` | The check did not run yet. This status occurs only in `features.yaml`. |
| `pass` | The check ran and the value is equal to the SQL truth. A check of a refusal compares the text of the error, because no truth value applies. |
| `partial` | The check ran and passed, but it cannot prove the whole feature. The entry has `cap: partial` and a `reason`. If a limit of Cube goes away, the check fails and tells this. `cap_stock` and `reason_stock` apply only on stock Cube. |
| `fail` | The check ran and a value, a member or the Cube database is missing or wrong. |
| `blocked` | The entry has no check. The `reason` tells what is necessary. |
| `unsupported` | The feature has no meaning in Metabase. The `reason` tells why. |

A number must be equal to the truth up to the rounding of a double. A check with a real tolerance gives it with `tol`. Times are compared with their fractions of a second.

The demo data does not make status or quantity a function of a customer, a product or a day. See `dev/clickhouse/seed.sql`.

## Probe model

The `semantic.*` checks for model changes write `dev/cube/model/zz_conformance_probe.yml`, sync, change the file, sync again and delete the file. They need write access to that directory and a Cube in dev mode, which reloads the model. Each run uses a new cube name, because Metabase keeps the edits of a person for a table that comes back. Inactive tables and archived objects of old runs stay in the Metabase app DB.

## Query compilation

The `compile.*` checks compare each number with ClickHouse. They also run `EXPLAIN` on the Cube SQL API and check how Cube runs the query, and they send the request from the plan to the REST API to read `usedPreAggregations`. They need the `big_orders` table, which is 10 million rows. They change the `start-of-week` setting and set it back. See `docs/query-compilation.md`.

## Metric catalog

The `metric.*` checks read `dev/cube/model/metrics.yml` and the tables `metric_edges`, `metric_balances` and `metric_sales` in `dev/clickhouse/seed-extra.sql`. Each check sends one query through `/api/dataset` and through the MCP tool `execute_query`, and compares both with hand SQL on ClickHouse. A metric from other metrics runs at each grain: the total, status, size, paid, quantity, month, quarter, status and month, and day. The `metric.rollup.*` and `metric.semi_additive.*` checks also ask Cube if the rollup served the query. `metric.model.stock_form` stops a model that asks for a driver feature.

## Per-person data

The `security.*` checks need the service `cube-secure`. They run last, because they add databases and people to Metabase. They remove the databases at the end. The people stay. They set a root cache policy for one check and remove it. The truth for each person is ClickHouse with the filter of the row-level rule in `dev/cube-secure/cube.js`.

## Mail and the browser

`mcp.alert_write` and `mcp.subscription_write` read the mail in the service `mail`, a local catcher that sends nothing out. They look for the value of ClickHouse in the text of the mail. `mb.other.drill_through_ui` drives the service `browser`, a headless Chromium, with the Chrome DevTools Protocol.

## Agent path

The `mcp.agent.*` checks find ids only through MCP tools, from the words of a question. The runner uses the REST API only to verify a dashboard filter, because no MCP tool runs a dashboard card with a parameter.

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
