# Guide for agents

This text is for an AI agent that uses the Metabase MCP server on a database with engine `cube`. You need no knowledge
of Cube. `browse_data` with action `list_databases` returns a short form of this text as the description of the
database.

## What the objects are

| You see | It is |
| --- | --- |
| Table | A cube or a view. A view is a curated set of members. Prefer a view if one fits the question. |
| Field | A dimension. Group and filter by it. |
| Measure | A number with its aggregation in the data model. Do not sum or count its column again. |
| Segment | A saved filter. |

## Find data

1. Call `search` with one keyword per entry in `term_queries`, for example `["revenue", "city"]`, and `type: ["table"]`.
   A live test on a Metabase with a Postgres app database finds `sales` first for `revenue by city`. On an H2 app database,
   the same text finds nothing, because each word must match, also `by`. Keywords work on both.
2. Read the description of each table. It lists `Measures:`, `Dimensions:`, `Segments:` and `Joins:`. Fields have no entry
   in the search index, so this text is how a search finds a dimension.
3. A search with `type: ["measure"]` shows `Table: Sales (sales, id 40).` in the description. Many tables can have a
   measure with the same name, and this text tells them apart.
4. Call `browse_data` with action `get_fields` and the table id, to get the ids of the measures, fields and segments.

## Query

Use `execute_query`. Revenue by city, on a table that has both:

```json
{"lib/type": "mbql/query", "stages": [{"lib/type": "mbql.stage/mbql", "source-table": 40,
  "aggregation": [["measure", {}, 122]],
  "breakout": [["field", {}, 326]]}]}
```

- Segment: `"filters": [["segment", {}, <segment id>]]`.
- Column of a table in `related_tables`: `["field", {}, <field id>]`. Call `get_fields` on that table to get the id. Metabase
  adds the join.
- Time bucket: `["field", {"temporal-unit": "month"}, <field id>]`.

Use `execute_sql` only if the query cannot be a structured query:

```sql
SELECT users_city, MEASURE(revenue) FROM sales GROUP BY 1;
SELECT b.city, MEASURE(a.revenue) FROM orders a CROSS JOIN users b WHERE a.__cubeJoinField = b.__cubeJoinField GROUP BY 1;
```

A join with `ON` fails. Cube takes the join condition from the data model.

## Save

1. Run the query with `execute_query`. Keep `query_handle`.
2. Call `question_write` with `method: "create"`, `name`, `query_handle` and `collection_id`. An inline `query` must also
   have `database`.
3. Call `dashboard_write` with `method: "create"` and these operations:

```json
[{"op": "add_card", "id": -1, "card_id": 7, "tab": null, "position": null, "size": null, "series": null, "inline_parameters": null},
 {"op": "add_parameter", "parameter_id": "city", "name": "City", "type": "string/=", "sectionId": "location"},
 {"op": "wire_parameter", "parameter_id": "city", "dashcard_id": -1, "target_field": 326}]
```

4. Call `get_parameter_values` with `target: "dashboard"`, the dashboard id and `parameter_id` to get filter values. Add
   `query` to search.

## Errors

| Error text | Next step |
| --- | --- |
| `This table has no column with that name` | Call `get_fields`. Use a view that has the column, or the `CROSS JOIN` form. |
| `Cube cannot plan this query` | Use a time bucket in place of `EXTRACT`. Use the `CROSS JOIN` form in place of `JOIN ... ON`. |
| `This connection gives data per person` | You cannot read as another person. Do not try `__user` or `SET user`. |
| `Your Metabase account has no value for the attribute` | Ask an admin to set the attribute. |
| `is a rolling window measure` | Group by a time bucket, and use a date filter on whole buckets. This also applies to a column of a model and to native SQL. |
| `Cube cannot filter on the measure column` | Filter on a dimension, or save a question that aggregates the measure and filter on its result. |
| `Cube cannot group by the measure` | Use the column as a metric, and group by a dimension. |
| `through different join paths` | Use one of the two columns in a query, or a view with a join alias for each path. |
| `Cube reads > on` | In native SQL, write `>=` with the next second. |
| `Cube reads the end of BETWEEN` | In native SQL, write the time in the literals, or use `>=` and `<`. |
| `Cube reads a time as whole seconds` | Use a range with `>=` and `<` in place of `=`. |

## Numbers that you must not trust

Cube returns numbers through JavaScript, so Cube rounds a value above 2^53. See [How queries reach Cube](query-compilation.md).

On a connection with per-person data, each person sees different rows. Do not compare a number with the number of another person.
