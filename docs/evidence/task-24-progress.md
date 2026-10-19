# Task 24 Browser Runtime execution record

Plan: `docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-implementation.md`, Task 24 only.
Spec: `docs/superpowers/specs/2026-10-17-shuliu-agent-foundation-v0.1-design.md`.
Base: `85a740d9b83776062905d5d6f9d133cc04a62a5e`.

## Takeover, 2026-10-19 (Asia/Shanghai)

- Clean checkout at `C:\Dev\shuliu-agent`, branch `feat/foundation-v0.1`; live remote branch matches base.
- User explicitly chose to retain this checkout and branch rather than create a linked worktree.
- Read project context, Architecture Spec, Accepted ADRs, security/risk register, approved plan, tool/approval/policy/agent/IPC implementations and tests.
- Installed task-local Node 22.23.3 (official SHA-256 checked), pnpm 11.28.6 and Corepack 0.36.0; existing uv 0.12.20 and Python 3.11.9. System defaults unchanged.
- Frozen pnpm and uv installs pass. No repository version declarations changed.
- Python baseline: 256 passed, 7 skipped (2 other-OS checks and 5 PostgreSQL checks without database configuration).
- TypeScript unit: 22 passed; security: 8 passed; contract: 68 passed; Desktop: 23 passed.
- Root/Desktop typecheck, Desktop build and real Electron/Python IPC pass (8 desktop IPC + 2 roundtrip + 2 Python IPC tests).
- Migration: 15 passed, 1 PostgreSQL skip. Integration: 29 passed, 4 PostgreSQL skips.
- Initial direct-node test attempt: 96 passed, 2 failures (uvx PATH and Electron resolution). Both pass after using the pinned toolchain and repository commands, without source changes.
- No lint script or Python lint configuration is declared by the repository.
- GitHub connector returns 404 for this repository's workflow queries; historical CI acceptance is recorded in the plan, not independently verified yet.
- Raw baseline logs: `D:\Documents\ChatGPT\数流\task24-evidence\baseline`.

## Scope rulings

- Follow the existing approved design/plan; no replacement ADR or new architecture approval cycle.
- Task 10 Broker and Task 18 capability proxy are the integration boundary. Task 24 must not enable executable Skills without a verified OS sandbox provider (Task 36).
- Task 33 is not implemented. Production side-effecting browser execution stays disabled; real-browser integration uses a controlled loopback fixture, never real accounts or external submissions. No in-memory store is described as durable.
- Task 25 and subsequent tasks are out of scope.

## TDD

RED 1: 21 Browser contract/security/session tests failed because BrowserRuntime is absent (2026-10-19). Broker identity-binding regression also failed: a caller-supplied `_principal` reached the Executor unchanged. No implementation had been written before these runs.

Dependency finding: `browser-use==0.13.11` cannot resolve in the existing Worker environment: browser-harness 0.1.13 pins websockets 15.0.1, while Worker requires >=16,<18. The failed `uv add` left both manifests and the existing lockfile unchanged. Versions 0.13.4–0.13.10 also depend on browser-harness and pin older shared libraries.

Ruling: keep Browser Use 0.13.11 in a separate, locked driver environment under `runtimes/browser-driver`, supervised by the trusted Browser Tool adapter. This preserves the established Worker dependency versions and browser-use architecture. The driver has no model invocation, cloud browser, Secret resolver, or user profile access; its process environment is minimized. This is dependency/process separation, not an OS sandbox. Task 26 will have to package this locked environment as part of the existing Release Unit; no installer work is included here.

Historical CI independently verified with local Git credential authentication: current base Generic CI run 39197264168 and Windows Validation run 39197264166 are completed/success. The GitHub connector's 404 is an access discrepancy, not a CI failure.

GREEN 1: 44 tests passed (Browser unit/policy integration and existing Broker tests). Shared result contract RED: 2 TypeScript tests and 1 Python test failed before implementation; GREEN: TypeScript 2 passed, combined Python 48 passed including 3 real Chrome tests.

Real-browser RED: 3 explicit failures for the missing real backend. GREEN: 3 real Chrome tests passed on Windows in 7.74s (navigate/read/click/type/scroll/close, click navigation, cancellation after a local fixture submission without retry).

Real-browser implementation findings: Windows requires SystemRoot for asyncio/Winsock and a temporary USERPROFILE/AppData/Local + Roaming layout for Chrome's non-default-profile CDP check. cdp-use awaits event callbacks on its receive loop, so interception schedules work rather than awaiting a send in that callback. No Chromium sandbox disabling was introduced.

