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
- At the time of this checkpoint, Git Credential Manager could not read/write its credential and the unauthenticated HTTPS read failed before HTTP authorization. A later read-only SSH-over-443 probe succeeded using the existing key and verified host key; no SSH configuration or key file was changed.
- Local classification: relevant Fake/unit and security tests **PASSED**; PostgreSQL tests **SKIPPED** locally for absent database; real Provider integration **SKIPPED** by gate; PG17/18 workflow result **NOT VERIFIED** and real Provider end-to-end **NOT RUN**; Task 30 **BLOCKED**; Task 31 **NOT STARTED**.

### 2026-10-25 remote push checkpoint

- Live remote comparison before push: `origin/feat/foundation-v0.1` was `458eb9cec0008af75ed64da7cb5e9cb3ef5608b1`; local was its descendant by exactly two Task 30 commits (`de56feb7aa0414a9d69246a10ea5d7cbb333e870` and `ee64bdd99a6419b00176fc9c4ce5ee9890d67d9f`). The ancestry check passed. Remote `main` was read as `db46ba1d7c6cff7657fba63652156cd138d2b05a` and was not changed.
- Normal SSH-over-443 push to `refs/heads/feat/foundation-v0.1` succeeded. A fresh `ls-remote` returned `ee64bdd99a6419b00176fc9c4ce5ee9890d67d9f`, exactly matching local HEAD. No force push was used.
- GitHub Actions for the pushed SHA remain **NOT VERIFIED**. The GitHub API connector returned 404 for the private repository's Actions endpoint; the open GitHub page showed Sign in / 404, so it did not expose run details. Do not count this as a passing CI result. The repository CI workflow is configured to run on feature-branch pushes; the corresponding Windows/PG17/PG18 job results and run ID are still required.
- The progress update recording the push is local and not yet committed; the pushed `ee64bdd` evidence version accurately said remote verification was pending at that point. Task 30 remains **BLOCKED**, real Provider calls remain unperformed, and Task 31 remains **NOT STARTED**.

### 2026-10-25 offline Task 30 follow-up

- Added `docs/evidence/task-30-embedding-provider-approval.md` as a decision packet, not an approval. It compares Alibaba Cloud Model Studio `text-embedding-v3`, SiliconFlow `BAAI/bge-m3`, and OpenAI `text-embedding-3-small`, links official API, model/pricing, and privacy sources, and records the unresolved account/region/contract checks. No provider was selected, no account or credential was configured, and no external request was sent.
- The code inspection in that packet records a concrete integration limitation: the current Gateway provider configuration and LiteLLM embedding call do not carry an approved custom `api_base` or requested dimensions. Therefore an OpenAI-compatible profile alone cannot safely establish the DashScope or SiliconFlow route; a chosen route needs a narrowly scoped Gateway change and tests.
- Added two durable-budget regression cases in `runtimes/python-worker/tests/models/test_persistent_budget.py`: a charge above the fixed reservation but below the account cap must be settled truthfully and block the next reservation; a charge above the account cap must still be recorded truthfully and block future reservations. Added a Gateway-level case in `runtimes/python-worker/tests/models/test_persistent_model_calls.py`: it verifies an over-limit actual charge is persisted with the ModelCall, while the next call is rejected at budget preflight with `request_status=not_sent` and does not invoke the adapter again. These tests characterize current behavior; they found no implementation defect, so no RED failure or production-code fix is claimed.
- Targeted Windows run: `uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/models/test_persistent_budget.py runtimes/python-worker/tests/models/test_persistent_model_calls.py -q -p no:cacheprovider` → **12 passed**. `UV_CACHE_DIR` was redirected to a task-specific temporary directory; `uv` printed a non-fatal access warning for the repository `.venv` lock file, but pytest ran and exited 0.
- Budget boundary remains a risk, not a claimed hard cap: the fixed reservation controls admission of a call, but an already issued provider call can have an actual charge greater than its reservation or configured account limit. Settlement must preserve the actual charge; strict per-call spend protection requires trusted price/region data and a conservative preflight bound on input size and batch count.
- No database integration, provider, real Embedding, remote CI, commit, or push was run in this follow-up. Current branch/HEAD at review: `feat/foundation-v0.1` / `ee64bdd99a6419b00176fc9c4ce5ee9890d67d9f`. The pre-existing UI redesign and untracked `scripts/` remain untouched and excluded. Task 30 remains **BLOCKED**; Task 31 remains **NOT STARTED**.

### 2026-10-25 DashScope `text-embedding-v3` offline integration follow-up

