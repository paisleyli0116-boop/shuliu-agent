# Task 35 — Skill Package Security Scan

- **Status:** IMPLEMENTED (unit + real-engine integration evidence complete; CI and Linux/macOS runs pending)
- **Date:** 2026-11-05
- **Worktree:** `C:\Dev\shuliu-task35-36` (independent worktree from `feat/foundation-v0.1` @ `817dd2f18514c7d9e94b71c8635f21ddd96969ce`)
- **Branch:** `feat/task35-scan-task36-sandbox`
- **Scanner selection and license review:** see `task-35-skill-scanner-review.md` (ClamAV 1.5.4, external-process invocation).

## What was implemented

- `runtimes/python-worker/shuliu_runtime/skills/security_scan.py` — `SkillScanner` port and `ClamAvScanner` adapter. Detects the engine via `clamscan --version`, scans a directory with the local signature database, enforces a hard timeout, and classifies results into `CLEAN` / `DETECTED` / `ERROR` with structured evidence (package digest, engine, engine version, ruleset version, timezone-aware timestamp).
- `runtimes/python-worker/shuliu_runtime/skills/lifecycle.py` — `SkillInstaller` now takes a `scanner`; `install` / `stage` run a mandatory scan **before** permission-approval processing. `DETECTED` raises `SKILL_SCAN_DETECTED`, timeout / unavailable engine / malformed engine output raise `SKILL_SCAN_FAILED`; both fail closed — the package is never staged or activated on any non-clean outcome. Installing a skill with no scanner configured is rejected (no silent bypass).
- `tests/release/skill-scanner-policy.yaml` + `tests/release/test_skill_security_gate.py` — release-gate semantics pinned now so the Task 27 manifest cannot accept missing, not-run, or fake-only scan evidence. Fake engines are never valid release evidence.
- `tests/fixtures/skills/eicar.com` — the EICAR test string stored base64-encoded (so repository checkout and real-time AV products are not triggered by the fixture file itself); tests decode it in memory.

## TDD evidence

- **RED:** installer tests first failed because no scan step existed — a skill whose package contains EICAR staged and activated successfully before the gate.
- **GREEN (unit, fake engine, 15 passed):** clean pass; EICAR detected; engine timeout; engine binary missing; engine `--version` probe failure; non-zero exit; malformed output; unknown verdict; all produce fail-closed results with no staging.
- **GREEN (lifecycle wiring, 24 passed):** `SKILL_SCAN_DETECTED` / `SKILL_SCAN_FAILED` propagate before permission approval; no scan → install rejected; existing lifecycle behavior unchanged.
- **GREEN (real ClamAV integration, Windows, 3 passed):** with `SHULIU_TEST_CLAMAV_SCAN_BINARY` / `SHULIU_TEST_CLAMAV_DATABASE_DIR` pointing at the reviewed 1.5.4 install, clean fixture scans CLEAN and the decoded EICAR fixture is detected by the real engine with real signatures (daily-28173).
- **GREEN (release gate, 9 passed):** policy pins clamav as the only approved engine; missing / not-run / fake / DETECTED / ERROR / digest-mismatch / incomplete-field evidence is BLOCKED.
- **Combined suite:** `runtimes/python-worker/tests/skills/` — **73 passed, 2 skipped** on Windows with real ClamAV enabled (71 passed, 4 skipped without env vars; the 2 permanent skips are Linux/macOS platform sandbox suites that only run on their runners).

## Limits and unrun evidence

1. Linux/macOS runs of the real-engine integration tests are not recorded yet (tests are environment-gated, not skipped-as-passing).
2. The gate is not yet wired into the shared Task 27 release-gate manifest or CI workflows — intentionally deferred per task constraints.
3. ClamAV is a baseline gate, not a guarantee against targeted malware; see the review doc.
4. No real Provider calls; no changes to Desktop, chat, Computer Runtime, Task 34 files, `main`, shared CI workflows, or the top-level gate manifest.
