# Compatibility

CI generates this table from the version matrix. Do not edit it. Only the pinned pair is required.
The other pairs report only.

| Metabase | Cube | ClickHouse | cube-clickhouse | Pair | Jar loads | Conformance | Coverage | Run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `v0.65.2.3` | `v1.8.15` | `26.8.36-alpine` | on | pinned | success | failure | coverage: 739/767 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41190161590) |
| `v0.65.2.3` | `v1.8.15` | `26.8.36-alpine` | off | pinned | success | failure | coverage: 735/767 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41190161590) |
| `v0.64.4.4` | `v1.8.15` | `26.8.36-alpine` | on | report only | success | failure | coverage: 739/767 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41190161590) |
| `v0.64.4.4` | `v1.8.15` | `26.8.36-alpine` | off | report only | success | failure | coverage: 735/767 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41190161590) |
| `v0.65.0.14-beta` | `v1.8.15` | `26.8.36-alpine` | on | report only | success | failure | coverage: 739/767 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41190161590) |
| `v0.65.0.14-beta` | `v1.8.15` | `26.8.36-alpine` | off | report only | success | failure | coverage: 735/767 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41190161590) |

Commit `0040e687a25c`, 2026-10-31.

<!-- cube-image rows -->

## Cube image with our fixes

The image `ghcr.io/andrewkomkov/cube` is temporary. See [the page of the image](cube-image.md). A candidate is not for use.

| Image | State | Metabase | ClickHouse | cube-clickhouse | Conformance | Coverage | Run |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `v1.8.15-mcd-candidate.ae9cfadfdaf0` | candidate | `v0.65.2.3` | `26.8.36-alpine` | off | failure | coverage: 728/767 pass on stock Cube | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41190176078) |
| `v1.8.15-mcd-candidate.ae9cfadfdaf0` | candidate | `v0.65.2.3` | `26.8.36-alpine` | on | failure | coverage: 739/767 pass on Cube with the ClickHouse dialect | [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41190176078) |
