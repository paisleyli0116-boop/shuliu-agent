# ADR-016: Scheduler Semantics

- **Status:** Accepted
- **Date:** 2026-10-17
- **Scope:** ShuLiu Agent Foundation V0.1

## Context

Foundation needs durable time-based triggers without coupling the scheduling library to Workflow business execution. Restart behavior, missed periodic occurrences, duplicate starts, and overlapping runs must be explicit so a long outage does not cause an uncontrolled burst of work.

## Decision

1. Use APScheduler 3.11.3 with a PostgreSQL-backed persistent datastore for V0.1 scheduling.
2. APScheduler owns Trigger calculation and durable scheduling state only. Workflow Engine owns WorkflowRun creation and execution; Scheduler callbacks must not contain business logic.
3. Each Schedule has a stable unique `schedule_id`. Startup registration uses replace semantics for an existing ID.
4. Periodic missed runs default to `coalesce: latest`. `coalesce: all` is allowed only when explicitly configured for a Schedule.
5. The default is no overlapping WorkflowRuns for the same Schedule. The Workflow Engine must enforce keyed serialization; APScheduler's `max_instances` setting alone is not the WorkflowRun concurrency control.
6. `misfire_grace_time` is configurable per Schedule. Eligible occurrences outside the configured grace period are recorded as `MISSED` and are not dispatched.
7. Every side-effecting WorkflowRun uses a stable occurrence identity / idempotency key so trigger retries cannot duplicate external effects.
8. V0.1 operates one active APScheduler 3.x scheduler against a given persistent job store. Multi-instance scheduler high availability is deferred to a future decision.
9. Schedule timezone and daylight-saving-time handling must be explicit. The deployment default and DST boundary policy remain open configuration decisions.

## Options Considered

- APScheduler 3.x stable series with persistent job store and application-owned Workflow execution semantics.
- APScheduler 4.x prerelease series with redesigned schedule / datastore concepts.
- A bespoke Foundation scheduler.

APScheduler 3.11.3 is selected for V0.1. At the time of this decision, the official project documentation labels the 4.0 series a prerelease and warns against production use. APScheduler 3.x documents explicit IDs and `replace_existing` for persistent job initialization, `coalesce`, `misfire_grace_time`, and `max_instances`. Its user guide also says job stores must not be shared between schedulers; therefore V0.1 uses one active scheduler per persistent store.

## Consequences

- Map Foundation `latest` to APScheduler 3.x coalescing enabled; map explicit `all` to coalescing disabled, then persist and dispatch the eligible occurrence records through the Workflow Engine.
- `replace` prevents duplicate Schedule registrations after restart; Schedule IDs are durable identifiers and must not be generated anew on each process start.
- Same-Schedule WorkflowRun serialization must be durable and application-owned, not inferred from APScheduler job concurrency.
- V0.1 does not claim multi-instance scheduler HA. A future HA design must choose a scheduler / datastore arrangement that provides safe coordination.

## Security and Compatibility Effects

- Schedule definitions and callback payloads stored in PostgreSQL must be treated as untrusted persisted data and validated before dispatch.
- Trigger execution cannot bypass Policy / Approval; all protected side effects remain in the Workflow / Tool path.
- Schedule schema and APScheduler job-store schema must be migrated and rolled back under the database migration policy.
- APScheduler's native Boolean coalescing flag is an adapter detail; Foundation's `latest | all` contract remains stable if the scheduler library changes later.

## Validation

- Verify persistent Schedule IDs survive restart and startup replacement does not create duplicate triggers.
- Verify `latest` produces at most the most recent eligible missed occurrence, while explicit `all` retains every eligible missed occurrence.
- Verify occurrences outside `misfire_grace_time` are marked `MISSED` and not dispatched.
- Verify the same Schedule cannot have two active WorkflowRuns, including after restart and under concurrent trigger delivery.
- Verify occurrence retries use the same idempotency key and cannot repeat an already accepted side effect.
- Verify timezone and DST cases against the configured deployment policy before enabling a production Schedule.

## References

- Architecture Spec: §§11, 21, 23.
- Implementation Plan: Workflow / Scheduler / Checkpoint / Recovery dependency group.
- [APScheduler 3.x User Guide](https://apscheduler.readthedocs.io/en/3.x/userguide.html)
- [APScheduler 3.x Scheduler API](https://apscheduler.readthedocs.io/en/3.x/modules/schedulers/base.html)
- [APScheduler 3.x Version History](https://apscheduler.readthedocs.io/en/master/versionhistory.html)
- [APScheduler 3.11.3 scheduler source](https://github.com/agronholm/apscheduler/blob/3.11.3/src/apscheduler/schedulers/base.py)
