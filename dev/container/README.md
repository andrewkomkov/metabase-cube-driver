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

The addresses on the loopback interface are those of the table in `AGENTS.md`.

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

## Memory

Each container is a small virtual machine, and the limit is the memory of that machine.

| Service | Limit | In the compose file |
| --- | --- | --- |
| `clickhouse` | 2 GB | 3 GB |
| `cube` | 2 GB | 2 GB |
| `cube-secure` | 1 GB | 1 GB |
| `cube-legacy` | 2 GB | 2 GB |
| `metabase`, `metabase-conformance` | 3 GB | 3 GB |
| `mail` | 256 MB | 128 MB |
| `browser` | 1 GB | 1 GB |

A conformance run uses at most 11.25 GB and the demo stack 8.25 GB, on a Mac with 16 GB. The two
Metabase services together do not fit, so `make conformance-imac` stops `metabase` first. Its
data stays. Start it again with `make up-imac`.

## Data and images

- The data of ClickHouse and Metabase is in named volumes: `mcd-clickhouse-data`,
  `mcd-metabase-data` and `mcd-metabase-conformance-data`. The script moves the image of each
  volume to `/Volumes/SanDisk2Tb/builds/metabase-cube-driver/volumes/` and leaves a symbolic
  link in the application directory of Apple `container`. `STACK_VOLUMES` sets another
  directory.
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
