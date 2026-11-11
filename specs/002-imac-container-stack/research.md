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

- **Decision**: named volumes. The script moves the image of each volume to
  `/Volumes/SanDisk2Tb/builds/metabase-cube-driver/volumes/` and leaves a symbolic link.
- **Rationale**: a bind mount of a directory on the external drive stopped ClickHouse with
  `chown: /var/lib/clickhouse/: Operation not permitted`. A named volume is an ext4 image in the
  application directory, on the internal disk. A volume behind a symbolic link took 200 MB of
  writes and kept them for the next container.
- **Alternatives**: `container system start --app-root` moves all data of Apple `container`. It
  restarts the services of each project on this Mac, so this feature does not do it.

## Images

- **Decision**: the images stay in the application directory on the internal disk. The script
  checks for 10 GB of free space before a pull.
- **Rationale**: only `--app-root` moves the image store, and the section on data tells why
  this feature does not use it.

## Model files

- **Decision**: bind mounts from the checkout, the same as in the compose file.
- **Rationale**: Cube in dev mode showed a new cube 3 seconds after a new file appeared in
  `dev/cube/model`. It removed the cube after the file went away.

## Logs from a point in time

- **Decision**: a check keeps the number of lines of the Cube log at its start
  (`stack_log_mark`) and reads the lines after that number.
- **Rationale**: `container logs` has no `--since` option.

## Restart policy

- **Decision**: no supervisor. `stack.sh check` fails the run if a service is not in the state
  running at the end.
- **Rationale**: `container run` has no restart policy. On nettop a Cube that restarts hides a
  crash. On the iMac a crash shows.
