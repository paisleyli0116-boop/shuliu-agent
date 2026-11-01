# ShuLiu Desktop UI Refresh — Design

Date: 2026-10-25
Status: Revised draft for user approval
Scope: Existing Foundation desktop UI, four current routes

## Intent

Make ShuLiu feel like a clear, professional desktop product for a non-developer business user. The selected scope is a consistent improvement across Tasks, Approvals, Runtime, and Provider Settings; professional appearance is the primary concern.

## Current state

The renderer is a React application with one shared sidebar and top bar, plus four routes. A single stylesheet defines small typography (many 9–12px labels and body copy), compact controls, pale surfaces, and a repeated demo notice. The task and approval routes explicitly use in-process demo data. The supplied Provider page screenshot shows generous unused space and small text at a normal desktop window size.

## Goals

- Establish one visual system for the shell and all four routes: readable type scale, spacing, contrast, controls, panels, and status language.
- Make page purpose, primary action, current state, and next step easy to identify.
- Keep demo-only behavior visible where it affects interpretation without repeating the same banner on every route.
- Preserve existing flows, accessibility basics, and security behavior.

## Non-goals

- No new product capability, route, backend, IPC, or data-flow behavior.
- No changes to Policy, Approval semantics, Provider credentials, SecretRef, canary gates, or runtime health behavior.
- No implication that demo data or unimplemented services are production-ready.
- No new UI framework or dependency.

## Approaches considered

1. **Token-only polish:** Adjust colors, font sizes, and spacing while leaving page structure mostly intact. This is the smallest change, but leaves the repeated demo banner and uneven page hierarchy.
2. **Task-workbench first:** Redesign the task page and its creation flow, then reuse those styles elsewhere. This improves one route while leaving the other pages inconsistent.
3. **Unified four-page pass (selected):** Refine the shared shell and all four routes using one visual system while preserving their current behavior. This matches the request to improve the overall UI.

## Design direction

The design call is a restrained modern Chinese enterprise workbench: white and warm-gray surfaces, dark readable text, and ShuLiu teal-green for primary actions. Use amber and red only for meaningful warning and error states. Avoid decorative gradients, dense ornament, and English micro-labels that compete with the Chinese page title.

Use an intentional type scale: page titles around 26–30px, section titles around 18–20px, body text around 14–16px, form labels and secondary metadata at least 12px. Keep buttons and form controls at comfortable desktop sizes. Align page gutters and panel spacing; use available width for content rather than leaving a narrow form beside a large empty area.

- **Shared shell:** Keep the brand, workspace selector, main navigation, and runtime health. Strengthen selected navigation and use consistent icon sizing. Reduce duplicate status displays so one health state is authoritative.
- **Tasks:** Give the queue and create-task form clear visual priority. Keep the demo queue notice next to task content, where users interpret task data; preserve the existing create action and demo behavior.
- **Approvals:** Make pending state and the existing approve/deny actions easy to scan. Keep risk and demo status explicit. Do not alter approval outcomes or add a new confirmation flow.
- **Runtime:** Lead with the actual runtime state and error code. Present the existing Electron ↔ Sidecar health information as a simple status summary; do not invent additional health stages.
- **Model and connection:** Give Provider, Model, credential state, and the manual connection-test action a clear order. Keep the explanatory security note readable and keep canary results distinct from credential status.

All interface copy remains Simplified Chinese except existing product names and technical identifiers.

## Acceptance criteria

- All four routes share consistent navigation, type hierarchy, spacing, controls, and state colors.
- Body copy is at least 14px and form labels/secondary metadata are at least 12px; important information is not rendered as microcopy.
- The four routes use their available content width coherently at 1440×900 and 1280×800 desktop sizes without horizontal overflow.
- Existing functionality and demo/live distinctions remain accurate; no new functionality or security behavior is introduced.
- Keyboard focus, disabled states, accessible labels, and error/status announcements remain usable.
- No Electron security settings, IPC contracts, API behavior, or persistence behavior change.
- Validate the desktop build and existing renderer tests, then visually inspect all four routes at both target sizes.

## Local work boundary

Keep changes local on feat/foundation-v0.1. Do not commit or push before 2026-10-30. Preserve unrelated working-tree changes, including Task 30 work and the pre-existing untracked scripts/ directory.
