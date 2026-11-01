# Compatibility

CI generates this table from the version matrix. Do not edit it. Only the pinned pair is required.
The other pairs report only.

| Metabase | Cube | ClickHouse | cube-clickhouse | Pair | Jar loads | Conformance | Coverage | Run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `v0.65.2.3` | `v1.8.15` | `26.10.3-alpine` | on | pinned | success | success | coverage: 1305/1343 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41298356108) |
| `v0.65.2.3` | `v1.8.15` | `26.10.3-alpine` | off | pinned | success | success | coverage: 1207/1343 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41298356108) |
| `v0.64.4.5` | `v1.8.15` | `26.10.3-alpine` | on | report only | success | success | coverage: 1305/1343 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41298356108) |
| `v0.64.4.5` | `v1.8.15` | `26.10.3-alpine` | off | report only | success | success | coverage: 1207/1343 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41298356108) |
| `v0.65.0.14-beta` | `v1.8.15` | `26.10.3-alpine` | on | report only | success | success | coverage: 1305/1343 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41298356108) |
| `v0.65.0.14-beta` | `v1.8.15` | `26.10.3-alpine` | off | report only | success | success | coverage: 1207/1343 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41298356108) |

Commit `5d6ced5f27fa`, 2026-11-01.
