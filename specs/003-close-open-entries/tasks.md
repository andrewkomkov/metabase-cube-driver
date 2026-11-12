# Tasks: Close the open entries

**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md)

A task for an entry is done when its result is in `conformance/features.yaml` and a live run on
nettop showed that status. The result line after each task gives the status before and after.

## Phase 1: Setup <!-- slop: the heading is from the spec-kit template -->

- [x] T001 Create `specs/003-close-open-entries/` and point `.specify/feature.json` at it
- [x] T002 Start a scratch Cube on nettop (`mcd-003-cube`) to test a change of `cube-clickhouse/index.js` before a commit

## Phase 2: User Story 1 - features that a person or an MCP agent uses (P1)

- [x] T003 [US1] `mb.agg.median`: add `PERCENTILE_CONT` to `cube-clickhouse/index.js`, a probe in `src/metabase/driver/cube/probe.clj`, a check in `conformance/run.py`
  - Result: `unsupported` to `pass`, and `partial` on stock Cube. The total, three groupings, a filter, two measures and MCP are equal to `quantileExactInclusive` on ClickHouse.
- [x] T004 [US1] `mb.agg.percentile`: the same template and probe, a check with two levels in `conformance/run.py`
  - Result: `unsupported` to `pass`, and `partial` on stock Cube. The same comparisons, for the levels 0.9 and 0.05.
- [x] T005 [US1] `dimension.type.geo.view`: give a geo dimension of a view the SQL of its cube in `cube-clickhouse/index.js`
  - Result: `partial` to `pass`, and `partial` on stock Cube. The map columns of `customer_locations` are equal to `latitude`, `longitude` and `count()` of `users`.
- [x] T006 [US1] `restapi.compare_date_range`: find the Metabase question that compares date ranges of a measure, add a check in `conformance/run.py`
  - Result: `unsupported` to `pass`. One card with a date filter gives each of two ranges for four kinds of measure. One question with a custom column gives both ranges.
- [x] T007 [US1] `restapi.total_query`: find the Metabase question that counts all rows of a query with a limit, add a check in `conformance/run.py`
  - Result: `unsupported` to `pass`. A second stage, a question over a saved question and MCP give `count()` of the groups, next to a page of 7 rows.
- [x] T008 [US1] `measure.format.abbr`: look for a Metabase column setting for an abbreviated number
  - Result: Stays `partial`. Metabase has no column setting for `compact`, see research.md. The reason has the evidence.
- [x] T009 [US1] `measure.format.accounting`: look for a Metabase column setting for parentheses
  - Result: Stays `partial`. Metabase has no column setting for `negativeInParentheses`. The reason has the evidence.
- [x] T010 [US1] `dimension.format.abbr`: the same as T008 for a field
  - Result: Stays `partial`, the same limit as T008.
- [x] T011 [US1] `metric.format.abbr`: the same as T008 for the metric catalog
  - Result: Stays `partial`, the same limit as T008.
- [x] T012 [US1] `metric.format.abbr_1`: the same as T008 with one decimal
  - Result: Stays `partial`, the same limit as T008.
- [x] T013 [US1] `metric.format.accounting`: the same as T009 for the metric catalog
  - Result: Stays `partial`, the same limit as T009.
- [x] T014 [US1] `metric.format.accounting_0`: the same as T009 with no decimals
  - Result: Stays `partial`, the same limit as T009.
- [x] T015 [US1] `semantic.drill_members_native`: look for a Metabase object that can hold the drill members of a measure
  - Result: Stays `unsupported`. The drill to rows of Metabase removes `:fields`, and a driver has no hook. The reason has the evidence.
- [x] T016 [US1] `compile.xray.field_all_cards_run`: find the cards that stop, and make them give the truth in `src/metabase/driver/cube.clj`
  - Result: `partial` to `pass`. The check now fails on a card that stops. 15 of 15 cards are equal to the truth.
- [x] T017 [US1] `compile.xray.time_field_all_cards_run`: the same as T016 for a time field
  - Result: `partial` to `pass`. 15 of 15 cards are equal to the truth.

## Phase 3: User Story 2 - each open entry has a reason with evidence (P2)

- [x] T018 [US2] `measure.opt.rolling_type_deprecated`
  - Result: Stays `unsupported`. The options are deprecated in Cube, and the model uses `to_date`.
- [x] T019 [US2] `dimension.opt.order`
  - Result: `blocked` to `pass`. The driver writes the order of a dimension with `order: desc` before Metabase adds its own. Seven question forms and MCP are equal to `ORDER BY` on ClickHouse.
- [x] T020 [US2] `granularity.custom.offset`
  - Result: Stays `partial`. The fix is in Cube Store, in Rust. The reason has the evidence.
- [x] T021 [US2] `security.sql_user_impersonation`
  - Result: Stays `unsupported`. The driver connects as the person by design, see docs/security.md. This feature did not test it again on `v1.8.20`.
- [x] T022 [US2] `view.meta_auto_run`
  - Result: Stays `unsupported`. The key controls Cube Cloud workbooks.
- [x] T023 [US2] `view.meta_default_ui_filters`
  - Result: Stays `unsupported`. The key controls Cube Cloud workbooks.
