---
name: run-conformance
description: Run the conformance suite against the live demo stack and read the result. Use after a deploy, and before any statement about coverage.
---

# Run conformance

`AGENTS.md` has the definition of done. `conformance/features.yaml` is the only source of truth for
coverage, and you report only the number that `conformance/run.py` prints.

## Steps

1. Deploy the state you want to test. Use the `deploy-nettop` skill.

2. Run the suite on nettop.

   ```sh
   ssh nettop.local 'cd ~/metabase-cube-driver; make conformance'
   ```

   This command does not use the secret store. It uses the service `metabase-conformance` on `127.0.0.1:3301`, with a new
   volume and a fixed throwaway admin. The service stops at the end of the run. The service `metabase`, its volume
   `metabase-data` and its admin do not change. See `conformance/README.md`.

   The run takes more than 15 minutes. If the SSH session can end before that, start the run with `nohup` and send the
   output to a file.

   Give options with `ARGS`, for example `make conformance ARGS="--only dimension.type.geo --no-write"`.

   The run rewrites the status lines in `conformance/features.yaml` on nettop. To keep these lines:

   1. Copy the file to the Mac.
   2. Commit the file on the Mac. Do not commit on nettop.
   3. Before the next `git pull`, run `git checkout conformance/features.yaml` on nettop.

3. Read the failures. For each failure, find the layer: driver, Cube, Metabase, or the check itself.

   ```sh
   ssh nettop.local 'cd ~/metabase-cube-driver/dev; docker compose logs --tail 200 cube'
   ssh nettop.local 'docker logs --tail 200 metabase-cube-driver-metabase-conformance-1'
   ```

4. Record the real status in `conformance/features.yaml`. Keep the feature and the check as
   they are, also if the check fails. Give a reason for each `partial`, `blocked` or `unsupported`. An entry with a check that cannot prove
   the whole feature has `cap: partial` and a reason.

5. Report the number that `conformance/run.py` printed, and the failures. Report the exact number, with no estimate.
