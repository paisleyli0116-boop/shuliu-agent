# ADR-017: Repository, Toolchain, and CI

- Status: Accepted
- Date: 2026-10-17
- Scope: ShuLiu Agent Foundation V0.1 repository and engineering workflow

## Context

Foundation spans Electron / TypeScript, Python Runtime, Cloud API, Worker, shared contracts, and Windows-only desktop capabilities. The repository must make cross-runtime contracts testable while keeping Windows verification honest.

## Decision

- Use a pnpm workspaces monorepo.
- Use Node.js 22 LTS, minimum 22.12, and Python 3.11.
- Use Vitest for TypeScript tests, pytest for Python tests, and GitHub Actions for CI.
- Use `uv` with `pyproject.toml` / committed `uv.lock` for reproducible Python dependency resolution. The detailed plan proposes a pinned tool version and package versions for approval.
- Run generic tests on Linux and macOS runners. Windows Computer Use, PyInstaller, NSIS, and install / upgrade / rollback validation run on a Windows runner or Windows real device. GUI automation requires an Interactive User Session.
- Initial repository layout:

  ```text
  apps/desktop
  services/cloud-api
  runtimes/python-worker
  packages/protocols
  packages/shared-types
  packages/event-model
  packages/testing
  skills/examples
  tests/contract
  tests/integration
  tests/e2e
  docs
  ```

- Do not develop product implementation directly on `main`. Implementation starts on `feat/foundation-v0.1` in a separate worktree and follows TDD.
- Windows packaging uses PyInstaller and NSIS, with Windows-only build and installation evidence.

## Consequences

- Shared contracts can be checked across TypeScript and Python, while runtime-specific code remains in its designated project.
- Windows-only checks cannot be reported as passing from macOS CI.
- Documentation-only changes may be made on `main`; the branch/worktree rule applies to product implementation.

## Security and Compatibility Effects

- Lockfiles and CI must prevent unnoticed dependency drift. The Windows release path must preserve installer and rollback evidence.
- OS-specific isolation and GUI behavior must be validated in the corresponding Windows environment.

## Validation

- Repository layout and runtime minimums are checked in CI.
- Linux / macOS jobs run the generic unit, contract, and integration suite.
- Windows jobs provide independent evidence for Windows build, installer, update, rollback, and Interactive User Session checks.
