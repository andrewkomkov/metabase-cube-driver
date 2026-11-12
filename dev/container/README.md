# The stack on the iMac

`stack.sh` runs the demo stack with Apple `container`, which has no compose command. The script
starts the services of `dev/docker-compose.yml` and `dev/docker-compose.conformance.yml`. The
ports, the throwaway credentials, the seeds and the mounts are the same. When you change a
service in a compose file, make the same change in `stack.sh`.

## Commands

| Command | Effect |
| --- | --- |
| `make up-imac` | Build the jar and start `clickhouse`, `cube`, `cube-secure`, `mail` and `metabase`. |
| `make down-imac` | Stop the containers. The data and the logs stay. |
| `make reset-imac` | Remove the containers, their volumes and the network. |
| `make conformance-imac` | Run the suite on stock Cube and with `cube-clickhouse`. See `conformance/README.md`. |
| `sh dev/container/stack.sh up SERVICE...` | Start only these services, in this sequence. |
| `sh dev/container/stack.sh reset SERVICE...` | Remove only these services and their volumes. |
| `sh dev/container/stack.sh status` | Show the state and the address of each container. |
| `sh dev/container/stack.sh check SERVICE...` | Fail if one of these services is not in the state running. |
| `sh dev/container/stack.sh model-dir` | Print the directory that Cube reads the model from. |
| `sh dev/container/stack.sh address SERVICE` | Print the address of a service on the network `mcd`. |

The addresses on the loopback interface are those of the table in `AGENTS.md`.

If a start fails with `Address already in use`, another program holds the port. An ssh tunnel
to the Metabase of nettop holds `127.0.0.1:3300`. Close the tunnel. As an alternative, give the
demo Metabase another port: `STACK_METABASE_PORT=3310 make up-imac`. The suite does not use
this port.

## Differences from docker compose

- Each container has the name `mcd-SERVICE` and is on the network `mcd`. The script changes only
  containers with this prefix, because other projects use Apple `container` on the same Mac.
- Apple `container` gives no name lookup between containers. After each start, the script writes
  the address of each service into `/etc/hosts` of each running container. If you start a
  container by hand, run `sh dev/container/stack.sh hosts`.
- Apple `container` has no restart policy. A service that stops stays in the state stopped.
  `make conformance-imac` fails with exit code 2 if a service is not in the state running at
  the end of a run.
- The script keeps a checksum of the arguments of each container in the label `mcd.config`.
  When an argument changes, for example `CUBE_CLICKHOUSE_FIXES`, `up` makes a new container.
- `container logs` cannot read a log from a point in time. The runner counts the lines of the
  log at the start of a check.
- A published port of Apple `container` can cut the end of a large answer of Cube. In a test,
  10 of 150 reads of a 555 kB answer from `127.0.0.1:4402` were short. From the address of the
  container, 0 of 150 were short. In the suite this shows as `IncompleteRead`. So
  `make conformance-imac` gives the runner the addresses of `cube` and `cube-legacy`. A script
  that reads large answers of Cube must use `stack.sh address`.
- Metabase had no short read in 250 reads of 2 MB and 4 MB through its published port. The
  runner uses that port, because the OAuth resource of the MCP server must be equal to
  `MB_SITE_URL`.

## Memory

Each container is a small virtual machine, and the limit is the memory of that machine.

| Service | Limit | In the compose file |
| --- | --- | --- |
| `clickhouse` | 2 GB | 3 GB |
| `cube` | 4 GB | 2 GB |
| `cube-secure` | 768 MB | 1 GB |
| `cube-legacy` | 1.5 GB | 2 GB |
| `metabase`, `metabase-conformance` | 3 GB | 3 GB |
| `mail` | 256 MB | 128 MB |
| `browser` | 1 GB | 1 GB |

A conformance run uses at most 12.5 GB and the demo stack 10 GB, on a Mac with 16 GB.

`cube` has more memory than in the compose file. The machine has no swap, and the kernel and
Cube Store use a part of the limit.

- With 2 GB, the kernel stopped Cube in the full suite, in
  `preagg.latency.big_orders_hit_is_faster_than_a_miss`: the Cube process held 1.9 GB.
- With 3 GB, the run on stock Cube gave the statuses of nettop. In the run with the dialect,
  the machine had 50 MB free. `preagg.latency.big_orders_hit_is_faster_than_a_miss` and
  `preagg.type.original_sql_in_cube_store_with_an_index_stops` failed with `IncompleteRead`:
  Cube closed the connection before the end of the answer.
- `cube-secure` used 270 MB and `cube-legacy` 750 MB in a full run, so they have less.

The two Metabase services together do not fit, so `make conformance-imac` stops `metabase`
first. Its data stays. Start it again with `make up-imac`.

## The external drive and a locked screen

With the screen of the Mac locked, Apple `container` cannot open a new directory or a new volume
image on `/Volumes/SanDisk2Tb`. The start of the container waits in `open` with no end. While
it waits, the service of Apple `container` accepts no other start, stop or `exec`, for each
project on this Mac. A container that runs keeps the files that it has open.

Agents work while the screen is locked, so the script keeps these files on the internal disk:

- If the checkout is below `/Volumes`, `up` copies the mounted files to
  `~/.local/state/metabase-cube-driver/stage` and mounts the copy. These are `dev/clickhouse`,
  `dev/cube`, `dev/cube-secure`, `cube-clickhouse` and the two plugins directories.
  `STACK_STATE` sets another directory. Run `up` again after you change a model file. The
  runner writes its probe model into the copy: `conformance/run_isolated.sh` sets
  `CUBE_MODEL_DIR` from `stack.sh model-dir`.
- If the checkout is on the internal disk, the script mounts it directly.
- The script stops a start that takes more than 90 seconds, and fails. If a start of another
  project blocks the service, a start of this stack fails in the same way.

## Data and images

- The data of ClickHouse and Metabase is in named volumes: `mcd-clickhouse-data`,
  `mcd-metabase-data` and `mcd-metabase-conformance-data`. A volume is an ext4 image in the
  application directory of Apple `container`, on the internal disk. After a full conformance
  run the volumes use about 2.5 GB.
- Apple `container` keeps a copy of the image file system for each container, also in the
  application directory. After a full conformance run these copies use about 13 GB: 4 GB for
  each of `cube` and `cube-legacy`, 2 GB for `clickhouse`. With the images, the stack uses
  about 21 GB of the internal disk. `make reset-imac` frees the copies and the volumes.
- `STACK_VOLUMES=DIRECTORY` moves each new volume image to that directory and leaves a symbolic
  link. Use it only when the screen stays unlocked.
- A bind mount cannot hold the data of ClickHouse: `chown` fails on the shared file system.
- The images stay in the application directory on the internal disk, about 5 GB. Only
  `container system start --app-root` moves them, and that command restarts the containers of
  each project on this Mac. The script stops before a pull if the internal disk has less than
  10 GB free.
- Each pinned image has a `linux/arm64` variant: ClickHouse, Cube, Metabase, mailpit,
  headless-shell and `postgres:16-alpine`. No service uses Rosetta.

## Not on the iMac

`make grid`, `make fuzz`, `make fuzz-native` and `make production` use `docker compose` and run
on nettop only.
