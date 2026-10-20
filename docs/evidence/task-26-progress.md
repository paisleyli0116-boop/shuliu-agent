# Task 26 — Windows 安装包与更新验收证据

## 范围与检查点

- 任务：Task 26（Installer / Update / Safe Mode），不含 Task 27。
- 分支：`feat/foundation-v0.1`。
- 本地实现基点：`d8a120f037fc04ebeec82068518a80b9459342c8`。
- 本机：Windows 11 专业版 10.0.22631；工具链 Node 22.23.3、Python 3.11.9；打包产物内 Electron 44.9.0 / Node 24.22.0；PyInstaller 6.22.3；electron-builder 26.15.3。
- 本地工作区隔离安装测试在临时安装目录和独立用户数据目录执行；它证明当前 Windows 主机上的安装流程，不等同于干净虚拟机镜像验收。

## TDD 与本地结果

- 下载中断测试先 RED：当时 `applyUpdate` 忽略下载边界并返回 `ACTIVE`，具体断言期望 `FAILED / DOWNLOAD_FAILED`。实现下载边界后该定向文件 7/7 通过；中断时不 staging、不回滚、不激活，结果不包含原始异常文本。
- 首轮根目录契约测试 1 项因 Windows workflow 未显式写出 `NSIS` 失败；补上真实 NSIS 打包步骤名称后根目录契约 68/68 通过。首轮根 TypeScript 检查也发现 2 个健康状态测试 fixture 被推断成 `string`；改成字面量类型后根类型检查通过。
- 打包迭代曾遇到 Windows 文件扫描器对 `.exe`/`.pyd` 资源复制返回 `EBUSY`，并遇到 NSIS 不支持当前 `LogSet` 配置。最终改为 PyInstaller one-file Sidecar、资源暂存 `.bin` 并由 NSIS 安装阶段恢复 `.exe` 名称，以受测的 `install.log` 收据记录安装结果；最终 NSIS 构建与安装 smoke 通过。
- Task 26 定向 Desktop 选择：5 个测试文件、22 项通过（签名、checksum、兼容性、更新失败路径、Safe Mode/回滚、打包契约与 Sidecar 启动）。
- 根目录单元与契约：24 + 68 项通过；根 TypeScript 类型检查通过。
- Desktop 全套：13 个文件、43 项通过；Desktop Main/Renderer 类型检查通过；Electron Main、Preload、Renderer 生产构建通过。
- Windows IPC：Desktop 子进程 9 项、IPC 契约 2 项、Python IPC 4 项通过。
- Python 迁移：15 项通过、1 项 PostgreSQL 用例因本机未配置 PostgreSQL 服务而跳过。
- Security：8 项通过。
- Windows 真 Chrome Browser Runtime 与 Tool Policy：60 项通过，保留 JUnit 报告：`%TEMP%\task26-browser-6cfceb40a9df41c4a3d21ddb635af7a0.xml`。
- Computer Runtime + Windows E2E：33 项通过；JUnit：`%TEMP%\task26-computer-38b7b6deddc24ea4bceae43e405bcd36.xml`。
- Skill 隔离：43 项通过、2 项跳过；Windows 平台 sandbox：2 项通过、2 项跳过。
- Python integration：53 项通过、4 项跳过（其中服务依赖用例需要 PostgreSQL 环境）。

## 实际安装包证据

- PyInstaller standalone Sidecar 构建成功；实际 packaged Sidecar Windows IPC `runtime.health` 返回健康状态并创建 schema `1.0.0` 数据库。
- 真实 x64 NSIS 安装包：0.1.0 基线及最终 0.1.1；最终安装包位于本机临时目录，不进入源码仓库。
- 最终 0.1.1 安装 smoke：通过。实际安装、首次启动、Electron/Node 版本、Preload、Sidecar IPC、数据库 schema、Safe Mode 错误退出码、NSIS 安装收据、静默卸载及用户数据保留均通过。证据：`%TEMP%\shuliu-task26-smoke-final-v2-9fbfbfcf54a44178829bd6ee6872c594`。
- 实际安装升级/回滚：0.1.0 → 0.1.1；故意禁用 Sidecar 后 Safe Mode；重新安装 0.1.0 并确认 marker 与 SQLite 数据保留；通过。证据：`%TEMP%\shuliu-task26-upgrade-final-a41443b954f44444911cb84b6daa5789`。
- 最终 0.1.1 NSIS 安装包 Ed25519 manifest/checksum 签名和运行时代码验签通过；SHA-256：`94679f0d0442ea85fdff282a2f82aa5e78674fbc8184d83d9595e95394dc252b`。签名私钥为本机临时生成，已删除；验证公钥与签名产物仅保存在系统临时目录。

## Task 26 原计划范围与验收判定

- 原始计划的强制能力是：签名更新包和 checksum 校验；兼容性 manifest；更新状态 `DOWNLOADED → VERIFIED → STAGED → MIGRATED → HEALTH_CHECKED → ACTIVE`；坏签名、checksum、迁移、兼容性、健康检查、下载中断和不安全回滚测试；Windows PyInstaller/NSIS、安装、升级、回滚、Sidecar health 证据。
- 原始计划没有明列 Renderer 更新 UI、IPC 更新入口、长期密钥轮换系统、Authenticode 或全新 Windows VM。`applyUpdate` 的受控状态机属于 Task 26；缺少用户可用自动更新入口是当前产品限制，但不能据此擅自扩成 UI/IPC 子任务。
- Architecture Spec §16 将签名信任根列为实施/发布配置项；当前计划没有为长期签名密钥或公钥部署指定具体 Task。Task 27 汇总 Release Gates，不负责替代信任根配置。

## 签名、验签与失败恢复的实际调用关系

