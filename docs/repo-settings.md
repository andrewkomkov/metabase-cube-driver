# Repository settings

## Enabled now

| Setting | Why |
| --- | --- |
| Actions can create pull requests | `update-pins` and `release-please` need it. |
| Allow auto-merge | The pin gate uses it. |
| Delete head branches | Removes merged `deps/*` branches. |
| Pages source: GitHub Actions | `pages` deploys the site. |
| Private vulnerability reporting | `SECURITY.md` links to it. |
| Dependabot security updates | Fixes for known problems in dependencies. |

The repository variable `PINS_AUTOMERGE` is `false` now, so the pin gate reports and does not merge. This
keeps the pins stable during the driver work.

## Enable after the driver work ends

Do not enable these while agents push to `main`. To enable pin merges, delete the variable `PINS_AUTOMERGE`.

**Branch protection or a ruleset on `main`:**

- Require a pull request. A review count of 0 keeps the pin gate working.
- Require these status checks, and require the branch to be up to date:
  - `lint / clj-kondo and cljfmt`
  - `lint / actionlint, zizmor and yamllint`
  - `lint / gitleaks`
  - `lint / compose config`
  - `build / jar`
  - `load-check / jar load check`
  - `conformance / conformance on the pinned pair`
- Require linear history.
- Block force pushes and deletions.
- Require code owner review for all pull requests that are not from the pin gate, if more people join.

Merges from the pin gate use `--auto`, so the merge waits for the required checks. `update-pins` dispatches
`ci` on the branch to produce those checks.

**Other settings:**

- Enable immutable releases. The pipeline attaches files while the release is a draft, so this is safe.
- Limit Actions to verified actions and to actions pinned to a full SHA.
- Add a tag ruleset for `v*`, so that only the release pipeline can create tags.
- Add required reviewers to the `github-pages` environment only if the site needs an approval step.
