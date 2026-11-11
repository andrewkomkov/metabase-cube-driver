# Tasks: The stack and the suite on the iMac

**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md)

## Phase 1: Research on the iMac

- [x] T001 Test name lookup, volumes, bind mounts and model reload with Apple `container`, and record the results in `specs/002-imac-container-stack/research.md`
- [x] T002 Pull each pinned image for `linux/arm64` and record which images need Rosetta in `dev/container/README.md` (FR-008)

## Phase 2: Foundation - the stack script

- [x] T003 Write `dev/container/stack.sh` with `up`, `stop`, `reset`, `check`, `hosts` and `status` for the services of `dev/docker-compose.yml` and `dev/docker-compose.conformance.yml` (FR-001, FR-002)
- [x] T004 Give each container the addresses of the other services in `/etc/hosts`, in `dev/container/stack.sh` (FR-003)
- [x] T005 Put the volume images on the external drive and check free disk space before a pull, in `dev/container/stack.sh` (FR-006)
- [x] T006 Set a memory limit for each service in `dev/container/stack.sh`, at most 12 GB for a conformance run (FR-007, SC-003)

## Phase 3: User Story 1 - the suite on the iMac (P1)

- [x] T007 [US1] Put the `docker` calls of `conformance/run.py` behind `stack_exec`, `stack_logs`, `stack_log_mark` and `stack_logs_since`, selected by `STACK` (FR-004)
- [x] T008 [US1] Compare the command lines of the helper with `STACK` not set against the old code of `conformance/run.py` (SC-004)
- [x] T009 [US1] Let `conformance/run_isolated.sh` start the services, load the extra seed and make a new Metabase with the two backends (FR-004)
- [x] T010 [US1] Add the target `conformance-imac` to `Makefile` (FR-005)
- [ ] T011 [US1] Run `make conformance-imac` on the iMac in the two variants and record the two coverage lines (SC-001)
- [ ] T012 [US1] Compare the iMac result with the statuses in `conformance/features.yaml` and record the cause of each difference (FR-010, SC-002)

## Phase 4: User Story 2 - the demo stack on the iMac (P2)

- [x] T013 [US2] Add the targets `up-imac`, `down-imac` and `reset-imac` to `Makefile` (FR-005)
- [ ] T014 [US2] Run `make up-imac`, check each address of the demo services, then check `stop` and `reset` of `dev/container/stack.sh`

## Phase 5: User Story 3 - the documents (P3)

- [x] T015 [P] [US3] Write `dev/container/README.md`: the commands, the memory limits, the place of the data, the differences from nettop (FR-009)
- [x] T016 [P] [US3] Describe the iMac procedure in the section "The iMac" of `AGENTS.md` (FR-009)
- [x] T017 [P] [US3] Describe the iMac run and the variable `STACK` in `conformance/README.md` (FR-009)

## Phase 6: Check

- [x] T018 Run `shellcheck` on `dev/container/stack.sh` and `conformance/run_isolated.sh`, `python3 -m py_compile conformance/run.py` and `yamllint --strict .`

## Dependencies

- Phase 2 needs Phase 1. Phases 3 and 4 need Phase 2. T011 needs T007 to T010. T012 needs T011.
- T015 to T017 change different files and can run at the same time.
