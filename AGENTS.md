# Agent guide

This repository is a Metabase driver plugin for Cube Core. Agents do most of the work here.
Read this file before you change anything.

## Goal

Every Cube semantic layer feature works natively in Metabase, for people and for AI agents.
An agent that uses the Metabase MCP server must find, understand and query Cube measures,
dimensions and segments without knowledge of Cube.

The main target is Cube on top of ClickHouse. Test with ClickHouse first and tune for it first.
Other Cube data sources must keep working, but they come second. The driver speaks to Cube only, so
ClickHouse behaviour reaches it through Cube SQL generation.

## Definition of done

`conformance/features.yaml` is the only source of truth for coverage.

- Each entry names one Cube or Metabase feature, a status and an executable check.
- Status `pass` requires a check that ran against the live stack and compared results with the truth.
- Get the truth from a direct SQL query on the demo ClickHouse. Cube and Metabase cannot supply it.
- Status `partial`, `blocked` or `unsupported` requires a reason in the entry.
- Report only the coverage number that `conformance/run.py` prints.

## Pinned versions

- Metabase `v0.65.2.3`, OSS image `metabase/metabase`.
- Cube `v1.8.15`, image `cubejs/cube`.
- ClickHouse `26.8.36-alpine`, image `clickhouse/clickhouse-server`.

Read Metabase code at tag `v0.65.2.3`. A clone is at `/Volumes/SanDisk2Tb/PycharmProjects/metabase`.
Use `git show v0.65.2.3:<path>` there, because its checkout is `master`. Do not change that clone.

## Layout

- `src/metabase/driver/cube.clj` and `src/metabase/driver/cube/`: the driver.
- `cube-clickhouse/`: a ClickHouse dialect for stock Cube, with the fixes that wait for a change in Cube.
- `resources/metabase-plugin.yaml`: the plugin manifest.
- `scripts/package.sh`: makes `dev/plugins/cube.metabase-driver.jar`.
- `dev/`: the compose stack, the demo seed and the demo Cube model.
- `conformance/`: the feature matrix and its runner.
- `dev/review/`: the review grid. `make grid` runs each shape with its truth. See `dev/review/README.md`.

## The live stack

The stack runs on host `nettop.local`, in `~/metabase-cube-driver`, with compose project `metabase-cube-driver`.

| Service | Address on nettop |
| --- | --- |
| Metabase | `127.0.0.1:3300` |
| Metabase for conformance, service `metabase-conformance` | `127.0.0.1:3301` |
| Cube REST | `127.0.0.1:4400` |
| Cube SQL API | `127.0.0.1:15433` |
| Cube with a row-level rule, service `cube-secure`, REST | `127.0.0.1:4401` |
| Cube with a row-level rule, service `cube-secure`, SQL API | `127.0.0.1:15434` |
| Cube with the legacy planner, service `cube-legacy`, REST | `127.0.0.1:4402` |
| Cube with the legacy planner, service `cube-legacy`, SQL API | `127.0.0.1:15436` |
| Stock Cube for `make grid`, service `cube-stock`, REST | `127.0.0.1:4403` |
| Stock Cube for `make grid`, service `cube-stock`, SQL API | `127.0.0.1:15435` |
| Mail catcher, service `mail` | `127.0.0.1:8025` |
| Headless browser, service `browser` | `127.0.0.1:9222` |

Run the suite on nettop with `make conformance`. It runs on stock Cube and with `cube-clickhouse`, and prints a coverage line for each. This command does not use the secret store. It uses
`metabase-conformance`, with a new volume and a fixed throwaway admin. The service `metabase` and its volume
`metabase-data` do not change.

Deploy with git: commit on the Mac, push, then `git pull` on nettop. Do not commit on nettop.

## Rules for nettop

Other production services run on this host.

- Change only compose project `metabase-cube-driver`.
- Leave every other container, image, volume and network as it is.
- Do not run `docker system prune`, `docker volume prune` or `docker image prune`.
- Stop and report if free disk space on `/` is below 15 GB.

## Rules for this repository

- Commit messages follow Conventional Commits, in lowercase imperative English.
- Do not add AI attribution or `Co-Authored-By` lines.
- Push to `main`.
- Keep all issues and code changes inside this repository. Leave `metabase/metabase` and `cube-js/cube` as they are.
- The demo stack uses fixed throwaway credentials for fake data. All other secrets go through the `secret` command.
- Do not narrow a feature to make a check pass. Record the real status.
