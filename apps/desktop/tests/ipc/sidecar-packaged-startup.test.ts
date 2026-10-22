import { describe, expect, it } from "vitest";
import { createSidecarLaunchOptions } from "../../src/main/runtime/launch-options";

describe("packaged Sidecar startup budget", () => {
  it("allows bounded cold-start time for the frozen Windows runtime", () => {
    const options = createSidecarLaunchOptions(
      "C:/shuliu",
      {},
      "win32",
      {
        packaged: true,
        resourcesPath: "C:/program/resources",
        userDataPath: "C:/user-data",
      },
    );

    expect(Reflect.get(options, "handshakeTimeoutMs")).toBe(15_000);
  });

  it("keeps the source runtime on the existing default handshake budget", () => {
    const options = createSidecarLaunchOptions("C:/shuliu", {}, "win32");

    expect(Reflect.get(options, "handshakeTimeoutMs")).toBeUndefined();
  });
});
