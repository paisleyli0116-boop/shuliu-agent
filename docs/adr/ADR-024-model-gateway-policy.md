# ADR-024: Model Gateway Capability and Routing Policy

- Status: Accepted
- Date: 2026-10-17

## Context

Skills need model capabilities without becoming coupled to a provider or bypassing privacy, budget, or health constraints.

## Decision

- A Skill declares capabilities such as `fast_chat`, `reasoning_high`, `vision`, or `embedding`; it never declares a concrete Provider or Model.
- Model Gateway selects a Provider / Model using requested capability, privacy classification, budget, provider health, and fallback policy.
- ShuLiu's own policy and capability-routing layer wraps LiteLLM Provider integration; LiteLLM cannot bypass ShuLiu policy checks.
- Provider priority and concrete model mapping are configuration, not Skill contract fields.
- Every fallback is re-evaluated through Policy. `LOCAL_ONLY` data cannot fall back to a remote Provider.

## Consequences

- Provider lists and budgets can change without changing a Skill version.
- ModelCall records preserve requested capability and resolved provider / model for audit and run reproducibility.

## Security and Compatibility Effects

- A provider health failure cannot override privacy or budget constraints.
- Unknown capabilities or missing eligible providers fail closed with a typed error.

## Validation

- Fake Provider tests cover capability matching, privacy, budget, health, fallback order, and `LOCAL_ONLY` isolation.