- `sign-update-package.mjs` 读取 Windows 安装器字节，生成 `payload.exe`、包含 `payload_sha256` 和 `signing_key_id` 的 manifest，以及 manifest 签名。`verify-update-package.mjs` 读取独立公钥文件，并调用构建后的 `verifyUpdatePackage`；当前 Windows Validation 中这两步是 CI artifact 检查。
- 产品状态机 `applyUpdate` 收到 `download()` 返回的签名包后，直接调用同一个 `verifyUpdatePackage`；只有验签和 checksum 成功才继续 `stage → preflightMigration → migrate → healthGate → activate`。迁移、健康或激活失败会先检查回滚兼容性，再回滚或进入 Safe Mode；坏包在 staging 前被拒绝。
- 但生产代码没有桌面端调用 `applyUpdate`，也没有下载/暂存签名包的平台适配器。CI 的签名包 CLI 验证、安装 smoke 与升级回滚脚本彼此独立，没有从实际下载包贯穿到产品 `applyUpdate` 的用户可用自动更新链路。当前不能声称自动更新已交付。

## 正式密钥配置要求（尚未配置）

- 正式公钥建议作为 Electron Main 构建产物内的固定信任表（`signing_key_id → PEM`）随受信版本发布，并作为 `trustedKeys` 注入 `applyUpdate`；不得从下载包自身、普通用户可写配置或可任意覆盖的运行时环境变量建立信任。公钥缺失或 key ID 未知必须 fail closed（现有 verifier 返回 `UPDATE_SIGNER_UNTRUSTED`）。当前仓库没有正式公钥，也没有 Main 进程加载/注入调用方。
- 正式私钥应只由受保护的发布签名环境持有，例如带审批和分支/标签限制的 GitHub Actions Environment secret，或不可导出的 HSM/KMS 签名服务；普通分支验证工作流不应接触该密钥。私钥不得进入仓库、构建产物、日志或测试证据。当前尚无可核验的正式 key ID、信任表或发布签名环境配置。
- 本次已将 `windows-validation.yml` 改为只生成、使用并清理 `ci-ephemeral` 测试密钥；它仅验证签名机制，不建立正式信任根，也不得用于正式发布。该 Windows Validation 不再读取发布私钥 secret。是否采用上述正式密钥方案仍需发布责任方实际配置和证据确认。

## 本轮验证

- 更新定向测试：`tests/update` 4 个文件、19 项通过。
- Desktop 测试：命令 `pnpm --filter @shuliu/desktop test -- update` 实际执行整个 Desktop 套件，13 个文件、43 项通过。
- 新增工作流密钥隔离契约测试：修改前 RED（34 项中 1 项按预期失败，因 workflow 引用了正式签名 secret）；修改后 GREEN，`tests/contract/repository-layout.test.ts` 34/34 通过。
- Windows workflow YAML：修正本轮编辑导致的步骤换行错误后，使用本地 YAML parser 解析通过。
- 此轮未修改 Electron 产品代码；前述本机 Windows 安装、升级、回滚证据仍适用。本轮没有重跑安装包 smoke，因为变更仅涉及验证 workflow 和契约测试。

## 远端 Windows CI
- Task 26 的原始实现提交为 `d0c1e08c687e66b0cb07c9917818e5b3b298373c`，父提交 `d8a120f037fc04ebeec82068518a80b9459342c8`。后续验收修订提交 `1a4f132c04aac0175eed8ea703c14abedd1a7371`（父提交为该实现 SHA）已推送到 `origin/feat/foundation-v0.1`。
- 用户于 2026-10-20 确认：完整 SHA `1a4f132c04aac0175eed8ea703c14abedd1a7371` 对应的 GitHub Windows Validation 已通过。本记录依赖该用户确认；当前材料未包含 Actions run URL / run ID，因此不声称本次会话独立读取了运行详情。
- 该 SHA 中的工作流在 `feat/**` 推送时触发 Windows Validation；该运行结果与本地测试分别记录。
## 安装包与发布范围

- 本机 Windows 11 的实际安装 smoke 和 0.1.0 → 0.1.1 升级/回滚证据见上文；使用临时安装目录不等同于干净 VM。原计划要求 Windows runner 或真实 Windows 设备，没有要求全新 VM。
- `electron-builder.yml` 中 `signAndEditExecutable` 仍为 `false`。Task 26 计划和 ADR-027 要求签名更新包，但没有把 Authenticode 列为 Task 26 强制项；安装器发布者证书/SmartScreen 状态未验证。是否要求 Authenticode 是正式 Windows 发布策略决策。
- Linux/macOS 不属于当前 Windows-only 验收平台。

## 关闭状态与阻塞
- **Task 26 工程验收通过**：原始计划要求的 Windows 更新包签名 / checksum、兼容性、状态机失败路径、PyInstaller / NSIS、安装、升级、回滚与 Sidecar health 均有本地证据；用户确认上述完整新 SHA 的 Windows Validation 通过。此结论仅适用于 Task 26 工程验收，不代表 Foundation V0.1 Release Gate 全部通过。
- **正式自动更新能力尚未交付**：产品没有桌面端 `applyUpdate` 调用链、下载 / 暂存适配器或正式受信公钥；CI-only `ci-ephemeral` 密钥不是发布信任根。不得声称用户可用的自动更新已实现。该差距作为正式发布事项保留，Task 26 工程验收不因此扩成 UI / IPC 实施任务。
- **未列为 Task 26 硬性条件**：Authenticode 与全新 VM；是否要求 Authenticode 由正式分发策略决定。
- 本轮仅修改 Task 26 证据文档、Windows Validation 工作流和其契约测试；未触碰 `docs/test-selection-map.md` 或 `scripts/` 等既有未跟踪工作。
