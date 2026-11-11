# Implementation Plan: Adopt spec-kit

**Branch**: `001-adopt-spec-kit` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

## Summary

Commit the spec-kit scaffold that `specify init --here --integration claude --script sh` made,
ratify the constitution, and point AGENTS.md, the four agents and the two project skills at the
spec-kit workflow. Add the iMac and the nettop cleanup rule to AGENTS.md.

## Technical Context

**Tools**: spec-kit CLI 1.3.3.dev0, shell scripts (`--script sh`), Claude skills.
**Files**: `.specify/`, `.claude/skills/speckit-*`, `AGENTS.md`, `.claude/agents/*.md`,
`.claude/skills/deploy-nettop/SKILL.md`, `.claude/skills/run-conformance/SKILL.md`, `specs/`.
**Testing**: `yamllint --strict .`; a grep that each agent and skill file names `specs/`.

## Constitution Check

- I-V: the change touches no driver code and no status in `conformance/features.yaml`. Pass.
- Development Workflow: this feature introduces it. Pass.

## Project Structure

```text
.specify/            spec-kit scaffold and the constitution
.claude/skills/      spec-kit skills next to deploy-nettop and run-conformance
specs/NNN-slug/      one folder per feature: spec.md, plan.md, tasks.md
```
