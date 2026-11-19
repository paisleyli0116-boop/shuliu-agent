# Desktop chat and read-only Computer integration

Date: 2026-11-18 (roundtrip verified 2026-11-19)  
Branch: `codex/desktop-integration`  
Base SHA: `86b1b93ffbb9f17d4d498f97afdbf3eb0f0bbfb1`  
State: locally integrated; real Electron ↔ Sidecar roundtrip **passed** on Windows (see below). Packaged-install validation and live provider calls remain not run by design.

## Implemented in the isolated worktree

- Added a text-only chat route through typed Renderer → Preload → trusted Electron Main → Sidecar → Python ConversationService → existing Model Gateway. Production chat is constructed only when the production identity and deployment Policy are supplied; it uses that identity's actual permissions and the existing Policy. Missing trusted dependencies fail closed before provider dispatch.
- Added read-only foreground Computer capability/observation through the existing production Computer Runtime and Tool Broker. The trusted organization and actor are server-owned. The foreground window is resolved only after the `computer_window` resource policy check. No click, typing, or other mutation capability was added.
- Kept conversation text in renderer memory only. There is no persisted conversation history, streaming, cancellation, tool use, knowledge selection, or Agent orchestration in this slice.
- The chat budget is a 20-request per-Sidecar availability guard, not a durable monetary cap. The UI discloses that provider calls may incur charges. Do not treat this guard as a spending limit.
- Corrected the IPC Electron fixture bundle so it forwards invocation arguments, including chat payloads.

## Verification on 2026-11-18

| Check | Result |
|---|---|
| TDD: production identity/Policy chat tests | RED: 4 failed before the authorization wiring; GREEN: 4 passed after it |
| TDD: foreground window policy resource test | RED: scoped `computer_window` DENY did not apply before resource binding; GREEN after fix |
| TDD: generated Electron fixture payload forwarding | RED: only `shuliu:chat:complete` reached `ipcRenderer.invoke`; GREEN after forwarding `...args` |
| Desktop unit/UI/IPC suite | 138 passed |
| Python models/computer/production/IPC suites | 346 passed |
| Desktop and root TypeScript checks | passed |
| Desktop production build | passed |
| Security suite | 10 passed |
| `git diff --check` | passed (Git emitted only line-ending normalization warnings) |
| Python event serializer roundtrip using the worktree venv | passed |
| Full `pnpm test:contract` | **75 passed, 0 failed** (2026-11-19) |
| Real Electron Main ↔ Python IPC roundtrip | **passed**: `tests/contract/ipc-roundtrip.test.ts` (2 tests, real Electron Main + real Renderer + real preload + real Sidecar subprocess), including the chat assertion `status=succeeded, request_status=http_response_received, response="fixture reply"` through `window.shuliu.chat.complete` |
| Event serializer roundtrip | **passed**: `tests/contract/event-roundtrip.test.ts` now prefers the project venv Python (created by `uv sync`) and falls back to `uvx` only when no venv exists |
| Live DeepSeek/provider call | not run; no credential was read and no provider request was made |
| Windows packaged-install validation | not run |

## Root-cause correction (2026-11-19): the 0x80000003 Electron failure

The earlier diagnosis ("this worktree's `node_modules` ACL blocks the Electron sandbox") was wrong. On 2026-11-19 the identical worktree passed the identical real-Electron tests on the same machine with no ACL change:

- `icacls` output of both `electron/dist` directories (failing vs. previously passing worktree) is byte-identical; `D:\Documents\ChatGPT\数流` grants the same ACEs to both.
- `tests/contract/ipc-roundtrip.test.ts` and `pnpm --filter @shuliu/desktop test:ipc` (69 tests) both pass here.

The actual differentiator was the **process environment that spawned Electron**: the earlier session ran inside its own sandboxed host, whose restricted child tokens (the `CodexSandboxUsers` ACEs visible on every path) collide with Chromium's own Windows sandbox token checks at `install_dir_access.cc:52`. Electron's Chromium sandbox cannot initialize inside another sandbox's restricted token. No machine ACL was modified, no sandbox flag was disabled, and nothing about the product code needed to change for this.

Practical rule recorded for future sessions: real-Electron verification must run from a process whose children can create Chromium sandbox tokens. From a restricted-token host, expect `0x80000003 install_dir_access.cc:52` before `app.whenReady()` — that is an artifact of the host, not evidence about the repository.

The event contract wrapper's first attempt was blocked when `uvx` tried to write its tool lock under the user `%APPDATA%`. Fixed by preferring the project venv Python in `event-roundtrip.test.ts` (the `uv sync` venv exists locally and in CI); the `uvx` path remains as fallback.

## Review notes and limits

- The current production IPC path still passes through the supervisor, whose Safe Mode blocks runtime requests. Python also denies non-recovery methods while persisted Safe Mode is active.
- Chat authorization denial and computer resource-policy denial are covered by tests using fake provider/runtime boundaries; these tests do not prove a live provider response or actual foreground desktop observation.
- There is no independent reviewer sign-off for this isolated worktree. The available review agent could not complete its review because of a usage limit; the code was manually inspected for the trust boundary, Policy use, request bounds, and absence of credential literals.
- No changes were made to `main`, no commit or push was performed, and no real Provider request was sent.
