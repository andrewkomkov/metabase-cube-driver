# Security

## Short answer

Per-person data in Cube works on Metabase OSS, through this driver. It uses no Metabase permission feature, paid or free.
The driver opens a connection to the Cube SQL API, with the name of the person as the user name.

The `security.*` entries in `conformance/features.yaml` test this against `dev/cube-secure`, with a row-level rule.

## Metabase features

| Feature | Plan | Can this driver use it |
| --- | --- | --- |
| Connection impersonation | Paid. Code in `enterprise/backend/src/metabase_enterprise/impersonation`. In OSS, `set-role-if-supported!` does nothing. | No. The driver turns off `:connection-impersonation`. It would send `SET ROLE`, and the pool could not give the connection back. Not tested, because the demo image is OSS. |
| Row and column security (sandboxing) | Paid. Code in `enterprise/backend/src/metabase_enterprise/sandbox`. | It adds MBQL filters above the driver, so it can work. It has no effect on native SQL. Not tested, because the demo image is OSS. |
| Database routing | Paid. | The driver turns it off. |
| User attributes | The API `PUT /api/user/:id` with `login_attributes` works on OSS. The check `security.user_attribute` uses it. | Yes. |

## What Cube offers

| Cube feature | Live result on Cube v1.8.20 | Used |
| --- | --- | --- |
| `checkSqlAuth(req, username)` gives the password and the security context | Works. The security context is then fixed for the connection. | Yes |
| `CUBEJS_SQL_SUPER_USER`, then `SET user = 'x'` | Works. With the default `canSwitchSqlUser`, the session then is `x`, and it cannot go back: `user 'x' is not allowed to switch`. So a connection pool cannot give the connection to the next person. A custom `canSwitchSqlUser` lets it go back, but each person who writes native SQL can then read as anybody. | No |
| `WHERE __user = 'x'` | On a super user connection, it works for each query. So a person who can write native SQL reads as anybody. | No |
| A normal user tries `SET user`, `SET ROLE` or `__user` | Refused. | This is why the driver uses a normal user |

## Set it up

