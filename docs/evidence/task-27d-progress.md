# Task 27-D Evidence — CI report provenance and fail-closed mapping

- Status: **PASSED for the Task 27-D implementation SHA**. The Foundation release remains **BLOCKED**.
- Working branch: `codex/task27-d-report-failclosed` (based on the current
  `feat/task27-release-gate` head at the time of implementation).
- Base SHA: `ace1b9a28a5ffc4033139e6d09bd995443d4ce59`.
- Scope: make CI-derived release evidence fail closed when a job/step has no
  successful conclusion, and bind GitHub Actions evidence to the workflow
  run's actual `head_sha`, not merely the requested release SHA.

## Existing remote evidence at the base SHA

The following completed runs target the exact base SHA above:

- [Generic CI run 44892355099](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) — success.
- [Windows Validation run 44892355151](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355151) — success.

The report jobs for both runs produced **BLOCKED**, correctly reflecting that
green workflows do not satisfy capability gates whose required evidence was
not produced. At this SHA the report has PASS evidence for 15 gate IDs: the
core checks, platform secure-storage checks, and PostgreSQL-backed model usage
budget, Knowledge ingestion/revocation, Knowledge RBAC/ACL, and durable
idempotency checks. Six gate IDs remain NOT_RUN; `skill_sandbox_provider`
requires separate Linux, macOS, and Windows rows, so the blocked table has
nine platform rows. See `docs/release/foundation-v0.1-checklist.md` for the
gate-by-gate status and links.

Both workflows also support `pull_request`. For that event, the report command
uses the PR head SHA; for pushes and manual runs it uses `github.sha`. This
keeps the requested evidence SHA aligned with the workflow-run API's `head_sha`
without weakening the exact-SHA check.

## Defects found and changes under validation

1. A job conclusion other than `success` (including absent, `neutral`, or
   `action_required`) could be interpreted as PASS. A step with no conclusion
   could also be interpreted as PASS. The report mapper now treats only an
   explicit `success` as PASS; absent/skipped work is NOT_RUN and hard job
   failures remain FAIL.
2. GitHub API mode fetched a run's jobs but did not read that run's actual
   `head_sha`. The CLI-provided target SHA was stamped onto all derived
   evidence, so stale green jobs could appear to belong to the requested
   commit. The mapper now fetches the run metadata, records its `head_sha` as
   evidence provenance, and lets the aggregator reject a mismatch against the
   requested release SHA. Local `--jobs-json` fixtures remain explicitly bound
   to the supplied SHA.

## TDD and local verification

- RED: the four missing/unknown conclusion cases failed because the mapper
  returned PASS instead of NOT_RUN.
- GREEN: the conclusion handling was made fail-closed; the focused release
  suite passed.
- RED: the CLI provenance regression failed because `_fetch_run_sha` did not
  exist.
- GREEN: GitHub run SHA is now fetched and attached to the evidence; the
  mismatch regression passed and confirms the report returns exit code 1 and
  includes `SHA_MISMATCH` when the run SHA differs from the target SHA. A
  matching-SHA case also confirms it does not create a false mismatch.
- Latest local checks on this worktree:
  - `uv run --locked --all-packages pytest tests/release -q` — **42 passed**.
  - `python -m compileall -q tests/release` — passed.
  - `git diff --check` — passed.
- These local checks were followed by exact-SHA remote validation recorded
  below.

## Delivery status

- Implementation commit: `dde47d62947db81f95cfc8782c403551879bd39b`, parent
  `ace1b9a28a5ffc4033139e6d09bd995443d4ce59`.
- Pushed to `github/feat/task27-release-gate`; the remote branch was read back
  as the same full SHA. GitHub created both push-triggered runs at
  `2026-11-21T11:21:06Z`.
