# ADR 0001: One runtime owns the agent loop

**Status:** Accepted
**Date:** 2026-08-29

## Context

Personal and corporate model access differ in authentication and protocol
transport. Allowing providers to own orchestration would duplicate policy,
tools, limits, evidence, and failure semantics.

## Decision

`AgentRuntime` exclusively owns turns, tool dispatch, policy/approval,
execution, budgets, and termination. Provider adapters only translate normalized
turns/events and provider errors. Authentication is a separate port.

## Consequences

Provider capability differences must be explicit. Integrations may require
narrow adapters, but cannot call tools or create nested loops. Fake providers
can test the complete runtime without model credentials.
