# Compatibility

CI generates this table from the version matrix. Do not edit it. Only the pinned pair is required.
The other pairs report only.

| Metabase | Cube | ClickHouse | cube-clickhouse | Pair | Jar loads | Conformance | Coverage | Run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `v0.65.2.3` | `v1.8.15` | `26.10.3-alpine` | on | pinned | success | success | coverage: 1361/1402 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42470858827) |
| `v0.65.2.3` | `v1.8.15` | `26.10.3-alpine` | off | pinned | success | success | coverage: 1261/1402 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42470858827) |
| `v0.64.5.5` | `v1.8.15` | `26.10.3-alpine` | on | report only | success | failure | coverage: 1360/1402 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42470858827) |
| `v0.64.5.5` | `v1.8.15` | `26.10.3-alpine` | off | report only | success | failure | coverage: 1260/1402 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42470858827) |
| `v0.64.5.5` | `v1.8.20` | `26.10.3-alpine` | on | report only | success | failure | coverage: 1324/1402 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42470858827) |
| `v0.64.5.5` | `v1.8.20` | `26.10.3-alpine` | off | report only | success | failure | coverage: 1242/1402 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42470858827) |
| `v0.65.0.14-beta` | `v1.8.15` | `26.10.3-alpine` | on | report only | success | cancelled | no data | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42470858827) |
| `v0.65.0.14-beta` | `v1.8.15` | `26.10.3-alpine` | off | report only | success | success | coverage: 1261/1402 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42470858827) |
| `v0.65.0.14-beta` | `v1.8.20` | `26.10.3-alpine` | on | report only | success | failure | coverage: 1325/1402 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42470858827) |
| `v0.65.0.14-beta` | `v1.8.20` | `26.10.3-alpine` | off | report only | success | failure | coverage: 1243/1402 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42470858827) |
| `v0.65.2.3` | `v1.8.20` | `26.10.3-alpine` | on | report only | success | failure | coverage: 1325/1402 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42470858827) |
| `v0.65.2.3` | `v1.8.20` | `26.10.3-alpine` | off | report only | success | failure | coverage: 1243/1402 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42470858827) |

Commit `eb8fea1ec0e5`, 2026-11-08.
