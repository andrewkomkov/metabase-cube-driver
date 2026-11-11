# Feature Specification: Close the open entries

**Feature Branch**: `003-close-open-entries`
**Created**: 2026-11-11
**Status**: In progress
**Input**: `conformance/features.yaml` has 1402 entries, and 41 are not `pass`. Find the layer
that stops each of them. Close the limit where a layer of this repository can do it, and prove
the result on the live stack. Where the limit is real, the status stays and the reason gives
the evidence.
Also make `dev/docker-compose.production.yml` follow the Cube pin for Cube Store.

<!-- STE deviation: the section titles and the Given/When/Then lines follow the spec-kit template. -->

## User Scenarios and Testing *(mandatory)*

### User Story 1 - A person or an MCP agent uses a feature that was closed (Priority: P1)

A person in Metabase, or an agent on the Metabase MCP server, asks for a number. The examples
are a median, a percentile, a comparison of two date ranges, and the total of a query with a
limit. Other examples are a map on a view, an x-ray, and the default order of a dimension.
Before this feature, the entry for each of these is `unsupported`, `blocked` or `partial`. After
it, each one that the pinned versions can do gives the number that ClickHouse gives.

**Why this priority**: People and agents use these features most.

**Independent Test**: `make conformance ARGS="--only <id>"` on the live stack shows `pass` for
the entry. The check compares the result with a direct query on ClickHouse.

**Acceptance Scenarios**:

1. **Given** an entry with status `unsupported` for a feature that the stack has, **Then** the
   entry has a check that ran live, and its status is `pass`.
2. **Given** an entry with status `partial` and a limit that this repository closes, **Then**
   the entry has no `cap: partial`, and the check shows `pass`.

---

### User Story 2 - A reader of the matrix trusts each open entry (Priority: P2)

A reader opens an entry that is not `pass`. The reason names the layer that stops the feature:
Cube, Metabase or ClickHouse. It also gives the evidence: a source file, an error text or an
upstream issue.

**Why this priority**: An open entry with a weak reason hides possible work, or a wrong number.

**Independent Test**: Each entry that is not `pass` has a reason with the layer and the evidence.

**Acceptance Scenarios**:

1. **Given** an entry with a real limit, **Then** the status stays, and the reason names the
   layer and the evidence.

---

### User Story 3 - The production compose file follows the Cube pin (Priority: P3)

`dev/docker-compose.production.yml` starts Cube Store `v1.8.15` while Cube is `v1.8.20`. A person
who runs `make production` tests a pair of versions that nobody releases together.

**Independent Test**: The default tag of Cube Store in the file is equal to the Cube pin. The
pin-update script changes this file together with `dev/docker-compose.yml`.

### Edge Cases

- A check must fail on an empty result. A row count alone is not a comparison.
- A feature keeps its full scope. If only a part works, the status stays `partial`.
- A change for one entry can break another entry. The full suite runs at the end.
- Two other agents push to `main`, and one of them uses the live stack. A lock keeps the runs
  apart.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each of the 41 open entries MUST have one task and one recorded result. The
  result is a new status with a live check, or the same status with the layer and the evidence.
- **FR-002**: An entry MUST get status `pass` only from a check that ran on the live stack. The
  check MUST compare the result with a direct query on the demo ClickHouse.
- **FR-003**: Each check MUST stay as strict as before, and each feature MUST keep its scope.
- **FR-004**: Median and percentile MUST work from the Metabase query builder, if Cube can
  compile them for ClickHouse with the dialect.
- **FR-005**: The number formats of Cube (`abbr`, `accounting`) MUST show in Metabase as far as
  a Metabase column setting can show them.
- **FR-006**: `dev/docker-compose.production.yml` MUST use the Cube pin for Cube Store, and the
  pin-update script MUST change this file.
- **FR-007**: The full suite MUST run once at the end on both variants. The statuses in the
  repository MUST be the statuses of that run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The coverage line of `conformance/run.py` for the variant with the dialect is
  higher than before. No entry that was `pass` has another status.
- **SC-002**: 100% of the entries that are not `pass` have a reason with the layer and the
  evidence.
- **SC-003**: The Cube Store tag in the production compose file is equal to the Cube pin.

## Assumptions

- The pinned versions stay: Metabase `v0.65.3.6`, Cube `v1.8.20`, ClickHouse `26.10.3-alpine`.
- This feature does not change Metabase or Cube. A fix for Cube goes to `cube-clickhouse/`.
- The live stack is nettop. A lock file keeps the runs of this feature and of feature 004 apart.
