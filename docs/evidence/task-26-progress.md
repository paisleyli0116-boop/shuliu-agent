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

## 发布限制与待验证项

- 尚未获得稳定的发布签名密钥及 key ID。CI 无密钥时使用 CI-only 临时密钥验证签名流程；该流程不建立长期发布者信任，不能把临时密钥产物称为正式可信更新发布。
- Windows NSIS 安装器的 Authenticode 签名目前关闭；本任务验证的是安装器功能和更新包 Ed25519 manifest，不验证 Windows 发布者证书或 SmartScreen 声誉。
- `applyUpdate` 是依赖注入的受控更新状态机；当前仓库没有把它连接到面向用户的更新下载/激活界面或配置正式受信公钥的 production caller。本任务验证状态机与真实安装器升级/回退脚本，不声称已交付自动更新 UI。
- GitHub Windows Validation 尚待本 Task 26 提交推送后实际运行；不得以本机结果代替远端 CI。
- Linux/macOS 不作为本阶段交付验收平台。