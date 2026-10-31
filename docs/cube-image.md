# Cube image with our fixes

`ghcr.io/andrewkomkov/cube` is Cube built from source, with the fixes that wait in pull requests to `cube-js/cube`.

**This image is temporary.** When Cube releases each fix, the queue becomes empty, the workflow opens an issue, and you go back to stock `cubejs/cube`. Use stock Cube with [the dialect](https://github.com/andrewkomkov/metabase-cube-driver/blob/main/cube-clickhouse/README.md) if you do not need the fixes in the table below.

## What the image adds

Stock Cube with the dialect has the fixes for B1, datetime64, interval, m5, N2 and M3. The image adds these:

| Finding | Pull request | Result with the image |
| --- | --- | --- |
| M1, M2 | [#12535](https://github.com/cube-js/cube/pull/12535) | A date parameter in a prepared statement works in a query that needs SQL generation. |
| geo | [#12537](https://github.com/cube-js/cube/pull/12537) | A `geo` dimension works through a view. |
| propagate | [#12502](https://github.com/cube-js/cube/pull/12502) | Two `sub_query` dimensions on one measure each read their own subquery. |
| gran | none yet | A custom granularity in months on a rollup puts a row before the origin in the correct bin. |
| big | none yet | The REST API keeps the digits of a 64-bit integer and of a long decimal. The SQL API does not change. |
| N1 | [#12536](https://github.com/cube-js/cube/pull/12536) | A generated time series starts on a bucket boundary. This is for Postgres, not for ClickHouse. |

The image also has B1, datetime64, interval, m5 and N2 from the queue. It does not have M3, so keep the dialect on ClickHouse. The gate tests the image with the dialect. It also runs the image with no dialect, and this result only goes in a report.

The findings that stay open with this image are in [Known wrong numbers](query-compilation.md#known-wrong-numbers).

## Use

Use a tag `<cube-tag>-mcd.<n>`. A higher `n` on the same Cube tag has a changed queue. The [compatibility table](compatibility.md) shows the tags.

```yaml
services:
  cube:
    image: ghcr.io/andrewkomkov/cube:v1.8.15-mcd.1
```

The image has `cubestored` from the same source, and Cube in dev mode starts it. If you run Cube Store as a separate service, the `gran` fix applies only when that service runs this binary.

A tag `<cube-tag>-mcd-candidate.<id>` did not pass the gate. Do not use it. A tag `buildcache-*` is a build cache.

## The queue

[`cube-image/queue.json`](https://github.com/andrewkomkov/metabase-cube-driver/blob/main/cube-image/queue.json) lists each patch: its branch, its finding, and its pull request or `null`.

| Kind of patch | Where the diff comes from |
| --- | --- |
| A patch with a pull request | The current head of the pull request. A new push to the pull request changes the image. |
| A held patch, with no pull request | A commit on the branch `mcd/patch-queue` of the fork `andrewkomkov/cube`. |

The branches of open pull requests do not change. If #12502 closes with no merge, `propagate` uses the fallback commit on `mcd/patch-queue`.

To add a patch, push its commit to `mcd/patch-queue` and add a line to `queue.json`. The order of the lines is the order of application. `exclude` leaves out a path, for example a test file that the tag does not have.

## The workflow

[`cube-image.yml`](https://github.com/andrewkomkov/metabase-cube-driver/blob/main/.github/workflows/cube-image.yml) runs each day and on dispatch. All jobs run on GitHub-hosted runners.

1. It finds the new stable tags from `first_tag` in the queue. A tag waits until `cubejs/cube` has its image, because the gate needs that image.
2. It applies the queue on the tag. If the tag has the merged pull request of a patch, the workflow skips this patch. A commit then removes the patch from the queue.
3. It makes a fingerprint from the patched source tree, the build recipe and the platforms. It compares this value with the label of the last published image. If they are equal, the run makes no image.
4. It builds the native module with cubesql and the Tesseract planner from source, in `rust:<channel>-trixie`. It builds Cube Store from source only if the queue changes `rust/cubestore/`. If not, it takes the released binary.
5. It builds `packages/cubejs-docker/dev.Dockerfile` of the tag, plus `cube-image/image.tail.Dockerfile`, and pushes the candidate tag. A check compares the checksums of the two binaries in the image with the build.
6. The gate runs the full conformance on the pinned Metabase and ClickHouse. It tests the candidate and stock Cube of the same tag, each with the dialect.
7. The gate needs 0 fail on the candidate. The pass count of the candidate must also be equal to or higher than on stock Cube. Then the candidate gets the tag `<cube-tag>-mcd.<n>`.

An `arm64` image builds on `ubuntu-24.04-arm` when `platforms` in `queue.json` has `arm64`.

## Issues

A failure opens one issue with the label `cube-image` and the exact error. The causes are a patch that does not apply, a failed build step, and a refusal of the gate. The last good image stays.

- Each cause has one open issue. The same cause on a later run updates that issue.
- A scheduled run does not repeat a failed build or gate with the same input. A dispatch always tries. A new driver commit also retries the gate.
- When an image passes the gate, the issues of that tag and of older tags close.
- When the queue is empty, the workflow opens an issue that says the image can retire.

## Conformance on the image

CI runs this as part of the gate. To run it on the host of the stack:

```sh
sh cube-image/conformance.sh ghcr.io/andrewkomkov/cube:v1.8.15-mcd.1
```

`VARIANT=off` runs the image with no dialect.

## Test the failure paths

These dispatch inputs publish nothing:

```sh
gh workflow run cube-image.yml -f tag=v1.8.15 -f test-mode=bad-patch
gh workflow run cube-image.yml -f tag=v1.8.15 -f test-mode=empty-queue
```

Each opens one issue. Close it after the test. `python3 cube-image/test_image.py` tests the decisions with no network.
