# ADR-015: Data Authority and Synchronization Boundary

- **Status:** Accepted
- **Date:** 2026-10-17
- **Scope:** ShuLiu Agent Foundation V0.1

## Context

Desktop clients may be offline while Cloud continues to own shared business state. A general bidirectional database synchronization system would require conflict resolution and authority rules for every replicated entity, which is outside the V0.1 Foundation scope.

## Decision

1. Cloud PostgreSQL is the business Source of Truth.
2. Local SQLite is limited to `cache`, `outbox`, `inbox`, `checkpoint`, and `local task state`.
3. V0.1 does not implement general bidirectional SQLite ↔ PostgreSQL database synchronization.
4. Local events intended for Cloud are persisted in an outbox and uploaded after connectivity is available.
5. The server deduplicates using `event_id` and / or `idempotency_key` and returns an ACK after durable acceptance.
6. The client marks an outbox event synchronized only after receiving the ACK. Events without an ACK remain pending and are retried after reconnect.

## Options Considered

- General bidirectional synchronization of local and Cloud databases, including entity-level conflict resolution.
- Cloud-authoritative shared business state with a narrowly scoped local outbox / inbox protocol.

The second option is selected for V0.1.

## Consequences

- Offline-first behavior is bounded to explicitly defined local task state and durable outbox events; local SQLite is not a second authority for shared business entities.
- Server APIs must make event ingestion idempotent and return an ACK that can be correlated to the submitted event.
- A lost ACK may cause retransmission; deduplication must ensure the event is not applied twice.
- Rebuildable cache data may be fetched again from Cloud; no general merge engine is required.

## Security and Compatibility Effects

- Outbox and inbox records must follow data classification, retention, and Secret-redaction requirements.
- Event schemas remain independently versioned; unsupported schema versions must fail visibly rather than silently corrupt state.
- Idempotency retention and event deletion policies remain implementation details to be sized and reviewed.

## Validation

- Verify upload → durable server acceptance → ACK → local synchronized state.
- Inject disconnects before upload, after server commit, and before ACK receipt; verify reconnect retries safely and applies each event at most once at the server boundary.
- Verify a pending outbox event survives process restart and is not marked synchronized before ACK.
- Verify cache reconstruction does not overwrite Cloud-owned business state.

## References

- Architecture Spec: §§8.1, 21–23.
- Implementation Plan: Contracts / Runtime, Workflow Recovery, and Cloud / Worker dependency groups.
