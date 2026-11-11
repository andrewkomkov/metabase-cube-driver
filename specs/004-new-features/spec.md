# Feature Specification: New features in the matrix

**Feature Branch**: `004-new-features`
**Created**: 2026-11-11
**Status**: In progress
<!-- ste: all — the request, word for word in substance -->
**Input**: Find Cube v1.8.20 and Metabase v0.65.3.6 features that have no entry in
`conformance/features.yaml`. Rank them by value for people and for MCP agents. Add entries with
executable checks, make the top ones work, and prove them on the live stack. At least 10 new
entries reach `pass`. An entry that cannot pass gets the real status and a reason.

<!-- ste: E1.8 — a section title of the spec-kit template -->
## User Scenarios & Testing *(mandatory)*

### User Story 1 - An agent uses each MCP tool and argument on Cube data (Priority: P1)

An AI agent connects to the Metabase MCP server. It browses a collection, lists models and reads
the values of a field. It runs a query with an expression, a join, a sort, a limit, bins or a
second stage. It sends SQL with values behind placeholders. Each answer is the same as the
answer of ClickHouse.

**Why this priority**: The MCP server is the path that agents use. A tool argument with no
check can give a wrong number and nobody sees it.

**Independent Test**: Run the `mcp.*` entries of this feature on the live stack. Each one
compares rows with a direct SQL query on ClickHouse.

**Acceptance Scenarios**:

<!-- ste: all — the Given/When/Then form of the spec-kit template -->
1. **Given** a saved question in a collection, **When** the agent browses the collection and
   runs the question, **Then** the rows are equal to the truth.
<!-- ste: all — the Given/When/Then form of the spec-kit template -->
2. **Given** a query with an expression, a join or a second stage, **When** the agent sends it
   to `execute_query`, **Then** the rows are equal to the truth.
<!-- ste: all — the Given/When/Then form of the spec-kit template -->
3. **Given** SQL with a placeholder and a value, **When** the agent sends them to
   `execute_sql`, **Then** the value is bound and the rows are equal to the truth.

---

### User Story 2 - A person uses the rest of the query builder (Priority: P2)

A person in Metabase uses a function, a filter or an export format that had no entry in the
matrix. The same applies to a dashboard filter type and a native variable type. The result is the same as the answer of
ClickHouse, or Metabase does not offer the function and the matrix tells why.

**Why this priority**: People find these functions in the menus of Metabase. Each one is a
promise of the product.

**Independent Test**: Run the `mb.*` entries of this feature on the live stack.

**Acceptance Scenarios**:

<!-- ste: all — the Given/When/Then form of the spec-kit template -->
1. **Given** a dashboard with a time grouping filter, **When** the person selects quarter,
   **Then** the card groups by quarter and the rows are equal to the truth.
<!-- ste: all — the Given/When/Then form of the spec-kit template -->
2. **Given** a custom expression with a cast function, **When** the query runs, **Then** each
   row is equal to the truth.
<!-- ste: all — the Given/When/Then form of the spec-kit template -->
3. **Given** a function that Cube cannot run, **When** the matrix is read, **Then** the entry
   has the real status and a reason.

---

### User Story 3 - A model author uses the rest of the Cube model options (Priority: P3)

A model author uses the options in the list below. Each one shows in Metabase and gives the
number of ClickHouse.

- A segment with `meta`, a private segment, and a segment in a view.
- A rolling measure in a view, and a count with `sql`.
- A dimension that reads a joined cube, and a hierarchy with a level of a joined cube.
- A legacy relationship name, and a cube whose SQL reads the SQL of another cube.

**Why this priority**: These options are less frequent than the options that the matrix
already has, but a model in production uses them.

**Independent Test**: Run the `measure.*`, `dimension.*`, `cube.*`, `segment.*`, `view.*`,
`join.*` and `hierarchy.*` entries of this feature on the live stack.

**Acceptance Scenarios**:

<!-- ste: all — the Given/When/Then form of the spec-kit template -->
1. **Given** a view that includes a segment, **When** a person filters by the segment,
   **Then** the rows are equal to the truth.
<!-- ste: all — the Given/When/Then form of the spec-kit template -->
2. **Given** a hierarchy with a level of a joined cube, **When** an agent reads the table
   description, **Then** the level has the name of its cube, and a query by the levels is equal
   to the truth.

### Edge Cases

- A relative date filter moves with the date of the run. The check computes its range from the
  date, and it fails if the range keeps no rows or all rows.
- A new cube adds words to the search index. The names in the new model file do not use the
  words that the other checks search for.
- A feature that the pinned Cube rejects cannot have a model. Its entry has the status
  `unsupported` and the text of the error.
- The 41 entries that were not `pass` before this feature belong to feature 003. This feature
  does not change them.

## Ranked gap list

The rank is the value for people and for MCP agents. A higher rank is worked first. The column
"Entry" names the ids. The status of each entry is in `conformance/features.yaml` only.

