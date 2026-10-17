# ADR-022: Policy Risk Matrix and Approval Defaults

- Status: Accepted
- Date: 2026-10-17

## Context

Tool execution needs stable risk categories and predictable approval requirements. Policy must remain the enforcement boundary for every side effect.

## Decision

| Risk | Meaning | Approval rule |
|---|---|---|
| LOW | Read, search, analyze | No implicit allow; an explicit Policy rule must allow it |
| MEDIUM | Non-destructive writes and ordinary forms | No implicit allow; an explicit Policy rule must allow or require approval |
| HIGH | External send, publish, formal record creation | Approval required by default |
| CRITICAL | Payment, bulk delete, permission or security configuration | Approval is mandatory |

- Decisions are `ALLOW`, `DENY`, or `REQUIRE_APPROVAL`.
- `DENY` has highest priority. If no explicit rule applies, Policy fails closed and denies.
- The Agent cannot approve its own action.
- Approval is bound to the action context; a parameter change invalidates the prior approval.

## Consequences

- Risk classification is part of the Tool contract and is audited.
- LOW / MEDIUM are not auto-allow classes; configured policy must make the allow decision.
- Permission granularity and per-tool timeouts are implementation policy data and cannot weaken the category defaults.

## Security and Compatibility Effects

- HIGH and CRITICAL actions cannot execute before approval. A policy configuration cannot downgrade CRITICAL to unapproved execution.
- Unknown or malformed risk classifications fail closed.

## Validation

- Unit and integration tests cover every category, DENY precedence, missing rules, approval expiry, context binding, and proof that denied / unapproved actions never reach the Executor.
