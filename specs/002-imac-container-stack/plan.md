# Implementation Plan: The stack and the suite on the iMac

**Branch**: `002-imac-container-stack` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

## Summary of the change

Add `dev/container/stack.sh`. It starts the services of `dev/docker-compose.yml` and of the
conformance overlay with Apple `container`. Put the `docker` calls of `conformance/run.py`
behind one helper that the variable `STACK` selects. Let `conformance/run_isolated.sh` use the
two backends. Add Makefile targets and the documents. Then run the full suite on the iMac.

## Technical Context

**Tools**: Apple `container` 1.7.1, POSIX `sh`, Python 3 (standard library only).
**Files**: `dev/container/stack.sh`, `dev/container/README.md`, `conformance/run.py`,
`conformance/run_isolated.sh`, `conformance/README.md`, `Makefile`, `AGENTS.md`.
**Testing**: `shellcheck`, `python3 -m py_compile`, a comparison of the command lines of the
helper with the old ones, and `make conformance-imac` on the iMac.
**Constraints**: 16 GB of memory. Agents cannot use `sudo` or a Docker tool. Other projects use
Apple `container` on the same Mac at the same time.

The decisions and the tests behind them are in [research.md](research.md).

## Constitution Check

- I. The feature matrix: each entry and each check stays as it is. The coverage lines in the
  report are the lines that `conformance/run.py` prints. Pass.
- II. ClickHouse first: the stack is Cube on ClickHouse. Pass.
- III. Honest status: a status from a run on the iMac goes into a commit only if nettop gives
  the same result. Each difference gets a cause in the report. Pass.
- IV. Pinned versions: the script uses the same pins as the compose file. Pass.
- V. Scope: all changes are in this repository. Pass.
- Environments: only Apple `container` on the iMac. The volumes go to the external drive. The
  images stay in the application directory of Apple `container`, see research.md. Pass with
  this recorded limit.

## Project Structure

```text
dev/container/stack.sh       up, stop, reset, check, hosts, status
dev/container/README.md      the procedure, the memory limits, the differences from nettop
conformance/run.py           stack_exec, stack_logs, stack_log_mark, stack_logs_since
conformance/run_isolated.sh  start_services, seed_extra, new_metabase for the two backends
Makefile                     up-imac, down-imac, reset-imac, conformance-imac
```

## Design

- **Names**: each container has the name `mcd-<service>` on the network `mcd`. Only containers
  with this prefix change.
- **Name lookup**: after each start, the script writes the address of each service into
  `/etc/hosts` of each running container.
- **Change of settings**: the script keeps a checksum of the arguments in the label
  `mcd.config`. A different checksum makes a new container. This is how a change of
  `CUBE_CLICKHOUSE_FIXES` gets to the Cube services.
- **Data**: named volumes `mcd-clickhouse-data`, `mcd-metabase-data` and
  `mcd-metabase-conformance-data`, with their images on the external drive.
- **Runner**: with `STACK` not set the helper builds the same `docker` command lines as before.
  With `STACK=container` it calls `container exec` and `container logs`.
- **Memory**: ClickHouse 2 GB, `cube` 2 GB, `cube-secure` 1 GB, `cube-legacy` 2 GB, Metabase
  3 GB, mail 256 MB, browser 1 GB. A conformance run uses at most 11.25 GB. The script stops
  the demo Metabase before a conformance run.
