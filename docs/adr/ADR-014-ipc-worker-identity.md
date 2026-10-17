# ADR-014: IPC and Worker Identity

- **Status:** Accepted
- **Date:** 2026-10-17
- **Scope:** ShuLiu Agent Foundation V0.1

## Context

The Desktop must communicate with its Python Sidecar over a local process boundary. Cloud must authenticate remote Workers without persisting a reusable long-lived credential in plaintext configuration. The two channels have different trust boundaries and use distinct protocols.

## Decision

### Electron ↔ Python Sidecar

1. Electron Main starts the Python Sidecar and communicates over `stdio + NDJSON`.
2. `stdout` is reserved exclusively for the NDJSON protocol channel; each message is one UTF-8 JSON line.
3. Protocol-external diagnostics go to `stderr`.
4. Long-term Secrets must not be placed in Sidecar command-line arguments or plaintext configuration.

### Cloud ↔ Worker

1. Use WSS for the Cloud ↔ Worker connection.
2. First enrollment uses a one-time Registration Token.
3. The Worker generates its device key locally and registers its device identity using that enrollment flow; Cloud assigns the resulting `worker_id`.
4. Runtime connections use short-lived session credentials.
5. Long-term Secrets must not be stored as plaintext in Worker configuration.
6. V0.1 does not require mTLS. The protocol should leave room for a future mTLS upgrade.

## Options Considered

- Local IPC over `stdio + NDJSON` with protocol output separated from diagnostics.
- Cloud ↔ Worker over WSS with one-time enrollment, a Worker-generated device key, and short-lived runtime sessions.
- Mandatory mTLS for V0.1 Worker connections.

The first two options are selected. Mandatory mTLS is deferred; this does not remove the requirement to authenticate WSS sessions.

## Consequences

- Runtime IPC needs explicit request / response schemas, correlation IDs, protocol version negotiation, and process health handling.
- Worker enrollment, device identity, and runtime session authentication are separate lifecycle steps.
- Device private-key storage, short-lived credential TTL and rotation, and user identity / authorization remain open implementation or architecture details.

## Security and Compatibility Effects

- Any non-protocol text on stdout can corrupt the IPC stream and must be treated as a protocol failure.
- Registration Tokens must be one-time and must not be logged or retained as reusable runtime credentials.
- Short-lived session credentials limit the useful lifetime of a disclosed runtime credential; exact TTL and renewal behavior remain to be specified.
- V0.1 authentication must not depend on mTLS being present, while message and identity design must not preclude adding it later.

## Validation

- Contract tests must reject malformed or version-incompatible NDJSON messages and verify stdout / stderr separation.
- Enrollment tests must prove a Registration Token cannot be reused and that the registered device maps to the Cloud-assigned `worker_id`.
- Session tests must verify expired credentials are rejected and no long-term plaintext Secret is written to configuration or logs.
- Future mTLS compatibility is a design review criterion; mTLS is not a V0.1 acceptance prerequisite.

## References

- Architecture Spec: §§12, 16, 19, 21, 23.
- Implementation Plan: Runtime Contracts, Desktop IPC, and Cloud / Worker dependency groups.
