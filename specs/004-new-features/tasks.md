# Tasks: New features in the matrix

**Input**: [spec.md](spec.md), [plan.md](plan.md)

A task is ticked only when its code exists and a live check on nettop ran.

## Phase 1: Setup of the gap list and the scratch stack

- [x] T001 Read `AGENTS.md`, the constitution, the agent files, the two project skills and `conformance/README.md`
- [x] T002 Compare the ids in `conformance/features.yaml` with the Cube v1.8.20 model options
- [x] T002a Compare the ids with the Metabase v0.65.3.6 clauses, parameter types and MCP tools
- [x] T003 Write the ranked gap list into `spec.md`
- [x] T004 Start a scratch Cube and ClickHouse in Apple `container` to compile the model before a push

## Phase 2: Checks and entries

- [ ] T005 [US3] Add `dev/cube/model/ledger.yml` with the cubes `ledger`, `ledger_fees` and the view `ledger_view`
- [ ] T006 [US3] Add the checks and entries for the Cube model options (FR-001, FR-002)
- [ ] T007 [US2] Add the checks and entries for expressions, filters, the auto bin, dashboard filters, native variables and XLSX export
- [ ] T008 [US1] Add the checks and entries for the MCP tools and arguments
- [ ] T009 [US3] Keep the cube name of a hierarchy level of a joined cube in `src/metabase/driver/cube/meta.clj` (FR-005)

## Phase 3: First targeted run

- [ ] T010 Push, pull on nettop under the lock, and run the new ids with `--no-write` on Cube with the dialect
- [ ] T011 For each failure, name the layer: the driver, Cube, Metabase or the check

## Phase 4: Fixes and real statuses

- [ ] T012 Correct each check that was wrong, with no change to its feature
- [ ] T013 Correct the driver or the model for each defect that this repository can correct
- [ ] T014 Give each entry that cannot pass the real status and a reason (FR-004)
- [ ] T015 Add the entries with no check: `measure.type.running_total`, `mb.join.explicit_right`, `mb.join.explicit_full`, `graphql.api`

## Phase 5: Proof

- [ ] T016 Run the new ids on stock Cube and on Cube with the dialect, with status lines written, and commit `conformance/features.yaml`
- [ ] T017 Run the full suite on both variants and compare each old status with the status before this feature (SC-003)
- [ ] T018 Review the claims with the checklist of the agent `cube-reviewer`

## Phase 6: Documents

- [ ] T019 [P] Describe the new features and limits in `docs/` (FR-007)
- [ ] T020 Record the coverage lines of the last targeted run and the last full run in this file
