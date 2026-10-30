# Install and connect

## Install

`MB_PLUGINS_DIR` is the plugins directory of Metabase. Download the jar from the
[latest release](https://github.com/andrewkomkov/metabase-cube-driver/releases/latest) into it:

```sh
curl -fL -o "$MB_PLUGINS_DIR/cube.metabase-driver.jar" \
  https://github.com/andrewkomkov/metabase-cube-driver/releases/latest/download/cube.metabase-driver.jar
```

Restart Metabase. If the download fails with error 404, no release exists yet.

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

## Demo stack

Start the demo stack with `make up`. These are fixed throwaway values for fake data, and each port binds
to the loopback interface only.

| Service | Address from the host | Address from the Metabase container |
| --- | --- | --- |
| Cube SQL API | `127.0.0.1:15433` | `cube:15432` |
| Cube REST API | `127.0.0.1:4400` | `cube:4000` |
| Metabase | `127.0.0.1:3300` | not applicable |
