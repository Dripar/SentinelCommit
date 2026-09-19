#!/bin/sh
# SentinelCommit installer.
#
# Installs the pre-commit hook into the current repository:
#   curl -sSL .../install.sh | sh     (from a checkout: ./install.sh)
#
# Safe to re-run. Refuses to clobber an unrelated existing hook.

set -e

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "error: not inside a git repository" >&2
    exit 1
}

SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# `git rev-parse --git-path` returns a path relative to the repo root for a
# normal checkout, but an absolute one inside a linked worktree or when
# GIT_DIR is set. Only prefix the root when it is actually relative.
HOOK_DIR=$(git rev-parse --git-path hooks)
case "$HOOK_DIR" in
    /* | [A-Za-z]:[/\\]*) ;;
    *) HOOK_DIR="$REPO_ROOT/$HOOK_DIR" ;;
esac
HOOK="$HOOK_DIR/pre-commit"

mkdir -p "$HOOK_DIR"

if [ -e "$HOOK" ] && ! grep -q "SentinelCommit" "$HOOK" 2>/dev/null; then
    echo "error: $HOOK already exists and was not installed by SentinelCommit." >&2
    echo "       Move it aside, or chain it from the SentinelCommit hook." >&2
    exit 1
fi

cp "$SRC_DIR/hooks/pre-commit" "$HOOK"
chmod +x "$HOOK"

echo "SentinelCommit installed -> $HOOK"

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo
    echo "note: ANTHROPIC_API_KEY is not set. Until it is, the hook fails open"
    echo "      (commits are allowed unaudited). Set it with:"
    echo "        export ANTHROPIC_API_KEY=sk-ant-..."
fi
