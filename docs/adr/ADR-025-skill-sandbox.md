# ADR-025: V0.1 Skill Trust and Sandbox

- Status: Accepted
- Date: 2026-10-17

## Context

Skills are extensibility units but may not receive implicit access to Foundation data, secrets, host resources, or arbitrary side effects.

## Decision

- V0.1 accepts only ShuLiu Official and Company Internal Skills. Third-party Skill Store is out of scope.
- Executable Skills run in a separate process with enforced isolation.
- A Skill process may not directly access Secrets, operate the OS, make arbitrary network requests, or write directly to Foundation databases.
- Every side effect follows `Tool Broker → Policy → Approval → Executor`.
- If the current OS / Worker cannot enforce the required isolation properties, the Skill is not activated on that platform.

## Consequences

- Sandboxing is a mandatory capability boundary, not a best-effort warning.
- OS-specific sandbox mechanisms and signature trust-root rotation remain validation / release configuration; they cannot relax the denied capabilities.

## Security and Compatibility Effects

- New or increased Skill permissions require renewed administrator approval.
- A failed Skill upgrade leaves the prior verified version usable.

## Validation

- Security tests attempt direct Secret, OS, network, and database access from the Skill process and expect denial.
- Tampered signatures, unknown publishers, permission increases without approval, and incompatible manifests cannot activate.
