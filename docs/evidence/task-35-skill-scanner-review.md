# Task 35 — Skill Security Scanner Selection and License Review

- **Status:** REVIEWED (ClamAV 1.5.4 approved for use as an external-process scanner engine)
- **Review date:** 2026-11-05
- **Reviewer:** Skill Security Owner (Task 35)
- **Scope:** Implementation Plan Task 35 — maintenance status, security advisories, license, offline capability, cross-platform support, rules update mechanism

## 1. Decision

`ClamAV 1.5.4` is approved as the sole real scanner engine for the `skill_security_scan` gate. It is invoked as an **external process** (`clamscan.exe` subprocess) by
`runtimes/python-worker/shuliu_runtime/skills/security_scan.py`; no ClamAV source or binary is linked into, distributed by, or derived from this repository.

The reviewed choice and gate rules are pinned in `tests/release/skill-scanner-policy.yaml` (source of truth for the release gate) and enforced by `tests/release/test_skill_security_gate.py` (9 passed).

## 2. Review findings

| Criterion | Finding |
|---|---|
| Maintainer | Cisco Talos (vendor-maintained, active upstream) |
| Release channel | https://github.com/Cisco-Talos/clamav/releases |
| Reviewed release | clamav-1.5.4 (recent stable at review time; maintenance status: active) |
| License | GPL-2.0-or-later. **External-process invocation only** — calling a separately installed `clamscan` binary does not create a derivative work, so this repo's licensing is unaffected. Any future static/dynamic linkage would require a licensing decision and re-approval. |
| Security advisories | https://www.clamav.net/announcements ; reviewed at approval time. New engine versions require re-review before being added to `approved_engines`. |
| Offline capability | Signature databases (`main.cvd`, `daily.cvd`, `bytecode.cvd`) are local files after `freshclam`; scanning itself requires no network. Rules update mechanism is `freshclam` against `database.clamav.net` (reviewed ruleset: daily-28173). |
| Platforms | Windows, Linux, macOS official builds — consistent with the runtime matrix. |
| Installation | Windows portable zip; no administrator rights required; no bundled unwanted software observed in the reviewed package. |

## 3. Verified local evidence (2026-11-05, Windows)

- Binary under review: `C:\Dev\tools\clamav\clamav-1.5.4.win.x64\clamscan.exe`
- `clamscan --version` reports ClamAV 1.5.4 with the local database at `C:\Dev\tools\clamav\db` (daily-28173).
- Real detection: the 68-byte EICAR test string (base64-encoded fixture, decoded in-test) is detected as `Win.Test.EICAR_HDB-1` by the real engine; a clean fixture scans CLEAN. See
  `runtimes/python-worker/tests/skills/test_security_scan_integration.py` (3 passed with `SHULIU_TEST_CLAMAV_SCAN_BINARY` / `SHULIU_TEST_CLAMAV_DATABASE_DIR` set).
- `freshclam` config note: the portable `freshclam.conf` sample contains a `DNSDatabaseInfo no` line that breaks updates; removing it restores updates. Recorded here because offline rules refresh is part of the security property.

## 4. Fail-closed contract

The gate fails closed on: `scanner_timeout`, `scanner_unavailable`, `unknown_verdict`, `checksum_mismatch`, `missing_evidence`, `fake_engine`. Unit evidence:
`runtimes/python-worker/tests/skills/test_security_scan.py` — 15 passed (clean, EICAR, timeout, unavailable binary, non-zero exit / malformed output, version-probe failure, and staging/activation blocked on every non-clean outcome). Lifecycle wiring evidence: `test_lifecycle.py` — 24 passed; install attempts with a DETECTED/ERROR scan result raise `SKILL_SCAN_DETECTED` / `SKILL_SCAN_FAILED` and never stage or activate.

## 5. Out of scope / limitations

- ClamAV detection rate is lower than commercial engines on targeted malware; it is the approved **baseline** gate, not a guarantee. Approval applies to engine 1.5.4 and the reviewed ruleset generation; engine upgrades or ruleset channel changes require re-review.
- Only Windows local evidence exists so far. Linux/macOS ClamAV runs of the same integration tests are not yet recorded (tracked in `task-35-progress.md`).
- No shared CI workflow or release-gate manifest file was modified in this task; wiring this gate into the Task 27 manifest is deferred and tracked there.
