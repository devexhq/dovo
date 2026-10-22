#!/usr/bin/env bash
# Create the annotated VERSION tag on SHA, titled TITLE, and push it.
set -euo pipefail

git tag -a "$VERSION" "$SHA" -m "$TITLE"
git push origin "refs/tags/$VERSION"
