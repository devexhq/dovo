#!/usr/bin/env bash
# Wait for the publish.yml run that a published VERSION release starts. A
# failure leaves the tag and release in place and prints how to rerun it.
set -euo pipefail

if [ "$CHOICE" != publish ]; then
  echo "Release not published; PyPI upload skipped."
  exit 0
fi

id=""
for _ in 1 2 3 4 5 6; do
  id=$(gh run list --workflow publish.yml --branch "$VERSION" --limit 1 --json databaseId --jq '.[0].databaseId // empty')
  [ -n "$id" ] && break
  sleep 10
done
if [ -z "$id" ]; then
  echo "No publish.yml run started for $VERSION." >&2
  exit 1
fi
if ! gh run watch "$id" --exit-status >/dev/null; then
  echo "PyPI upload failed; the tag and release stay: $(gh run view "$id" --json url --jq .url)" >&2
  echo "Fix the cause, then rerun it with: gh run rerun $id" >&2
  exit 1
fi
echo "Published $VERSION to PyPI."
