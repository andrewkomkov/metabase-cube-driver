# Contributing

Read [`AGENTS.md`](AGENTS.md) first. It applies to people and to agents.

## Definition of done

[`conformance/features.yaml`](conformance/features.yaml) is the only source of truth for coverage.

A feature has status `pass` only if a check ran against the live stack. The check must compare the
result with a direct SQL query on the demo ClickHouse.

Do not narrow a feature to make a check pass. Record the real status and the reason.

## Work on the code

You need Docker, Python 3 and `make`. The [devcontainer](.devcontainer/devcontainer.json) has all of them.

| Command | What it does |
| --- | --- |
| `make package` | Builds `dev/plugins/cube.metabase-driver.jar`. |
| `make up` | Starts the demo stack. |
| `make load-check` | Proves that Metabase loads the jar. Run it on a fresh stack. |
| `make conformance` | Runs the conformance suite. |
| `make lint` | Runs the static checks. They need the tools from `make tools`. |
| `make down` | Stops the stack and removes its data. |
| `make docs` | Builds the docs site. |

## Commits and pull requests

- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), in lowercase imperative English.
- Release notes and versions come from these messages. `feat` makes a new minor version and `fix` makes a new patch version.
- Do not add AI attribution or `Co-Authored-By` lines.
- Keep a pull request small. Split large work into stacked pull requests.

## CI

| Workflow | When | What it does |
| --- | --- | --- |
| `ci` | Push and pull request | Static checks, jar build, load check, conformance on the pinned pair. |
| `nightly` | Each night | Version matrix. It writes the [compatibility table](https://andrewkomkov.github.io/metabase-cube-driver/compatibility/). |
| `update-pins` | Each day | Opens a pull request for each new Metabase, Cube, ClickHouse or tool version. |
| `conformance` | On request | Tests a branch and the base branch, and gates a pin update. |
| `release-please` | Push to main | Keeps the release pull request up to date and runs the release. |
| `release` | On request | Runs a dry run of the release, or publishes a draft release. |
| `pages` | Docs change | Builds the docs site. |

Shared logic is in `reusable-*.yml` workflows and in [`.github/actions/`](.github/actions). Pin each
action to a commit SHA. Give each job the least `permissions` that it needs.

Workflows use only GitHub-hosted runners and no secrets. A badge or a table must show only a number that
CI made.
