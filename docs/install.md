# Install and connect

## Install

`MB_PLUGINS_DIR` is the plugins directory of Metabase. Metabase must be able to write to this directory. The `metabase/metabase` image runs as uid 2000. If it cannot write there, it writes `Metabase cannot use the plugins directory` to its log, uses `/tmp`, and starts with no error and no Cube driver.

Download the jar from the
[latest release](https://github.com/andrewkomkov/metabase-cube-driver/releases/latest) and make the directory writable:

```sh
mkdir -p "$MB_PLUGINS_DIR"
curl -fL -o "$MB_PLUGINS_DIR/cube.metabase-driver.jar" \
  https://github.com/andrewkomkov/metabase-cube-driver/releases/latest/download/cube.metabase-driver.jar
chmod 644 "$MB_PLUGINS_DIR/cube.metabase-driver.jar"
# The image runs as uid 2000. Use chown 2000 instead of chmod 777 if you can.
chmod 777 "$MB_PLUGINS_DIR"
```

Restart Metabase. If the download fails with error 404, no release exists yet.

To check the install, look for `Registered driver :cube` in the log of Metabase. **Cube** is then in the list of **Admin > Databases > Add database**.

Each release also has a demo image with the driver inside, at
`ghcr.io/andrewkomkov/metabase-cube-driver/demo`.

## Verify the jar

```sh
gh attestation verify cube.metabase-driver.jar --repo andrewkomkov/metabase-cube-driver
```

## Connect

In Metabase, go to **Admin > Databases > Add database** and select **Cube**.

| Field | Value |
| --- | --- |
| Host and port | The Cube SQL API. The default port is `15432`. |
| Database name, user and password | The values of the Cube SQL API. |
| Cube REST URL | The base URL of the Cube REST API. The driver reads the data model from it. |
| Cube API secret | The value of `CUBEJS_API_SECRET`. The driver signs a short-lived JWT with it. |
| Cube SQL user for each person | Empty, or `email`, or the name of a user attribute. See [Security](security.md). |

## Required Cube settings

Set `CUBEJS_PRE_AGGREGATIONS_ALLOW_NON_STRICT_DATE_RANGE_MATCH=false` and raise `CUBEJS_DB_QUERY_LIMIT` on Cube. With the defaults, Cube returns wrong numbers with no error. See [How queries reach Cube](query-compilation.md).

## Demo stack

Start the demo stack with `make up`. These are fixed throwaway values for fake data, and each port binds
to the loopback interface only.

| Service | Address from the host | Address from the Metabase container |
| --- | --- | --- |
| Cube SQL API | `127.0.0.1:15433` | `cube:15432` |
| Cube REST API | `127.0.0.1:4400` | `cube:4000` |
| Metabase | `127.0.0.1:3300` | not applicable |