- [Generic CI run 44898688267](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44898688267)
  — **success**, target SHA exactly matches the implementation commit. Ubuntu,
  macOS, PostgreSQL 17, PostgreSQL 18, and the release-gate report job all
  completed successfully. The release-gate report job's successful execution
  does not make the Foundation release PASS; the report remains BLOCKED on
  missing capability evidence listed below.
- [Windows Validation run 44898688243](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44898688243)
  — **success**, target SHA exactly matches the implementation commit. The
  Windows job completed all steps, including real Chrome Browser Runtime,
  Skill process isolation, sandbox/security checks, Electron↔Python IPC,
  packaging, install/Safe Mode/uninstall, upgrade, rollback, and data
  preservation. No step failed or was skipped in the Windows job.
- The implementation SHA is remotely validated. This progress-file
  synchronization is a documentation-only follow-up and will be recorded
  separately; it does not change the implementation or gate results.

### Documentation synchronization and latest-SHA CI

- Documentation-only commit: `cb82b49e37e11e58e29f8ddce8e4e4dab8ba59af`.
  The GitHub branch `feat/task27-release-gate` was read back at this exact SHA.
- [Windows Validation run 44899661651](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44899661651)
  — **success**, exact target SHA `cb82b49e37e11e58e29f8ddce8e4e4dab8ba59af`.
  The Windows validation and release-report jobs completed successfully,
  including packaging, install/health, IPC, security isolation, upgrade,
  rollback, and data-preservation steps.
- [Generic CI run 44899661573](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44899661573)
  — final attempt 2 **success**, exact target SHA unchanged. Ubuntu, macOS,
  PostgreSQL 17, PostgreSQL 18, and the release-gate report job all completed
  successfully. Attempt 1 had a macOS WSS handshake timeout during the test's
  second connection; Ubuntu and both PostgreSQL jobs passed. Only that failed
  macOS job was rerun. It passed on attempt 2 without source changes, so the
  first failure was not reproduced; its underlying cause remains unconfirmed.
- The successful CI evidence above does not clear missing product capability
  gates or owner approvals. Overall Foundation release status remains
  **BLOCKED** as listed below.

## Remaining release blockers

The release aggregator must remain BLOCKED until every required gate has
valid, full-SHA-bound evidence. On the base SHA, evidence is still missing for
real model provider connection, provider license/security approval, real
embedding retrieval, full-process crash recovery, per-OS Skill sandbox
provider evidence (Linux/macOS/Windows), and real Skill scanner evidence.
Task 27-D does not waive or implement those capabilities. The Task 27-D report
changes are verified at the implementation SHA above. The overall Foundation
release must remain BLOCKED until every remaining gate has valid,
full-SHA-bound evidence and required owner approvals.

### Windows process-crash gate wiring (2026-11-21)

- Found that Windows Validation did not execute a dedicated full-process
  recovery step, so `process_crash_recovery` stayed NOT_RUN even though the
  repository contains real Sidecar restart and dispatch/ACK-loss tests.
- Added `Run Windows full-process recovery and ACK-loss tests` to the Windows
  workflow. It runs the real Sidecar process-kill/restart suite, workflow
  dispatch/ACK-loss subprocess suite, and Electron Main recovery coordinator
  subprocess suite. The JUnit outputs are retained in the Windows evidence
  artifact even if a later validation step fails.
- Added a fail-closed mapping from that exact Windows step to
  `process_crash_recovery`; missing/skipped steps remain NOT_RUN and failed
  steps remain FAIL.
- RED: the new release-report test failed with a missing
  `process_crash_recovery` result before the mapping existed.
- GREEN (local): `tests/release/test_ci_gate_report.py` **20 passed**;
  real Sidecar/workflow crash recovery **6 passed**; Electron Main recovery
  tests **9 passed**; Windows workflow YAML parse succeeded.
- Remote exact-SHA Windows Validation is pending for the commit containing
  this wiring. Until it passes, `process_crash_recovery` remains unverified.
  Other release blockers are unchanged; Task 27 remains BLOCKED.
