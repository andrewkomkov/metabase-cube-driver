# Compatibility

CI generates this table from the version matrix. Do not edit it. Only the pinned pair is required.
The other pairs report only.

| Metabase | Cube | ClickHouse | cube-clickhouse | Pair | Jar loads | Conformance | Coverage | Run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `v0.65.2.3` | `v1.8.15` | `26.10.3-alpine` | on | pinned | success | success | coverage: 1353/1391 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42205753032) |
| `v0.65.2.3` | `v1.8.15` | `26.10.3-alpine` | off | pinned | success | success | coverage: 1253/1391 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42205753032) |
| `v0.64.5.2` | `v1.8.15` | `26.10.3-alpine` | on | report only | success | failure | coverage: 1352/1391 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42205753032) |
| `v0.64.5.2` | `v1.8.15` | `26.10.3-alpine` | off | report only | success | failure | coverage: 1252/1391 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42205753032) |
| `v0.64.5.2` | `v1.8.19` | `26.10.3-alpine` | on | report only | success | failure | coverage: 1316/1391 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42205753032) |
| `v0.64.5.2` | `v1.8.19` | `26.10.3-alpine` | off | report only | success | failure | coverage: 1234/1391 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42205753032) |
| `v0.65.0.14-beta` | `v1.8.15` | `26.10.3-alpine` | on | report only | success | success | coverage: 1353/1391 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42205753032) |
| `v0.65.0.14-beta` | `v1.8.15` | `26.10.3-alpine` | off | report only | success | success | coverage: 1253/1391 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42205753032) |
| `v0.65.0.14-beta` | `v1.8.19` | `26.10.3-alpine` | on | report only | success | failure | coverage: 1317/1391 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42205753032) |
| `v0.65.0.14-beta` | `v1.8.19` | `26.10.3-alpine` | off | report only | success | failure | coverage: 1235/1391 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42205753032) |
| `v0.65.2.3` | `v1.8.19` | `26.10.3-alpine` | on | report only | success | failure | coverage: 1317/1391 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42205753032) |
| `v0.65.2.3` | `v1.8.19` | `26.10.3-alpine` | off | report only | success | failure | coverage: 1235/1391 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42205753032) |

Commit `b461f2eb2305`, 2026-11-06.

<!-- cube-image rows -->

## Cube image with our fixes

The image `ghcr.io/andrewkomkov/cube` is temporary. See [the page of the image](cube-image.md). A candidate is not for use.

| Image | State | Metabase | ClickHouse | cube-clickhouse | Conformance | Coverage | Run |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `v1.8.15-mcd.1` | published | `v0.65.2.3` | `26.10.3-alpine` | off | failure | coverage: 877/919 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41217906818) |
| `v1.8.15-mcd.1` | published | `v0.65.2.3` | `26.10.3-alpine` | on | success | coverage: 889/919 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41217906818) |
| `v1.8.17-mcd-candidate.e0f64bbac0ac` | candidate | `v0.65.2.3` | `26.10.3-alpine` | off | failure | coverage: 1238/1396 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42218095308) |
| `v1.8.17-mcd-candidate.e0f64bbac0ac` | candidate | `v0.65.2.3` | `26.10.3-alpine` | on | failure | coverage: 1321/1396 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/42218095308) |
