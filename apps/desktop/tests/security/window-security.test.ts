import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { createSecureWindowWebPreferences, isTrustedRendererRequest } from "../../src/main/window-security";
import viteConfig from "../../vite.config";

describe("Electron renderer security boundary", () => {
  it("isolates the renderer and points at the controlled preload", () => {
    expect(createSecureWindowWebPreferences("/app/dist/preload/entry.cjs")).toEqual({
      preload: "/app/dist/preload/entry.cjs",
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    });
  });

  it("accepts IPC only from the expected window and exact renderer entry URL", () => {
    const trustedContents = {};
    const rendererUrl = "file:///app/dist/renderer/index.html";
    expect(isTrustedRendererRequest({ sender: trustedContents, senderFrame: { url: rendererUrl } }, trustedContents, rendererUrl)).toBe(true);
    expect(isTrustedRendererRequest({ sender: trustedContents, senderFrame: { url: "file:///tmp/attacker.html" } }, trustedContents, rendererUrl)).toBe(false);
    expect(isTrustedRendererRequest({ sender: {}, senderFrame: { url: rendererUrl } }, trustedContents, rendererUrl)).toBe(false);
  });

  it("builds relative renderer assets for file URLs and applies a restrictive CSP", () => {
    expect(viteConfig.base).toBe("./");
    const html = readFileSync(new URL("../../src/renderer/index.html", import.meta.url), "utf8");
    const policy = html.match(/http-equiv="Content-Security-Policy" content="([^"]+)"/i)?.[1];

    expect(policy).toContain("default-src 'self'");
    expect(policy).toContain("script-src 'self'");
    expect(policy).not.toContain("unsafe-eval");
  });
});
