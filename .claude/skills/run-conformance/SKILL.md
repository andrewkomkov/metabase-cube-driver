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

   The runner needs the admin password of the demo Metabase. Send it on stdin, so that it stays out of the command line
   on nettop. The name is `METABASE_CUBE_DRIVER_ADMIN_PASSWORD`.

   ```sh
   secret run --mask P=METABASE_CUBE_DRIVER_ADMIN_PASSWORD -- sh -c 'printf "%s\n" "$P" | ssh nettop.local "read -r METABASE_ADMIN_PASSWORD; export METABASE_ADMIN_PASSWORD; cd ~/metabase-cube-driver && python3 conformance/run.py"'
   ```

   If Metabase has no admin, the runner makes one and connects Cube. The run rewrites the status lines in
   `conformance/features.yaml` on nettop. Do not commit there: copy the file to the Mac, commit it there, and run
   `git checkout conformance/features.yaml` on nettop before the next `git pull`.

3. Read the failures. For each failure, find the layer: driver, Cube, Metabase, or the check itself.

   ```sh
   ssh nettop.local 'cd ~/metabase-cube-driver/dev && docker compose logs --tail 200 cube'
   ssh nettop.local 'cd ~/metabase-cube-driver/dev && docker compose logs --tail 200 metabase'
   ```

4. Record the real status in `conformance/features.yaml`. Do not narrow a feature or weaken a check to
   make it pass. Give a reason for each `partial`, `blocked` or `unsupported`. An entry with a check that cannot prove
   the whole feature has `cap: partial` and a reason.

5. Report the number that `conformance/run.py` printed, and the failures. Do not round it and do not
   add your own estimate.
