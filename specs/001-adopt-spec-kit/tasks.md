# Tasks: Adopt spec-kit

**Input**: [spec.md](spec.md), [plan.md](plan.md)

## Phase 1: Scaffold

- [x] T001 Run `specify init --here --force --non-interactive --integration claude --script sh`
- [x] T002 Ratify `.specify/memory/constitution.md` from AGENTS.md (FR-002)
- [x] T003 Point `.specify/feature.json` at `specs/001-adopt-spec-kit`

## Phase 2: User Story 1 - work goes through a spec (P1)

- [x] T004 [US1] Add a spec-kit section to `AGENTS.md` (FR-003)
- [x] T005 [P] [US1] Name the workflow in each `.claude/agents/*.md` (FR-004)
- [x] T006 [P] [US1] Name the workflow in `.claude/skills/deploy-nettop/SKILL.md` and `run-conformance/SKILL.md` (FR-004)

## Phase 3: User Story 2 - the iMac (P2)

- [x] T007 [US2] Describe the iMac test machine in `AGENTS.md` (FR-005)

## Phase 4: User Story 3 - nettop cleanup (P3)

- [x] T008 [US3] State the nettop cleanup rule in `AGENTS.md` (FR-006)

## Phase 5: Check

- [x] T009 Run `yamllint --strict .` and the grep for `specs/` in agent and skill files (SC-001, SC-003)
