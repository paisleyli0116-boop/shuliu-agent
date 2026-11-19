# ShuLiu Desktop Real Chat — Design

Date: 2026-11-04
Status: Implemented in an isolated local worktree. The previously recorded `C:\Dev\shuliu-agent` Fake Provider roundtrip is historical evidence; the current isolated worktree has not passed its real Electron↔Sidecar roundtrip because the sandboxed Electron binary is denied access by the worktree `node_modules` ACL. See `docs/evidence/desktop-chat-computer-integration.md` for current verification and limits.
Scope: First usable text-chat slice in the existing Windows Desktop and Python Sidecar.

## Intent and choices

The current chat route is a disabled capability preview. The first usable slice will let the user select a model that the Sidecar actually loaded, type a message, explicitly confirm that the message may be sent to that provider, and manually send it through the existing Model Gateway. The user asked to begin implementation; the builder chooses a narrow vertical slice so the missing desktop-to-Gateway boundary is established before adding more product behavior.

Three approaches were considered: call LiteLLM from the Renderer (rejected because it would expose credentials and bypass the Gateway); add a new Cloud chat API (not supported by the current code and would expand the deployment scope); and send a typed request through trusted Electron Main IPC to a Python Sidecar chat service that uses the existing Gateway (selected because it preserves the current architecture and SecretRef/Policy/Gateway boundary).

## Behavior and constraints

- Only an explicit user Send action can make a Provider request. App startup, route loading, model selection, and history rendering never send one.
- Renderer supplies provider/model identifiers and user/assistant text only. Python constructs organization, subject, permission, privacy, and budget context; provider/model must exactly match an enabled runtime profile.
- Chat is text-only and PUBLIC-only in this slice. The UI requires an explicit confirmation before sending text to the selected provider. Policy, SecretRef, health, budget, LiteLLM, and no-fallback behavior remain in the Gateway.
- Requests are bounded, one at a time, non-streaming, and capped at 512 output tokens. There is no automatic retry. Duplicate request IDs are rejected or replay only the already-cached safe result in the current Sidecar lifetime.
- Conversation text remains only in the open Renderer window. This slice does not persist prompts or responses to SQLite, Cloud, logs, traces, or audit metadata. The Sidecar records only the existing Gateway's non-content ModelCall metadata.
- No tools, Skills, approval actions, Agent orchestration, Knowledge retrieval, attachments, Markdown rendering, streaming, cancellation, cross-restart history, or automatic fallback are part of this slice. UI must state those limits honestly.
- No real Provider call will be made during implementation or automated verification.

## Acceptance

With a healthy Sidecar, a configured `fast_chat` profile appears in the chat model selector. A manually submitted message traverses typed Preload → trusted Main → Sidecar allowlist → Python chat service → Model Gateway. The Gateway remains the only Provider call path. Policy/budget/credential/runtime failures display a safe code and Trace ID. Safe Mode, missing model, unchecked external-processing consent, malformed input, and duplicate/in-flight requests do not reach the Provider. The UI keeps the conversation only in memory and never presents fake messages or sample models.

The UI vertical slice is complete when Python service/IPC tests, Desktop IPC/Preload/UI tests, typecheck, build, and an Electron-sidecar roundtrip using a Fake Provider pass. A real DeepSeek call remains a separate user-triggered action and is not part of this implementation run.

## Deferred work

Durable session history and retention policy, session list/multiple conversations, Knowledge selection and ACL, Agent selection/default model routing, streaming/cancellation, Markdown rendering, tool/approval event presentation, and a durable USD budget account in Desktop runtime require separate implementation and acceptance. The in-memory request allowance is an availability guard, not a provider billing ceiling.

## Verification record — 2026-11-04

- RED→GREEN reproduced and fixed structured error loss: `MODEL_CHAT_FAILED` had been replaced in Desktop Main by `IPC_REQUEST_FAILED`, and the failure stage became `sidecar_ipc`. The safe code is now allowlisted and a regression test covers the exact result.
- Python chat/service/IPC/provider connection tests: **31 passed**. The NDJSON integration uses a Fake Provider through `IpcServer` and the real Model Gateway; it is not a live Provider test.
- Desktop tests: **124 passed** across 26 files.
- Desktop typecheck: passed; root typecheck: passed.
- Desktop production build: passed; IPC fixture build: passed.
- Security suite: **10 passed** across 4 files.
- The first Electron attempt in the isolated worktree was blocked by its AppContainer ACL. In the actual product worktree, the fixture was built with `build:ipc-test`, the test harness was corrected to keep Electron sandboxing enabled, and Electron Main → Sidecar → Fake Provider contract tests then passed **2/2**. The roundtrip returned the real Gateway-shaped fixture response.
- The current product Desktop was launched from `C:\Dev\shuliu-agent\apps\desktop\dist\renderer\index.html`; live UI showed Sidecar online and Python Runtime healthy. Its Profile list showed DeepSeek loaded but **0 configured credentials**, and the AI Chat page therefore correctly shows no available model. No message was sent.
- Live DeepSeek/provider request: **not run**. No credential was read by this implementation session.
- Desktop integration changes are present as uncommitted modifications in `C:\Dev\shuliu-agent` on `feat/foundation-v0.1`; no commit or push was made. The running app is the freshly built development Desktop, not a newly installed release package.
- Known product limitation: local Desktop has only a 20-request-per-Sidecar in-memory guard, not a durable monetary budget. It resets when Sidecar restarts. UI explicitly discloses this; do not treat it as a provider billing ceiling.
- Remaining user setup for live chat: configure the DeepSeek API Key through “模型与连接” in this Desktop. The implementation does not read, recover, or display it. After that, the user must explicitly confirm the message is public/non-sensitive and click Send; no request is sent on startup or page navigation.

## Isolated-worktree revalidation — 2026-11-18

The merged Desktop chat and read-only Computer slice was revalidated in `D:\Documents\ChatGPT\数流\desktop-integration` on branch `codex/desktop-integration` (base `86b1b93ffbb9f17d4d498f97afdbf3eb0f0bbfb1`). Desktop tests passed 138/138, relevant Python tests 346/346, security tests 10/10, root/Desktop typechecks passed, and the production build passed. TDD also found and fixed a generated IPC fixture defect that dropped invocation arguments.

The real Electron IPC contract still fails before application startup: Windows reports `install_dir_access.cc:52`, `0x80000003`, because the sandbox token cannot read the Electron distribution beneath this worktree's `node_modules`. No ACL was changed and Electron sandboxing remains enabled. The event contract's `uvx` wrapper was blocked by a user-level uv tools lock ACL; the same serializer roundtrip passed when run with the worktree venv. Thus the isolated worktree is not yet accepted as a real Desktop runtime, and no live Provider request was made.
