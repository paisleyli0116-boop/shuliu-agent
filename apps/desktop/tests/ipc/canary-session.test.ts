import { describe, expect, it } from "vitest";
import { resolve } from "node:path";
import { createSidecarLaunchOptions } from "../../src/main/runtime/launch-options";

async function loadCanarySession() {
  const module = await import("../../src/main/runtime/canary-session").catch(() => null);
  expect(module?.consumeProviderCanarySession).toBeTypeOf("function");
  return module!.consumeProviderCanarySession;
}

describe("process-scoped Provider canary session", () => {
  it("consumes only the exact explicit opt-in and removes it from the Main process environment", async () => {
    const consume = await loadCanarySession();
    const ordinaryEnvironment: NodeJS.ProcessEnv = { PATH: "safe-path" };
    const explicitEnvironment: NodeJS.ProcessEnv = {
      PATH: "safe-path",
      SHULIU_MODEL_CONNECTION_TEST: "1",
    };
    const invalidEnvironment: NodeJS.ProcessEnv = {
      PATH: "safe-path",
      SHULIU_MODEL_CONNECTION_TEST: "true",
    };

    expect(consume(ordinaryEnvironment)).toBe(false);
    expect(ordinaryEnvironment.SHULIU_MODEL_CONNECTION_TEST).toBeUndefined();
    expect(consume(explicitEnvironment)).toBe(true);
    expect(explicitEnvironment.SHULIU_MODEL_CONNECTION_TEST).toBeUndefined();
    expect(consume(invalidEnvironment)).toBe(false);
    expect(invalidEnvironment.SHULIU_MODEL_CONNECTION_TEST).toBeUndefined();
  });

  it("passes the consumed one-shot opt-in to the packaged Sidecar without retaining it in Main", async () => {
    const consume = await loadCanarySession();
    const mainEnvironment: NodeJS.ProcessEnv = {
      PATH: "safe-path",
      SHULIU_MODEL_CONNECTION_TEST: "1",
    };
    const enabled = consume(mainEnvironment);
    const sidecarEnvironment: NodeJS.ProcessEnv = { ...mainEnvironment };
    if (enabled) sidecarEnvironment.SHULIU_MODEL_CONNECTION_TEST = "1";
    const launch = createSidecarLaunchOptions(
      resolve("/installed/resources/app.asar"),
      sidecarEnvironment,
      "win32",
      { packaged: true, resourcesPath: resolve("/installed/resources"), userDataPath: resolve("/profile/shuliu") },
    );

    expect(enabled).toBe(true);
    expect(mainEnvironment.SHULIU_MODEL_CONNECTION_TEST).toBeUndefined();
    expect(launch.env.SHULIU_MODEL_CONNECTION_TEST).toBe("1");
  });
});
