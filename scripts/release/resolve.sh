#!/usr/bin/env bash
# Check that VERSION is untagged, then record the release title, the
# origin/main commit to tag and the previous v* tag the notes diff against.
# TITLE is the optional theme appended to the tag in the release title.
set -euo pipefail

git fetch --quiet --tags origin
if git rev-parse -q --verify "refs/tags/$VERSION" >/dev/null; then
  echo "Tag $VERSION already exists." >&2
  exit 1
fi

title=$VERSION
if [ -n "${TITLE:-}" ]; then
  title="$VERSION - $TITLE"
fi

{
  echo "title=$title"
  echo "sha=$(git rev-parse origin/main)"
  echo "previous=$(git describe --tags --abbrev=0 --match 'v*' origin/main)"
} >>"${DOVO_OUTPUT:-/dev/stdout}"
