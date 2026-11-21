# Task 27-C — Electron Native safeStorage Platform Smoke

ADR-027 required gate: **Mac Keychain 与 Windows DPAPI 的真实 Electron `safeStorage` 验收**，
不得以 FakeSafeStorage 作为平台证据。

## Scope

| 文件 | 作用 |
| --- | --- |
| `apps/desktop/tests/fixtures/secure-storage-platform/main.cjs` | Electron Main fixture：直接调用真实 `safeStorage`，经编译版 `LocalSecureStorage`（`build:ipc-test` 产物，来自 `src/main/security/secure-storage.ts`）执行完整存取链 |
| `apps/desktop/tests/security/secure-storage-platform.test.ts` | vitest spec：spawn 真实 Electron、读取 JSON 报告、断言全部步骤；Linux 自动 skip（CI 无 OS keyring） |
| `apps/desktop/package.json` | 新增 `test:secure-storage-platform` 脚本（先 `build:ipc-test` 再跑 spec） |
| `.github/workflows/ci.yml` | macOS runner 步骤（macOS Keychain 证据） |
| `.github/workflows/windows-validation.yml` | Windows runner 步骤（Windows DPAPI 证据） |

## 验证步骤（fixture 内逐项执行并写入报告）

1. `safeStorage.isAsyncEncryptionAvailable()` === true（真实 OS 后端可用）
2. `LocalSecureStorage.put()` 加密落盘
3. 读取 vault 原始字节，断言**磁盘无明文**（值前缀也不存在）
4. `get()` 读回解密，与原文一致
5. `delete()` 后 `get()` 抛 `SecretNotFoundError`
6. 明文不出现在 fixture stdout/stderr

报告同时记录 `platform` 与 `getSelectedStorageBackend()`（Windows/macOS 返回 undefined → `null`，
该 API 仅在 Linux 报告具体后端；DPAPI/Keychain 的有效性由步骤 1–5 证明）。

## Windows DPAPI 本机实机证据（2026-11-21, Win32）

```
$ electron --no-sandbox --headless --disable-gpu --disable-dev-shm-usage \
    tests/fixtures/secure-storage-platform/main.cjs   # SHULIU_SECURE_STORAGE_* 已设
exit=0
{
  "platform": "win32",
  "backend": null,
  "available": true,
  "encrypted_readback": true,
  "no_plaintext_on_disk": true,
  "deleted": true,
  "ok": true,
  "error": null
}
```

环境约束：spawn 前必须剥离宿主注入的 `ELECTRON_RUN_AS_NODE`（否则 Electron 降级为纯 Node，
证据无效）；spec 内已强制处理。

## 测试结果

- `pnpm --filter @shuliu/desktop test:secure-storage-platform` → 2 passed（Windows 实机，真实 DPAPI）
- `pnpm --filter @shuliu/desktop test` → 24 files / 115 tests passed（含本套件）
- `pnpm --filter @shuliu/desktop typecheck` → 通过

## CI 接线状态

| Runner | 步骤 | 状态 |
| --- | --- | --- |
| `windows-latest`（windows-validation.yml） | `test:secure-storage-platform`（DPAPI） | 待远端执行 |
| `macos-latest`（ci.yml generic matrix） | `test:secure-storage-platform`（Keychain） | 待远端执行 |
| Linux | spec 内 `describe.skipIf`，不作为平台证据 | 符合规格 |

 blocker：GitHub 账户扣费失败导致 Generic CI job 全部拒绝启动（run 44451572217），
 billing 恢复后需重跑以补 macOS / Windows 远端 runner evidence。

## Gate 归属

本套件只覆盖 `secure_storage` 相关 required gate 的平台证据，不冒充
Fake 替代：fixture 中的 `safeStorage` 为 Electron 原生对象，adapter 为生产代码编译产物，
未注入任何 Fake。