1. In `cube.js`, write `checkSqlAuth`. Give all people one password, which only Metabase and Cube know. Give each person a security context.
2. In `cube.js`, write the rule, for example `queryRewrite`. See [`dev/cube-secure/cube.js`](https://github.com/andrewkomkov/metabase-cube-driver/blob/main/dev/cube-secure/cube.js).
3. In `cube.js`, let the connection user see all members, because sync uses that user. Sync also sends this name as `user` in the JWT for the REST API.
4. In Metabase, set **Cube SQL user for each person** on the connection. Write `email`, or the name of a user attribute.

## What happens

- A query opens its own connection with the person as the user name, so there is one connection per query. A live test of one query gave a median of 199 ms, against 115 ms with the connection pool.
- A person with no attribute value gets an error. A person that `checkSqlAuth` refuses gets an error. The query stops there.
- MCP calls run as the person of the OAuth token.

## Limits

- **The shared password.** Each client that has it can sign in as any person on the Cube SQL API. Keep that port on a private network. Metabase has no separate password for each person, so Cube trusts the name.
- **Public links and static embeds fail.** They have no person, and the account of the connection would show all rows.
- **The Metabase result cache is off for this connection.** The cache key has no person, and a live test showed the rows of one person to another. Metabase gives a driver no hook for this, so the driver wraps the internal var `maybe-return-cached-results`. The check `security.metabase_cache` guards it on the pinned version.
- **Filter lists are search boxes.** Metabase keeps one list of values per field, so the driver sets each dimension to `search`, and the person's own query gives the values. A connection that gets **Cube SQL user for each person** after its first sync loses its stored lists when you save the connection. See `security.attribute_added_later_clears_the_lists`.
- **Saved questions and models keep no fingerprint of their result.** Metabase stores min, max, quartiles, average and distinct counts of the last run of a card in its `result_metadata`, and each reader of the card gets them with no run, through `GET /api/card/:id`. On per-person data these are values of the rows of another person: a live test gave bob the revenue of each city from a card that an admin ran. The driver makes each query on this connection row-restricted for Metabase, as a sandbox is, so Metabase stores no metadata from a run and returns no fingerprint for a save. Sync and a save of the connection remove the fingerprints that a card kept from before. Automatic binning of a column of a card has less data. See `security.card_fingerprints` and `security.card_fingerprints_cleared`.
- **Sync reads no rows, so the fingerprint of a field is empty.** Metabase keeps one fingerprint per field. Automatic numeric binning has less data. The tests do not cover this.
- **All people see one model.** Metabase keeps one set of tables, fields and measures per database. It comes from the connection user. A member that `access_policy` hides from a person stays visible in Metabase. A query on it gives an error, or an empty result. For a view that the person cannot read, the error says that the data model hides it from this person. `COMPILE_CONTEXT` changes values for each person. The list of members stays the same.
- **Scheduled deliveries run as their creator.** This is how Metabase works. Recipients get the rows of the creator. The tests do not cover this, because the demo has no email or Slack channel.
- **Metabase permissions still apply on top.** This feature only limits what Cube returns.
- **Other Cube data sources.** The live tests use ClickHouse only.

## Changes to Metabase state

A plugin runs in the Metabase process. Each item below changes something that is not a driver method for `:cube`. Each one acts only on a Cube database, and each one has a live check on Sample Database, which uses another driver.

| Change | Metabase versions | Why a driver method cannot do this | On another database | Check |
| --- | --- | --- | --- | --- |
| A wrap of the var `metabase.query-processor.middleware.cache/maybe-return-cached-results` | All | The cache middleware runs before the driver, and the cache key has no person. | The query goes through the original middleware. | `isolation.other_driver_cache`, `security.metabase_cache` |
| A wrap of the var `metabase.query-processor.middleware.row-restricted-metadata/row-restricted?` | v0.65.2.3. A Metabase with no such var does not load the driver. | Metabase decides in its own middleware whether a run keeps a fingerprint, and it asks no driver. | The original function gives the answer. | `isolation.other_db_card_fingerprint`, `security.card_fingerprints` |
| A wrap of the var `metabase.agent-lib.representations.repair/repair` | Only a version with the private function `wrap-iso-date`, such as v0.64.3. The driver changes no var for `between` on v0.65.2.3. | The repair step runs before the query processor, and the driver gets whole days. | The original function gets the same arguments. | `isolation.other_driver_mcp_between`, `mcp.query.between_keeps_time` |
| The topics `:event/sync-metadata-end` and `:event/sync-end` derive from a topic of the driver, with a method of `events/publish-event!` | All | A driver has no hook after sync. | The method reads the database, finds no Cube database and does nothing. | `isolation.other_driver_sync` |

The sync of a Cube database writes to `metabase_table`, `metabase_field`, `metabase_database.description`, Measures and Segments. On a connection with per-person data, it also removes the fingerprints from `result_metadata` of the cards of that database. It writes only to the objects of that database.

## Private members and dev mode

**A Cube with `CUBEJS_DEV_MODE=true` is unsafe for production.** It accepts each JWT, also one with a wrong signature, and its SQL API and REST API read each cube and member that the model marks `public: false`. Only the demo stack runs in dev mode. A production Cube runs with `CUBEJS_DEV_MODE=false` and Cube Store. See `dev/docker-compose.production.yml`.

What gives a value of a private member, on Cube `v1.8.20`:

| Door | Dev mode off | Dev mode on |
| --- | --- | --- |
| The query builder, `/api/dataset`, a dashboard, a field list, a parameter value, an x-ray and MCP `execute_query`: by field id, by name in each clause and each stage | No. Cube has no such member, and the driver stops the name of a key first. | No. The driver stops it. |
| Raw rows of a table with a private primary key | No. | No. The column is `NULL`. A later stage, a model or a saved question shows that `NULL`, and it cannot filter, group, sort or aggregate on it, because Cube would read a filter there as a filter on the member. |
| Native SQL and MCP `execute_sql`, for a person with native permission | No. Cube answers `not found`. | The driver parses the text and stops it, with a clear message. See [The scan of native SQL](#the-scan-of-native-sql). |
| A saved native question that reads a private member or names a column after one, such as `NULL AS id` | No. | The same grammar stops it, also as the source of a query builder question. So a person with the query builder only cannot use it to read or to infer a value. |
| The SQL API or the REST API of Cube, past Metabase | No. | **Yes.** Each client that reaches the port reads each member. The driver cannot stop this. |

The only real stop is `CUBEJS_DEV_MODE=false`. The grammar below is a defence on top of it, for a mistake in a deployment. It is not a security boundary, so do not rely on it. Metabase refuses native SQL to a person with the query builder only, on both modes.

`cube.native/check-no-private-member!` reads the SQL of each query, also a compiled one, so a form of MBQL that a check of the clauses does not see stops there. It reads the model from `/v1/meta`, at most one minute old. If a member becomes private, a Cube in dev mode can read it in that minute.

### The grammar of native SQL

`src/metabase/driver/cube/grammar.clj` reads the text that a person or an agent wrote, and the text of a saved native question, also for a person with the query builder only. It parses the text, and it resolves each column in the sources of its `SELECT`.

- A name of a private member stops the query, with or without a table, through each alias.
- An alias with the name of a private member stops the query, because Cube reads a filter on a column of a subquery with this name as a filter on the member.
- A private cube stops the query. A table that the data model does not have also stops, so system tables stop.
- `*` and `t.*` read the public dimensions, written out by the driver, so they give no private value.
- A `SELECT` with no columns stops.
- A text that the parser cannot read stops.

`native.scan.adversarial_private_member`, `native.grammar.mutations_refuse` and `security.saved_native_question_infers_no_private_member` test this, with the cases in `dev/review/release_cases.py`.

### What the grammar leaves open on a Cube in dev mode

- **A person who reaches the Cube ports.** See the table above. The driver cannot stop this.
- **A difference between the lexer and the parser of Cube.** A later version of Cube can read a text in another way than this lexer. The grammar reads no plan of Cube. Run `native.scan.adversarial_private_member` on each new version of Cube.
- **A public member that reads a private one in the model.** The value is public by the choice of the model, and the grammar does not read the SQL of the model.
- **A member that became private in the last minute.** See above.

### A private primary key as the target of a foreign key

A join needs no value of the key, because Cube takes the join from the data model. So sync keeps a private primary key as a hidden field of type `cube.dimension/private-key`: `stock_items.warehouse_id` stays a foreign key to `warehouses.id`, with an implicit join to `warehouses.code`.

A private key that no join targets does not sync. With dev mode off, `/v1/meta` has no private member, but its joins name the key, so the driver adds the same hidden field from the join. The model has the same fields on both modes, and the same foreign keys with one exception: a cube that inherits a join from a private parent has no such foreign key with dev mode off, because `/v1/meta` then does not show the parent. That column then has no implicit join. The checks are `security.private_member_gives_no_value` and `sync.private_primary_key_keeps_the_foreign_key`. `make production` reads the same doors with dev mode off.

A foreign key column is a public member of its own cube, so `stock_items.warehouse_id` shows its values, which are the values of the key. To hide them, make that column private in the model. The foreign key then goes.

## Delete a Cube database

Metabase v0.64.3 has a defect, and a live test shows it. After you delete a database that has Measures, each search for a Measure gives HTTP 500, in the
REST API and in the MCP tool `search`. The cause is a Measure in the search index with no table. To prevent it, archive the Measures of the
database, wait some seconds for the search index, and then delete the database.

## Secrets

- The API returns `**MetabasePass**` in place of the SQL password, and it hides the API secret. The check `security.secret.not_in_api` reads the admin and non-admin API and MCP.
- The check `security.secret.not_in_logs` forces wrong connections and reads the Metabase log at the default log level. It looks for the secret, the sent password and a JWT, and it finds none.
- Cube writes the JWT of a request that it refuses to its own log, as `{"message":"invalid signature","token":"..."}`. The driver cannot change this. The same check reads the log of `cube-secure`: it finds no secret and no token with a valid signature. A refused token shows the user name of the connection, so keep the Cube log private.
- Post the API secret as `cube-api-secret-value`, as the Metabase UI does. The driver does not read a value in the key `cube-api-secret`. A live test shows that Cube then refuses the connection with HTTP 403, if dev mode is off.
- Dev mode of Cube accepts each JWT, also one with a wrong signature. A live test confirmed this. To protect the REST API with the API secret, run Cube with `CUBEJS_DEV_MODE=false`.
- See [Private members and dev mode](#private-members-and-dev-mode) for hidden cubes and members (`CUBEJS_DEV_MODE`, hidden).
- `POST /api/database/validate` repeats the request body, also the secret that the admin sent. Metabase does this for each driver, and only an admin can call it. It shows no stored value. See `security.validate_echoes_the_request_limit`.
