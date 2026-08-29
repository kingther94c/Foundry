# ADR 0004: Use a local versioned JSONL journal and artifact store

**Status:** Proposed
**Date:** 2026-08-29

## Context

Foundry needs append-oriented evidence, crash tolerance, local inspection, and
offline operation. Tool/command outputs can be large or sensitive.

## Decision

Store small normalized events in a per-session, versioned JSONL journal. Store
bounded large payloads as content-addressed local artifacts referenced by digest.
Omit known secret fields structurally and redact free text before either sink.

## Consequences

JSONL is debuggable and supports partial recovery but needs explicit sequencing,
flush policy, schema migration, retention, concurrent-writer exclusion, and
truncated-tail handling. Encryption-at-rest and retention defaults remain open.
