# Task 36 — Windows Skill Sandbox Attack Validation

- **Status:** VALIDATED-ON-WINDOWS (real-machine attack evidence; provider stays fail-closed)
- **Date:** 2026-11-05
- **Worktree:** `C:\Dev\shuliu-task35-36`, branch `feat/task35-scan-task36-sandbox`, base `817dd2f18514c7d9e94b71c8635f21ddd96969ce`
- **Scope ruling:** per Implementation Plan Task 36 and ADR-025, OS mechanisms not proven by runner attack tests keep `UnavailableSandboxProvider` behavior. Only Windows evidence was collected here; Linux/macOS suites remain skipped on this machine and must run on their own runners before those platforms could ever enable executable Skills.

## New files

- `runtimes/python-worker/shuliu_runtime/skills/platform/__init__.py`
- `runtimes/python-worker/shuliu_runtime/skills/platform/windows.py` — `WindowsJobSandboxProvider`: reports `enforced_controls = {separate_process}` and `supported=False` with a reason enumerating every unproven control; `wrap_command` always raises `SKILL_SANDBOX_UNAVAILABLE`. `SkillProcessHost._require_enforced_controls` therefore refuses to start any executable Skill on Windows.
- `runtimes/python-worker/tests/skills/platform/test_sandbox_attacks.py` — 9 Windows-only tests using the exact production mechanism (`_spawn` with `CREATE_SUSPENDED`, `_attach_windows_job`, `_resume_windows_process`, Job kill-on-close).

## Controls: proven vs. unproven (Windows real-machine evidence, 2026-11-05)

| Control | Result | Evidence |
|---|---|---|
| `separate_process` | **PROVEN** | Attack child spawned suspended and Job-assigned before first instruction; descendant created with `CREATE_BREAKAWAY_FROM_JOB` remains a Job member (`IsProcessInJob`/membership query) — breakaway cannot remove it from the Skill Job (creation is either denied with `ERROR_ACCESS_DENIED` on clean runners or contained, as observed here); a grandchild surviving its parent is terminated by Job kill-on-close; late-joining descendants during termination are contained (existing race tests in `test_windows_sandbox.py`). |
| `filesystem_scope` | **NOT PROVEN** | Job-contained child successfully read a file outside its package. |
| `network_scope` | **NOT PROVEN** | Job-contained child completed a full loopback TCP exchange (send/receive) once the environment allowed Winsock to initialize. |
| `no_direct_os_control` | **NOT PROVEN** | Job-contained child successfully spawned and ran an arbitrary child process capturing its output. |
| `secret_isolation` | **NOT PROVEN (beyond env)** | Job-contained child successfully read a same-user "secret" file. Env-level isolation IS proven separately: the minimal Skill environment omits every parent value (planted `SHULIU_TEST_TASK36_SECRET` absent; only the 7 minimal keys plus known execution-sandbox injection present). |
| `foundation_db_isolation` | **NOT PROVEN** | Job-contained child opened and queried a same-user SQLite Foundation-style database. |

Because five required controls are unproven, the provider reports `supported=False`; executable Skills remain **disabled on Windows** (fail closed, per ADR-025).

## Incidental observations (not security controls)

1. The production `_minimal_environment` omits `SystemRoot`; on Windows this breaks Winsock initialization (`WSASYSNOTREADY`, WinError 10106) in Skill processes, so they cannot use sockets at all. This is an **artifact, not an enforced network control** — it must not be claimed as `network_scope` — but it is recorded because it affects any future Skill that legitimately needs the network and because the network attack test restores `SystemRoot` to measure the Job Object itself.
2. The WorkBuddy execution environment on this machine injects `LSBOX_AUDIT_SHMEM`, `LSBOX_SHMEM_NAME`, `SANDBOX_IPC_TRACE_PARENT`, `SANDBOX_IPC_TRACE_SESSION` into every child process regardless of the requested env. The env test allows exactly those keys as extras; on clean Windows runners the extras set is empty.

## TDD evidence

- **RED:** `tests/skills/platform/test_sandbox_attacks.py` failed collection (`ModuleNotFoundError: shuliu_runtime.skills.platform`) before implementation. First GREEN iteration also exposed a test-harness bug: spawning without `CREATE_SUSPENDED` made `_resume_windows_process` fail closed — tests were switched to the production `_spawn`.
- **GREEN:** `pytest runtimes/python-worker/tests/skills/platform -q` → **23 passed, 2 skipped** (skips are the Linux/macOS suites, correct on this machine). Full skills regression: **80 passed, 4 skipped**. Process-host lifecycle and crash-cleanup suites are included in that run.

## Limits and unrun evidence

1. Linux (`namespace`/`bwrap`-style) and macOS (`sandbox-exec` does NOT count as evidence) platform providers were not implemented; their suites remain `skipif`-gated and those platforms keep executable Skills disabled.
2. No shared CI workflow (`ci.yml`, `windows-validation.yml`) or release-gate manifest was touched, per task constraints — runner execution of these attack tests must be wired by a later authorized change; the current evidence is local Windows real-machine only.
3. `skill_sandbox_provider` release gate stays BLOCKED: no OS currently has a provider proving all required controls; do not record the local evidence above as release evidence.
4. No real Provider calls; no changes to Desktop, chat, Computer Runtime, Task 34 files, or `main`.