- User selected Alibaba Cloud Model Studio `text-embedding-v3` for technical preparation. This supersedes earlier “no Provider selected” wording above, but is not legal/privacy/security/budget approval and does not authorize external requests.
- RED evidence: the new constrained-profile and LiteLLM route tests initially failed because strict `SHULIU_MODEL_PROFILES_JSON` parsing rejected endpoint/dimensions and `ModelProviderConfig` could not carry them (**3 failed, 66 passed**). A preservation test then caught the first validator draft rejecting an existing-shape DashScope chat profile (**1 failed, 43 passed**); the validator was narrowed to DashScope embedding profiles and that configuration remains accepted.
- A further RED test found that omitting dimensions from a configured embedding request continued into credential resolution and returned `MODEL_CREDENTIAL_UNAVAILABLE`; it now fails closed as `MODEL_REQUEST_INVALID` / `not_sent` / `preflight` before budget reservation, policy work or secret resolution. The paired missing/mismatched dimension tests report **2 passed** after the fix.
- Minimal implementation is limited to `runtimes/python-worker/shuliu_runtime/models/capability.py`, `config.py`, `gateway.py`, `providers/litellm_adapter.py`, and `services/cloud-api/app/knowledge/embedding.py`. DashScope `text-embedding-v3` requires an HTTPS workspace endpoint on an explicit Beijing or Singapore host allowlist, exact `/compatible-mode/v1` path, and one of the documented dimensions; all other hosts, schemes, ports, userinfo, paths, query/fragment values, model/provider/capability combinations fail closed. Profile endpoint values are omitted from repr output. The Cloud Knowledge adapter now sends its configured vector dimensions through the Gateway; mismatched dimensions fail before budget reservation/provider invocation. The LiteLLM route uses `openai/text-embedding-v3`, the profile endpoint and dimensions, with `num_retries=0`; non-DashScope routes retain their existing adapter options. Returned vector dimensions are checked against the approved profile.
- GREEN evidence: final Python worker `tests/models` **136 passed**; Cloud API `tests/knowledge` **12 passed, 2 skipped**; the direct embedding Gateway contract test **5 passed** before the final dimension-omission guard (the final full Knowledge suite was rerun afterward and remains green). The 2 skips require PostgreSQL and remain unverified. Commands used temporary `UV_CACHE_DIR` and disabled pytest cache; `uv` emitted a non-fatal access warning for the repository `.venv` lock, while pytest exited 0.
- Offline locked dependency verification: `uv.lock` pins `litellm-core==1.106.0`; LiteLLM resolves `openai/text-embedding-v3` with the configured compatible base to provider `openai` and accepts `api_base` plus `dimensions`. No HTTP request was made.
- Provider price/currency is now a concrete blocker: this LiteLLM version has no model-cost map entry for `text-embedding-v3` (with or without `openai/`). The durable PostgreSQL budget accepts only USD, while Alibaba's current official price pages list CNY for both Beijing and Singapore deployments. A successful call with missing cost is rejected during durable settlement. No guessed price, exchange rate, or ledger currency was introduced. A budget owner must approve a pricing/currency policy and a conservative pre-call reservation bound before actual Provider verification.
- Alibaba's official current model guidance recommends `text-embedding-v4` for new text search/RAG indexes and positions v3 for compatibility with existing v3 indexes. The implementation follows the user's explicit v3 selection and does not silently switch models; the responsible owner must confirm whether v3 compatibility is the intended index requirement before profile/index activation. Official price and privacy pages also state the pricing is region-dependent and that Model Studio stores data from model/application calls while not using it for training; exact retention/region/contract terms still require review.
- Current profile region/workspace endpoint and SecretRef are not configured; no credential was read; no Provider request was made. PostgreSQL 17/18 CI, real provider response, vector persistence from the provider response, real ModelCall/cost/budget settlement, privacy/security approval and legal/license review remain **NOT RUN / BLOCKED**. Task 30 remains **BLOCKED**; Task 31 remains **NOT STARTED**. Current workspace HEAD is still `ee64bdd99a6419b00176fc9c4ce5ee9890d67d9f`; these latest local edits are not committed or pushed.

### 2026-10-25 endpoint allowlist independent-review correction

- Read-only independent review found that the endpoint regex applied case-insensitive matching to its full string, allowing `/COMPATIBLE-MODE/v1` and `/compatible-mode/V1`. This was confirmed in a focused RED run: **2 failed, 6 passed**.
- Restricted case-insensitive matching to the HTTPS scheme and hostname; the API path must now exactly match `/compatible-mode/v1`. Added coverage that uppercase scheme/hostname remain valid while path case variants are rejected.
- Focused GREEN run: endpoint allowlist tests **9 passed**. Final Worker `tests/models`: **139 passed**. Final Cloud API `tests/knowledge`: **12 passed, 2 skipped** (PostgreSQL integration prerequisites unavailable). Both commands exited 0; `uv` printed the non-fatal `.venv/.lock` access warning.
- No network request, secret access, commit, push, or UI/script change was made. Current branch/HEAD remain `feat/foundation-v0.1` / `ee64bdd99a6419b00176fc9c4ce5ee9890d67d9f`. Task 30 remains **BLOCKED** and Task 31 remains **NOT STARTED**.

### 2026-10-25 PostgreSQL verification readiness

- Re-read the Task 30 acceptance section in the Implementation Plan. It requires the real Gateway → Embedding → pgvector ingest/query → ACL-first retrieval path, plus PostgreSQL migration, integration and security checks; FakeEmbedding-only evidence is insufficient.
- Read-only inspection of `.github/workflows/ci.yml` confirms a PostgreSQL 17/18 matrix backed by `pgvector/pgvector:0.8.7-pg<version>-bookworm`, a `pg_isready` service health check, and `SHULIU_DATABASE_URL` in the job environment. The job runs migration tests, durable ModelCall/budget integration, Cloud Knowledge tests, Knowledge ACL integration, and Worker lease concurrency tests. A job failure is not converted to a successful conclusion by the workflow.
- Local readiness check found no `SHULIU_DATABASE_URL`, `DATABASE_URL` or `TEST_DATABASE_URL` in the current process and no `docker`, `podman`, `psql` or `pg_isready` executable. No local PostgreSQL integration was attempted in this check; the two Knowledge tests that require a database remain skipped, not passed.
- No remote workflow was started or queried. Exact-SHA PG17/18 CI evidence is still required; local environment absence does not change Task 30 status (**BLOCKED**) or Task 31 status (**NOT STARTED**).
