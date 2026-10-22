#!/usr/bin/env bash
# Check that VERSION is untagged and its milestone is closed, then record the
# milestone title, the origin/main commit to tag and the previous v* tag.
set -euo pipefail

git fetch --quiet --tags origin
if git rev-parse -q --verify "refs/tags/$VERSION" >/dev/null; then
  echo "Tag $VERSION already exists." >&2
  exit 1
fi

milestone=$(gh api 'repos/{owner}/{repo}/milestones?state=all&per_page=100' \
  --jq "[.[] | select(.title | startswith(\"$VERSION \"))][0] | select(. != null) | [.title, .state, .open_issues] | @tsv")
if [ -z "$milestone" ]; then
  echo "No milestone titled '$VERSION ...'." >&2
  exit 1
fi
IFS=$'\t' read -r title state open <<<"$milestone"
if [ "$state" != closed ] || [ "$open" != 0 ]; then
  echo "Milestone '$title' is $state with $open open issues." >&2
  exit 1
fi

{
  echo "title=$title"
  echo "sha=$(git rev-parse origin/main)"
  echo "previous=$(git describe --tags --abbrev=0 --match 'v*' origin/main)"
} >>"${DOVO_OUTPUT:-/dev/stdout}"