Recovery expansion: dynamic-element and actual Chrome-crash tests initially failed (missing bounded DOM settle and owned browser PID). Added both; subsequent runs also exposed Windows long-path cleanup and disappearing temporary files. A dedicated Windows long-path test failed before the extended-path cleanup fix. Recovery test stabilization is still in progress; the earlier 3-test GREEN is not a full acceptance claim.

Origin security RED: real redirect, subresource and iframe tests passed; a real popup reached the forbidden loopback target (1 failed / 3 passed). Added paused CDP target handling; verification pending. Browser request interception is not an OS network sandbox.

## Checkpoint at 2026-10-19 15:29 Asia/Shanghai — not accepted

- Popup fix GREEN: `pytest runtimes/python-worker/tests/browser/test_real_browser.py -k unapproved -q --tb=short` → **4 passed, 7 deselected in 16.95s**. New CDP targets are paused and closed; no page JavaScript override is used as the security boundary.
- Most recent combined Browser run before the last cleanup correction: **33 passed, 2 failed in 25.90s**. Both failures were disappearing Windows temporary files during cleanup after Chrome crash/cancellation. The correction ignores only FileNotFoundError during recursive cleanup; it is **not yet reverified**.
- The dedicated Windows long-path cleanup test was RED (1 failed, 3 passed, 5.31s) before extended-path cleanup was implemented. Target paths are resolved and verified to remain below the store root before deletion.
- A full Python regression launch was rejected **before execution** by automatic approval review: account usage limit prevented the review from completing. The service explicitly said this was a review failure, not a finding that the action was unsafe. No full-regression result exists for the latest tree. Do not bypass the approval failure or start duplicate processes.
- User requests focused security/cleanup tests first, then full regression, independent review, commit/push and CI. No Task 25 work is authorized.
- Next authorized test batch: Windows long profile paths, cancellation, timeout, driver disconnect, Chrome crash and all four unapproved-origin cases. Every real browser uses its own generated profile. After this batch passes, run remaining Browser/Approval/Skill proxy tests; then full Python and TypeScript/contract/security/IPC/build checks in independent batches.
- Additional source-review finding pending a focused regression: browser-use `Element.fill` delegates to a TypeText watchdog that can automatically retry on a concatenation mismatch. This must be constrained or replaced with a single-dispatch input path before acceptance, to honor the no-unknown-side-effect-retry rule. No production browser execution gate is enabled.
- Not yet completed: latest cleanup verification, full regression, independent review, Task 24 commit/push and new CI. HEAD remains `85a740d9b83776062905d5d6f9d133cc04a62a5e`; Task 24 changes are uncommitted on `feat/foundation-v0.1`.

## Resumed, 2026-10-19 — focused fixes verified

- User explicitly resumed Task 24. Quota reported ordinary usage allowed; automatic command approvals succeeded. No approval bypass was used.
- Before resume, 27 modified/new files were copied and SHA-256 verified in independent backup `D:\Documents\ChatGPT\数流\task24-backups\20261019-154717-aaf6f714`; original Git state and checkpoint were preserved.
- Exact prior failures `test_real_browser_crash_is_not_success` and `test_real_cancel_after_external_fixture_effect_does_not_retry`: **2 passed in 5.98s**. Related session/Windows long-path cleanup, timeout and driver disconnect: **6 passed in 6.29s**. Logs: `resume-cleanup-two-20261019.log`, `resume-related-cleanup-20261019.log` in the external evidence directory.
- Input source-review correction: browser-use's concatenation retry is guarded by `not is_sensitive`, while its actor sets that flag true, so that particular retry branch is inactive. However the active clear/type/framework-event behavior still duplicates input-triggered effects.
- Actual real Chrome input RED: `test_real_type_dispatches_one_input_effect_without_fallback` (ASCII and Chinese) **2 failed in 13.76s**, producing 3 and 4 fixture save requests respectively. Replaced watchdog fill with field selection and a single CDP `Input.insertText`, without fallback or action retries. Unsupported input fields fail closed.
- Input GREEN including the existing all-actions test: **3 passed in 11.55s**. Logs: `input-single-effect-red-20261019.log`, `input-single-effect-green-20261019.log`. These are actual local HTTP effects, not FakeBrowser assertions.
- Full regression follows these focused passes; final evidence uses a separate `final-20261019` directory, preserving baseline and failed-run logs.

## Full regression before independent review

