# Task 25 — Windows Computer Runtime Evidence

## Scope and environment

This record covers Task 25 only. Validation ran on Windows machine `PC-20260731MOTD` in WTS session `1`, with the `Default` input desktop. The native capability check returned `AVAILABLE`. The pre-existing untracked files `docs/test-selection-map.md` and `scripts/dev-env.ps1` are outside this task.

The UI Automation binding is `pywinauto==0.6.9` (BSD 3-Clause, upstream `https://github.com/pywinauto/pywinauto`), enabled only on Windows. The lock also adds `comtypes==1.4.17` and `pywin32==312` as Windows transitive dependencies. The packages are unmodified. A full release license / vulnerability scan has not been run.

## Implemented behavior

- `ComputerRuntime` exposes capability, observe, act, and `prepare_approval_context` through `ToolBroker`. Approval preparation revalidates identity, permission, and Policy and returns the exact action context that `ApprovalService` binds; execution still consumes that approval through the Broker. Mutating UI actions are CRITICAL and require Policy allow plus human Approval.
- Win32 checks the process session, WTS connected state, input desktop, target window visibility, owning session, bounds, and DPI. Session 0, a locked desktop, a disconnected session, and unknown session state fail closed. Active RDP sessions are accepted when their own WTS session is active and the input desktop is `Default`.
- UIA observations are marked `UNTRUSTED_UI_CONTENT`; sensitive controls are redacted. Actions require a fresh matching observation and target. UIA RuntimeId is included in element identity and the observation fingerprint, so a recreated control cannot inherit an earlier approved element reference; missing RuntimeId fails closed. UI-TARS/UFO predictions are normalized to observed semantic targets, then sent through the same Broker route. Raw coordinates are only resolved against the latest observed UIA bounds. Unsupported `left_double` is refused instead of being silently changed to a single click.
- Actions are re-observed and verified. A post-dispatch error triggers one reconciliation observation; an unobservable or uncertain result is reported as unknown and is never retried. Edit values are not retained in the observation cache, audit event, approval parameters, or evidence. Live observations retain UIA names and window titles for target selection and mark all content as untrusted; durable evidence omits those labels and all control values.
- Typed input is represented in approval parameters only by a runtime-keyed HMAC digest. The preparation API does not retain plaintext; it must be called again after a runtime restart because the in-memory key changes, which makes an earlier approval fail closed.
- Windows Validation runs the Computer Runtime / interactive-session tests with `SHULIU_REQUIRE_INTERACTIVE_COMPUTER=1` and uploads the JUnit report even when the test step fails. A noninteractive runner therefore fails visibly rather than passing through a skip.

## TDD and verification evidence

RED cases observed during implementation:

- The initial contract test could not import `shuliu_runtime.computer` before the runtime existed.
- The active-session contract test failed because the RDP/session-ID guard had not been implemented.
- The `left_double` regression failed because the operator returned a single-click action instead of rejecting the unsupported double-click.
- The observation-cache privacy test found the raw control value retained in `_latest`.
- The approval integration test failed because `ComputerRuntime` had no public way to produce the exact digest-bound context for `ApprovalService`.
- The evidence privacy test found dynamic window titles and element labels persisted in observation evidence.
- The recreated-control test failed because an identical visible tree with a changed UIA RuntimeId was still accepted as `VERIFIED`.
- Invalid session/desktop reports were incorrectly returned as `AVAILABLE`.
- A malformed operator observation raised `AttributeError` instead of a typed operator error.

After the corresponding changes, the Windows Computer Runtime and real UIA end-to-end command passed:

```text
uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/computer tests/e2e/windows -q -rs --tb=short
33 passed in 1.90s
```

The real UIA end-to-end test created and owned a WinForms fixture window, found it through Win32 enumeration, observed its controls through UIA, typed a non-sensitive marker through the Tool Broker path, verified the resulting control value, checked machine/session fields in evidence, and terminated its own fixture process. A separate contract test used the actual `ApprovalService` to prepare, request, approve, consume, and execute a typed action; the approval context contains only the HMAC digest and not the text.

Additional checks:

```text
uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/tools runtimes/python-worker/tests/policy runtimes/python-worker/tests/approval runtimes/python-worker/tests/events -q --tb=short
69 passed in 0.13s

pnpm test:security
3 test files passed; 8 tests passed in 251ms

uv lock --check
Resolved 90 packages; lock is current

uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/tools runtimes/python-worker/tests/policy runtimes/python-worker/tests/approval runtimes/python-worker/tests/events -q --tb=short
69 passed in 0.13s

uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests -q -ra
293 passed, 2 skipped in 71.11s (Linux/macOS sandbox tests are platform-specific)

git diff --check
passed

Windows Validation workflow YAML parse
passed
```

## Remaining limits and CI state

- The Computer Runtime accepts an already-produced UI-TARS/UFO action proposal and constrains it through fresh UIA state and Tool Broker. It does not invoke a live UI-TARS model or capture/store screenshots. No real model/provider call was made; provider configuration is handled by its separate planned task.
- The local Windows interactive-session test passed. Remote GitHub Actions for this Task 25 commit have not yet run. The workflow's required interactive-session setting will fail, with a diagnostic message and uploaded JUnit report, if the remote runner lacks an interactive desktop; the hosted runner's interactive desktop availability remains unconfirmed.
- No Linux/macOS Computer Use result is claimed. They are not current delivery gates.
- Installer, update, rollback, Electron-to-Sidecar integration, and complete desktop recovery remain outside Task 25 and are not claimed as complete.
