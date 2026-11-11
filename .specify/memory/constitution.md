# metabase-cube-driver Constitution

## Core Principles

### I. The Feature Matrix Is the Only Truth

- `conformance/features.yaml` is the only source of truth for coverage. Each entry names one Cube
  or Metabase feature, a status and an executable check.
- Status `pass` MUST have a check that ran against a live stack and compared the result with the
  truth. The truth MUST come from a direct SQL query on the demo ClickHouse, never from Cube or
  Metabase.
- Status `partial`, `blocked` or `unsupported` MUST carry a reason in the entry.
- Coverage numbers in commits, docs and reports MUST be the numbers that `conformance/run.py`
  prints, and nothing else.

Rationale: a claim of coverage that no check proves is the main way this project can lie.

### II. ClickHouse First

The main target is Cube on top of ClickHouse. Test with ClickHouse first and tune for it first.
Other Cube data sources MUST keep working, but they come second. The driver speaks to Cube only,
so ClickHouse behaviour reaches it through Cube SQL generation.

### III. Honest Status (NON-NEGOTIABLE)

A feature MUST NOT be narrowed to make a check pass. When a feature does not fully work, the entry
records the real status and the reason. A task in `tasks.md` is ticked only when the code it
claims exists and was checked.

### IV. Pinned Versions

- Metabase `v0.65.3.6` (OSS image `metabase/metabase`), Cube `v1.8.20` (image `cubejs/cube`),
  ClickHouse `26.10.3-alpine` (image `clickhouse/clickhouse-server`).
- Metabase code is read at the tag (`git show v0.65.3.6:<path>` in the reference clone), not at
  `master`. A pin changes only through its own feature and commit.

### V. Scope Stays in This Repository

Issues and code changes stay inside this repository. `metabase/metabase` and `cube-js/cube` are
not changed. A fix that waits for a change in Cube goes to `cube-clickhouse/`, the ClickHouse
dialect for stock Cube.

## Environments and Constraints

- Live stack `nettop.local`, in `~/metabase-cube-driver`, compose project `metabase-cube-driver`.
  Other production services run there: change only this compose project, never run
  `docker system|volume|image prune`, and stop and report when `/` has less than 15 GB free.
- Development stack on the iMac (the Mac the agents run on) through Apple `container`. Docker,
  Compose, Colima, Lima, Podman and OrbStack MUST NOT be installed there. Build output, caches and
  images go to `/Volumes/SanDisk2Tb`.
- The demo stacks use fixed throwaway credentials for fake data. Every other secret goes through
  the `secret` command and is never written into a file, a commit or a log.

## Development Workflow

- Every change (feature, fix, refactor, chore) goes through spec-kit: a numbered folder
  `specs/NNN-slug/` with `spec.md`, `plan.md` and `tasks.md`. `.specify/feature.json` points at
  the active one. A new feature is preferred over reopening a finished one.
- Commits follow Conventional Commits in lowercase imperative English, with no AI attribution.
  Commit on the Mac, push to `main`, deploy with `git pull` on nettop. Never commit on nettop.
- Before a claim of coverage goes into a commit or a report, `make conformance` runs on a live
  stack. Then an adversarial review (agent `cube-reviewer`) reads the claim.

## Governance

This constitution and `AGENTS.md` bind every agent and person who changes the repository.
`AGENTS.md` holds the runtime details: layout, addresses and commands. When the two disagree, fix
the wrong one in the same change. An amendment changes the version below. Use MAJOR for a removed
or redefined principle, MINOR for a new principle or section, and PATCH for wording. Each review checks the change against these principles.

**Version**: 1.0.0 | **Ratified**: 2026-11-11 | **Last Amended**: 2026-11-11
