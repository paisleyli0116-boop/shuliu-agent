# Task 30 progress — Real Embedding / Knowledge Retrieval

**Status: IN_PROGRESS / BLOCKED — local implementation and non-billable checks recorded; release gate not passed.**
**Task 31 has not started.**

## Scope

Implement the approved Task 30 path: configured Embedding Profile → Model Gateway with explicit provider/model pinning → pgvector ingestion and query → ACL-first retrieval. The index now records provider, model, dimensions and index version. Profile changes fail closed while an organization is partially re-embedded. Fake embeddings remain confined to deterministic tests; real-provider coverage is a separately gated integration test.

No Task 31 parser work was started. No DeepSeek or other real Provider request was made. The initial checkpoint had no Task 30 commit or push; the later instructions in this document supersede the earlier date deferral and authorize a scoped Task 30 push after review and file-scope verification.

## Local checkpoint

- Branch: `feat/foundation-v0.1`
- HEAD: `458eb9cec0008af75ed64da7cb5e9cb3ef5608b1`
- Local remote-tracking ref: `origin/feat/foundation-v0.1` at the same SHA (0 ahead / 0 behind at last local check; no fresh GitHub write/read performed).
- The worktree also contains independent Desktop UI changes and untracked `scripts/`; both are preserved and excluded from Task 30.
- Task 29 is treated as complete per its later exact-SHA evidence record; this change does not edit Task 29 evidence.

## RED evidence

- The new Embedding Gateway integration tests initially failed because `app.knowledge.embedding` did not exist (4 failing cases).
- The model-pinning regression initially failed because `ModelGateway.complete_for_provider` did not accept an explicit model.
- Initial SQLite migration verification exposed unsupported direct constraint alteration; migration now uses SQLite batch alteration for the test dialect while PostgreSQL keeps additive constraint creation.

## Initial checkpoint verification (historical; superseded by the 2026-10-25 update below)

