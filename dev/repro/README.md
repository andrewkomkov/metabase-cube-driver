# Cube repros

Each directory holds one claim about Cube, with no Metabase and no driver. `docs/cube-findings.md` has the verdicts and the output.

```sh
cd dev/repro
python3 run.py B1-strict-lower-bound
python3 run.py --down
```

Run this on a host with Docker. Compose project `mcd-repro` uses ports 4500 and 4501 on the loopback interface. `all_claims.txt` lists all directories. Credentials are fixed throwaway values for fake data.

## Claim format

A claim is a directory with `model/`, `seed.clickhouse.sql`, `seed.postgres.sql`, `claim.json`, and an optional `cube.env` and `cube.js`. The output goes to `results/VERSION-CONFIG.txt` and to stdout.

`claim.json` holds `{"configs": [...], "steps": [...]}`. Each step has an `id` and one of these keys:

| Key | Effect |
| --- | --- |
| `sql` | A statement for the Cube SQL API, through `psql`. With `"explain": true`, the plan is added. |
| `script` | Text for one `psql` session on the Cube SQL API, so that `\bind` and `SET` work. |
| `truth`, `truth_ch`, `truth_pg` | SQL for the database itself. The last two replace `truth` for one database. |
| `rest` | A query for `/v1/load`. With `"rest_sql": true`, `/v1/sql` is added. |
| `meta` | A cube or view name. Prints that part of `/v1/meta`. With `"extended": true`, it adds `?extended=true`. |
| `meta_mentions` | A list of words. Prints how often `/v1/meta?extended=true` holds each. |
| `http` | `{"method", "path", "token", "body"}` for a raw REST call. |
| `logs` | A regex. Prints the matching lines of the Cube log. |
