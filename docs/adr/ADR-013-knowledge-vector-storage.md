# ADR-013: Knowledge Vector Storage

- **Status:** Accepted
- **Date:** 2026-10-17
- **Scope:** ShuLiu Agent Foundation V0.1

## Context

Foundation needs semantic retrieval for server-side Knowledge while enforcing document access controls. A separate vector service would add another store, backup path, operational surface, and synchronization boundary.

## Decision

1. Use Cloud PostgreSQL with `pgvector` as the V0.1 vector store.
2. Do not introduce a second, independent vector database in V0.1.
3. Treat the vector index as Foundation server-side Knowledge infrastructure.
4. Build the authorized candidate set using trusted tenant / organization, scope, classification, and Permission context **before** vector search and ranking.
5. Prohibit global vector Top-K retrieval followed by application-layer ACL filtering.

## Options Considered

- PostgreSQL + `pgvector`, colocated with Cloud business data and Knowledge metadata.
- A separate vector database, which would require another service and data-lifecycle boundary.

The first option is selected for V0.1. This ADR does not decide the document parser, embedding provider/profile, index migration method, or cache invalidation details.

## Consequences

- PostgreSQL schema, migrations, backup, and query operations own Knowledge vector records.
- Retrieval queries must include authorization predicates in the candidate selection before similarity ordering and limiting.
- ACL changes and document deletion must invalidate or remove affected index/cache content; detailed propagation remains an implementation decision.
- A future change to a separate vector database requires a new ADR and migration plan.

## Security and Compatibility Effects

- ACL filtering is a retrieval boundary, not a post-processing convenience. Unauthorized chunks must never enter the candidate result set returned to the caller.
- Tenant, scope, classification, and Permission inputs must come from trusted authorization context, not from model-generated query text.
- Protocol versions remain independent of the database and index schema versions.

## Validation

- Add query-level tests proving ACL predicates constrain the candidate set before vector ordering / limit.
- Add negative tests proving inaccessible documents cannot appear in retrieved chunks, citations, or result counts.
- Test concurrent permission revocation, cache invalidation, and deletion propagation before claiming the implementation verified.

## References

- Architecture Spec: §§8.1, 13, 21, 23.
- Implementation Plan: Knowledge and Model Gateway dependency group; ADR-023 follow-up details.
