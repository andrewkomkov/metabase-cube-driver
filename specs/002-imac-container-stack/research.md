# Research: The stack and the suite on the iMac

Each decision below comes from a test on the iMac with Apple `container` 1.7.1.

## Name lookup between containers

- **Decision**: write the addresses into `/etc/hosts` of each running container after each start.
- **Rationale**: a container on a network from `container network create` got `NXDOMAIN` for the
  name of another container. An alias with a full domain name gave the same result. The local
  DNS domain (`container system dns create`) needs `sudo`, and it gives names with a domain, not
  the plain compose names. `/etc/hosts` in a container is a normal file that root can write.
- **Alternatives**: a bind mount of one hosts file into each container. The mount is a copy, so a
  later change on the host did not show in the container. A DNS container needs a fixed address,
  and `container run` has no option for an address.

## Data of ClickHouse and Metabase

- **Decision**: named volumes in the application directory, on the internal disk. `STACK_VOLUMES`
  moves the image of each new volume to another directory and leaves a symbolic link.
- **Rationale**: a bind mount of a directory on the external drive stopped ClickHouse with
  `chown: /var/lib/clickhouse/: Operation not permitted`. A volume behind a symbolic link to the
  external drive took 200 MB of writes and kept them for the next container. But it cannot
  start while the screen is locked, see the next section.
- **Alternatives**: `container system start --app-root` moves all data of Apple `container`. It
  restarts the services of each project on this Mac, so this feature does not do it.

## The external drive and a locked screen

- **Decision**: the files that a container mounts and the volume images stay on the internal
  disk. A checkout below `/Volumes` gets a copy of the mounted files in
  `~/.local/state/metabase-cube-driver/stage`. The script stops a start after 90 seconds.
- **Rationale**: the display went off at 17:52 and the screen locked. From 18:05, each start
  with a bind mount or a volume image on `/Volumes/SanDisk2Tb` waited with no end. A sample of
  `container-runtime-linux` showed the wait in `open`, below `VZSharedDirectory`. A start with a
  bind mount from `/private/tmp` or with no mount took 1 second at the same time. While one
  start waits, the service accepts no other start, stop or `exec`. A start of another project
  on this Mac blocked this stack in the same way.
- **Alternatives**: the external drive for all data, with the rule that the screen stays
  unlocked. An agent cannot keep that rule.

## Images

- **Decision**: the images stay in the application directory on the internal disk. The script
  checks for 10 GB of free space before a pull.
- **Rationale**: only `--app-root` moves the image store, and the section on data tells why
  this feature does not use it.

## Model files

- **Decision**: bind mounts from the checkout, the same as in the compose file.
- **Rationale**: Cube in dev mode showed a new cube 3 seconds after a new file appeared in
  `dev/cube/model`. It removed the cube after the file went away. For a checkout below
  `/Volumes` the mount is the copy on the internal disk, and `CUBE_MODEL_DIR` tells the runner
  where to write its probe model.

## Logs from a point in time

- **Decision**: a check keeps the number of lines of the Cube log at its start
  (`stack_log_mark`) and reads the lines after that number.
- **Rationale**: `container logs` has no `--since` option.

## Restart policy

- **Decision**: no supervisor. `stack.sh check` fails the run if a service is not in the state
  running at the end.
- **Rationale**: `container run` has no restart policy. On nettop a Cube that restarts hides a
  crash. On the iMac a crash shows.

## Memory of Cube

- **Decision**: 4 GB for `cube`, 768 MB for `cube-secure` and 1.5 GB for `cube-legacy`. The sum
  for a conformance run is 12.5 GB.
- **Rationale**: the first full run had 2 GB for `cube`, the value of the compose file. The
  kernel of the container stopped the Cube process with 1.9 GB in
  `preagg.latency.big_orders_hit_is_faster_than_a_miss`, and each later check failed. A limit in
  Apple `container` is the memory of a virtual machine with no swap. It also holds the kernel
  and Cube Store. `cube-secure` used 270 MB in that run.
- The second full run had 3 GB for `cube`. The run on stock Cube gave 1268/1402, the same
  statuses as nettop. The run with the dialect gave 1360/1402: two checks failed with
  `IncompleteRead`, `preagg.latency.big_orders_hit_is_faster_than_a_miss` and
  `preagg.type.original_sql_in_cube_store_with_an_index_stops`. The Cube machine had 50 MB free
  and read 565 GB from its disk, which shows that it had no memory for its file cache.
  `cube-legacy` used 750 MB in that run.

## Published ports

- **Decision**: the runner reads Cube at the addresses of `cube` and `cube-legacy`. It reads
  Metabase, the mail catcher and the browser at the published loopback ports.
- **Rationale**: a full run failed checks with `IncompleteRead`, also with 4 GB for `cube`. 10
  of 150 reads of the 555 kB answer of `/cubejs-api/v1/meta` through `127.0.0.1:4402` were
  short. 0 of 150 reads from the address of the same container were short. Metabase had 0
  short reads in 250 reads of 2 MB and 4 MB through its published port. With Metabase at the
  address of its container, the MCP checks failed with `invalid_scope`, because the OAuth
  resource must be equal to `MB_SITE_URL`.
- **Alternatives**: a second try in the runner after a short read. It changes the runner on
  nettop and can hide a real fault.

## Result of the full run on the iMac

The run was `make conformance-imac` on 2026-11-12, on commit `bda1148` of `main` with this
feature on top. The runner printed these lines:

```text
coverage: 1325/1469 pass on stock Cube
coverage: 1425/1469 pass on Cube with the ClickHouse dialect
```

The statuses of the run are not in a commit. The comparison with `conformance/features.yaml`
of that commit gave these differences:

| Entries | File | iMac | Cause |
| --- | --- | --- | --- |
| 63 entries of feature 004 | `todo` | 58 pass with the dialect, 55 on stock Cube, 4 partial, 3 unsupported on stock Cube, 1 fail | The file had no result from nettop for them. |
| `mb.expr.float` | `todo` | `fail` in the two variants | ClickHouse gives `Illegal types String and Float64 of arguments of function multiply`. This is one of the 63. It is a fault of the check or of the driver, not of the iMac. |
| `dimension.type.geo.view`, `preagg.rollup_lambda`, `preagg.type.rollup_lambda_after_the_batch_with_source`, `preagg.type.rollup_lambda_over_the_batch_with_source` | `status: partial` | `pass` with the dialect | Feature 003 changed the dialect and left only `cap_stock` in these entries. The `status` line is older than that change. |
| `compile.xray.field_all_cards_run`, `compile.xray.time_field_all_cards_run` | `partial` in the two variants | `pass` in the two variants | Feature 003 changed the driver and removed the cap. The status lines are older than that change. |

All other entries had the status of the file. No difference came from the platform.

An earlier run on commit `bdd0fb9`, with 3 GB for `cube`, gave `1268/1402` on stock Cube, which
is the count of the file. The two failures of its dialect run are in the section on memory.
