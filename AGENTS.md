# Agent guide

This repository is a Metabase driver plugin for Cube Core. Agents do most of the work here.
Read this file before you change anything.

## Goal

Every Cube semantic layer feature works natively in Metabase, for people and for AI agents.
An agent that uses the Metabase MCP server must find, understand and query Cube measures,
dimensions and segments without knowledge of Cube.

## Definition of done

`conformance/features.yaml` is the only source of truth for coverage.

- Each entry names one Cube or Metabase feature, a status and an executable check.
- Status `pass` requires a check that ran against the live stack and compared results with the truth.
- The truth comes from a direct SQL query on the demo Postgres, not from Cube and not from Metabase.
- Status `partial`, `blocked` or `unsupported` requires a reason in the entry.
- Never report a coverage number that `conformance/run.py` did not print.

## Pinned versions

- Metabase `v0.64.3`, OSS image `metabase/metabase`.
- Cube `v1.8.15`, image `cubejs/cube`.

Read Metabase code at tag `v0.64.3`. A clone is at `/Volumes/SanDisk2Tb/PycharmProjects/metabase`.
Use `git show v0.64.3:<path>` there, because its checkout is `master`. Do not change that clone.

## Layout

- `src/metabase/driver/cube.clj` and `src/metabase/driver/cube/`: the driver.
- `resources/metabase-plugin.yaml`: the plugin manifest.
- `scripts/package.sh`: makes `dev/plugins/cube.metabase-driver.jar`.
- `dev/`: the compose stack, the demo seed and the demo Cube model.
- `conformance/`: the feature matrix and its runner.

## The live stack

The stack runs on host `nettop.local`, in `~/metabase-cube-driver`, with compose project `metabase-cube-driver`.

| Service | Address on nettop |
| --- | --- |
| Metabase | `127.0.0.1:3300` |
| Cube REST | `127.0.0.1:4400` |
| Cube SQL API | `127.0.0.1:15433` |

Deploy with git: commit on the Mac, push, then `git pull` on nettop. Do not commit on nettop.

## Rules for nettop

Other production services run on this host.

- Change only compose project `metabase-cube-driver`.
- Do not stop, remove or change any other container, image, volume or network.
- Do not run `docker system prune`, `docker volume prune` or `docker image prune`.
- Stop and report if free disk space on `/` is below 15 GB.

## Rules for this repository

- Commit messages follow Conventional Commits, in lowercase imperative English.
- Do not add AI attribution or `Co-Authored-By` lines.
- Push to `main`. Do not open issues or pull requests on `metabase/metabase` or `cube-js/cube`.
- The demo stack uses fixed throwaway credentials for fake data. All other secrets go through the `secret` command.
- Do not narrow a feature to make a check pass. Record the real status.
