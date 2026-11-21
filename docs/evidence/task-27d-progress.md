# Task 27-D Evidence — CI report provenance and fail-closed mapping

- Status: **IN_PROGRESS**. The Foundation release remains **BLOCKED**.
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
- These are local tests only. They are not remote CI evidence for the pending
  changes.

## Delivery status

- Local commit: recorded in the worktree on `codex/task27-d-report-failclosed`,
  parent `ace1b9a28a5ffc4033139e6d09bd995443d4ce59`.
- Remote branch is still at the base SHA. Git push was attempted once with the
  existing GCM configuration and failed because the stored GitHub token is
  invalid and the `wincredman` credential store could not persist credentials.
  A one-command DPAPI-backed GCM attempt also fell through to an HTTP
  username/password prompt and failed authentication; no password or token was
  entered. The connected GitHub write API refused blob creation with HTTP 403
  (`Resource not accessible by integration`). No remote ref was changed and no
  CI run exists for the local candidate.
- Remote delivery and exact-SHA CI remain pending until GitHub write
  authorization is restored.

## Remaining release blockers

The release aggregator must remain BLOCKED until every required gate has
valid, full-SHA-bound evidence. On the base SHA, evidence is still missing for
real model provider connection, provider license/security approval, real
embedding retrieval, full-process crash recovery, per-OS Skill sandbox
provider evidence (Linux/macOS/Windows), and real Skill scanner evidence.
Task 27-D does not waive or implement those capabilities. The new report
changes still require review and Generic CI plus Windows Validation for their
own resulting SHA before this subtask can be considered verified.
