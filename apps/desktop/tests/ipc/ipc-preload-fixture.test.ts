import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";

describe("IPC Electron fixture preload bundle", () => {
  it("forwards the typed chat payload through ipcRenderer.invoke", async () => {
    const bundlePath = resolve(process.cwd(), ".ipc-test-build/preload.cjs");
    const bundle = readFileSync(bundlePath, "utf8");
    const invokeCalls: unknown[][] = [];
    let exposedApi: any;

    runInNewContext(bundle, {
      require: (name: string) => {
        if (name !== "electron") throw new Error("unexpected module");
        return {
          contextBridge: {
            exposeInMainWorld: (_key: string, api: unknown) => { exposedApi = api; },
          },
          ipcRenderer: {
            invoke: (...args: unknown[]) => {
              invokeCalls.push(args);
              return Promise.resolve({ status: "succeeded" });
            },
          },
        };
      },
    });

    const payload = {
      providerId: "deepseek",
      modelId: "deepseek/deepseek-flash",
      messages: [{ role: "user", content: "fixture payload" }],
      requestId: "11111111-2222-4333-8444-555555555555",
      traceId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      externalProcessingConfirmed: true,
    };
    await exposedApi.chat.complete(payload);

    expect(invokeCalls).toEqual([["shuliu:chat:complete", payload]]);
  });
});
