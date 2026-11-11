# Tasks: Close the open entries

**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md)

A task for an entry is done when its result is in `conformance/features.yaml` and a live run on
nettop showed that status. The result line after each task gives the status before and after.

## Phase 1: Setup <!-- slop: the heading is from the spec-kit template -->

- [x] T001 Create `specs/003-close-open-entries/` and point `.specify/feature.json` at it
- [x] T002 Start a scratch Cube on nettop (`mcd-003-cube`) to test a change of `cube-clickhouse/index.js` before a commit

## Phase 2: User Story 1 - features that a person or an MCP agent uses (P1)

- [ ] T003 [US1] `mb.agg.median`: add `PERCENTILE_CONT` to `cube-clickhouse/index.js`, a probe in `src/metabase/driver/cube/probe.clj`, a check in `conformance/run.py`
- [ ] T004 [US1] `mb.agg.percentile`: the same template and probe, a check with two levels in `conformance/run.py`
- [ ] T005 [US1] `dimension.type.geo.view`: give a geo dimension of a view the SQL of its cube in `cube-clickhouse/index.js`
- [ ] T006 [US1] `restapi.compare_date_range`: find the Metabase question that compares date ranges of a measure, add a check in `conformance/run.py`
- [ ] T007 [US1] `restapi.total_query`: find the Metabase question that counts all rows of a query with a limit, add a check in `conformance/run.py`
- [ ] T008 [US1] `measure.format.abbr`: look for a Metabase column setting for an abbreviated number
- [ ] T009 [US1] `measure.format.accounting`: look for a Metabase column setting for parentheses
- [ ] T010 [US1] `dimension.format.abbr`: the same as T008 for a field
- [ ] T011 [US1] `metric.format.abbr`: the same as T008 for the metric catalog
- [ ] T012 [US1] `metric.format.abbr_1`: the same as T008 with one decimal
- [ ] T013 [US1] `metric.format.accounting`: the same as T009 for the metric catalog
- [ ] T014 [US1] `metric.format.accounting_0`: the same as T009 with no decimals
- [ ] T015 [US1] `semantic.drill_members_native`: look for a Metabase object that can hold the drill members of a measure
- [ ] T016 [US1] `compile.xray.field_all_cards_run`: find the cards that stop, and make them give the truth in `src/metabase/driver/cube.clj`
- [ ] T017 [US1] `compile.xray.time_field_all_cards_run`: the same as T016 for a time field

## Phase 3: User Story 2 - each open entry has a reason with evidence (P2)

- [ ] T018 [US2] `measure.opt.rolling_type_deprecated`
- [ ] T019 [US2] `dimension.opt.order`
- [ ] T020 [US2] `granularity.custom.offset`
- [ ] T021 [US2] `security.sql_user_impersonation`
- [ ] T022 [US2] `view.meta_auto_run`
- [ ] T023 [US2] `view.meta_default_ui_filters`
- [ ] T024 [US2] `preagg.rollup_lambda`
- [ ] T025 [US2] `mb.other.actions`
- [ ] T026 [US2] `mb.other.model_persistence`
- [ ] T027 [US2] `mb.other.uploads`
- [ ] T028 [US2] `compile.ch.decimal_above_double`
- [ ] T029 [US2] `native.not_like_three_values`
- [ ] T030 [US2] `legacy.view_default_filters`
- [ ] T031 [US2] `native.view_joined_dimension_weighted`
- [ ] T032 [US2] `dimension.opt.propagate_filters_next_to_another_sub_query`
- [ ] T033 [US2] `security.dev_mode_warning_for_hidden_members`
- [ ] T034 [US2] `security.validate_echoes_the_request_limit`
- [ ] T035 [US2] `preagg.build_range.date_range_over_the_end_reads_only_the_range`
- [ ] T036 [US2] `preagg.build_range.whole_table_reads_only_the_range`
- [ ] T037 [US2] `preagg.option.non_strict_inside_a_bucket_reads_whole_buckets`
- [ ] T038 [US2] `preagg.type.count_distinct_approx_stops`
- [ ] T039 [US2] `preagg.type.rollup_lambda_after_the_batch_with_source`
- [ ] T040 [US2] `preagg.type.rollup_lambda_over_the_batch_with_source`
- [ ] T041 [US2] `preagg.type.rollup_lambda_gap_loses_the_last_partition`
- [ ] T042 [US2] `preagg.type.original_sql_in_cube_store_with_an_index_stops`
- [ ] T043 [US2] `zone.time_measure_repeated_hour_limit`

Each task of this phase has the same steps. Read the reason. Test the limit on Cube `v1.8.20`,
or read the code of the pinned version. Then close the entry, or put the layer and the evidence
into the reason in `conformance/features.yaml`.

## Phase 4: User Story 3 - the Cube Store pin (P3)

- [ ] T044 [US3] Use the Cube pin for `cubejs/cubestore` in `dev/docker-compose.production.yml`
- [ ] T045 [US3] Make `set_pin` in `.github/scripts/pins.py` change `dev/docker-compose.production.yml`, and make `read_pins` stop on a difference

## Phase 5: Check

- [ ] T046 Run `make conformance` on nettop on both variants and copy `conformance/features.yaml` to the repository
- [ ] T047 Run the adversarial review (agent `cube-reviewer`) on the new `pass` entries
- [ ] T048 Update `docs/` and `cube-clickhouse/README.md` for each limit that is closed
