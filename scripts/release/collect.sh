#!/usr/bin/env bash
# Write the previous release's notes and every pull request merged between
# PREVIOUS and SHA, with the issues each closes, for the notes agent to read.
set -euo pipefail

mkdir -p .agentic
gh release view "$PREVIOUS" --json body --jq .body >.agentic/release-previous.md
git log --merges --format=%s "$PREVIOUS..$SHA" |
  sed -n 's/^Merge pull request #\([0-9]*\) .*/\1/p' |
  while read -r pr; do
    gh pr view "$pr" --json number,title,body,closingIssuesReferences \
      --jq '"## PR #\(.number): \(.title)\nCloses: \([.closingIssuesReferences[].number | "#\(.)"] | join(", "))\n\n\(.body)\n"'
  done >.agentic/release-changes.md
