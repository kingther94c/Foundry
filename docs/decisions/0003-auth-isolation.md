# ADR 0003: Isolate authentication and gate personal subscription auth

**Status:** Accepted
**Date:** 2026-08-29

## Context

Development would benefit from ChatGPT Plus/Pro subscription access; production
will use a corporate Gateway token acquired through an internal-subnet auth
mechanism. A supported independent-client ChatGPT flow has not been verified.

## Decision

Authentication is represented by `CredentialSource` and protected storage,
separate from model protocol adapters. Personal subscription auth is implemented
only after current official evidence establishes a supported route. Private
endpoints, browser scraping, copied caches, and silent API-key fallback are
prohibited. Fake/replay backends unblock runtime development.

## Consequences

Personal live validation may remain blocked. That cannot justify weakening the
boundary. Corporate auth discovery can proceed independently, and both paths
will feed the same Responses adapter when their protocol permits.
