#!/bin/sh
# Builds the docs site. It copies the CI-generated pages from the branch "status", and states the
# absence of a page instead of a number.
set -eu

url="https://raw.githubusercontent.com/${GITHUB_REPOSITORY:-andrewkomkov/metabase-cube-driver}/status"
for page in coverage compatibility; do
  if ! curl -fsSL "$url/$page.md" -o "docs/$page.md"; then
    printf '# %s\n\nCI did not publish this page yet.\n' "$page" > "docs/$page.md"
  fi
done
mkdocs build --strict
