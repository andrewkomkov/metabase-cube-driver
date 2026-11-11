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

- [x] T005 [US3] Add `dev/cube/model/ledger.yml` with the cubes `ledger`, `ledger_fees` and the view `ledger_view`
- [x] T006 [US3] Add the checks and entries for the Cube model options (FR-001, FR-002)
- [x] T007 [US2] Add the checks and entries for expressions, filters, the auto bin, dashboard filters, native variables and XLSX export
- [x] T008 [US1] Add the checks and entries for the MCP tools and arguments
- [x] T009 [US3] Keep the cube name of a hierarchy level of a joined cube in `src/metabase/driver/cube/meta.clj` (FR-005)

## Phase 3: First targeted run

The first run on Cube with the dialect gave `coverage: 49/57 pass`. The failures and their layers:

- `mb.expr.get_day_of_week_iso`: the driver. It used the first day of the week of the setting.
- `mb.expr.now`, `mb.expr.today`: Cube. It has no template for `NOW()` on ClickHouse.
- `mb.expr.host`, `.domain`, `.path`: Cube and ClickHouse. A regular expression with a lookbehind has no form.
- `mb.expr.split_part`: Cube. It has no template for `SPLIT_PART` on ClickHouse.
- `mcp.browse.get_fields_values`: the check. Metabase gives only the values that a scan stored.


- [x] T010 Push, pull on nettop under the lock, and run the new ids with `--no-write` on Cube with the dialect
- [x] T011 For each failure, name the layer: the driver, Cube, Metabase or the check

## Phase 4: Fixes and real statuses

- [x] T012 Correct each check that was wrong, with no change to its feature
- [x] T013 Correct the driver or the model for each defect that this repository can correct
- [x] T014 Give each entry that cannot pass the real status and a reason (FR-004)
- [x] T015 Add the entries with no check: `measure.type.running_total`, `mb.join.explicit_right`, `mb.join.explicit_full`, `graphql.api`, `mb.expr.convert_timezone`, and `mb.expr.host`, `.domain`, `.subdomain`, `.path`
- [x] T015a Count the day of the week from Monday for the mode `iso`, in `src/metabase/driver/cube.clj`
- [x] T015b Write `now` and `today` as a time literal, in `src/metabase/driver/cube.clj`
- [x] T015c Add the templates for `SPLIT_PART` and `REGEXP_SUBSTR` to `cube-clickhouse/index.js`, with a probe in `probe.clj` and the methods in `cube.clj`
- [x] T015d Give a message for `host`, `domain`, `subdomain` and `path`, with the check `compile.expr.url_function_gets_a_clear_message`
- [x] T015e Start the scan of the field before `mcp.browse.get_fields_values` reads its values

## Phase 5: Proof

- [ ] T016 Run the new ids on stock Cube and on Cube with the dialect, with status lines written, and commit `conformance/features.yaml`
- [ ] T017 Run the full suite on both variants and compare each old status with the status before this feature (SC-003)
- [ ] T018 Review the claims with the checklist of the agent `cube-reviewer`

## Phase 6: Documents

- [ ] T019 [P] Describe the new features and limits in `docs/` (FR-007)
- [ ] T020 Record the coverage lines of the last targeted run and the last full run in this file