- [x] T024 [US2] `preagg.rollup_lambda`
  - Result: `partial` to `pass`, and `partial` on stock Cube. The dialect reads all source rows before the client closes. The whole range is equal to the truth, and the check accepts no error with the dialect.
- [x] T025 [US2] `mb.other.actions`
  - Result: Stays `unsupported`. The Cube SQL API is read only.
- [x] T026 [US2] `mb.other.model_persistence`
  - Result: Stays `unsupported`. The Cube SQL API is read only.
- [x] T027 [US2] `mb.other.uploads`
  - Result: Stays `unsupported`. The Cube SQL API is read only.
- [x] T028 [US2] `compile.ch.decimal_above_double`
  - Result: Stays `partial`. The SQL API of Cube has the type Double, in Rust.
- [x] T029 [US2] `native.not_like_three_values`
  - Result: Stays `partial`. The corrections have a proof only at the top of `WHERE`, `HAVING` and `FILTER`. Other places need new proofs in `dev/fuzz`, and this feature does not add them.
- [x] T030 [US2] `legacy.view_default_filters`
  - Result: Stays `partial`. The legacy planner drops `default_filters`. It is not the default planner.
- [x] T031 [US2] `native.view_joined_dimension_weighted`
  - Result: Stays `partial`. The driver does not rewrite native SQL into the weighted form.
- [x] T032 [US2] `dimension.opt.propagate_filters_next_to_another_sub_query`
  - Result: Stays `partial`. The fix is cube-js/cube#12502, in the Rust planner. The reason has the evidence.
- [x] T033 [US2] `security.dev_mode_warning_for_hidden_members`
  - Result: Stays `partial`. Dev mode of Cube reads hidden members.
- [x] T034 [US2] `security.validate_echoes_the_request_limit`
  - Result: Stays `partial`. The endpoint of Metabase repeats the request for each driver.
- [x] T035 [US2] `preagg.build_range.date_range_over_the_end_reads_only_the_range`
  - Result: Stays `partial`. Documented behaviour of the REST API of Cube. The Metabase entry gives the truth.
- [x] T036 [US2] `preagg.build_range.whole_table_reads_only_the_range`
  - Result: Stays `partial`. Documented behaviour of the REST API of Cube. The Metabase entry gives the truth.
- [x] T037 [US2] `preagg.option.non_strict_inside_a_bucket_reads_whole_buckets`
  - Result: Stays `partial`. The option of the rollup asks for this behaviour.
- [x] T038 [US2] `preagg.type.count_distinct_approx_stops`
  - Result: Stays `partial`. Cube has no sketch that ClickHouse writes and Cube Store reads.
- [x] T039 [US2] `preagg.type.rollup_lambda_after_the_batch_with_source`
  - Result: `partial` to `pass`, and `partial` on stock Cube. The same fix as T024.
- [x] T040 [US2] `preagg.type.rollup_lambda_over_the_batch_with_source`
  - Result: `partial` to `pass`, and `partial` on stock Cube. The same fix as T024.
- [x] T041 [US2] `preagg.type.rollup_lambda_gap_loses_the_last_partition`
  - Result: Stays `partial`. Documented behaviour of Cube. The Metabase entry gives the truth.
- [x] T042 [US2] `preagg.type.original_sql_in_cube_store_with_an_index_stops`
  - Result: Stays `partial`. cube-js/cube#7420. The docs of Cube advise against this form.
- [x] T043 [US2] `zone.time_measure_repeated_hour_limit`
  - Result: Stays `partial`. The SQL API gives a time as text with no offset.

Each task of this phase has the same steps. Read the reason. Test the limit on Cube `v1.8.20`,
or read the code of the pinned version. Then close the entry, or put the layer and the evidence
into the reason in `conformance/features.yaml`.

## Phase 4: User Story 3 - the Cube Store pin (P3)

- [x] T044 [US3] Use the Cube pin for `cubejs/cubestore` in `dev/docker-compose.production.yml`
  - Result: The default tag is `v1.8.20`.
- [x] T045 [US3] Make `set_pin` in `.github/scripts/pins.py` change `dev/docker-compose.production.yml`, and make `read_pins` stop on a difference
  - Result: `pins.py set cube v1.9.0` changed both files, and `pins.py read` stops on a difference.

## Phase 5: Check

- [x] T046 Run `make conformance` on nettop on both variants and copy `conformance/features.yaml` to the repository
  - Result: Run of 2026-11-12 on nettop at commit `0ebe427`: `coverage: 1325/1469 pass on stock Cube` and `coverage: 1425/1469 pass on Cube with the ClickHouse dialect`. The only `fail` is `mb.expr.float`, an entry of feature 004.
- [x] T047 Run the adversarial review (agent `cube-reviewer`) on the new `pass` entries
  - Result: The review found no wrong number. It asked for `partial` on the two REST entries, cold reads in the lambda check, more percentile cases and a stricter x-ray count. Commit `fb44310` has these changes.
- [x] T048 Update `docs/` and `cube-clickhouse/README.md` for each limit that is closed
  - Result: `cube-clickhouse/README.md`, `docs/query-compilation.md`, `docs/semantic-sync.md`, `docs/cube-image.md` and `README.md` have the new state.
