# ADR-018: V0.1 Identity Model

- Status: Accepted
- Date: 2026-10-17
- Scope: Foundation V0.1 internal deployment

## Context

V0.1 needs explicit user, organization, and permission boundaries without taking on a full multi-tenant SaaS architecture.

## Decision

- V0.1 is a single-company internal version. Data structures reserve `organization_id`; full multi-tenant SaaS is out of scope.
- The identity and authorization data model is `User + Organization Membership + Role + Permission`.
- Session / Access credentials are short-lived. Long-lived Refresh / Device Secrets must use secure storage.
- Identity Provider integration must use a replaceable abstraction interface. The concrete production provider is a deployment choice and must be selected before production Cloud deployment.

## Consequences

- Organization scope remains explicit in persisted records and authorization context.
- Implementation can use a fake provider for deterministic tests; production authentication cannot bypass the provider abstraction.
- Full tenant isolation, tenant administration, and SaaS provisioning are not V0.1 deliverables.

## Security and Compatibility Effects

- Membership, Role, and Permission checks are server-side and fail closed.
- Credential lifetime values, refresh rotation, and provider-specific details are configuration, not protocol constants.

## Validation

- Tests prove cross-organization access is denied and permissions are enforced server-side.
- Provider contract tests prove a second provider implementation can satisfy the same interface.
- Secret storage tests prove long-lived credentials are never written in plaintext to ordinary configuration or logs.
