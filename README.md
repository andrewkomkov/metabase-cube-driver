# Metabase driver for Cube

A [Metabase](https://github.com/metabase/metabase) driver plugin for
[Cube Core](https://github.com/cube-js/cube). The goal is that Cube measures, dimensions and segments
work natively in Metabase, for people and for AI agents. The main target is Cube on top of ClickHouse.

This is an unofficial community project. It is not affiliated with or endorsed by Metabase or Cube.

## Status

Early development. **There is no released jar yet, and no feature is proven.**

Coverage is not written by hand in this file. The only source of truth is
[`conformance/features.yaml`](conformance/features.yaml). Each entry there names a feature, a status
and an executable check, and a `pass` requires a check that ran against a live stack and compared the
results with a direct SQL query on ClickHouse. The file is planned: if the link does not open, no
coverage exists yet.

The [`conformance` workflow](.github/workflows/conformance.yml) packages the jar, starts the demo
stack and runs the suite on GitHub-hosted runners.

## Compatibility

| Component  | Pinned version   | Image                         |
| ---------- | ---------------- | ----------------------------- |
| Metabase   | `v0.64.3`        | `metabase/metabase`           |
| Cube       | `v1.8.15`        | `cubejs/cube`                 |
| ClickHouse | `26.8.36-alpine` | `clickhouse/clickhouse-server` |

These are the versions that the demo stack runs. Other versions are not tested. Other Cube data
sources are a second priority.

## Install

Planned: the release asset does not exist yet. When it does, install it in one step, then restart
Metabase:

```sh
curl -fL -o "$MB_PLUGINS_DIR/cube.metabase-driver.jar" \
  https://github.com/andrewkomkov/metabase-cube-driver/releases/latest/download/cube.metabase-driver.jar
```

`MB_PLUGINS_DIR` is the plugins directory of your Metabase.

## Connect

Planned: the connection fields come from `resources/metabase-plugin.yaml`, which does not exist yet.
Until then, these are the addresses of the demo stack, which you start with
`cd dev && docker compose up -d clickhouse cube`:

| Service       | Address from the host | Address from the Metabase container |
| ------------- | --------------------- | ----------------------------------- |
| Cube SQL API  | `127.0.0.1:15433`     | `cube:15432`                        |
| Cube REST API | `127.0.0.1:4400`      | `cube:4000`                         |
| Metabase      | `127.0.0.1:3300`      | not applicable                      |

The demo SQL API user is `cube` and the password is `cube-dev-only`. These are fixed throwaway values
for fake data, and every port binds to the loopback interface only. Do not use them anywhere else.

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

This flow is a goal and is not yet proven with this driver. See
[`conformance/features.yaml`](conformance/features.yaml) for what passes.

## Development

Start with [`AGENTS.md`](AGENTS.md). Agents do most of the work in this repository. Project subagents
are in [`.claude/agents/`](.claude/agents) and repeated tasks are in
[`.claude/skills/`](.claude/skills).

## License

[AGPL-3.0](LICENSE).
