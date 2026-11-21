# Foundation v0.1 Release Checklist (ADR-027)

Manifest of record: `tests/release/gate-manifest.yaml`（唯一权威清单，21 个 required gate）。
聚合器：`tests/release/gate_aggregator.py` → 结果只有 `PASS | BLOCKED`，任何 gate
missing / fail / not-run 都 BLOCKED，不允许人工豁免。

CI 报告：每个 workflow 末尾的 gate report job（`ci_gate_report.py`）从 GitHub Actions
job / step 结论推导逐 gate 结果并生成 `gate-report-*.json` artifact 与 job summary。
fail-closed：步骤缺失或跳过一律 NOT_RUN，绝不算 PASS。

## Gate 状态表

状态口径：`PASS`（证据齐全且通过）/ `NOT_RUN`（无 CI 覆盖或被跳过）/ `FAIL`。
证据位于 `docs/evidence/` 与 GitHub Actions run。

| # | Gate | 平台 | 验收入口 | 当前状态 | 证据 |
| - | ---- | ---- | -------- | -------- | ---- |
| 1 | unit | linux | Generic CI ubuntu 单元测试 job/step | PASS（run 44892355099 @ `ace1b9a`） | [Generic CI](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) |
| 2 | contract | linux | Generic CI ubuntu 协议与契约 step | PASS（run 44892355099 @ `ace1b9a`） | [Generic CI](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) |
| 3 | integration | linux | Generic CI ubuntu 集成测试 step | PASS（run 44892355099 @ `ace1b9a`） | [Generic CI](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) |
| 4 | migration | linux | Generic CI + PG17/PG18 migration jobs | PASS（run 44892355099 @ `ace1b9a`） | [Generic CI](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) |
| 5 | security | linux | Generic CI ubuntu security step | PASS（run 44892355099 @ `ace1b9a`） | [Generic CI](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) |
| 6 | ipc_handshake | linux | Generic CI real Electron ↔ Python Sidecar IPC step | PASS（run 44892355099 @ `ace1b9a`） | [Generic CI](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) |
| 7 | windows_build | windows | PyInstaller Sidecar + Electron build + NSIS | PASS（run 44892355151 @ `ace1b9a`） | [Windows Validation](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355151) |
| 8 | installer_smoke | windows | Installed-app smoke / Safe Mode / upgrade / rollback / uninstall | PASS（run 44892355151 @ `ace1b9a`） | [Windows Validation](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355151) |
| 9 | runtime_health | windows | Installed runtime health gate | PASS（run 44892355151 @ `ace1b9a`） | [Windows Validation](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355151) |
| 10 | secure_storage_macos | macos | Real Electron safeStorage round trip via Keychain | PASS（run 44892355099 @ `ace1b9a`） | [Generic CI](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) |
| 11 | secure_storage_windows | windows | Real Electron safeStorage round trip via DPAPI | PASS（run 44892355151 @ `ace1b9a`） | [Windows Validation](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355151) |
| 12 | real_model_provider_connection | - | Task 28 structured release evidence | NOT_RUN（本 SHA 报告无合格证据） | [Generic CI report](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) |
| 13 | model_provider_license_security | - | Task 28 owner-approved license/security review | NOT_RUN（批准材料未作为合格 gate evidence 提交） | [Generic CI report](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) |
| 14 | model_usage_budget_persistence | linux | PG17 + PG18 durable model usage/budget integration step | PASS（两版本均运行；run 44892355099 @ `ace1b9a`） | [Generic CI](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) |
| 15 | real_embedding_knowledge_retrieval | - | Task 30 real provider + persisted retrieval evidence | NOT_RUN（不得用 Fake 证据替代） | [Generic CI report](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) |
| 16 | knowledge_document_ingestion_pdf_docx_xlsx | linux | PG17 + PG18 Knowledge parser/ingestion and revocation step | PASS（两版本均运行；run 44892355099 @ `ace1b9a`） | [Generic CI](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) |
| 17 | knowledge_server_rbac_trace_audit | linux | PG17 + PG18 Knowledge ACL integration step | PASS（两版本均运行；run 44892355099 @ `ace1b9a`） | [Generic CI](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) |
| 18 | durable_idempotency | linux | PG17 + PG18 durable Tool idempotency restart/concurrency step | PASS（两版本均运行；run 44892355099 @ `ace1b9a`） | [Generic CI](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099) |
| 19 | process_crash_recovery | windows | Task 34 full-process recovery with structured release evidence | NOT_RUN（当前报告没有合格的完整进程故障证据） | [Windows Validation report](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355151) |
| 20 | skill_sandbox_provider | linux / macos / windows | Per-OS enforced controls + adversarial evidence | NOT_RUN（3 个平台均缺合格结构化证据） | [Generic CI](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355099)；[Windows Validation](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355151) |
| 21 | skill_security_scan | - | Task 35 real scanner evidence for Skill installation | NOT_RUN（当前报告没有合格扫描器证据） | [Windows Validation report](https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/44892355151) |

## 聚合结论

**BLOCKED（按设计）**：最新已核验 SHA `ace1b9a28a5ffc4033139e6d09bd995443d4ce59` 的报告为 15 个 gate ID 有 PASS 证据、6 个 gate ID 仍 NOT_RUN（Skill sandbox 在 Linux/macOS/Windows 共 3 行）。CI 绿色只代表实际执行的 job 成功，不能把缺失的 Provider、Embedding、进程恢复、OS 沙箱或扫描器证据视为通过。所有 required gate 全部 PASS 前，Foundation v0.1 不发布。

## Release 前必做

1. Tasks 28–36 逐个完成并留存 runner evidence（Fake-only / InMemory store /
   组件级重建 / PID 冒充一律不算 PASS，聚合器强制拒绝）。
2. 每个 gate 的 evidence 必须绑定当前 release 的完整 40 位 commit SHA
   （SHA 不匹配 → 聚合器直接 FAIL）。
3. `skill_sandbox_provider` 需在 linux/macos/windows 三个 runner 分别产出对抗性
   攻击证据（未授权文件/网络/Secret/DB/OS 命令），并记录 enforced controls。
4. 原生 secure storage evidence 只允许 `platform`、`safe_storage_available`、
   `round_trip`、`plaintext_absent`、`delete_verified`、`run_id`、`commit_sha` 字段。
5. 证据保留与出处规则见 `tests/release/evidence-policy.yaml`。

## 更新记录

- 2026-11-21：建立清单。Generic CI run 44886336954（repo 转 public 后首个全绿 run）
  提供 gate 1–6、10 的 PASS；Windows Validation 于浏览器超时修复后重跑确认 gate
  7–9、11；gate 12–21 等 Tasks 28–36。
- 2026-11-21：Task 27-D 核对 `ace1b9a28a5ffc4033139e6d09bd995443d4ce59` 的双工作流和 gate-report job。Generic CI run 44892355099、Windows Validation run 44892355151 均为 success，但 gate report 仍为 BLOCKED；已证明通过的扩展 gate 为 PG17/PG18 的 ModelCall 预算、文档摄取/权限撤销、Knowledge RBAC/ACL 及持久幂等。报告器 fail-closed 修复当前在本地验证中，尚待其新 SHA 的远端 CI。
