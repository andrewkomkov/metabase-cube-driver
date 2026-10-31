# Cube audit

This directory holds the cases, the runner and the results for [docs/cube-audit.md](../../docs/cube-audit.md). It uses Cube, ClickHouse and Postgres only. It does not use Metabase or the driver.

## Run

Run `audit.py` on a host with Docker. Compose project `mcd-audit` uses ports 4600 and 4601 on the loopback interface. Each service has `mem_limit: 1g`. Credentials are fixed throwaway values for fake data.

```sh
python3 audit.py B1                                    # each config of one defect
python3 audit.py all                                   # each defect
python3 audit.py B1 --config ch-on --case plain_gt_lte # one case on one config
python3 audit.py N1 --only-tags                        # the older Cube tags of one defect
python3 audit.py M1 --bisect v1.7.27 v1.8.14 --case no_oid --config pg-on
python3 audit.py --down                                # removes the stack, its volumes and the pulled images
python3 render.py                                      # writes docs/cube-audit.md
```

The runner waits while available memory is below 1500 MB. It stops if free disk space on `/` is below 15 GB.

## Configs

A config is `DB-PLANNER`, with optional suffixes.

| Part | Values |
| --- | --- |
| `DB` | `ch` for ClickHouse, `pg` for Postgres |
| `PLANNER` | `on` and `off` set `CUBEJS_TESSERACT_SQL_PLANNER`. `def` does not set it, so the tag default applies. |
| `+ch=TAG` | Another ClickHouse image tag, for example `ch-on+ch=25.3-alpine` |
| `+nopush` | `CUBESQL_SQL_PUSH_DOWN=false` |

## Files

| Path | Content |
| --- | --- |
| `cases/DEFECT.py` | `MODEL`, `SEED_CH`, `SEED_PG`, `CASES`, and the optional `EXTRA_CONFIGS`, `TAGS`, `TAG_CASES`, `TAG_CONFIGS` |
| `results/DEFECT/TAG--CONFIG.json` | The output of each case, with `EXPLAIN` or the generated SQL |
| `answers/DEFECT.md` | The verdict and the answers to the five questions |
| `answers/_intro.md` | The first part of the report |
| `tags.txt` | The tag order for `--bisect` |
| `run-corrected.sh`, `run-bisect.sh` | The runs after the first full run, in order |

## Case format

Each case has an `id`, a `why` that derives the expected value, and one input key.

| Key | Effect |
| --- | --- |
| `sql` | A simple query on the SQL API |
| `wire` | `{sql, params, oids}`. Parse, Bind and Execute on the SQL API, with these parameter type OIDs. |
| `rest` | A query for `/v1/load`. It also records `usedPreAggregations` and `/v1/sql`. |
| `direct` | SQL on the database itself, with no Cube |
| `meta` | A cube or view. It lists its geo members from `/v1/meta`. |

These keys are optional.

| Key | Effect |
| --- | --- |
| `expected`, `expected_error` | The value or the error text. A dictionary with `ch` and `pg` gives a value for each database. |
| `truth` | Direct SQL to show next to the expected value |
| `setup` | Statements to run first on the same connection, for example `SET TIME ZONE` |
| `explain` | A literal form of a `wire` query, for `EXPLAIN` |
| `sorted_rows` | Compare rows in any order, for a query with no `ORDER BY` |
| `configs`, `skip` | Limit the case to some configs, or skip a database |
