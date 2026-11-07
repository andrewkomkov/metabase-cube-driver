# Compatibility

CI generates this table from the version matrix. Do not edit it. Only the pinned pair is required.
The other pairs report only.

| Metabase | Cube | ClickHouse | cube-clickhouse | Pair | Jar loads | Conformance | Coverage | Run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `v0.65.2.3` | `v1.8.15` | `26.10.3-alpine` | on | pinned | success | success | coverage: 1357/1396 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42362384202) |
| `v0.65.2.3` | `v1.8.15` | `26.10.3-alpine` | off | pinned | success | success | coverage: 1257/1396 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42362384202) |
| `v0.64.5.5` | `v1.8.15` | `26.10.3-alpine` | on | report only | success | failure | coverage: 1356/1396 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42362384202) |
| `v0.64.5.5` | `v1.8.15` | `26.10.3-alpine` | off | report only | success | failure | coverage: 1256/1396 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42362384202) |
| `v0.64.5.5` | `v1.8.20` | `26.10.3-alpine` | on | report only | success | failure | coverage: 1319/1396 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42362384202) |
| `v0.64.5.5` | `v1.8.20` | `26.10.3-alpine` | off | report only | success | failure | coverage: 1238/1396 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42362384202) |
| `v0.65.0.14-beta` | `v1.8.15` | `26.10.3-alpine` | on | report only | success | success | coverage: 1357/1396 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42362384202) |
| `v0.65.0.14-beta` | `v1.8.15` | `26.10.3-alpine` | off | report only | success | success | coverage: 1257/1396 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42362384202) |
| `v0.65.0.14-beta` | `v1.8.20` | `26.10.3-alpine` | on | report only | success | failure | coverage: 1321/1396 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42362384202) |
| `v0.65.0.14-beta` | `v1.8.20` | `26.10.3-alpine` | off | report only | success | failure | coverage: 1239/1396 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42362384202) |
| `v0.65.2.3` | `v1.8.20` | `26.10.3-alpine` | on | report only | success | failure | coverage: 1321/1396 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42362384202) |
| `v0.65.2.3` | `v1.8.20` | `26.10.3-alpine` | off | report only | success | failure | coverage: 1239/1396 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42362384202) |

Commit `b88e0416b2dc`, 2026-11-07.
