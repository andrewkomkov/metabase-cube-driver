# Agent guide

This repository is a Metabase driver plugin for Cube Core. Agents do most of the work here.
Read this file before you change anything.

## Spec-kit

Every change goes through spec-kit: features, fixes, refactors and chores.

1. Read `.specify/memory/constitution.md`. It is binding, together with this file.
2. Find the active feature in `.specify/feature.json` and read its `spec.md` and `tasks.md`.
3. If the work does not fit an unfinished feature, make a new folder `specs/NNN-slug/` with
   `/speckit-specify`, `/speckit-plan` and `/speckit-tasks` before you write code.
4. Tick a task only when its code exists and a check ran. Leave open work unticked and say so.

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

- Metabase `v0.65.3.6`, OSS image `metabase/metabase`.
- Cube `v1.8.20`, image `cubejs/cube`.
- ClickHouse `26.10.3-alpine`, image `clickhouse/clickhouse-server`.

Read Metabase code at tag `v0.65.3.6`. A clone is at `/Volumes/SanDisk2Tb/PycharmProjects/metabase`.
Use `git show v0.65.3.6:<path>` there, because its checkout is `master`. Do not change that clone.

## Layout

- `src/metabase/driver/cube.clj` and `src/metabase/driver/cube/`: the driver.
- `cube-clickhouse/`: a ClickHouse dialect for stock Cube, with the fixes that wait for a change in Cube.
- `resources/metabase-plugin.yaml`: the plugin manifest.
- `scripts/package.sh`: makes `dev/plugins/cube.metabase-driver.jar`.
- `dev/`: the compose stack, the demo seed and the demo Cube model.
- `dev/container/`: the same stack for Apple `container` on the iMac. See `dev/container/README.md`.
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

## The iMac

The iMac is the Mac the agents run on (`iMac-Andrei`). It is the second test machine, next to nettop.

- Run Linux containers with Apple `container` only. Do not install Docker, Compose, Colima, Lima,
  Podman or OrbStack.
- Put build output, caches and container data on `/Volumes/SanDisk2Tb/builds/metabase-cube-driver/`.
- Run `df -h /System/Volumes/Data` before a large build. Stop and report if less than 10 GB is free.
- Use the iMac when nettop is busy, low on disk, or another agent runs the suite there.

`dev/container/stack.sh` runs the same services there, on the loopback addresses of the table above.

| Command | Effect |
| --- | --- |
| `make up-imac` | Start the demo stack. |
| `make down-imac` | Stop the containers. The data stays. |
| `make reset-imac` | Remove the containers and their data. |
| `make conformance-imac` | Run the suite on stock Cube and with `cube-clickhouse`. |

- The containers have the names `mcd-SERVICE`. Change only containers, volumes and the network with the prefix `mcd`: other projects use Apple `container` on this Mac.
- `make grid`, `make fuzz`, `make fuzz-native` and `make production` run on nettop only.
- Commit a status from a run on the iMac only if nettop gives the same result.
- The memory limits, the place of the data and the differences from docker compose are in `dev/container/README.md`.

## Rules for nettop

Other production services run on this host.

- Change only compose project `metabase-cube-driver`.
- Leave every other container, image, volume and network as it is.
- Do not run `docker system prune`, `docker volume prune` or `docker image prune`.
- Stop and report if free disk space on `/` is below 15 GB.
- To free disk, remove only images of this compose project that no container uses and that no
  compose file in the clone names as a default. Run `docker rmi` with the image names. Do not prune.

## Rules for this repository

- Commit messages follow Conventional Commits, in lowercase imperative English.
- Do not add AI attribution or `Co-Authored-By` lines.
- Push to `main`.
- Keep all issues and code changes inside this repository. Leave `metabase/metabase` and `cube-js/cube` as they are.
- The demo stack uses fixed throwaway credentials for fake data. All other secrets go through the `secret` command.
- Do not narrow a feature to make a check pass. Record the real status.
