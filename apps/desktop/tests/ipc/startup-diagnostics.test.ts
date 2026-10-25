import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

const temporaryDirectories: string[] = [];

async function getDiagnosticWriter() {
  try {
    return (await import("../../src/main/runtime/startup-diagnostics")).appendDesktopStartupDiagnostic;
  } catch {
    return undefined;
  }
}

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
});

describe("desktop startup diagnostics", () => {
  it("persists allowlisted startup facts without raw errors or paths", async () => {
    const directory = await mkdtemp(join(tmpdir(), "shuliu-desktop-startup-diagnostics-"));
    temporaryDirectories.push(directory);
    const write = await getDiagnosticWriter();
    expect(write).toBeTypeOf("function");
    if (!write) return;

    await write(directory, {
      stage: "renderer_load",
      status: "failed",
      code: "RENDERER_LOAD_FAILED",
      exitCode: 0x80000003,
      path: "C:\\Users\\private\\profile\\api-key.txt",
      error: "Bearer sk-private-marker raw exception details",
    });

    const log = await readFile(join(directory, "logs", "desktop-startup.jsonl"), "utf8");
    const record = JSON.parse(log.trim()) as Record<string, unknown>;

    expect(record).toMatchObject({
      event: "desktop_startup",
      stage: "renderer_load",
      status: "failed",
      code: "RENDERER_LOAD_FAILED",
      exit_code: 0x80000003,
    });
    expect(log).not.toMatch(/private-marker|Bearer|api-key\.txt|raw exception|Users\\private/i);
  });

  it("replaces unknown stage and error values with safe placeholders", async () => {
    const directory = await mkdtemp(join(tmpdir(), "shuliu-desktop-startup-diagnostics-"));
    temporaryDirectories.push(directory);
    const write = await getDiagnosticWriter();
    expect(write).toBeTypeOf("function");
    if (!write) return;

    await write(directory, {
      stage: "C:\\Users\\private\\secret-path",
      status: "failed",
      code: "sk-private-marker",
      exitCode: "not-an-exit-code",
    });

    const log = await readFile(join(directory, "logs", "desktop-startup.jsonl"), "utf8");
    const record = JSON.parse(log.trim()) as Record<string, unknown>;

    expect(record.stage).toBe("unknown");
    expect(record.code).toBe("UNKNOWN");
    expect(record.exit_code).toBeNull();
    expect(log).not.toMatch(/private-marker|Users\\private/i);
  });
});
