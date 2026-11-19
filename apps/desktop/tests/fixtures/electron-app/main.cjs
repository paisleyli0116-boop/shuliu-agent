const path = require("node:path");
const { app, BrowserWindow, ipcMain } = require("electron");
const { SidecarSupervisor } = require("../../../.ipc-test-build/main/runtime/supervisor.js");
const { registerRuntimeIpc } = require("../../../.ipc-test-build/main/runtime/ipc.js");
const { registerDesktopIpc } = require("../../../.ipc-test-build/main/ipc/desktop-ipc.js");
const { RuntimeChatService } = require("../../../.ipc-test-build/main/ipc/chat.js");

const python = process.env.SHULIU_TEST_PYTHON_EXECUTABLE;
const sidecar = process.env.SHULIU_TEST_SIDECAR;
const cwd = process.env.INIT_CWD || process.cwd();
const logs = [];
const waitFor = async (predicate, timeoutMs = 8_000) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  return predicate();
};
const captureCode = async (operation) => {
  try {
    await operation();
    return "NO_ERROR";
  } catch (error) {
    return typeof error?.code === "string" ? error.code : "UNKNOWN_ERROR";
  }
};

app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  let supervisor;
  let incompatible;
  let window;
  let stage = "startup";
  try {
    stage = "sidecar-handshake";
    supervisor = new SidecarSupervisor({
      command: python,
      args: [sidecar],
      cwd,
      env: { ...process.env, SHULIU_SECRET_SENTINEL: "sensitive-ipc-test-value" },
      onLog: (entry) => logs.push(entry),
    });
    await supervisor.start();
    stage = "health";
    const health = await supervisor.health();
    stage = "concurrency";
    const concurrent = await Promise.all([
      supervisor.request("test.echo", { value: "one" }),
      supervisor.request("test.echo", { value: "two" }),
    ]);
    const environment = await supervisor.request("test.environment", {});
    stage = "timeout";
    const timeoutCode = await captureCode(() => supervisor.request("test.delay", { seconds: 1 }, { timeoutMs: 25 }));

    stage = "cancellation";
    const controller = new AbortController();
    const cancellation = supervisor.request("test.delay", { seconds: 1 }, { timeoutMs: 2_000, signal: controller.signal });
    setTimeout(() => controller.abort(), 25);
    const cancellationCode = await captureCode(() => cancellation);
    stage = "crash-and-restart";
    const crashCode = await captureCode(() => supervisor.request("test.crash", {}));
    await waitFor(() => supervisor.state === "ready" || supervisor.state === "safe_mode");
    const restartedHealth = supervisor.state === "ready" ? await supervisor.health() : { status: "safe_mode" };

    stage = "renderer";
    window = new BrowserWindow({
      show: false,
      webPreferences: {
        preload: path.resolve(__dirname, "../../../.ipc-test-build/preload.cjs"),
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
      },
    });
    const isTrustedSender = (event) => {
      const candidate = event;
      return candidate?.sender === window.webContents && typeof candidate?.senderFrame?.url === "string" && candidate.senderFrame.url.startsWith("file:");
    };
    registerRuntimeIpc(ipcMain, supervisor, isTrustedSender);
    registerDesktopIpc(ipcMain, {
      tasks: { list: async () => [], get: async () => null, create: async () => null },
      approvals: { list: async () => [], decide: async () => null },
      providers: { list: async () => ({ providers: [] }), testConnection: async () => null, saveProfile: async () => null, replaceCredential: async () => null },
      chat: new RuntimeChatService((method, params, options) => supervisor.request(method, params, options)),
    }, isTrustedSender);
    await window.loadFile(path.join(__dirname, "index.html"));
    const renderer = await window.webContents.executeJavaScript(`(async () => {
      const health = await window.shuliu.runtime.health.get();
      return {
        status: health.status,
        nodeIntegration: typeof require === "function" || typeof process !== "undefined",
        arbitraryRequest: typeof window.shuliu.runtime.request === "function" || typeof window.shuliu.runtime.exec === "function"
      };
    })()`);
    stage = "fake-provider-chat-roundtrip";
    const chat = await window.webContents.executeJavaScript(`window.shuliu.chat.complete({
      providerId: "fixture",
      modelId: "fixture/chat",
      messages: [{ role: "user", content: "fixture prompt" }],
      requestId: "11111111-2222-4333-8444-555555555555",
      traceId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      externalProcessingConfirmed: true,
    })`);

    stage = "safe-mode-after-repeated-crashes";
    const repeatedCrashCode = await captureCode(() => supervisor.request("test.crash", {}));
    await waitFor(() => supervisor.state === "safe_mode");

    stage = "protocol-mismatch";
    incompatible = new SidecarSupervisor({
      command: python,
      args: [sidecar, "incompatible"],
      cwd,
      env: process.env,
      maxRestarts: 0,
    });
    const protocolMismatchCode = await captureCode(() => incompatible.start());
    const report = {
      health,
      chat,
      concurrent: concurrent.map((item) => item.value),
      sidecarSecretVariablePresent: environment.sensitive_variable_present,
      sidecarUtf8: environment.stdin_encoding === "utf-8" && environment.stdout_encoding === "utf-8",
      timeoutCode,
      cancellationCode,
      crashCode,
      restartedHealth,
      renderer,
      repeatedCrashCode,
      repeatedCrashState: supervisor.state,
      protocolMismatchCode,
      electronNodeVersion: process.versions.node,
      secretAbsentFromLogs: !JSON.stringify(logs).includes("fixture-secret-marker"),
      safeLogFieldsOnly: logs.every((item) => Object.keys(item).sort().join(",") === "code,event,level"),
    };
    process.stdout.write(`SHULIU_IPC_RESULT:${JSON.stringify(report)}\n`);
  } catch (error) {
    process.stdout.write(`SHULIU_IPC_RESULT:${JSON.stringify({ errorCode: typeof error?.code === "string" ? error.code : "UNKNOWN_ERROR", errorName: typeof error?.name === "string" ? error.name : "UNKNOWN", stage, state: supervisor?.state })}\n`);
    process.exitCode = 1;
  } finally {
    if (window && !window.isDestroyed()) window.destroy();
    if (incompatible) await incompatible.close();
    if (supervisor) await supervisor.close();
    app.quit();
  }
});
