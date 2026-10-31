# CI and releases

## Pipelines

Shared logic is in reusable workflows and composite actions. All other workflows call them.

| Unit | What it does | Used by |
| --- | --- | --- |
| `reusable-lint.yml` | clj-kondo, cljfmt, actionlint, zizmor, yamllint, gitleaks, compose config | `ci` |
| `reusable-build.yml` | Builds the jar | `ci`, release |
| `reusable-stack.yml` | Starts the stack, runs the load check and conformance | `ci`, `nightly`, `conformance`, release |
| `reusable-status.yml` | Writes badges and pages to the branch `status` | `ci`, `nightly` |
| `reusable-release.yml` | Verifies and publishes a release | `release-please`, `release` |
| `actions/build-jar`, `start-stack`, `run-conformance`, `setup-tools` | One step each | The workflows above |

## Version matrix

`nightly` tests Metabase (pinned, previous stable, newest beta) by Cube (pinned, newest) on the pinned
ClickHouse. Each pair runs with `cube-clickhouse` and on stock Cube. The pinned pair is required. The other pairs
report only. Equal versions count once. `ci` also runs the pinned pair on stock Cube, as the job `conformance-stock`.

## Self-update

Dependabot updates the actions, the Python tools and the devcontainer.

`update-pins` updates the images and the CI binaries. Dependabot cannot read the pins in the compose file.
For each new version it opens one pull request, on the branch `deps/<name>`. Pull requests from
`GITHUB_TOKEN` start no workflows, so `update-pins` dispatches `ci` and `conformance`.

`conformance` tests the branch and `main` in one run. Then the pin gate acts on the pass counts:

| Result | Action |
| --- | --- |
| The pass count did not fall | Comment, green status, squash merge |
| The pass count fell | Comment, red status, an issue with the label `pin-regression` and the diff |
| No result to compare | Comment, error status, no merge |

To stop the merges, set the repository variable `PINS_AUTOMERGE` to `false`.

## Releases

`release-please` reads the Conventional Commits on `main` and keeps a release pull request up to date.
A merge of that pull request creates a draft release and a tag. Then `reusable-release.yml` runs:

1. It builds the jar from the tag and compares the manifest version with the tag.
2. It runs the load check and conformance on that jar.
3. It makes the checksum, the SBOM and the attestations, and builds and tests the demo image.
4. If the load check and conformance are green, it attaches the files and adds the coverage to the notes.
5. It pushes the demo image and publishes the release.

If a gate is red, the release stays a draft and users do not see it.

To test the pipeline with no release, run `gh workflow run release.yml`. This dry run pushes only the
image tag `dry-run`.
