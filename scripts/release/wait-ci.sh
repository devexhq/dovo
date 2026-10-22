#!/usr/bin/env bash
# Wait for every workflow run on SHA and fail if any of them fails. Runs a
# release started (publish.yml) are not CI, so they are left out.
set -euo pipefail

ids=$(gh run list --commit "$SHA" --json databaseId,event --jq '.[] | select(.event != "release") | .databaseId')
if [ -z "$ids" ]; then
  echo "No workflow runs found for $SHA." >&2
  exit 1
fi
for id in $ids; do
  if ! gh run watch "$id" --exit-status >/dev/null; then
    echo "Workflow run $id failed on $SHA." >&2
    exit 1
  fi
done
echo "Every workflow run on $SHA passed."
