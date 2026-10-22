#!/usr/bin/env bash
# Create the GitHub release for VERSION from the approved notes. CHOICE is the
# approval answer: publish, draft or tag-only.
set -euo pipefail

case "$CHOICE" in
  tag-only)
    echo "Tag only; no release created."
    exit 0
    ;;
  draft) mode=--draft ;;
  publish) mode=--latest ;;
  *)
    echo "Unexpected release choice '$CHOICE'." >&2
    exit 1
    ;;
esac
gh release create "$VERSION" --verify-tag "$mode" --title "$TITLE" --notes-file .agentic/release-notes.md
