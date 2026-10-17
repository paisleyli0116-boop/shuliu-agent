# ShuLiu Agent Foundation V0.1 Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement an approved subsystem plan task by task. Do not begin implementation from this umbrella plan alone; complete its decision and decomposition gates first.

**Goal:** Turn the approved ShuLiu Agent Foundation V0.1 architecture into a small, ordered set of implementation plans and then deliver a verifiable Foundation without adding a role-specific business agent.

**Architecture:** Define protocol and repository decisions first, then build a local execution slice around the Python Runtime and SQLite. Add Desktop IPC, Skill lifecycle, Knowledge and Model Gateway, and Cloud/Worker in separately reviewable plans. Use fake providers and tools for deterministic core tests; gate all external side effects through Policy and result verification.

**Tech Stack:** Confirmed baseline: Electron Desktop, Python Runtime, local SQLite, Cloud PostgreSQL, and APScheduler 3.11.3 for Trigger scheduling. Confirmed transports: stdio + NDJSON for Electron↔Python; WSS for Cloud↔Worker. Node/Python version floors, frameworks, packaging, and provider choices remain subject to the ADR gate below.

**Spec:** `docs/superpowers/specs/2026-10-17-shuliu-agent-foundation-v0.1-design.md`

**Plan Status:** Umbrella implementation plan. It has not passed the task-level TDD and acceptance-command review required for direct code execution.

## Confirmed Design Decisions

These decisions are recorded for the V0.1 Implementation Plan as of 2026-10-17. ADR-013–016 have since been reconciled into the Architecture Spec and their accepted ADR records. Other plan-level decisions must not be treated as architecture facts until they are reconciled into the Architecture Spec; the Spec remains the architecture authority.

| Area | Confirmed decision |
|---|---|
| Identity | V0.1 is for one company. Reserve `organization_id`; do not implement full multi-tenant SaaS. Users use `User + Role + Permission`. Workers use a one-time registration Token plus device identity. |
| Data authority | Cloud PostgreSQL is the business Source of Truth. SQLite stores only cache, outbox, inbox, checkpoint, and local task state. Do not implement full bidirectional synchronization. |
| Communication | Electron↔Python uses stdio + NDJSON. Cloud↔Worker uses WSS with heartbeat, lease, ACK, and idempotency. |
| Security | Store local Secrets in OS secure storage and Cloud Secrets in a cloud secret manager. Policy defaults to Fail Closed. High-risk operations require Approval. Skills and Updates must pass signature and version-compatibility checks. |
| Knowledge | Cloud PostgreSQL + pgvector; tenant / scope / classification ACL filters run before vector search; no second vector database. |
| Scheduler | APScheduler 3.11.3 persists triggers; Workflow Engine owns execution. Stable Schedule IDs, replace on startup, `latest` coalescing by default, explicit `all`, no same-Schedule overlap, configurable `misfire_grace_time`, and idempotent side effects. |

ADR-013–016 now close the Architecture Spec's former Pending items for vector storage, IPC / Worker identity, data synchronization, and scheduling. Cloud user identity / tenant authorization remains a separate open decision; Worker enrollment identity is covered by ADR-014. Do not reopen ADR-013–016 during plan decomposition.

## Global Constraints

