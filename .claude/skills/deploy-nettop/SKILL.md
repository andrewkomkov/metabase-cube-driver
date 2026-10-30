---
name: deploy-nettop
description: Deploy the committed state of this repository to the demo stack on nettop.local. Use before a live check of a driver, model or compose change.
---

# Deploy to nettop

`AGENTS.md` has the rules for this host. Other production services run there. Do not skip them.

## Steps

1. Commit and push from the Mac. Do not commit on nettop.

   ```sh
   git push origin main
   ```

2. Check free disk space on `/`. Stop and report if it is below 15 GB.

   ```sh
   ssh nettop.local 'df -h /'
   ```

3. Update the clone on nettop.

   ```sh
   ssh nettop.local 'cd ~/metabase-cube-driver && git pull --ff-only'
   ```

4. Start or update ClickHouse and Cube. The compose file sets the project name.

   ```sh
   ssh nettop.local 'cd ~/metabase-cube-driver/dev && docker compose up -d clickhouse cube'
   ```

5. Check that Cube is ready and that it agrees with ClickHouse. Both commands must show the same
   count, which is 5000 for the demo seed.

   ```sh
   ssh nettop.local 'curl -fsS http://127.0.0.1:4400/readyz; echo'
   ssh nettop.local 'cd ~/metabase-cube-driver/dev && docker compose exec -T clickhouse clickhouse-client -u shop --password shop-dev-only -q "SELECT count() FROM shop.orders"'
   ssh nettop.local "curl -fsS -G http://127.0.0.1:4400/cubejs-api/v1/load --data-urlencode 'query={\"measures\":[\"orders.count\"]}' | jq '.data'"
   ```

   Cube can return HTTP 400 for up to a minute after a restart. Wait and try again.

## Planned

These steps need files that were not in the repository when this skill was written. Run
`git pull`, then check that the file exists. If it does not, tell the user and stop at step 5.

6. Make the jar. Needs `scripts/package.sh`.

   ```sh
   ssh nettop.local 'cd ~/metabase-cube-driver && scripts/package.sh'
   ```

   `AGENTS.md` says that this makes `dev/plugins/cube.metabase-driver.jar`. Check where the script
   expects to run and which tools it needs, then correct this step.

7. Start Metabase, so that it loads the new jar. The service reads the plugin at start.

   ```sh
   ssh nettop.local 'cd ~/metabase-cube-driver/dev && docker compose up -d --force-recreate metabase'
   ```

8. Wait for Metabase.

   ```sh
   ssh nettop.local 'for i in $(seq 1 60); do curl -fsS http://127.0.0.1:3300/api/health && exit 0; sleep 5; done; exit 1'
   ```
