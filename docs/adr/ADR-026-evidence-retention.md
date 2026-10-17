# ADR-026: Evidence Retention

- Status: Accepted
- Date: 2026-10-17

## Context

Evidence supports verification and audit but may contain sensitive business or personal information. A single fixed retention duration is not appropriate as a protocol invariant.

## Decision

- Evidence retention is a configurable policy and must not be encoded as a fixed protocol duration.
- Evidence collection is risk-based, access-controlled, and redacted according to data sensitivity.
- Deployment / release configuration must specify retention and capacity behavior before release.
- Audit records cannot be silently removed by ordinary business cleanup.

## Consequences

- Policy can set different retention periods by evidence class and deployment context.
- Export, deletion, and capacity limits must preserve legal / audit controls configured by the organization.

## Security and Compatibility Effects

- Retention settings do not authorize collecting unnecessary secrets or sensitive payloads.
- Cleanup must be observable and auditable.

## Validation

- Tests verify configured expiry, access controls, redaction, cleanup audit, and that no fixed duration is assumed by the protocol.
