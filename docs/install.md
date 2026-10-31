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

## Memory of Cube

The SQL API of Cube keeps the rewrite graph of each query in memory, up to 500 graphs in each of two caches, for each of 100 compiled models. A change of the data model makes a new compiled model, so in dev mode the memory grows with each change. On the demo stack, one run of the suite took 2.6 GB, and a process that lived through a few runs reached 7.5 GB. The memory stays until the entries go idle for one hour.

Set `CUBESQL_QUERY_CACHE_SIZE` and `CUBEJS_COMPILER_CACHE_SIZE`, and give the container a memory limit. The demo stack has `50`, `4` and `2g`. A query that is not in the cache takes the time of a new rewrite.

## The ClickHouse dialect

On ClickHouse, install [`cube-clickhouse`](https://github.com/andrewkomkov/metabase-cube-driver/tree/main/cube-clickhouse) in one step: copy the directory next to `cube.js`, then add `...require('./cube-clickhouse')` to the export of `cube.js`. With no `cube.js`:

```sh
echo "module.exports = require('./cube-clickhouse');" > cube.js
```

It holds the fixes that wait for a change in Cube, as a subclass of `ClickHouseQuery`, on the stock `cubejs/cube` image. The driver works with no dialect too. Then it stops more queries with a clear message, see [How queries reach Cube](query-compilation.md#date-bounds).

## Demo stack

Start the demo stack with `make up`. These are fixed throwaway values for fake data, and each port binds
to the loopback interface only.

| Service | Address from the host | Address from the Metabase container |
| --- | --- | --- |
| Cube SQL API | `127.0.0.1:15433` | `cube:15432` |
| Cube REST API | `127.0.0.1:4400` | `cube:4000` |
| Metabase | `127.0.0.1:3300` | not applicable |
| Mail catcher, its web page | `127.0.0.1:8025` | `mail:1025` for SMTP |

The demo stack uses `cube-clickhouse`. Set `CUBE_CLICKHOUSE_FIXES=off` for stock Cube.
