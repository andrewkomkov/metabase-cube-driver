# Metabase driver for Cube

[![ci](https://github.com/andrewkomkov/metabase-cube-driver/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/andrewkomkov/metabase-cube-driver/actions/workflows/ci.yml)
[![coverage](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fandrewkomkov%2Fmetabase-cube-driver%2Fstatus%2Fbadges%2Fcoverage.json)](https://andrewkomkov.github.io/metabase-cube-driver/coverage/)
[![compatibility](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fandrewkomkov%2Fmetabase-cube-driver%2Fstatus%2Fbadges%2Fcompatibility.json)](https://andrewkomkov.github.io/metabase-cube-driver/compatibility/)
[![latest release](https://img.shields.io/github/v/release/andrewkomkov/metabase-cube-driver?sort=semver)](https://github.com/andrewkomkov/metabase-cube-driver/releases/latest)

A [Metabase](https://github.com/metabase/metabase) driver plugin for
[Cube Core](https://github.com/cube-js/cube). The goal is that Cube measures, dimensions and segments
work natively in Metabase, for people and for AI agents. The main target is Cube on top of ClickHouse.

This is an unofficial community project. It is not affiliated with or endorsed by Metabase or Cube.

## Status

Early development. CI makes each number in the badges above. If a badge shows `no data` or a failure, CI
has no green result.

Coverage is not written by hand in this file. The only source of truth is
[`conformance/features.yaml`](conformance/features.yaml). Each entry there names a feature, a status
and an executable check, and a `pass` requires a check that ran against a live stack and compared the
results with a direct SQL query on ClickHouse. A check of a refusal compares the error text. A check that cannot
prove the whole feature has the status `partial` and a reason. See the [coverage page](https://andrewkomkov.github.io/metabase-cube-driver/coverage/).

## Compatibility

| Component  | Pinned version   | Image                         |
| ---------- | ---------------- | ----------------------------- |
| Metabase   | `v0.64.3`        | `metabase/metabase`           |
| Cube       | `v1.8.15`        | `cubejs/cube`                 |
| ClickHouse | `26.8.36-alpine` | `clickhouse/clickhouse-server` |

These are the versions that the demo stack runs. The [compatibility table](https://andrewkomkov.github.io/metabase-cube-driver/compatibility/)
shows the other versions that CI tests. Other Cube data sources are a second priority.

## Install

No release exists yet, so this download fails until the first release. Install the jar, make the directory
writable, then restart Metabase:

```sh
mkdir -p "$MB_PLUGINS_DIR"
curl -fL -o "$MB_PLUGINS_DIR/cube.metabase-driver.jar" \
  https://github.com/andrewkomkov/metabase-cube-driver/releases/latest/download/cube.metabase-driver.jar
chmod 644 "$MB_PLUGINS_DIR/cube.metabase-driver.jar"
# The image runs as uid 2000. Use chown 2000 instead of chmod 777 if you can.
chmod 777 "$MB_PLUGINS_DIR"
```

`MB_PLUGINS_DIR` is the plugins directory of your Metabase. If Metabase cannot write to it, Metabase uses `/tmp` and
starts with no error and no Cube driver. Look for `Registered driver :cube` in its log. See [`docs/install.md`](docs/install.md).

On ClickHouse, also install [`cube-clickhouse`](cube-clickhouse/README.md) on Cube, in one step. It holds the fixes that wait for a change in Cube, on the stock image.

## Connect

The connection fields are in [`resources/metabase-plugin.yaml`](resources/metabase-plugin.yaml). These
are the addresses of the demo stack. Start it with `make up`:

| Service       | Address from the host | Address from the Metabase container |
| ------------- | --------------------- | ----------------------------------- |
| Cube SQL API  | `127.0.0.1:15433`     | `cube:15432`                        |
| Cube REST API | `127.0.0.1:4400`      | `cube:4000`                         |
| Metabase      | `127.0.0.1:3300`      | not applicable                      |

The demo SQL API user is `cube` and the password is `cube-dev-only`. These are fixed throwaway values
for fake data, and every port binds to the loopback interface only. Do not use them anywhere else.

## Known wrong numbers

Most shapes where Cube gives a wrong number now give the truth or stop with a clear message. See the list of what the driver stops in
[How queries reach Cube](docs/query-compilation.md#what-the-driver-stops). The driver cannot stop these, so each gives a number with no error. With [the ClickHouse dialect](cube-clickhouse/README.md):

- A long decimal below 2^53 loses digits, because the Cube SQL API has the type Double, and its value fits a Double. A dimension with `sql: toString(column)` keeps them.
- Native `COUNT(*)` on no rows gives `NULL` where SQL gives 0.
- Native `SELECT *`, or a raw select of a `switch` dimension, gives each row once for each value of the dimension.
- Native SQL in a shape that the scan of the text does not read, such as an expression on the time column, or a filter in a `WITH` query.

On stock Cube with no dialect, also:

- Native `x >= a AND x < b` on a `DateTime64` leaves out the last second before `b`. The query builder has this only on a column with a rollup.
- `NOT (x > t)` on a `DateTime64` compares whole seconds, and a time shift with `x > t` on a `DateTime64` leaves out the first second after `t`.
- A raw `DateTime64` value shows whole seconds.
- With `CUBEJS_TESSERACT_SQL_PLANNER=false`, which is not the default, each bucket of a rolling window gets one wrong number.

The table with the entries is in [Known wrong numbers](docs/query-compilation.md#known-wrong-numbers).

## Use with an AI agent

Metabase has an [MCP server](https://github.com/metabase/metabase/blob/v0.64.3/docs/ai/mcp.md) at
`/api/metabase-mcp`. The agent signs in with OAuth and acts with the permissions of that person. The
client brings the model, so Metabase needs no AI provider. **Disable all AI features** in
**Admin > AI** must be off.

To connect Claude Code to a Metabase on your machine:

```sh
claude mcp add --transport http metabase http://127.0.0.1:3300/api/metabase-mcp
```

Then run `/mcp` in Claude Code and authenticate. Site URL in Metabase must be an address that the
client can reach. The demo stack sets it with `MB_SITE_URL`.

The aim of this driver is that an agent can use the
[MCP tools](https://github.com/metabase/metabase/blob/v0.64.3/docs/ai/mcp-tools.md) with no knowledge
of Cube:

1. Find data with `search` and `browse_data`.
2. Read the descriptions of tables and fields with `get_content`.
3. Query with `execute_query`.

The `mcp.agent.*` entries in [`conformance/features.yaml`](conformance/features.yaml) run this flow. Agents can read
[`docs/agents.md`](docs/agents.md). To send the Metabase person to the Cube security context, see
[`docs/security.md`](docs/security.md).

## Development

Start with [`AGENTS.md`](AGENTS.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md). Agents do most of the work in this repository. Project subagents
are in [`.claude/agents/`](.claude/agents) and repeated tasks are in
[`.claude/skills/`](.claude/skills).

## License

[AGPL-3.0](LICENSE).
