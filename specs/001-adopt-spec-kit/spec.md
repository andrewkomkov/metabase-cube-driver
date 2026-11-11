# Feature Specification: Adopt spec-kit

**Feature Branch**: `001-adopt-spec-kit`
**Created**: 2026-11-11
**Status**: Done
**Input**: Adopt spec-kit in the repository. Ratify a constitution from AGENTS.md. Point every
agent and skill at spec-kit. Record the iMac as a second test machine and the nettop cleanup rule.

<!-- STE deviation: the section titles and the Given/When/Then lines follow the spec-kit template. -->

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An agent starts work through a spec (Priority: P1)

An agent opens the repository to fix or add something. It reads AGENTS.md and finds that each
change needs a numbered feature folder with a spec, a plan and tasks. It also finds the
constitution and treats it as binding.

**Why this priority**: Without this rule, work goes straight to code and the documents come
late or are missing. That is the failure the owner wants to stop.

**Independent Test**: Read AGENTS.md, the constitution and each project agent file. Each one
names the spec-kit workflow and the folder `specs/`.

**Acceptance Scenarios**:

1. **Given** a new agent session, **When** the agent reads AGENTS.md, **Then** it finds the rule
   to work through `specs/NNN-slug/` and a link to the constitution.
2. **Given** the agent `cube-driver-dev`, **When** it reads its own file, **Then** the file tells
   it to work against the `tasks.md` of the active feature.

---

### User Story 2 - An agent tests on the iMac (Priority: P2)

nettop is short of disk space and runs other services. The owner wants agents to test on the
iMac too. An agent reads AGENTS.md and finds the iMac as a second test machine, with the rule to
use Apple `container` and the external drive.

**Why this priority**: It unblocks live checks when nettop is busy or full.

**Independent Test**: AGENTS.md has a section for the iMac with its tool and storage rules.

**Acceptance Scenarios**:

1. **Given** nettop has less than 15 GB free, **When** an agent needs a live check, **Then**
   AGENTS.md tells it that the iMac is the other place to run the stack.

---

### User Story 3 - An agent frees disk on nettop safely (Priority: P3)

An agent finds nettop low on disk. AGENTS.md tells it which images it may remove: only images of
this compose project that no container uses.

**Independent Test**: AGENTS.md states the cleanup rule.

### Edge Cases

- The scaffold adds files under `.claude/skills/`. They must not replace the project skills
  `deploy-nettop` and `run-conformance`.
- An image of an old pin can still be the default of a service in a compose file. Such an image
  is in use and stays.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The repository MUST contain the spec-kit scaffold (`.specify/`) and the spec-kit
  skills for Claude.
- **FR-002**: `.specify/memory/constitution.md` MUST hold a ratified constitution. Its principles
  come from AGENTS.md.
- **FR-003**: AGENTS.md MUST tell every agent to work through spec-kit and to read the
  constitution.
- **FR-004**: Each file in `.claude/agents/` and the skills `deploy-nettop` and `run-conformance`
  MUST name the spec-kit workflow.
- **FR-005**: AGENTS.md MUST describe the iMac as a second test machine (Apple `container`,
  output on the external drive).
- **FR-006**: AGENTS.md MUST state the nettop cleanup rule.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of the agent files and the two project skills name the spec-kit workflow.
- **SC-002**: The constitution has no placeholder tokens left.
- **SC-003**: Lint (`yamllint --strict .`) stays green after the change.

## Assumptions

- The spec-kit CLI version on the Mac (1.3.3.dev0) is the version to commit.
- The iMac is the Mac on which the agents run (`iMac-Andrei`).
- The work that runs the stack on the iMac is a separate feature.
