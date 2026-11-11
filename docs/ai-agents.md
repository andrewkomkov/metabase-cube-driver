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

## Tool arguments with a check

The matrix has a check for each of these calls on Cube data. Each check compares the rows with ClickHouse.

| Tool | Argument or shape | Entry |
| --- | --- | --- |
| `browse_collection` | A collection id, to find a saved question with no search | `mcp.browse_collection` |
| `browse_data` | `list_models`, and a question on the model with `source-card` | `mcp.browse.list_models` |
| `browse_data` | `list_tables` with `search`, which reads the title of a cube | `mcp.browse.list_tables_search` |
| `browse_data` | `get_fields` with `response_format: detailed`, for the values of a field | `mcp.browse.get_fields_values` |
| `execute_query` | An expression, a sort with a limit, an explicit join, two stages, bins | `mcp.execute_query.expression`, `.order_limit`, `.explicit_join`, `.two_stages`, `.binning` |
| `execute_query` | `validate_only`, and then the `query_handle` | `mcp.execute_query.validate_only` |
| `execute_sql` | `template_tag_values`, so a value is not part of the SQL text | `mcp.execute_sql.template_tag_values` |
| `run_saved_question` | `parameters` | `mcp.run_saved_question.parameters` |
