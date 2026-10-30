# Use with an AI agent

Metabase has an MCP server at `/api/metabase-mcp`. The agent signs in with OAuth and has the permissions
of that person. The client brings the model, so Metabase needs no AI provider.

To connect Claude Code to a Metabase on your machine:

```sh
claude mcp add --transport http metabase http://127.0.0.1:3300/api/metabase-mcp
```

Then run `/mcp` in Claude Code and authenticate. The site URL of Metabase must be an address that the
client can reach.

The aim of this driver is that an agent can use the MCP tools with no knowledge of Cube:

1. Find data with `search` and `browse_data`.
2. Read the descriptions of tables and fields with `get_content`.
3. Query with `execute_query`.

[Guide for agents](agents.md) is the short text for the agent itself. [Security](security.md) tells how to give each
person their own rows in Cube.

[Cube objects in Metabase](semantic-sync.md) tells where each part of the Cube data model is.
[Coverage](coverage.md) shows which of these checks pass.
