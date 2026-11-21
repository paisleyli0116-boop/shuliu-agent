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
| 1 | unit | linux | `pnpm test:unit` + Python 各 pytest 单测（Generic CI ubuntu） | PASS（run 44886336954） | CI |
| 2 | contract | linux | `pnpm test:contract`（Generic CI ubuntu） | PASS（run 44886336954，75/75） | CI |
| 3 | integration | linux | `pnpm test:integration`（不含 real_browser/postgres 部分） | PASS（run 44886336954） | CI |
| 4 | migration | linux | PG17 + PG18 服务迁移测试（postgres-migration job） | PASS（run 44886336954） | CI |
| 5 | security | linux | `pnpm test:security` | PASS（run 44886336954） | CI |
| 6 | ipc_handshake | linux | `pnpm test:ipc`（真实 Electron ↔ 真实 Python sidecar） | PASS（run 44886336954） | CI |
| 7 | windows_build | windows | PyInstaller sidecar + Electron build + NSIS 打包（Windows Validation） | PASS（run 44446821943） | CI |
| 8 | installer_smoke | windows | 安装冒烟 / Safe Mode / 升级 / 回滚 / 卸载（Windows Validation） | PASS（run 44446821943） | CI |
| 9 | runtime_health | windows | 安装后 runtime health gate（Windows Validation） | PASS（run 44446821943） | CI |
| 10 | secure_storage_macos | macos | `test:secure-storage-platform`（真实 Keychain） | PASS（run 44886336954，本机+runner） | CI + task-27c 文档 |
| 11 | secure_storage_windows | windows | `test:secure-storage-platform`（真实 DPAPI） | 本机 PASS（task-27c 文档）；runner 证据见最新 Windows Validation run | CI + task-27c 文档 |
| 12 | real_model_provider_connection | - | Task 28（未实施） | NOT_RUN | — |
| 13 | model_provider_license_security | - | Task 28（未实施） | NOT_RUN | — |
| 14 | model_usage_budget_persistence | - | Task 29（未实施） | NOT_RUN | — |
| 15 | real_embedding_knowledge_retrieval | - | Task 30（未实施） | NOT_RUN | — |
| 16 | knowledge_document_ingestion_pdf_docx_xlsx | - | Task 31（未实施） | NOT_RUN | — |
| 17 | knowledge_server_rbac_trace_audit | - | Task 32（未实施） | NOT_RUN | — |
| 18 | durable_idempotency | - | Task 33（未实施） | NOT_RUN | — |
| 19 | process_crash_recovery | windows | Task 34（代码已并入；独立 runner 证据 gate 待接线） | NOT_RUN | task-34 进度文档 |
| 20 | skill_sandbox_provider | linux/macos/windows | Task 36（未实施；Windows fail-closed 测试已就位） | NOT_RUN | — |
| 21 | skill_security_scan | - | Task 35（未实施） | NOT_RUN | — |

## 聚合结论

**BLOCKED（按设计）**：10 个核心 gate 已有 PASS 证据；12 个能力 gate 等 Tasks 28–36
落地后补齐。所有 gate PASS 之前 Foundation v0.1 不发布，且不引入任何岗位业务逻辑。

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