- Foundation provides shared runtime capabilities; role-specific business agents, business rules, and business data models are out of scope.
- Initial protocol compatibility baseline is `protocol_version: 1`; Foundation, Runtime, Worker, Skill, Event, and database Schema versions remain independent.
- Cloud PostgreSQL is the business Source of Truth. SQLite is limited to cache, outbox, inbox, checkpoint, and local task state; no full bidirectional sync.
- Local outbox events upload with stable `event_id` and `idempotency_key`; the server ACK follows durable acceptance and duplicate IDs are not applied twice. Unacknowledged events remain queued and resume after reconnect.
- Knowledge uses PostgreSQL + pgvector. Authorization context filters tenant / organization, scope, classification, and permission before vector search; post-search ACL filtering is prohibited.
- APScheduler 3.11.3 only emits persisted schedule occurrences. Workflow Engine executes them. Same-Schedule WorkflowRuns do not overlap; `latest` is the default misfire coalescing policy, `all` requires explicit Schedule configuration.
- Worker enrollment uses a one-time Registration Token and device-generated key; runtime authentication uses short-lived session credentials. V0.1 does not require mTLS.
- An AgentRun pins the Agent, Skill, and Workflow versions it starts with; updates affect new runs.
- Policy defaults to Fail Closed. Every external side effect passes Policy, and every high-risk operation requires Approval before execution.
- Browser and Computer actions follow `OBSERVE → ACT → VERIFY`; an unverified result cannot silently advance a high-risk Workflow.
- Retryable side effects use idempotency keys and verify external state before recovery; do not replay a pending ToolCall blindly.
- Worker result commits validate the current Lease and fencing token.
- Electron↔Python uses stdio + NDJSON; Cloud↔Worker uses WSS with heartbeat, lease, ACK, and idempotency.
- Skill and Update packages must pass signature and version-compatibility checks. Skill installation also checks checksum, dependencies, permissions, and contract tests; V0.1 trusts only ShuLiu official and enterprise-internal publishers.
- Local Secrets use OS secure storage; Cloud Secrets use a cloud secret manager.
- Secrets, credentials, and unnecessary sensitive content must not appear in Event, Log, Trace, or Evidence payloads.
- Releases use SemVer, a compatibility manifest, signed packages, migration checks, health gates, and compatibility-checked rollback.
- Windows-only packaging, installation, upgrade, and rollback validation runs on a Windows runner or Windows device; Mac CI must not claim these checks passed.

## Review Focus

- A crash after an external action but before result persistence must recover by checking external state and idempotency before any retry. Owner: Workflow and recovery plan.
- An Approval must become invalid if the protected action parameters change after approval. Owner: Agent, Policy, and Tool plan.
- A `LOCAL_ONLY` request must never fall back to a remote Model Provider. Owner: Model Gateway plan.
- A Worker result with an expired Lease or stale fencing token must be rejected. Owner: Cloud and Worker plan.
- A tampered Skill package or unapproved permission increase must not activate, while a failed upgrade leaves the prior version usable. Owner: Skill lifecycle plan.

## Repository and File Structure

The repository contains `README.md`, `AGENTS.md`, the Architecture Spec, the project-memory documents, the architecture decision register, the technical baseline, and the risk register. It has no application code yet.

Planning documents created by this plan:

- `docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-implementation.md` — this umbrella plan.
- `docs/adr/ADR-013-knowledge-vector-storage.md` through `docs/adr/ADR-016-scheduler-semantics.md` — accepted architecture decisions.
- `docs/adr/ADR-017-repository-and-toolchain.md` through `docs/adr/ADR-027-release-and-acceptance-gates.md` — decision records to draft and review before code.
- `docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-01-contracts-runtime.md`
- `docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-02-agent-policy-tools.md`
- `docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-03-workflow-recovery.md`
- `docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-04-desktop-ipc.md`
- `docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-05-skill-lifecycle.md`
- `docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-06-model-knowledge.md`
- `docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-07-cloud-worker.md`
- `docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-08-release-acceptance.md`

Proposed code layout for review in ADR-017 (not yet an accepted repository decision):

```text
apps/desktop/          Electron Main, preload, and Renderer
packages/contracts/    Machine-readable protocol schemas and generated bindings
runtime/python/        Python Foundation Runtime and local persistence adapters
services/cloud/        Cloud Control Plane
services/worker/       Worker Runtime
tests/contracts/       Cross-language and protocol compatibility tests
tests/integration/     Deterministic integration tests with fake dependencies
tests/e2e/             Desktop-to-runtime acceptance flows
```

Each implementation plan must replace proposed paths with the paths accepted by ADR-017 and give exact interfaces, test names, and verification commands. No code paths are considered fixed by this umbrella plan.

## Decision Gate Before Code

Create each ADR as a short record containing Context, Options, Decision, Consequences, Security and Compatibility Effects, and Validation. Mark a decision `Accepted` only after review; mark work outside V0.1 `Deferred` with an owner and a revisit trigger. Do not leave an acceptance-critical choice as an unowned `TBD`.

