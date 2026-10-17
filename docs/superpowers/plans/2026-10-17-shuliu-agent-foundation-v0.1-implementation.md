# ShuLiu Agent Foundation V0.1 Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement an approved subsystem plan task by task. Do not begin implementation from this umbrella plan alone; complete its decision and decomposition gates first.

**Goal:** Turn the approved ShuLiu Agent Foundation V0.1 architecture into a small, ordered set of implementation plans and then deliver a verifiable Foundation without adding a role-specific business agent.

**Architecture:** Define protocol and repository decisions first, then build a local execution slice around the Python Runtime and SQLite. Add Desktop IPC, Skill lifecycle, Knowledge and Model Gateway, and Cloud/Worker in separately reviewable plans. Use fake providers and tools for deterministic core tests; gate all external side effects through Policy and result verification.

**Tech Stack:** Confirmed baseline: Electron Desktop, Python Runtime, local SQLite, Cloud PostgreSQL. Confirmed transports: stdio + NDJSON for Electron↔Python; WSS for Cloud↔Worker. Exact language and runtime versions, frameworks, packaging, and provider choices remain subject to the ADR gate below.

**Spec:** `docs/superpowers/specs/2026-10-17-shuliu-agent-foundation-v0.1-design.md`

**Plan Status:** Umbrella implementation plan. It has not passed the task-level TDD and acceptance-command review required for direct code execution.

## Confirmed Design Decisions

These decisions are confirmed for the V0.1 Implementation Plan as of 2026-10-17. They supersede the matching pending labels in the Architecture Spec for this plan and must be reconciled into the corresponding ADR records before implementation.

| Area | Confirmed decision |
|---|---|
| Identity | V0.1 is for one company. Reserve `organization_id`; do not implement full multi-tenant SaaS. Users use `User + Role + Permission`. Workers use a one-time registration Token plus device identity. |
| Data authority | Cloud PostgreSQL is the business Source of Truth. SQLite stores only cache, outbox, inbox, checkpoint, and local task state. Do not implement full bidirectional synchronization. |
| Communication | Electron↔Python uses stdio + NDJSON. Cloud↔Worker uses WSS with heartbeat, lease, ACK, and idempotency. |
| Security | Store local Secrets in OS secure storage and Cloud Secrets in a cloud secret manager. Policy defaults to Fail Closed. High-risk operations require Approval. Skills and Updates must pass signature and version-compatibility checks. |

The existing Architecture Spec labels on identity, data synchronization, and communication are historical pending markers. The ADR update in Task 0 reconciles those sections; do not reopen the four confirmed decisions during plan decomposition.

## Global Constraints

- Foundation provides shared runtime capabilities; role-specific business agents, business rules, and business data models are out of scope.
- Initial protocol compatibility baseline is `protocol_version: 1`; Foundation, Runtime, Worker, Skill, Event, and database Schema versions remain independent.
- Cloud PostgreSQL is the business Source of Truth. SQLite is limited to cache, outbox, inbox, checkpoint, and local task state; no full bidirectional sync.
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

The repository currently contains `README.md` and the Architecture Spec; it has no application code or repository-specific `AGENTS.md`.

Planning documents created by this plan:

- `docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-implementation.md` — this umbrella plan.
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
| ADR-017 | Open | Repository layout, language/runtime version floors, package managers, test tooling, CI, and branch/release conventions |
| ADR-018 | Confirmed | Single-company V0.1 identity model, reserved `organization_id`, `User + Role + Permission`, and Worker one-time registration Token plus device identity |
| ADR-019 | Confirmed | Cloud PostgreSQL Source of Truth; SQLite cache/outbox/inbox/checkpoint/local task state; no full bidirectional sync |
| ADR-020 | Confirmed | stdio + NDJSON for Electron↔Python; WSS + heartbeat + lease + ACK + idempotency for Cloud↔Worker |
| ADR-021 | Partially confirmed | Communication uses heartbeat and lease; scheduler semantics, timezone, missed triggers, deduplication, Lease duration, heartbeat interval, and grace period remain open |
| ADR-022 | Partially confirmed | Policy defaults to Fail Closed and high-risk operations require Approval; exact risk mapping, permission granularity, timeouts, retry limits, and uncertain side-effect handling remain open |
| ADR-023 | Open | Knowledge local index, Cloud vector storage, parsers, embedding route, ACL invalidation, and source sync |
| ADR-024 | Open | Model Provider set, capability mapping, budgets, local-model scope, and `LOCAL_ONLY` enforcement |
| ADR-025 | Partially confirmed | Skill and Update signature and version-compatibility checks are mandatory; sandbox, scanning, signature trust roots, publisher workflow, and deprecation window remain open |
| ADR-026 | Open | Event, Audit, and Evidence access, encryption, retention, capacity, export, and deletion |
| ADR-027 | Partially confirmed | Updates require signatures and compatibility checks; compatibility window, release rollout batches, health observation window, rollback constraints, and performance gates remain open |

The original Architecture Spec already records ADR-001 through ADR-016. If a decision here changes one of those recorded outcomes, update the corresponding ADR and Architecture Spec in the same reviewed change.

## Delivery Order

| Order | Subsystem plan | Prerequisites | V0.1 outcome |
|---:|---|---|---|
| 0 | ADR decision set and repository layout | User review of this plan | Implementation-critical choices have an owner and accepted or deferred status |
| 1 | Contracts and Runtime bootstrap | ADR-017, ADR-020 | Versioned schemas, error envelope, migration baseline, and a healthy Python Runtime bootstrap |
| 2 | Agent, Policy, Approval, and Tool execution | 1, ADR-018, ADR-022, ADR-024 | Deterministic local Task → AgentRun → Policy → Approval → Tool → verified result path |
| 3 | Workflow, Scheduler, Checkpoint, and recovery | 1–2, ADR-019, ADR-021, ADR-022 | Durable waiting, retry, restart recovery, and idempotency behavior |
| 4 | Desktop IPC | 1–3, ADR-017, ADR-020 | Renderer can create and observe a task through the allowlisted Main↔Python interface |
| 5 | Skill lifecycle and package security | 1–3, ADR-025 | Verified staging, contract tests, activation, permission-change Approval, rollback, and removal guards |
| 6 | Model Gateway and Knowledge | 1–3, ADR-023, ADR-024, ADR-026 | Policy-aware model routing and ACL-filtered knowledge retrieval with traceable sources |
| 7 | Cloud Control Plane and Worker | 1–3, ADR-018–021, ADR-026 | Authenticated task dispatch, one-time Worker registration, Lease renewal, heartbeat, ACK, fencing, result reconciliation, and dead-letter handling |
| 8 | Release, migration, and acceptance | 1–7, ADR-026–027 | Signed Desktop/Runtime release, health gate, compatible rollback, Windows install/upgrade acceptance |

The first useful running slice is local and deterministic. Cloud/Worker work starts only after identity, data ownership, transport, and lease semantics are accepted. Skill and Knowledge work can proceed independently after the contracts and their security/privacy ADRs are accepted.

## Tasks

### Task 0: Review and close architecture decisions

**Files:**
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
- Consumes: accepted ADRs 017–027 and the Architecture Spec.
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
- Consumes: Task 0 accepted ADRs and Task 1 subsystem plans.
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
