# ADR 0005: Support dirty Git worktrees

**Status:** Accepted
**Date:** 2026-08-29

## Context

The user wants Foundry to work in an existing Git repository without requiring
them to discard or commit current work. Initial testing may occur on local main.

## Decision

Accept dirty worktrees. Capture a Git/content baseline, use last-observed content
digests as optimistic concurrency guards before writes, and report Foundry-touched
files separately from baseline/concurrent changes. Never use destructive Git
commands, staging, commits, stashes, or branch changes as an internal safeguard.

## Consequences

The implementation and test matrix are more complex than clean-tree-only work.
Foundry can prove file versions it observed and wrote, but must avoid claiming
perfect line ownership. Whether the first write to a baseline-dirty file requires
specific approval remains a product question.
