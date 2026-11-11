# Implementation Plan: New features in the matrix

**Branch**: `004-new-features` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

## Summary of the plan

Add one entry and one check for each gap in the ranked list of the spec. Run the checks on the
live stack. Change the driver, the sync or the demo model where a check shows a defect that
this repository can correct. Record the real status of the other entries with a reason.

## Technical Context

**Language**: Clojure for the driver, Python 3 standard library for the runner, YAML for the
Cube model.

**Files**: `conformance/features.yaml`, `conformance/run.py`, `dev/cube/model/ledger.yml`,
`src/metabase/driver/cube/meta.clj`, `docs/`.

**Testing**: `make conformance` on nettop, with `ARGS="--only <regex>"` for a targeted run and
with no `--only` for the full run. Each run holds `~/mcd-conformance.lock`.

**Scratch stack**: Cube and ClickHouse in Apple `container` on the iMac, to compile the model
before a push. Only a run on nettop gives proof and a status.

**Constraints**: two other agents push to `main`. Rebase before each push. A force-push is
forbidden. Feature 003 owns the entries that had another status than `pass` before this feature.

## Constitution Check

- I. The matrix is the only truth: each new status comes from a run of `conformance/run.py` on
  nettop. Pass.
- II. ClickHouse first: each truth is SQL on the demo ClickHouse. Pass.
- III. Honest status: a check that fails keeps its feature and gets the real status. Pass.
- IV. Pinned versions: no pin changes. Metabase code is read at `v0.65.3.6`. Pass.
- V. Scope: all changes are in this repository. Pass.

## Design

1. **Checks.** A new section at the end of `conformance/run.py` holds the checks. They use the
   helpers `Q`, `N`, `check`, `mcp_stage` and `assert_rows` that the runner has.
2. **Demo model.** `dev/cube/model/ledger.yml` has the cubes `ledger` and `ledger_fees` and the
   view `ledger_view`. Other checks search for the words `orders`, `revenue` and `customers`.
   The names in this file do not contain them.
3. **Driver.** `hierarchy-line` in `meta.clj` keeps the cube name of a level of a joined cube.
   More changes follow from the first targeted run.
4. **Entries with no check.** The pinned Cube rejects some features, and some have no meaning
   in Metabase. Such a feature gets the status `unsupported` and a reason.
5. **Documents.** `docs/semantic-sync.md`, `docs/query-compilation.md` and `docs/ai-agents.md`
   get the new features and limits.

## Project Structure

```text
conformance/features.yaml          new entries at the end of the file
conformance/run.py                 new section before main()
dev/cube/model/ledger.yml          new demo cubes and view
src/metabase/driver/cube/meta.clj  hierarchy text
docs/                              user documents
specs/004-new-features/            this feature
```

## Risks

- A compile error in one model file stops each cube. The scratch stack compiles the model
  before each push.
- A new table changes the search results and the table lists of other checks. The full run at
  the end shows this, and SC-003 of the spec forbids it.
