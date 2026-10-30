# Cube objects in Metabase

After each metadata sync, the driver writes the Cube data model into Metabase objects. It adds no second
semantic model: if Metabase has an object for a Cube feature, the feature is in that object.

The step runs on the Metabase event `:event/sync-metadata-end`. The same data comes from `/v1/meta?extended=true`.

## Where each feature is

| Cube | Metabase |
| --- | --- |
| Cube or view | Table |
| Title of a cube or view | Table display name |
| Dimension | Field |
| Title of a member | Field display name. A measure title is also the Measure name. |
| Description of a cube, view or member | Description of the object |
| Measure | Measure entity. Summarize shows it. Its column stays in the table, hidden, because the Measure reads it. |
| Segment | Segment entity. The filter menu shows it. Its column stays in the table, hidden. |
| Format `currency`, `percent`, `link`, `imageUrl` | Semantic type `Currency`, `Percentage`, `URL`, `ImageURL` |
| Numeric format | Field settings: number style, decimals, currency code. A Measure result inherits them. |
| Join with one equality of two plain members | Foreign key, on the many side of the join |

A table has no schema, because Cube has one namespace. The MCP tool `browse_data` then lists the tables with only a
database id.

These features have no Metabase object, so they are text in a description. The MCP tools `search`, `get_content` and
`browse_data` return that text:

| Cube | Text | Where |
| --- | --- | --- |
| Hierarchies | `Hierarchies: Fulfilment: status > size.` | Table |
| Folders | `Folders: Money: net_sales, [Ratios: count].` | Table |
| View groups | `View groups: Revenue views (revenue_views).` | Table |
| `meta` | `Meta: {"owner":"finance"}.` | Table, field, Measure |
| Drill members | `To drill into this measure, use: id, status, created_at.` | Measure |
| Links | `Links: Search status.` | Field |
| Link label | `Link label: Open invoice.` | Field |
| Custom number or time format | `Number format: .2~s.`, `Time format: %Y-%m-%d.` | Field, Measure |
| Members of a cube, with titles | `Measures: Revenue, Order count.`, `Dimensions: Status.`, `Segments: Paid.` | Table |
| Joins of a cube | `Joins: users, products.` | Table |
| Table of a member | `Table: Sales (sales, id 40).` | Measure, Segment |
| How to query this database | `Cube semantic layer. Each table is a cube or a view...` | Database, if its description is empty |

## Keys

Each Measure and Segment has a fixed `entity_id`, a hash of the Metabase database id and the member name
(`orders.revenue`). The creator is the Metabase internal user. So:

- A second sync finds the same object and adds nothing. A sync writes only the values that changed.
- A new member name is a new object. Cube has no rename, so the old object is archived.

## What happens on a change in the model

| Change | Result |
| --- | --- |
| New member | New Measure or Segment |
| New title, description, measure type | The same object changes |
| Member removed, or `public: false` | Measure or Segment archived. The field becomes inactive. |
| Cube removed | Table inactive. Its Measures and Segments archived. |
| Member comes back | The same object is active again |
| Join removed | Foreign key removed |

## Edits by a person

**A sync never replaces an edit by a person.** A person can always edit an object in Metabase. The next sync
keeps the edit and does not ask.

- **Tables and fields.** Metabase stores a user value in `metabase_table_user_settings` and
  `metabase_field_user_settings`, and shows it over the synced value. The driver writes only the synced
  columns. Each edited attribute stays, and the other attributes still follow Cube. To follow Cube again, clear the
  value in Metabase.
- **Measures and segments.** Metabase has no such tables for these objects, but each edit through the UI, the API
  or MCP adds a Revision, and the driver adds no Revision. If an object has a Revision, the driver treats the whole object
  as owned by a person: it does not change the name, the description, the definition or the archived state. The log
  counts these objects as `:kept`.
- **Archive by a person.** This is an edit, so the object stays archived, also if the member stays in Cube.

There is one exception: if the member goes away from Cube, the object is archived, also if a person edited it, because
a query on it can only fail. If the member comes back, the object is active again if a person did not archive it.

To give a Measure or Segment back to Cube, make one more edit and write `cube-sync:reset` as the revision message, for
example `PUT /api/measure/:id` with `{"description": "x", "revision_message": "cube-sync:reset"}`. The edit must
change a value, because Metabase adds no Revision for no change. The next sync writes the Cube values over the object.

Measures and Segments that a person creates are not part of this step. The driver does not change them, also if their
column goes away.

## Limits

- A measure of type `min` or `max` on a time column returns NULL. `/v1/meta` types it as a number, and the SQL API
  returns NULL for `MEASURE()`. Use a measure of type `time`.
- A join gives a foreign key only if the condition is one equality, for example `{CUBE}.user_id = {users.id}`, and
  the target is a public primary key. Other joins work in Cube SQL, and they have no Metabase foreign key. A primary key
  in Cube is not public unless the model says `public: true`.
- A view has no joins in `/v1/meta`, so a view has no foreign keys.
- Metabase sends the condition of a join, and Cube ignores it: the SQL API uses the join of the data model. `inner-join`
  and `left-join` give the same rows. A join with no path in the data model fails.
- Number formats that abbreviate (`.2~s`) or use parentheses (`(,.2~f`) have no Metabase setting. They become
  a decimal format, and the description keeps the original text.
