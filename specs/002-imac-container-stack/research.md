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
