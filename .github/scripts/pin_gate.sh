#!/bin/sh
# Acts on the comparison of a pin update: reports it, then merges, opens an issue, or does neither.
# Usage: pin_gate.sh DIFF_FILE. The environment comes from the gate job in conformance.yml.
set -eu

diff_file=$1

# The GitHub API sometimes answers with a server error.
retry() {
  for delay in 10 30 60; do
    "$@" && return 0
    sleep "$delay"
  done
  "$@"
}

repo=$GITHUB_REPOSITORY
sha=$GITHUB_SHA
title=$(retry gh pr view "$PR" --repo "$repo" --json title --jq .title)

case "$DECISION" in
  ok)
    state=success
    description="pass count did not fall: $BASE_PASS to $CANDIDATE_PASS"
    ;;
  fell)
    state=failure
    description="pass count fell: $BASE_PASS to $CANDIDATE_PASS"
    ;;
  *)
    state=error
    description="no comparable conformance result"
    ;;
esac

retry gh api "repos/$repo/statuses/$sha" -f state="$state" -f context=conformance/pin-gate \
  -f target_url="$RUN_URL" -f description="$description" >/dev/null

{
  echo "## Conformance gate: $DECISION"
  echo
  echo "$description. [Run]($RUN_URL), commit \`$sha\`."
  echo
  cat "$diff_file"
} > comment.md
retry gh pr comment "$PR" --repo "$repo" --body-file comment.md

if [ "$DECISION" = fell ]; then
  issue_title="Pin update lowers conformance: $title"
  number=$(retry gh issue list --repo "$repo" --state open --label pin-regression --search "\"$issue_title\" in:title" \
    --json number --jq '.[0].number // empty')
  if [ -n "$number" ]; then
    retry gh issue comment "$number" --repo "$repo" --body-file comment.md
  else
    retry gh issue create --repo "$repo" --title "$issue_title" --label pin-regression --body-file comment.md
  fi
  exit 1
fi

if [ "$DECISION" != ok ]; then
  exit 1
fi

if [ "${AUTOMERGE:-}" = false ]; then
  echo "the variable PINS_AUTOMERGE is false, so the pull request stays open"
  exit 0
fi

author=$(retry gh pr view "$PR" --repo "$repo" --json author --jq .author.login)
branch=$(retry gh pr view "$PR" --repo "$repo" --json headRefName --jq .headRefName)
head=$(retry gh pr view "$PR" --repo "$repo" --json headRefOid --jq .headRefOid)
case "$branch" in
  deps/*) ;;
  *) echo "::error::$branch is not a pin update branch"; exit 1 ;;
esac
if [ "$author" != "app/github-actions" ] || [ "$head" != "$sha" ]; then
  echo "::error::refusing to merge: the author is $author and the head is $head, tested $sha"
  exit 1
fi

# --auto waits for required checks, but GitHub rejects it on a branch without protection rules.
gh pr merge "$PR" --repo "$repo" --auto --squash --delete-branch --match-head-commit "$sha" ||
  retry gh pr merge "$PR" --repo "$repo" --squash --delete-branch --match-head-commit "$sha"
# GITHUB_TOKEN merges start no push workflows, so start them here.
retry gh workflow run ci.yml --repo "$repo" --ref main
retry gh workflow run release-please.yml --repo "$repo" --ref main