- Knowledge, migration and permission-related Python subset: **19 passed, 4 skipped**.
- Model Gateway capability routing and LOCAL_ONLY fallback subset: **16 passed**.
- Repository migration suite via `pnpm test:migration`: **18 passed, 1 skipped, 596 deselected**. Pytest emitted a cache-write warning because this restricted session cannot write the repository cache; command exit code was 0.
- Security suite via `pnpm test:security`: **4 test files passed, 10 tests passed**.
- Task 30 PostgreSQL profile-reindex integration: **skipped** because no database URL was configured for this safe local run.
- Real Provider → ModelCall/budget → pgvector end-to-end test: **skipped by explicit gate** (`SHULIU_REAL_EMBEDDING_TEST=0\)); no external request was issued.
- No remote CI result exists for these local modifications because they have not been pushed.

## Remaining acceptance blockers

1. Run the profile reindex and pgvector retrieval integration against PostgreSQL 17 and 18; local PostgreSQL was not configured.
2. With a separately approved Provider profile, OS SecretRef and budget, run the explicitly gated real embedding integration. It performs an ingestion embedding and a query embedding; this is a billable external operation and was not authorized or executed in this turn.
3. Verify the resulting durable ModelCall and settled-budget records with actual provider responses and database evidence.
4. Review the exact Task 30 file set and remote history, then push only after confirming a safe fast-forward; obtain exact-SHA CI evidence.
5. Complete the original Task 30 release gate. Until the real Provider and pgvector evidence is present, Task 30 remains **BLOCKED** and Task 31 must not start.

## Task 30 files currently changed

- `pyproject.toml`
- `runtimes/python-worker/shuliu_runtime/models/gateway.py`
- `runtimes/python-worker/tests/models/test_capability_routing.py`
- `services/cloud-api/app/knowledge/__init__.py`
- `services/cloud-api/app/knowledge/embedding.py` (new)
- `services/cloud-api/app/knowledge/ingest.py`
- `services/cloud-api/app/knowledge/retrieval.py`
- `services/cloud-api/app/persistence/postgres.py`
- `services/cloud-api/migrations/versions/0009_knowledge_embedding_profile.py` (new; follows the current 0008 head)
- `services/cloud-api/tests/knowledge/test_acl_before_vector_search.py`
- `services/cloud-api/tests/knowledge/test_embedding_gateway_integration.py` (new)
- `services/cloud-api/tests/knowledge/test_embedding_profile_delete_recovery.py` (new)
- `services/cloud-api/tests/knowledge/test_real_embedding_retrieval.py` (new)
- `services/cloud-api/tests/knowledge/test_revocation_invalidation.py`
- `services/cloud-api/tests/persistence/test_postgres_migrations.py`
- `tests/integration/test_knowledge_permissions.py`
- `tests/integration/test_real_knowledge_retrieval.py` (new)

## 2026-10-25 continuation and current evidence

This update records fresh checks from the Windows working tree. The earlier note deferring GitHub writes until 2026-10-30 is superseded by the current user instruction to push the Task 30 change for PG17/18 validation. Only the Task 30 file set and this evidence file are intended for that commit; concurrent UI redesign files and untracked `scripts/` remain excluded.

### Current repository checkpoint

- Branch: `feat/foundation-v0.1`.
- Base HEAD and `origin/feat/foundation-v0.1` before this Task 30 commit: `458eb9cec0008af75ed64da7cb5e9cb3ef5608b1` (no committed divergence).
- The worktree also contains independent UI redesign modifications and untracked `scripts/`; these are protected and excluded from the Task 30 commit.
- The current GitHub credential check did not authenticate: the Windows Credential Manager helper could not persist/read a credential and noninteractive `ls-remote` ended before HTTP authorization; an unauthenticated GitHub API branch lookup returned 404. The local remote-tracking ref remains at the recorded base but is not proof of the live remote SHA. No push or Task 30 remote CI result is claimed.
- Task 31 remains not started.

### Review and local tests

- A second read-only review inspected the embedding adapter, profile-scoped ingestion/retrieval, ACL-first SQL, migration downgrade/reapply, and Task 29 durable ModelCall/budget settlement. The Gateway finalizer updates reservation and ModelCall in one database transaction; repeated settlement is idempotent only when amount and usage match; unknown provider request status remains reserved for reconciliation and is not retried. PostgreSQL row locking and concurrent reservation behavior still require the PG17/18 runs below.
- **RED:** a new migration regression attempted to insert a profile row with a NULL dimension or index version. Both cases were accepted by the old CHECK constraint (`2 failed`, as expected). SQL CHECK treats NULL comparisons as UNKNOWN, which may pass; this was the concrete schema defect.
- **GREEN:** after adding explicit `IS NOT NULL` checks for both values in migration and SQLAlchemy metadata, the same regression cases passed (`2 passed`).
- Cloud API Knowledge tests: **10 passed, 2 skipped**; skipped tests require `SHULIU_DATABASE_URL`.
- Model Gateway capability routing: **14 passed**.
- Embedding Gateway adapter tests: **5 passed**.
- Migration suite: **20 passed, 1 skipped, 596 deselected**.
- Repository integration command: **54 passed, 7 skipped, 556 deselected**. PostgreSQL-backed tests were skipped because the local database URL/service is absent. The test command also exercised the existing Windows browser integration tests; they passed in this run.
- Persistent ModelCall and budget unit tests: **9 passed**.
- Task 30 profile-reindex PostgreSQL test: **skipped**, reason `SHULIU_DATABASE_URL is not configured`.
- Real Provider embedding integration: **skipped**, reason the explicit `SHULIU_REAL_EMBEDDING_TEST=1` gate was left off. No Provider HTTP request was made and no API Key was read.
- Local Python package lint/type-check commands are not defined in the repository manifests; syntax/import checks were exercised by the pytest runs. A read-only independent review was performed; detailed findings are recorded below.

### Provider, data, cost and privacy readiness

- The current process exposes one configured profile: provider `deepseek`, model `deepseek/deepseek-flash`, capability `fast_chat`, enabled; a SecretRef is configured for that chat profile. The embedding profile variable `SHULIU_KNOWLEDGE_EMBEDDING_PROFILE_JSON` is absent. The existing profile is therefore not authorized/configured for embeddings. SecretRef resolution was not attempted.
- The gated acceptance fixture sends the same fixed public text, `ShuLiu Task 30 public embedding retrieval verification.`, once for ingestion and once for query. It creates an isolated generated organization/document, asserts a persisted vector and hit, then removes only those generated rows. The test config sets a USD account limit of `$0.25` and reserves `$0.05` per invocation (at most `$0.10` reserved across the two calls). These are test budget controls, not the Provider's quoted price. Actual price cannot be assessed until an embedding-capable Provider/model is selected; the chosen Provider's usage/cost evidence must be returned and durably settled by the Gateway.
- DeepSeek's official [model list](https://api-docs.deepseek.com/api/list-models/) and [pricing page](https://api-docs.deepseek.com/quick_start/pricing/) document DeepSeek chat models and chat-token pricing. The current configured DeepSeek profile exposes only `fast_chat`, and these sources do not provide an embedding profile/price for this test. DeepSeek chat pricing is not treated as an embedding quote.
- No customer/organization content is needed for the canary; only the fixed public fixture should leave the system. The embedding provider, model, data-processing terms, privacy approval, and separate authorization for two external embedding calls remain human approval items.

### Remaining acceptance status

- PG17 profile reindex, migration and durable usage jobs: **NOT RUN for this worktree's new SHA yet**. Existing `.github/workflows/ci.yml` has a PostgreSQL 17/18 matrix with `pgvector/pgvector:0.8.7`, sets `SHULIU_DATABASE_URL`, and runs Knowledge, migration, ACL and durable usage tests. These must be checked against the exact pushed SHA; no prior SHA counts.
- PG18 equivalents: **NOT RUN for this worktree's new SHA yet**.
- Real embedding provider response, vector persistence from that response, and corresponding durable cost/budget settlement: **BLOCKED / NOT RUN**, pending an explicit embedding-capable Provider/model configuration, provider/privacy approval, SecretRef configuration and one separate user authorization.
- Task 30 release gate: **BLOCKED**. Local Fake/unit tests and any database-only CI cannot satisfy the plan's real-provider release gate. Task 31 remains not started.

### Delete/re-embed recovery follow-up

- **RED:** added `test_deleting_last_unprofiled_document_marks_complete_embedding_profile_ready`. It builds the migrated schema, leaves one fully profiled document and one legacy/unprofiled document while the registered profile is `BUILDING`, then deletes the unprofiled document through the actual `delete_document()` implementation. Before the fix, the assertion failed with `BUILDING != READY`.
- **GREEN:** `delete_document()` now takes the same organization advisory transaction lock as ingestion, and recomputes all registered profile states inside the document/ACL/chunk deletion transaction. Ingestion uses the same helper, so the READY rule remains consistent. The regression now reports **1 passed**.
- **RED:** a second regression left one document with chunks from both the active and an older profile, then deleted the last unprofiled document. The old document-level `count(distinct document_id)` rule incorrectly reported `READY`; the test failed with `READY != BUILDING`.
- **GREEN:** profile readiness now also requires every organization chunk to match the registered profile metadata, in addition to every document having a matching chunk. Both deletion/re-embedding regressions report **2 passed**. This transition test uses SQLite and replaces only the PostgreSQL guard/advisory-lock operation; it does not prove PostgreSQL lock behavior.
- Related Windows local suite after the mixed-profile fix: Knowledge, migration, ACL permission and real-retrieval selection: **23 passed, 5 skipped**. The skips are explicit: four PostgreSQL tests lacked `SHULIU_DATABASE_URL`; the gated real embedding test required `SHULIU_REAL_EMBEDDING_TEST=1`. No external request was made.
- The first RED invocation reported a restricted-session `uv` cache lock warning and pytest cache-write warnings, but executed the assertion and exited 1 for the expected `BUILDING` state. The green and related runs used a temporary `UV_CACHE_DIR` and disabled pytest's cache provider; `uv` still warned it could not acquire the repository `.venv` lock, but pytest executed and returned the results above.
- **Independent review:** the reviewer confirmed the deletion-state fix is transactionally aligned with ingestion and found no new permission bypass. The reviewer identified the mixed-profile chunk readiness gap; a failing test and exact-chunk check now address it. Final read-only review found no SQL/transaction defect in the fix. Both regressions use SQLite with the PostgreSQL guard and lock replaced, so PostgreSQL advisory-lock contention is still unverified.
- **Budget/cost risk:** Task 29's durable budget code reserves the account's configured fixed reservation amount before a call and later settles the returned actual cost. Independent review confirmed settlement does not enforce an upper bound against the account limit after actual cost is known, while the Embedding adapter accepts multiple texts. No approved embedding provider/model or verified price is configured, so reservation adequacy for an embedding batch is unknown. This requires budget-owner review/configuration or a separately scoped budget-limit fix before claiming a hard spend ceiling; it is not represented as tested by the small public fixture.
- Current status remains **BLOCKED**. PostgreSQL 17/18 execution, an approved embedding-capable profile and price/data-processing review, separate authorization for the two real provider calls, actual provider response, and post-call durable ModelCall/reservation/account evidence are still missing. Task 31 remains not started.

### Local commit checkpoint

- Task 30 implementation and current evidence were committed on `feat/foundation-v0.1` as `de56feb` (`feat: complete real embedding knowledge retrieval`), parent `458eb9cec0008af75ed64da7cb5e9cb3ef5608b1`. The commit contains the 17 Task 30 code/test/config files plus this progress document; concurrent Desktop UI files and `scripts/` were not staged.
- A fresh authenticated remote read could not be completed: Git Credential Manager returned `fatal: Unable to persist credentials with the 'wincredman' credential store` / `fatal: unable to get password from user`. The unauthenticated HTTPS read also failed before HTTP authorization. SSH over port 443 stopped at host-key verification, and the current restricted process cannot read the existing user SSH configuration. Therefore `de56feb` is local-only; no push or exact-SHA GitHub CI run has occurred. Do not infer the live remote head from the local `origin/feat/foundation-v0.1` tracking ref.
- Local classification: relevant Fake/unit and security tests **PASSED**; PostgreSQL tests **SKIPPED** locally for absent database; real Provider integration **SKIPPED** by gate; PG17/18 remote CI and real Provider end-to-end **NOT RUN**; Task 30 **BLOCKED**; Task 31 **NOT STARTED**.
