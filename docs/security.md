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

| Cube feature | Live result on Cube v1.8.15 | Used |
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
- **Filter lists are search boxes.** Metabase keeps one list of values per field, so the driver sets each dimension to `search`, and the person's own query gives the values.
- **Sync reads no rows, so the fingerprint of a field is empty.** Metabase keeps one fingerprint per field. Automatic numeric binning has less data. The tests do not cover this.
- **All people see one model.** Metabase keeps one set of tables, fields and measures per database. It comes from the connection user. A member that `access_policy` hides from a person stays visible in Metabase. A query on it gives an error, or an empty result. `COMPILE_CONTEXT` changes values for each person. The list of members stays the same.
- **Scheduled deliveries run as their creator.** This is how Metabase works. Recipients get the rows of the creator. The tests do not cover this, because the demo has no email or Slack channel.
- **Metabase permissions still apply on top.** This feature only limits what Cube returns.
- **Other Cube data sources.** The live tests use ClickHouse only.

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
- With `CUBEJS_DEV_MODE=true`, native SQL also reads hidden cubes and members. `SELECT COUNT(*) FROM secret_audit` gives a number on the demo Cube. A Cube with `CUBEJS_DEV_MODE=false` refuses it with `Table or CTE with name 'secret_audit' not found`. Do not rely on `public: false` on a Cube in dev mode. See `security.dev_mode_warning_for_hidden_members`.
- `POST /api/database/validate` repeats the request body, also the secret that the admin sent. Metabase does this for each driver, and only an admin can call it. It shows no stored value. See `security.validate_echoes_the_request_limit`.
