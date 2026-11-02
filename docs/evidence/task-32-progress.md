# Task 32 progress — Trusted Knowledge RBAC / Trace / Audit

**Status: IN_PROGRESS; acceptance BLOCKED (CI / real PostgreSQL unavailable — GitHub Actions billing).**

## Scope

Connect the authenticated principal, current organization Membership, and Role / Permission checks to the Task 16 Knowledge ACL; the authorization context may only be constructed server-side; access and provenance enter Trace / Audit without query text, chunk content, or secrets. Task 31 remains recorded as IN_PROGRESS / acceptance BLOCKED in `docs/evidence/task-31-progress.md`; this Task 32 work does not change that status.

## Worktree checkpoint

- Branch: `feat/foundation-v0.1`
- HEAD at this checkpoint: `916d25515ee5bd203d951393482e227356297978`.
- Task 32 work is committed separately from the unrelated uncommitted Desktop UI / Task 30 changes present in the shared worktree.
- No main-branch operation, real Provider request, or customer-document processing was performed.

## Implementation

- `app/knowledge/dependencies.py` — `MembershipSnapshot` (server-side facts only), `KnowledgeMembershipPort` / `KnowledgeRBACPort` protocols, `KnowledgeAuthorizationDenied` (stable machine-readable reasons, no exception-text leakage), and `build_knowledge_context(user_id, snapshot, rbac, trace_id)` which fails closed on: missing/inactive membership, revoked permission (live `RBACRepository.authorize` call per `knowledge.*` permission), RBAC service exception, and incomplete ACL context. `PostgresMembershipPort` reads the ACTIVE membership plus role-derived permission codes from Cloud PostgreSQL; scopes / classifications / privacy / budget policy are injected server-side configuration, never request data.
- `app/knowledge/routes.py` — narrow `POST /v1/knowledge/search` router. `KnowledgeSearchRequest` uses `extra="forbid"`, so forged `organization_id` / `subject_id` / `scopes` / `classifications` / `permissions` / `knowledge_permissions` / `budget_ref` / `privacy` fields are rejected with 422 before any code runs. Authz failures return 403 `KNOWLEDGE_AUTHORIZATION_DENIED`; retrieval failures return 503 `KNOWLEDGE_RETRIEVAL_UNAVAILABLE` with no exception text; the response carries only authorized `KnowledgeHit` fields plus `trace_id` / `policy_version` and no authorization fields.
- `app/knowledge/audit.py` — append-only `KnowledgeAuditRecord` (subject / org / trace / outcome / policy_version / document_refs / reason / occurred_at; never query text, content, or secrets), `InMemoryKnowledgeAuditSink` for tests, `PostgresKnowledgeAuditSink` persisting into the Cloud `events` table with idempotency key `knowledge-audit:{trace_id}:{outcome}`. Audit append failure fails closed with 503 `KNOWLEDGE_AUDIT_UNAVAILABLE` and no hits in the response.

## TDD and local verification evidence

- RED: all three planned test modules failed collection with `ModuleNotFoundError: No module named 'app.knowledge.audit'` before implementation existed.
- GREEN: `uv run --locked --package shuliu-cloud-api pytest services/cloud-api/tests/knowledge -q` → **45 passed, 3 skipped** (skips are pre-existing PostgreSQL / real-embedding gates).
- New unit suites: `test_routes_trusted_authorization.py` (8 tests: forged-field rejection, server-computed context, missing membership 403 + audit, revoked permission 403 + audit, RBAC failure fail-closed without error-text leak, unauthenticated 401 with no audit, happy path 200 with VERIFIED audit containing document refs and no auth fields in the response, direct context-builder denial for inactive/revoked membership) and `test_retrieval_trace_audit.py` (4 tests: payload holds metadata without sensitive text, Postgres sink appends into `events`, audit append failure → 503 with no hits, retrieval failure → 503 without content or VERIFIED audit).
- Regression: full `services/cloud-api/tests` → **94 passed, 6 skipped** (all skips pre-existing gates).
- Integration: `tests/integration/test_knowledge_server_rbac.py` (2 tests) is gated on `SHULIU_DATABASE_URL` per the existing integration convention and currently skips locally; it exercises real `MembershipRepository` + `RBACRepository` + `PostgresMembershipPort`, role-revocation denial, membership-revocation denial, and `PostgresKnowledgeAuditSink` insertion into `events`.

## Acceptance status (BLOCKED)

The plan's acceptance commands (`pnpm test:integration`, `pnpm test:security`, PG17/PG18 Knowledge integration) require CI, which is currently blocked by the GitHub Actions billing / spending-limit issue (see `docs/evidence/task-29-progress.md`). The following remain outstanding:

- [ ] Real PostgreSQL 17/18 run of `tests/integration/test_knowledge_server_rbac.py` in CI.
- [ ] `pnpm test:integration` and `pnpm test:security` green runs.
- [ ] Cross-organization forgery / revocation / Trace-Audit evidence review by the Cloud / Security owner.

## GitHub CI 验证结果（2026-11-01，真实运行）

- 推送 HEAD：`30ad778`（`feat/foundation-v0.1`）。
- Generic CI run `41310851081`：**success**（PG17 22/22、PG18 22/22、macOS 36/36、Ubuntu 36/36）。其中 `Run Cloud API Knowledge unit tests`（含本任务的 12 个新测试）与 `pnpm test:security` 均为 success。
- Windows Validation run `41310851060`：**success**，35/35 步。
- 本任务的 PostgreSQL 集成模块 `tests/integration/test_knowledge_server_rbac.py` 之前只在无数据库的通用 job 中 skip。已将它加入 `PostgreSQL migration` job 的 Knowledge ACL 步骤并标记 `pytest.mark.postgres`，使真实 RBAC / 撤权 / 审计写入在 PG17 与 PG18 上执行。
- 仍待完成：Cloud / Security owner 对跨组织伪造、撤权与 Trace / Audit 证据的独立审查。

## Owner 批准（2026-11-02）

- 所有者已审查本任务的跨组织伪造、撤权与 Trace / Audit 证据（含 CI run `41312173780` 真实 PG17/18 执行与 Windows 实机功能验收），批准通过。
- 本任务验收状态由 BLOCKED 变更为 **PASSED**。
