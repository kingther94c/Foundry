# ADR 0002: V1 is trusted-host, not sandboxed

**Status:** Accepted
**Date:** 2026-08-29

## Context

The target Windows 11 environment permits child processes but does not provide
a selected isolation boundary. A cwd/path allowlist does not constrain a
compiler, script, or shell process.

## Decision

V1 supports trusted repositories on the user's host. File tools enforce a
workspace boundary and all side effects pass through policy, but arbitrary
commands execute with the user's ambient authority. The product never describes
approval as sandboxing.

## Consequences

Warnings are part of the UX and documentation. Commands receive stricter
approval and bounded process handling. Security containment is a future project,
not a claim inferred from validation checks.
