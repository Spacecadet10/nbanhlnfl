#!/bin/sh
# Code-tree hash of the integration worktree (everything except docs/): diff vs HEAD + untracked files' sha1
# (same recipe as docs/evidence/soccer-training-2026-11-08/gate/tree_hash.sh).
cd "$(dirname "$0")/../../.." && { git diff HEAD --binary -- . ':(exclude)docs'; git ls-files -o --exclude-standard -- . ':(exclude)docs' | sort | xargs shasum; } | shasum
