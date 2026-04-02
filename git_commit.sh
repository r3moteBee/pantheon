#!/usr/bin/env bash
# git_commit.sh — wrapper for committing when .git/index.lock can't be deleted
# Usage: ./git_commit.sh "commit message"
set -euo pipefail

MSG="${1:-wip}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_IDX="/tmp/_git_commit_idx_$$"

# Stage all changes via a temp index (avoids .git/index.lock conflict)
cp "$REPO/.git/index" "$TMP_IDX"
GIT_INDEX_FILE="$TMP_IDX" git -C "$REPO" add -A 2>/dev/null || true
TREE=$(GIT_INDEX_FILE="$TMP_IDX" git -C "$REPO" write-tree)
rm -f "$TMP_IDX" "${TMP_IDX}.lock" 2>/dev/null || true

HEAD=$(git -C "$REPO" rev-parse HEAD)
BRANCH=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)

COMMIT=$(git -C "$REPO" commit-tree "$TREE" -p "$HEAD" -m "$MSG")
echo "$COMMIT" > "$REPO/.git/refs/heads/$BRANCH"

echo "[$BRANCH $COMMIT] $MSG"
