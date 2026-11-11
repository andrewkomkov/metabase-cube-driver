# Feature Specification: Close the open entries

**Feature Branch**: `003-close-open-entries`
**Created**: 2026-11-11
**Status**: In progress
**Input**: `conformance/features.yaml` has 1402 entries, and 41 are not `pass`. For each of them,
find out if the driver, the `cube-clickhouse` dialect, the demo model or the check can make the
whole feature work at the pinned versions. Implement it and prove it on the live stack. Where the
limit is real, keep the status and give evidence in the reason. Also make
`dev/docker-compose.production.yml` follow the Cube pin for Cube Store.

<!-- STE deviation: the section titles and the Given/When/Then lines follow the spec-kit template. -->

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A person or an MCP agent uses a feature that was closed (Priority: P1)

A person in Metabase, or an agent on the Metabase MCP server, asks for a median, a percentile, a
comparison of two date ranges, a total next to the rows, an abbreviated number, the drill members
of a measure, a map on a view or an x-ray. Today the entry for each of these is `unsupported` or
`partial`. After this feature, each one that the pinned versions can do gives the number that
ClickHouse gives.

**Why this priority**: These are the features that people and agents use most.

**Independent Test**: `make conformance ARGS="--only <id>"` on the live stack shows `pass` for
the entry, and the check compares the result with a direct query on ClickHouse.

**Acceptance Scenarios**:

1. **Given** an entry with status `unsupported` that the stack can do, **When** the work is done,
   **Then** the entry has a check, the check ran live, and the status is `pass`.
2. **Given** an entry with status `partial`, **When** the limit behind it can be removed,
   **Then** the entry loses `cap: partial` and the same check, or a stronger one, shows `pass`.

---

### User Story 2 - A reader of the matrix trusts each open entry (Priority: P2)

A reader opens an entry that is still not `pass`. The reason names the layer that stops the
feature (Cube, Metabase or ClickHouse), the pinned version, and the evidence: a source file, an
error text or an upstream issue.

**Why this priority**: An open entry with a weak reason hides work that can be done, or hides a
wrong number.

**Independent Test**: Each entry that is not `pass` has a reason with the layer and the evidence.

**Acceptance Scenarios**:

1. **Given** an entry whose limit is real, **When** the work is done, **Then** the status stays
   and the reason names the layer and the evidence at the pinned version.

---

### User Story 3 - The production compose file follows the Cube pin (Priority: P3)

`dev/docker-compose.production.yml` starts Cube Store `v1.8.15` while Cube is `v1.8.20`. A person
who runs `make production` tests a pair of versions that nobody releases together.

**Independent Test**: The default tag of Cube Store in the file is equal to the Cube pin, and the
pin-update script changes it together with the other files.

### Edge Cases

- A check must not pass on an empty result or on a row count only.
- A feature must not be narrowed. If only a part works, the status stays `partial`.
- A change for one entry must not break another entry. The full suite runs at the end.
- Two other agents push to `main` and one of them uses the live stack. Runs do not overlap.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each of the 41 open entries MUST have one task and one recorded result: a new
  status with a live check, or the same status with a reason that names the layer and the evidence.
- **FR-002**: An entry MUST get status `pass` only from a check that ran on the live stack and
  compared the result with a direct query on the demo ClickHouse.
- **FR-003**: A check MUST NOT be made weaker and a feature MUST NOT be narrowed.
- **FR-004**: Median and percentile MUST work from the Metabase query builder, if Cube can
  compile them for ClickHouse with the dialect.
- **FR-005**: The number formats of Cube (`abbr`, `accounting`) MUST show in Metabase as far as
  a Metabase column setting can show them.
- **FR-006**: `dev/docker-compose.production.yml` MUST use the Cube pin for Cube Store, and the
  pin-update script MUST change this file.
- **FR-007**: The full suite MUST run once at the end on both variants, and the statuses in the
  repository MUST be the statuses of that run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The coverage line that `conformance/run.py` prints is higher than before on the
  variant with the dialect, and no entry that was `pass` changes to another status.
- **SC-002**: 100% of the entries that are not `pass` have a reason with the layer and the
  evidence.
- **SC-003**: The Cube Store tag in the production compose file is equal to the Cube pin.

## Assumptions

- The pinned versions stay: Metabase `v0.65.3.6`, Cube `v1.8.20`, ClickHouse `26.10.3-alpine`.
- Metabase and Cube are not changed. A fix for Cube goes to `cube-clickhouse/`.
- The live stack is nettop. A lock file keeps the runs of this feature and of feature 004 apart.
