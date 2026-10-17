# ADR-023: Knowledge Implementation Boundary

- Status: Accepted boundary; implementation parameters remain configurable
- Date: 2026-10-17

## Context

ADR-013 fixes the V0.1 vector store and ACL ordering. Parser, embedding model, and cache invalidation choices remain product integration details.

## Decision

- Use Cloud PostgreSQL + pgvector; do not add a separate vector database or local vector index in V0.1.
- Construct the authorized organization / scope / classification / Permission candidate set before vector search.
- Route Embedding through Model Gateway and apply the same privacy and `LOCAL_ONLY` policy as other model calls.
- Parser, embedding profile, index migration, and cache invalidation implementation are configurable Knowledge integration choices. No integration may bypass ACL-before-search or revocation / deletion invalidation.

## Consequences

- Tests can use FakeEmbedding and deterministic fixtures before a production provider is selected.
- A production Knowledge source is not enabled until parser, embedding, migration, and invalidation behavior passes integration tests.

## Security and Compatibility Effects

- Post-search application filtering is prohibited as an ACL control.
- Revoked or deleted source content must not remain retrievable through an index or cache.

## Validation

- Database-level tests verify ACL predicates are applied before vector ranking.
- Integration tests cover concurrent revocation, deletion propagation, and cache invalidation.
