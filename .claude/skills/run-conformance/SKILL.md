---
name: run-conformance
description: Run the conformance suite against the live demo stack and read the result. Use after a deploy, and before any statement about coverage.
---

# Run conformance

`AGENTS.md` has the definition of done. `conformance/features.yaml` is the only source of truth for
coverage, and you report only the number that `conformance/run.py` prints.

## Status: planned

`conformance/run.py` and `conformance/features.yaml` were not in the repository when this skill was
written. Before you continue:

```sh
git pull --ff-only
ls conformance/run.py conformance/features.yaml
```

If they are missing, say so and stop. Do not report coverage from any other source. If they are
present, read `conformance/run.py` for its arguments and correct this skill.

## Steps

1. Deploy the state you want to test. Use the `deploy-nettop` skill.

2. Run the suite on nettop.

   ```sh
   ssh nettop.local 'cd ~/metabase-cube-driver && python3 conformance/run.py'
   ```

3. Read the failures. For each failure, find the layer: driver, Cube, Metabase, or the check itself.

   ```sh
   ssh nettop.local 'cd ~/metabase-cube-driver/dev && docker compose logs --tail 200 cube'
   ssh nettop.local 'cd ~/metabase-cube-driver/dev && docker compose logs --tail 200 metabase'
   ```

4. Record the real status in `conformance/features.yaml`. Do not narrow a feature or weaken a check to
   make it pass. Give a reason for each `partial`, `blocked` or `unsupported`.

5. Report the number that `conformance/run.py` printed, and the failures. Do not round it and do not
   add your own estimate.
