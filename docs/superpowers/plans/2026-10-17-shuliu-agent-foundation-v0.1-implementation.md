# ShuLiu Agent Foundation V0.1 Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement an approved subsystem plan task by task. Do not begin implementation from this umbrella plan alone; complete its decision and decomposition gates first.

**Goal:** Turn the approved ShuLiu Agent Foundation V0.1 architecture into a small, ordered set of implementation plans and then deliver a verifiable Foundation without adding a role-specific business agent.

**Architecture:** Define protocol and repository decisions first, then build a local execution slice around the Python Runtime and SQLite. Add Desktop IPC, Skill lifecycle, Knowledge and Model Gateway, and Cloud/Worker in separately reviewable plans. Use fake providers and tools for deterministic core tests; gate all external side effects through Policy and result verification.

**Tech Stack:** Confirmed baseline: Electron Desktop, Python Runtime, local SQLite, Cloud PostgreSQL. Exact language and runtime versions, frameworks, transport, identity, packaging, and provider choices remain subject to the ADR gate below.

**Spec:** `docs/superpowers/specs/2026-10-17-shuliu-agent-foundation-v0.1-design.md`

## Global Constraints

- Foundation provides shared runtime capabilities; role-specific business agents, business rules, and business data models are out of scope.
- Initial protocol compatibility baseline is `protocol_version: 1`; Foundation, Runtime, Worker, Skill, Event, and database Schema versions remain independent.
- Desktop local persistence uses SQLite; Cloud shared persistence uses PostgreSQL. The local/cloud authority and synchronization rules require an ADR.
- An AgentRun pins the Agent, Skill, and Workflow versions it starts with; updates affect new runs.
- Every external side effect passes Policy; actions requiring a human decision wait for Approval before execution.
- Browser and Computer actions follow `OBSERVE → ACT → VERIFY`; an unverified result cannot silently advance a high-risk Workflow.
- Retryable side effects use idempotency keys and verify external state before recovery; do not replay a pending ToolCall blindly.
- Worker result commits validate the current Lease and fencing token.
- Skill installation verifies publisher, signature, checksum, compatibility, dependencies, permissions, and contract tests; V0.1 trusts only ShuLiu official and enterprise-internal publishers.
- Secrets, credentials, and unnecessary sensitive content must not appear in Event, Log, Trace, or Evidence payloads.
- Releases use SemVer, a compatibility manifest, signed packages, migration checks, health gates, and compatibility-checked rollback.

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

| ADR | Decision to close |
|---|---|
| ADR-017 | Repository layout, language/runtime version floors, package managers, test tooling, CI, and branch/release conventions |
| ADR-018 | User, administrator, identity, tenant, role, and secret-storage model |
| ADR-019 | Local/cloud data authority, offline writes, sync queue, conflict handling, and deletion behavior |
| ADR-020 | Electron↔Python IPC transport and Cloud↔Worker transport, authentication, credentials, and rotation |
| ADR-021 | Scheduler semantics, timezone, missed triggers, deduplication, Worker Lease duration, heartbeat, and grace period |
| ADR-022 | Tool risk levels, Approval matrix, permission granularity, timeouts, retry limits, and uncertain side-effect handling |
| ADR-023 | Knowledge local index, Cloud vector storage, parsers, embedding route, ACL invalidation, and source sync |
| ADR-024 | Model Provider set, capability mapping, budgets, local-model scope, and `LOCAL_ONLY` enforcement |
| ADR-025 | Skill sandbox, scanning, signature trust roots, publisher workflow, and deprecation window |
| ADR-026 | Event, Audit, and Evidence access, encryption, retention, capacity, export, and deletion |
| ADR-027 | Compatibility window, release rollout batches, health observation window, rollback constraints, and performance gates |

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
| 7 | Cloud Control Plane and Worker | 1–3, ADR-018–021, ADR-026 | Authenticated task dispatch, Lease renewal, fencing, result reconciliation, and dead-letter handling |
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

- [ ] **Step 1: Draft ADRs 017–027 from the decision table above**
- [ ] **Step 2: Review each option for security, data privacy, compatibility, and V0.1 delivery impact**
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