| ADR | Status | Decision to record or close |
|---|---|---|
| ADR-013 | Accepted | Cloud PostgreSQL + pgvector; apply tenant / scope / classification ACL filters before vector search; no second vector database |
| ADR-014 | Accepted | stdio + NDJSON with stdout protocol / stderr logs; WSS Worker enrollment with one-time token, device key, `worker_id`, short-lived session credentials; no mandatory mTLS in V0.1 |
| ADR-015 | Accepted | Cloud PostgreSQL Source of Truth; SQLite cache/outbox/inbox/checkpoint/local task state; outbox + server ACK + event/idempotency de-duplication; no general bidirectional sync |
| ADR-016 | Accepted | APScheduler 3.11.3 persistent Trigger; Workflow Engine Execution; stable ID + replace; latest by default, all explicit; no same-Schedule overlap; configurable misfire grace; idempotent side effects |
| ADR-017 | Open | Repository layout, language/runtime version floors, package managers, test tooling, CI, and branch/release conventions |
| ADR-018 | Confirmed in Plan; Spec reconciliation pending | Single-company V0.1 identity model, reserved `organization_id`, and `User + Role + Permission`; Architecture Spec §23 still lists identity / tenant model as pending |
| ADR-019 | Superseded | Data authority and sync are governed by ADR-015; no duplicate decision record |
| ADR-020 | Superseded | IPC and Worker identity are governed by ADR-014; no duplicate decision record |
| ADR-021 | Superseded | Scheduler semantics are governed by ADR-016; no duplicate decision record |
| ADR-022 | Partially confirmed | Policy defaults to Fail Closed and high-risk operations require Approval; exact risk mapping, permission granularity, timeouts, retry limits, and uncertain side-effect handling remain open |
| ADR-023 | Partially closed by ADR-013 | Vector store and ACL-before-search are fixed; parsers, embedding route/profile, index migration and ACL cache invalidation remain open |
| ADR-024 | Open | Model Provider set, capability mapping, budgets, local-model scope, and `LOCAL_ONLY` enforcement |
| ADR-025 | Partially confirmed | Skill and Update signature and version-compatibility checks are mandatory; sandbox, scanning, signature trust roots, publisher workflow, and deprecation window remain open |
| ADR-026 | Open | Event, Audit, and Evidence access, encryption, retention, capacity, export, and deletion |
| ADR-027 | Partially confirmed | Updates require signatures and compatibility checks; compatibility window, release rollout batches, health observation window, rollback constraints, and performance gates remain open |

The original Architecture Spec already records ADR-001 through ADR-016. If a decision here changes one of those recorded outcomes, update the corresponding ADR and Architecture Spec in the same reviewed change.

## Delivery Order

| Order | Subsystem plan | Prerequisites | V0.1 outcome |
|---:|---|---|---|
| 0 | ADR decision set and repository layout | User review of this plan | Implementation-critical choices have an owner and accepted or deferred status |
| 1 | Contracts and Runtime bootstrap | ADR-014, ADR-017 | Versioned schemas, error envelope, migration baseline, and a healthy Python Runtime bootstrap |
| 2 | Agent, Policy, Approval, and Tool execution | 1, ADR-018, ADR-022, ADR-024 | Deterministic local Task → AgentRun → Policy → Approval → Tool → verified result path |
| 3 | Workflow, Scheduler, Checkpoint, and recovery | 1–2, ADR-015, ADR-016, ADR-022 | Durable waiting, latest/all scheduling semantics, no overlapping Schedule runs, restart recovery, and idempotency behavior |
| 4 | Desktop IPC | 1–3, ADR-014, ADR-017 | Renderer can create and observe a task through the allowlisted Main↔Python interface |
| 5 | Skill lifecycle and package security | 1–3, ADR-025 | Verified staging, contract tests, activation, permission-change Approval, rollback, and removal guards |
| 6 | Model Gateway and Knowledge | 1–3, ADR-013, ADR-023–024, ADR-026 | Policy-aware model routing and ACL-prefiltered pgvector retrieval with traceable sources |
| 7 | Cloud Control Plane and Worker | 1–3, ADR-014–015, ADR-018, ADR-026 | Authenticated task dispatch, one-time Worker registration, short-lived sessions, Lease renewal, heartbeat, ACK, fencing, result reconciliation, and dead-letter handling |
| 8 | Release, migration, and acceptance | 1–7, ADR-026–027 | Signed Desktop/Runtime release, health gate, compatible rollback, Windows install/upgrade acceptance |

The first useful running slice is local and deterministic. Cloud/Worker work starts after ADR-014 / ADR-015 and the remaining Cloud user-identity / tenant-authorization details in ADR-018 are addressed. Skill and Knowledge work can proceed independently after the contracts and their security/privacy ADRs are accepted.

## Tasks