- Windows NT 10.0.22631.0; installed real Chrome 155.0.8059.85, headless. Required-real-browser full Python command: `uv run --locked --all-packages pytest -q -ra --junitxml=.../python-full.xml` → **298 passed, 7 skipped in 58.69s**, exit 0. All 13 real Chrome cases passed; no browser skip. Skips: Linux/macOS sandbox checks (2), PostgreSQL configuration absent locally (5).
- Ten repository checks all exit 0: TS unit **24**, security **8**, contract **68**, Desktop **23**; root/Desktop typecheck; IPC (8 Desktop + 2 real Electron/Python roundtrip + 2 Python); migration **15 passed / 1 PostgreSQL skip**; integration **49 passed / 4 PostgreSQL skips**; Desktop build.
- Command wall times: unit 1.08s, security 0.98s, root typecheck 0.59s, Desktop typecheck 0.73s, Desktop tests 4.09s, IPC 11.79s, contract 1.58s, migration 1.80s, integration 51.25s, build 1.17s. Logs and `results.json` are retained under `D:\Documents\ChatGPT\数流\task24-evidence\final-20261019`.
- Refactor evidence: original and new Browser tests pass together with the existing suite after replacing implicit input fallback with single dispatch. Repository has no lint command/config; no invented lint pass is claimed.
- Independent fresh-context review requested after these passes. Commit/push and new Linux/macOS/Windows/PostgreSQL CI remain pending at this checkpoint.

## Independent review fix pass — 2026-10-19

- Fresh reviewer found five P2 issues: unbound live anchor/form destination and overlay click; redaction output length growth; first cleanup failure prevented remaining sessions from closing; normalized driver disconnect deferred profile deletion; TypeScript FakeBrowser tests were not selected by Generic CI.
- Wrote regression tests first. Actual RED: **8 failed in 28.86s**. Secret redaction expanded 8,000 chars to 17,000 and 256 to 544; one close error left later sessions open; normalized driver loss retained its profile; real Chrome tests dispatched clicks to changed links, form destinations and overlay controls. Log: `D:\Documents\ChatGPT\数流\task24-evidence\review-fixes-red-20261019.log`.
- Fixes: final truncation after redaction; attempt every session close and aggregate failures while retaining failed entries; immediately close fatal normalized protocol errors; include action target/form attributes in observation and revalidate page/account/element/hit-test immediately before one CDP click; run `pnpm test:unit` in Generic CI.
- Focused GREEN after these fixes: contract + session cleanup + four live Chrome mutation cases + original click/type paths → **16 passed in 25.57s**. Log: `review-fixes-green-20261019.log`.
- Added immediate profile removal assertions to actual Chrome process-disconnect and Chrome-crash tests: **2 passed in 5.60s**, `review-crash-cleanup-green-20261019.log`.
- Fresh review's explicitly deferred areas remain outside Task24: durable idempotency/restart recovery (Task33), OS sandbox (Task36), packaging (Task26), and authoritative business receipts. It was a single read-only pass; no findings were silently dropped.
- Final one-pass regression after the review fix pass is running before commit. Linux/macOS/PostgreSQL CI still awaits push.

## Review fixes verified — final pre-commit validation

- Full Windows Python regression after all review fixes: `uv run --locked --all-packages pytest -q -ra --junitxml=.../python-full.xml` → **306 passed, 7 skipped in 73.20s**, exit 0. All **17 real Chrome cases** ran (including link/form target changes, an overlay, post-evidence mutation, actual single-input effects, navigation, timeout, cancellation, Chrome crash and driver disconnect).
- Skips remain limited to Linux/macOS sandbox-only tests (2) and PostgreSQL-backed tests without local database configuration (5).
- Ten local pnpm checks after the fix pass all exit 0: unit **24**, security **8**, root/Desktop typecheck, Desktop tests **23**, IPC **8 Electron + 2 real Electron/Python + 2 Python**, contract **68**, migration **15 / 1 PostgreSQL skip**, integration **53 / 4 PostgreSQL skips**, Desktop build.
- Added `pnpm test:unit` to Generic CI so the TypeScript FakeBrowser contract tests run on Linux and macOS. Both browser workflows require real Chrome and preserve a JUnit artifact at repository root.
- Final configuration review removed the nonexistent `artifacts/` parent directory from JUnit paths; the artifact uploader now points at the file pytest creates directly. Workflow syntax is covered by `git diff --check`; hosted workflow execution awaits push.
- Reviewer findings 1–5 have a RED reproducer each and focused GREEN evidence. Review reported no Critical finding. Deferred scope is Task33 durable idempotency, Task36 OS sandbox, Task26 installer packaging, authoritative business receipts and exhaustive network containment; production action gate remains closed.
- Pre-commit state: feature branch only, original base commit unchanged, no tests active. Full evidence is under `D:\Documents\ChatGPT\数流\task24-evidence\final-20261019-reviewed` plus focused RED/GREEN logs beside it.
