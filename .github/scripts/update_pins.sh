#!/bin/sh
# Opens or refreshes one pull request per available update, then dispatches its workflows.
# Usage: update_pins.sh [ONLY_NAME]. GH_TOKEN must allow contents, pull requests and actions.
set -eu

only=${1:-}
repo=$GITHUB_REPOSITORY

gh auth setup-git
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git remote set-url origin "https://github.com/$repo.git"

updates=$(python3 .github/scripts/pins.py updates)
echo "$updates" | jq .

echo "$updates" | jq -c '.[]' | while read -r update; do
  kind=$(echo "$update" | jq -r .kind)
  name=$(echo "$update" | jq -r .name)
  from=$(echo "$update" | jq -r .from)
  to=$(echo "$update" | jq -r .to)
  if [ -n "$only" ] && [ "$only" != "$name" ]; then
    continue
  fi

  branch="deps/$name"
  title="chore(deps): update $name from $from to $to"
  git fetch -q origin main
  git switch -q -C "$branch" origin/main
  if [ "$kind" = pin ]; then
    python3 .github/scripts/pins.py set "$name" "$to"
  else
    python3 .github/scripts/pins.py set-tool "$name" "$to"
  fi
  git add -A
  git commit -q -m "$title"

  # Skip a branch that already has this content, so a day with no change starts no runs.
  if git fetch -q origin "$branch" 2>/dev/null && git diff --quiet FETCH_HEAD HEAD; then
    echo "$branch has no changes"
    continue
  fi
  git push -q --force origin "$branch"

  number=$(gh pr list --repo "$repo" --head "$branch" --state open --json number --jq '.[0].number // empty')
  body="Scheduled pin update. The conformance workflow tests this branch and the base branch, and reports on this pull request. It merges only if the pass count did not fall."
  if [ -n "$number" ]; then
    gh pr edit "$number" --repo "$repo" --title "$title"
  else
    url=$(gh pr create --repo "$repo" --base main --head "$branch" --title "$title" --body "$body" --label dependencies)
    number=${url##*/}
  fi

  gh workflow run conformance.yml --repo "$repo" --ref "$branch" -f pr="$number"
  gh workflow run ci.yml --repo "$repo" --ref "$branch"
done