| Rank | Gap | Entry |
| --- | --- | --- |
| 1 | MCP `execute_query` with an expression, a sort and a limit, an explicit join, two stages, bins, and `validate_only` | `mcp.execute_query.expression`, `.order_limit`, `.explicit_join`, `.two_stages`, `.binning`, `.validate_only` |
| 2 | MCP `execute_sql` with `template_tag_values` | `mcp.execute_sql.template_tag_values` |
| 3 | MCP `browse_collection`, `browse_data` with `list_models`, with `search`, and with the values of a field | `mcp.browse_collection`, `mcp.browse.list_models`, `mcp.browse.list_tables_search`, `mcp.browse.get_fields_values` |
| 4 | MCP `run_saved_question` with parameters | `mcp.run_saved_question.parameters` |
| 5 | Dashboard filter types: time grouping, relative date, month and year, quarter and year, text contains, boolean, ID | `mb.dashboard.filter_time_grouping`, `.filter_relative_date`, `.filter_month_year`, `.filter_quarter_year`, `.filter_string_contains`, `.filter_boolean`, `.filter_id` |
| 6 | A segment in a view, and a rolling measure in a view | `view.segment`, `view.rolling_measure` |
| 7 | Filters: `in`, `not-in`, relative range with an offset, relative range with the current period, exclude hours and quarters, not between dates, `during`, map box | `mb.filter.in`, `.not_in`, `.relative_with_offset`, `.relative_include_current`, `.exclude_hour_of_day`, `.exclude_quarter_of_year`, `.not_date_between`, `.during`, `.inside` |
| 8 | Expression functions: `text`, `integer`, `float`, `date`, `datetimeSubtract`, `week` with a mode, `weekday` with a mode, `now`, `today`, a literal, `host`, `domain`, `path`, `splitPart` | `mb.expr.text`, `.integer`, `.float`, `.date`, `.datetime_subtract`, `.get_week_iso`, `.get_week_us`, `.get_day_of_week_iso`, `.now`, `.today`, `.literal`, `.host`, `.domain`, `.path`, `.split_part` |
| 9 | Native variables: time grouping, boolean, a reference to a saved question | `mb.native.time_grouping_param`, `.param_boolean`, `.card_reference` |
| 10 | Export to XLSX | `mb.export.xlsx` |
| 11 | Auto bin of a number | `mb.breakout.bin_default` |
| 12 | Segment `meta` and a private segment | `segment.meta`, `segment.public_false` |
| 13 | A hierarchy with a level of a joined cube | `hierarchy.level_of_a_joined_cube` |
| 14 | A dimension that reads a dimension of a joined cube | `dimension.opt.cross_cube_reference` |
| 15 | A count with `sql` | `measure.type.count_with_sql` |
| 16 | A cube whose SQL reads `sql()` of another cube | `cube.sql_function_of_another_cube` |
| 17 | The legacy relationship names (`belongs_to`) | `join.legacy_relationship_name` |
| 18 | Measure type `running_total` | `measure.type.running_total` |
| 19 | Right join and full join in the query builder | `mb.join.explicit_right`, `mb.join.explicit_full` |
| 20 | The GraphQL API of Cube | `graphql.api` |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each gap in the ranked list MUST have an entry in `conformance/features.yaml`.
  The entry has one feature, a status and, if a check is possible, an executable check.
- **FR-002**: A check MUST compare its result with a direct SQL query on the demo ClickHouse.
- **FR-003**: A check of a relative date MUST fail when its range keeps no rows or all rows.
- **FR-004**: An entry with the status `partial`, `blocked` or `unsupported` MUST have a reason
  that names the layer: the driver, Cube or Metabase.
- **FR-005**: The description of a table MUST name the cube of a hierarchy level that comes
  from a joined cube.
- **FR-006**: The demo model MUST have each new Cube option in one new file. The searches of
  the other checks MUST NOT match its names.
- **FR-007**: The user documents in `docs/` MUST describe each new feature and each new limit.
- **FR-008**: The entries that feature 003 owns MUST stay as they are. See the last edge case.

### Key Entities

- **Entry**: one record in the matrix. It has an id, an area, a title, a status for each Cube
  variant, a check and, if necessary, a reason.
- **Check**: a function in the runner that reads Metabase or MCP and compares with the truth.
- **Demo model file**: `ledger.yml`, the cubes and the view that use the new Cube options.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: At least 10 new entries have the status `pass` after a run on the live stack.
  This applies to stock Cube and to Cube with the dialect.
- **SC-002**: 100% of the new entries that are not `pass` have a reason.
- **SC-003**: No entry that was `pass` before this feature has another status after the full
  run of this feature.
- **SC-004**: The coverage line that the runner prints counts the new entries.

## Assumptions

- The live stack is nettop. Another agent uses it too, so each run takes the lock file.
- Metabase code is read at the tag `v0.65.3.6`. This session read it from the public repository
  at that tag, because the local reference clone was not available to this worktree.
- An entry with the status `unsupported` and no check is coverage of the truth, as for
  `mb.agg.median`.
