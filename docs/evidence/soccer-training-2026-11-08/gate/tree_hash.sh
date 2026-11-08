#!/bin/sh
# Code-tree hash of the soccer worktree (everything except docs/): tracked diff vs HEAD + untracked files' sha1.
cd "$(dirname "$0")/../../../.." && { git diff HEAD --binary -- . ':(exclude)docs'; git ls-files -o --exclude-standard -- . ':(exclude)docs' | sort | xargs shasum; } | shasum
