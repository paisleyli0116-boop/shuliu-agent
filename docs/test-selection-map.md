# 测试选择映射表

本文件说明：**改动落在哪里，就该跑哪一组测试**。

目的不是减少测试覆盖，而是避免在改动范围已明确时反复执行无关的全量测试。
任何情况下都不得通过跳过测试、关闭安全检查或把失败标记为通过来换取速度。

适用范围：Foundation V0.1 当前阶段，Windows 实机为主要开发环境。

---

## 一、基本原则

1. **先定向，后回归。** 改动后先跑与该改动直接相关的测试；只有定向测试通过、且风险需要时才跑更大范围。
2. **失败用例优先。** 已知失败或刚修复的用例先单独跑（用 `-k` / 指定文件路径），确认转绿后再扩大。
3. **先读报告，再决定是否重跑。** 有 JUnit XML 或 CI 工件时先解析失败节点，不要直接重跑完整套件。
4. **纯文档改动不默认触发代码测试。** 但涉及验收记录的文档，最终 Release Gate 仍以实际测试结果为准。
5. **最终验收不可省略。** 本表只约束开发过程中的中间轮次；Release Gate 要求的检查必须完整执行一次。

---

## 二、按改动位置选择测试

### 2.1 TypeScript / 桌面端

| 改动位置 | 定向测试 | 说明 |
|---|---|---|
| `packages/shared-types/**` | `pnpm test:unit` | 类型与错误定义，用例极少且快（约 1 秒） |
| `packages/event-model/**` | `pnpm test:unit` | 事件模型、脱敏、指标 |
| `packages/protocols/**` | `pnpm test:unit` | 协议校验 |
| `apps/desktop/src/main/**` | `pnpm --filter @shuliu/desktop test` + `typecheck` | 主进程逻辑 |
| `apps/desktop/src/renderer/**` | `pnpm --filter @shuliu/desktop test:ui` | 渲染层 UI 测试 |
| `apps/desktop/tests/ipc/**` | `pnpm --filter @shuliu/desktop test:ipc` | 需要先 `build:ipc-test` |
| `tests/security/**` | `pnpm test:security` | 仓库安全扫描 |
| `tests/contract/**` | `pnpm test:contract` | 含真实 Electron ↔ Python 往返 |
| 任意 `tsconfig*.json` | `pnpm typecheck` | 配置改动影响面广，必须跑类型检查 |

### 2.2 Python 运行时

| 改动位置 | 定向测试 |
|---|---|
| `runtimes/python-worker/<模块>/**` | `uv run --locked --all-packages pytest runtimes/python-worker/tests/<模块> -q` |
| `runtimes/python-worker/tests/browser/**` | 单文件 + `-k <用例名>`，见 2.3 |
| `runtimes/browser-driver/**` | `uv run --locked --project runtimes/browser-driver pytest <对应测试> -q` |
| `services/cloud-api/<模块>/**` | `uv run --locked --package shuliu-cloud-api pytest services/cloud-api/tests/<模块> -q` |
| `tests/integration/**` 相关实现 | `pnpm test:integration`（约 51 秒，含真实依赖） |
| 数据库迁移相关 | `pnpm test:migration` |

**注意**：`uv run` 必须带 `--locked`，确保用的是 `uv.lock` 锁定的依赖，不得临时升级。

### 2.3 真实 Chrome 测试（最需要精确选择的一组）

这组测试单次 20–60 秒，且是历史重复最多的环节。**必须用 `-k` 精确到用例**，不要跑整个 `tests/browser` 目录。

```powershell
# 单个用例
uv run --locked --all-packages pytest runtimes/python-worker/tests/browser/test_real_browser.py -k "unapproved" -q --tb=short

# 一组相关用例（如清理相关）
uv run --locked --all-packages pytest runtimes/python-worker/tests/browser/test_real_browser.py -k "crash or disconnect" -q --tb=short
```

已知的用例分组（用于 `-k` 选择）：

| 分组关键词 | 覆盖内容 |
|---|---|
| `navigate` | 导航、读取、点击、输入、滚动、关闭 |
| `unapproved` | 未批准来源（重定向 / 子资源 / iframe / 弹窗） |
| `crash` | Chrome 崩溃后的清理与状态 |
| `disconnect` | driver 断连 |
| `cancel` / `timeout` | 取消与超时语义 |
| `type` | 单次输入效果（无回退重试） |
| `longpath` | Windows 长路径清理 |

**约束**：`SHULIU_REQUIRE_REAL_BROWSER=1` 不得移除。真实 Chrome 验收不可用 FakeBrowser 替代。

### 2.4 文档与证据

| 改动位置 | 处理方式 |
|---|---|
| `docs/**` 纯说明文档 | 不触发代码测试 |
| `docs/evidence/**` 验收记录 | 不触发测试，但记录中的数字必须来自真实运行产物 |
| `.github/workflows/**` | 至少 `git diff --check`；工作流语法改动需要一次实际 CI 验证 |
| `package.json` scripts | 修改的脚本必须实际执行一次 |

---

## 三、风险分级：什么时候必须跑全量

| 风险等级 | 触发条件 | 要求 |
|---|---|---|
| **低** | 单一模块内部实现、无接口变化 | 定向测试即可 |
| **中** | 跨模块接口、事件契约、IPC 协议 | 定向 + `pnpm test:contract` |
| **高** | 安全边界、审批机制、权限、脱敏、沙箱 | 定向 + `pnpm test:security` + 相关安全用例，不得省略 |
| **最高** | 依赖变更、锁文件变更、CI 配置变更 | 完整回归 + 一次真实 CI 运行 |

---

## 四、开发过程 vs 最终验收

| 阶段 | 测试范围 |
|---|---|
| 写测试（RED） | 只跑新增/修改的用例，确认失败原因符合预期 |
| 最小实现（GREEN） | 只跑该用例 + 同文件用例 |
| 整理代码（REFACTOR） | 该模块全部用例 |
| 提交前自查 | 受影响模块 + 契约测试（若接口变动） |
| **Release Gate** | **完整回归一次**：Python 全量 + 十项仓库检查 + CI 三类平台结果 |

Release Gate 不得用"之前跑过"替代。任何代码改动之后，最终验收必须重新执行。

---

## 五、有报告时先读报告

定位失败时优先使用已有产物，不要重跑：

| 产物 | 位置 | 用途 |
|---|---|---|
| 本地 Python 全量报告 | 运行命令中 `--junitxml=<路径>` 指定 | 解析 `<failure>` 节点即可拿到失败用例与堆栈 |
| CI 浏览器工件 | Actions 运行页的 `browser-tests-<os>.xml` | 跨平台失败定位，无需本地复现 |
| CI 运行结果 | `gh run view <id>` 或 JSON 导出 | 确认失败 job 与失败步骤 |

读取 JUnit 的方式：

```powershell
# 只看失败的用例名
Select-String -Path python-full.xml -Pattern '<failure' -Context 0,3
```

只有在**没有新诊断信息**的情况下，才考虑重跑。

---

## 六、明确禁止的"优化"

以下做法一律不允许，即使能显著提速：

- 跳过或删除测试用例
- 把失败用例标记为 skip / xfail 来过关
- 关闭 `SHULIU_REQUIRE_REAL_BROWSER`
- 用 FakeBrowser 断言替代真实 Chrome 验收
- 放宽安全扫描、审批检查或权限校验
- 用缓存的历史结果冒充本次验收
- 并行运行真实 Chrome 测试（会争抢浏览器 profile，制造不稳定失败）