### Task 0: Review and close architecture decisions

**Files:**
- Read: `docs/adr/ADR-013-knowledge-vector-storage.md` through `docs/adr/ADR-016-scheduler-semantics.md`
- Create: `docs/adr/ADR-017-repository-and-toolchain.md` through `docs/adr/ADR-027-release-and-acceptance-gates.md`
- Modify only when decisions change: the matching ADR or section in `docs/superpowers/specs/2026-10-17-shuliu-agent-foundation-v0.1-design.md`

**Interfaces:**
- Consumes: Architecture Spec sections 6–20 and ADR-001 through ADR-016.
- Produces: accepted decisions or explicitly deferred decisions with owners, revisit triggers, and validation criteria.

- [ ] **Step 1: Draft ADRs 017–027; copy confirmed decisions verbatim and mark their status**
- [ ] **Step 2: Review security, data privacy, compatibility, and V0.1 delivery impact for Open or Partially confirmed decisions; do not reopen confirmed decisions without an explicit user request**
- [ ] **Step 3: Record accepted or deferred status and update superseded architecture statements**
- [ ] **Step 4: Verify every open decision has an owner and revisit trigger; verify no acceptance-critical `TBD` remains**
- [ ] **Step 5: Commit the ADR decision set**

### Task 1: Produce implementation plans for independent subsystems

**Files:**
- Create the eight plan files listed under Repository and File Structure.
- Modify: this umbrella plan only if subsystem order or dependencies change.

**Interfaces:**
- Consumes: accepted ADRs 013–016, accepted or explicitly deferred ADRs 017–027, and the Architecture Spec.
- Produces: independently executable plans with exact paths, contracts, test cases, expected outputs, and commit boundaries.

- [ ] **Step 1: Write the contracts and Runtime bootstrap plan**
- [ ] **Step 2: Write the Agent, Policy, Approval, and Tool execution plan**
- [ ] **Step 3: Write the Workflow, Scheduler, Checkpoint, and recovery plan**
- [ ] **Step 4: Write the Desktop IPC plan**
- [ ] **Step 5: Write the Skill lifecycle and security plan**
- [ ] **Step 6: Write the Model Gateway and Knowledge plan**
- [ ] **Step 7: Write the Cloud and Worker plan**
- [ ] **Step 8: Write the release and acceptance plan**
- [ ] **Step 9: Cross-check that every Architecture Spec acceptance criterion maps to at least one subsystem-plan task**

### Task 2: Review the plan set and authorize implementation method

**Files:**
- Read: this umbrella plan and all eight subsystem plans.
- Modify: plans only to resolve review findings.

**Interfaces:**
- Consumes: Task 0 accepted ADRs 013–016, resolved Task 0 decision gates, and Task 1 subsystem plans.
- Produces: an approved plan set and a selected execution method.

- [ ] **Step 1: Check plan coverage against Architecture Spec sections 2–20 and acceptance criteria 20.2**
- [ ] **Step 2: Check that every implementation task has exact files, interfaces, tests, and a passing command**
- [ ] **Step 3: Review the five Review Focus cases and confirm each has an owning test**
- [ ] **Step 4: Record the user's approval and execution method before implementation begins**

## Implementation Exit Criteria

Implementation is complete only when the subsystem plans collectively satisfy Architecture Spec §20.2, including protocol contract tests, policy and approval behavior, immutable run versions, idempotent recovery, Lease/fencing enforcement, Safe Mode, Skill supply-chain checks, release health gates, and failure-injection continuity of Event/Audit. The Architecture Spec's initial performance targets remain non-blocking until ADR-027 defines the measured environment and release thresholds.

## Self-Review

- **Spec coverage:** The delivery order maps each subsystem in §§5–20 to a plan; Task 1 requires an explicit §20.2 acceptance-criterion cross-check before implementation.
- **Step scan:** This document is an umbrella plan. Its tasks produce ADRs, subsystem plans, and an approved execution method; exact source files and test commands belong in the eight subsystem plans after ADR-017 fixes the code layout and ADRs close runtime choices.
- **Type consistency:** No implementation interfaces are declared here; interface names and types are intentionally owned by the contracts and subsystem plans.
- **Review Focus:** The five failure classes above are assigned to owning plans and must appear as tests in those plans.
- **Proportion:** The document sequences independent subsystems and gates without duplicating the Architecture Spec or prescribing implementation bodies.
