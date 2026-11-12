# Feature Specification: The stack and the suite on the iMac

**Feature Branch**: `002-imac-container-stack`
**Created**: 2026-11-11
**Status**: Done, with task T020 open
**Input**: Run the demo stack and the conformance suite on the iMac with Apple `container`. Today
they run only on nettop with `docker compose`.

<!-- ste: E1.8 — the section title comes from the spec-kit template -->

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An agent runs the suite on the iMac (Priority: P1)

nettop is short of disk space and runs other services. An agent on the iMac gives one command.
The command starts the services, runs the suite on stock Cube and with `cube-clickhouse`, and
prints one coverage line for each.

**Why this priority**: A coverage claim needs a live run. The iMac is the second machine that
can supply it.

**Independent Test**: Run `make conformance-imac` on the iMac. It prints two coverage lines.

**Acceptance Scenarios**:

<!-- ste: E4.2, E4.4, E3.3 — the Given/When/Then form comes from the spec-kit template -->
1. **Given** the iMac with Apple `container` and no Docker, **When** the agent runs
   `make conformance-imac`, **Then** the suite runs in the two variants and prints two coverage
   lines.
2. **Given** nettop, **When** the agent runs `make conformance`, **Then** the runner makes the
   same `docker` calls as before this feature.
3. **Given** an entry with a different result on the iMac and on nettop, **When** the agent
   reports, **Then** the report gives the cause and no status changes to hide it.

---

### User Story 2 - A person uses the demo stack on the iMac (Priority: P2)

A person starts the demo stack on the iMac and opens Metabase on the same loopback port as on
nettop. Later the person stops or resets the stack.

**Why this priority**: It lets a person look at the driver with no access to nettop.

**Independent Test**: `make up-imac` starts the services. Each address in the AGENTS.md table
answers. `make down-imac` stops them and the data stays. A reset removes the data.

**Acceptance Scenarios**:

<!-- ste: E4.2, E4.4, E3.3 — the Given/When/Then form comes from the spec-kit template -->
1. **Given** a stopped stack, **When** the person runs `make up-imac`, **Then** ClickHouse has the
   seed, each Cube answers `/readyz` and Metabase answers on `127.0.0.1:3300`.
2. **Given** a running stack, **When** the person runs the reset command, **Then** the containers
   and their data are gone and the next start loads the seed again.

---

### User Story 3 - An agent finds the iMac procedure (Priority: P3)

An agent reads AGENTS.md and `conformance/README.md` and finds the commands, the memory limits,
the place of the data and the known differences from nettop.

**Independent Test**: The two files name the commands and the limits.

### Edge Cases

- Apple `container` does not resolve a container name on a network. The services must find each
  other by the same names as in the compose file.
- Apple `container` has no restart policy. A service that stops stays stopped, and the run must
  show it.
- The external drive is not mounted. The script must stop with a clear message.
- Free space on the internal disk is below 10 GB. The script must stop before it pulls an image.
- The demo Metabase and the Metabase for conformance together do not fit in the memory budget.
- The screen of the Mac locks during a run. Apple `container` then cannot open a new file on the
  external drive.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: One script MUST start, stop and reset the stack on the iMac with Apple `container`
  only.
- **FR-002**: The stack on the iMac MUST have the services, loopback ports, throwaway
  credentials, seeds and mounts of `dev/docker-compose.yml` and of the conformance overlay.
- **FR-003**: Each service MUST reach the other services by the compose service name.
- **FR-004**: `conformance/run.py` MUST make its container calls through one helper. An
  environment variable selects the backend. With the variable not set, the commands MUST be the
  same as before.
- **FR-005**: The Makefile MUST have targets for the iMac: start, stop and conformance.
- **FR-006**: The data of ClickHouse and Metabase and the plugin directories MUST be on the
  external drive where Apple `container` permits it. A run MUST NOT need an unlocked screen.
- **FR-007**: The memory limits of the services that run together MUST fit in 16 GB with space
  for macOS. The documents MUST give the values.
- **FR-008**: Each pinned image MUST run as arm64, or with Rosetta if it has no arm64 variant. The
  documents MUST name each image that uses Rosetta.
- **FR-009**: AGENTS.md and `conformance/README.md` MUST describe the procedure for the iMac.
- **FR-010**: No status in `conformance/features.yaml` changes because of a run on the iMac
  unless nettop gives the same result.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The full suite runs on the iMac in the two variants and prints two coverage lines.
- **SC-002**: Each difference between the iMac result and the committed statuses has a recorded
  cause.
- **SC-003**: The sum of the memory limits for a conformance run is at most 12.5 GB. The first
  value was 12 GB. Cube with the dialect needs 4 GB, see research.md.
- **SC-004**: With the backend variable not set, a test of the helper shows the same command
  lines as the code before this feature.

## Assumptions

- The iMac is `iMac-Andrei`: Apple Silicon, 8 cores, 16 GB, Apple `container` 1.7.1.
- Agents cannot use `sudo`, and the local DNS domain of Apple `container` needs it. Thus the
  stack does not use that domain.
- The grid, the fuzz tools and the production overlay stay on nettop. They are not in this
  feature.
- nettop is not touched in this feature.
