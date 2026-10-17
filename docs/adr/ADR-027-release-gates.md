# ADR-027: Foundation V0.1 Release Gates

- Status: Accepted
- Date: 2026-10-17

## Context

Foundation spans cross-language contracts, persistent data, privileged desktop operations, and Windows-specific packaging. A release cannot be justified by a partial CI run.

## Decision

All of the following must pass before a V0.1 release:

1. Unit tests.
2. Contract tests.
3. Integration tests.
4. Database migration checks.
5. Security tests.
6. Electron ↔ Python IPC handshake.
7. Windows build, including PyInstaller and NSIS packaging.
8. Installer smoke test.
9. Runtime health gate.

Windows Computer Use, build, install, upgrade, and rollback evidence must come from a Windows runner or Windows real device. GUI Computer Use requires an Interactive User Session. A missing required result blocks release.

## Consequences

- Linux / macOS generic CI results do not stand in for Windows evidence.
- Release thresholds, rollout batches, and health observation windows are versioned configuration and must be reviewed before a stable release.

## Security and Compatibility Effects

- Signed packages, compatibility manifests, migration checks, and health-gated activation remain required.
- Rollback is allowed only when database and protocol compatibility checks pass.

## Validation

- GitHub Actions publishes an explicit pass / fail / not-run result for each gate.
- Release automation refuses to publish when any required gate is not passed or its platform evidence is absent.
