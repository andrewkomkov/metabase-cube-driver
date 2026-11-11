# Implementation Plan: Close the open entries

**Branch**: `003-close-open-entries` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

## Summary

Read the reason of each of the 41 open entries. Find the layer that stops the feature. Where a
layer of this repository can close the limit, change it and prove the result on nettop. Where
Cube, Metabase or ClickHouse stops the feature, keep the status and put the evidence into the
reason. [research.md](research.md) has the decision for each entry.

## Technical Context

**Layers**:

- `cube-clickhouse/index.js`: patches for stock Cube. It runs in the Cube process, so it can
  change the JavaScript of Cube. It cannot change the Rust planner or Cube Store.
- `src/metabase/driver/`: the driver. A probe in `cube/probe.clj` asks Cube what it can do.
- `conformance/run.py` and `conformance/features.yaml`: the checks and the matrix. `cap_stock`
  and `reason_stock` record a limit that only stock Cube has.
- `dev/cube/model/`: the demo model.
- `.github/scripts/pins.py`: the pin-update script.

**Testing**: `make conformance ARGS="--only <ids>"` on nettop, inside
`flock ~/mcd-conformance.lock`. A scratch Cube container on nettop (`mcd-003-cube`, ports 4460
and 15460) tests a dialect change before a commit. The full suite runs once at the end.

**Constraints**: The pinned versions stay. Metabase and Cube are not changed. Free disk on
nettop stays above 15 GB.

## Constitution Check

- I. Each new `pass` has a check that compares with ClickHouse. Pass.
- II. The dialect changes are for ClickHouse. Other data sources keep their path. Pass.
- III. No check gets weaker. An entry with a real limit keeps its status. Pass.
- IV. No pin changes. The Cube Store tag follows the Cube pin that exists. Pass.
- V. A fix for Cube goes to `cube-clickhouse/`. Pass.

## Project Structure

```text
cube-clickhouse/index.js          dialect patches
src/metabase/driver/cube*.clj     driver and probes
conformance/run.py, features.yaml checks and matrix
dev/cube/model/                   demo model
.github/scripts/pins.py           pin-update script
specs/003-close-open-entries/     spec.md, plan.md, research.md, tasks.md
```

## Phases

1. Research: one decision for each entry, in `research.md`.
2. Fixes, in order of value: median and percentile, the geo dimension of a view, the REST-only
   query forms, the number formats, the x-rays, then the other entries.
3. Reasons: each entry that stays open gets the layer and the evidence.
4. The Cube Store pin.
5. The full suite on both variants, the adversarial review, and the statuses of that run.
