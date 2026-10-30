#!/bin/sh
# Copies a directory to the root of the orphan branch "status". README badges and the docs site read
# from that branch, so CI-generated files stay out of the main history.
# Usage: publish_status.sh DIR. GH_TOKEN must have contents: write.
set -eu

src=$(CDPATH= cd -- "$1" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

gh auth setup-git
cd "$work"
git init -q -b status
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git remote add origin "https://github.com/${GITHUB_REPOSITORY}.git"
if git fetch -q --depth 1 origin status; then
  git reset -q --hard FETCH_HEAD
fi

for attempt in 1 2 3 4 5; do
  cp -R "$src/." .
  git add -A
  if git diff --cached --quiet; then
    echo "status has no changes"
    exit 0
  fi
  git commit -q -m "chore: update ci status from run ${GITHUB_RUN_ID}"
  if git push -q origin status; then
    echo "published status"
    exit 0
  fi
  echo "push failed on attempt $attempt"
  sleep $((attempt * 3))
  git fetch -q --depth 1 origin status
  git reset -q --hard FETCH_HEAD
done
exit 1
