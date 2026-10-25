import { describe, expect, it } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const read = (path: string) => readFileSync(join(root, path), "utf8");

describe("Foundation repository layout", () => {
  it.each([
    "apps/desktop",
    "services/cloud-api",
    "runtimes/python-worker",
    "packages/protocols",
    "packages/shared-types",
    "packages/event-model",
    "packages/testing",
    "skills/examples",
    "tests/contract",
    "tests/integration",
    "tests/e2e",
    "docs",
  ])("contains the %s directory", (path) => {
    expect(existsSync(join(root, path))).toBe(true);
  });

  it.each([
    "pnpm-workspace.yaml",
    "package.json",
    "pnpm-lock.yaml",
    "tsconfig.base.json",
    ".node-version",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    ".gitignore",
    ".github/workflows/ci.yml",
    ".github/workflows/windows-validation.yml",
    "apps/desktop/package.json",
    "services/cloud-api/pyproject.toml",
    "runtimes/python-worker/pyproject.toml",
    "packages/protocols/package.json",
    "packages/shared-types/package.json",
    "packages/event-model/package.json",
    "packages/testing/package.json",
    "skills/examples/README.md",
  ])("tracks the %s skeleton file", (path) => {
    expect(existsSync(join(root, path))).toBe(true);
  });

  it("pins supported runtimes and exposes the planned task scripts", () => {
    const manifest = JSON.parse(read("package.json"));
    expect(manifest.packageManager).toBe("pnpm@11.28.6");
    expect(manifest.engines.node).toContain(">=22.13.0");
    for (const script of [
      "test",
      "typecheck",
      "test:unit",
      "test:contract",
      "test:integration",
      "test:migration",
      "test:security",
      "test:ipc",
      "test:e2e",
      "test:windows",
    ]) {
      expect(manifest.scripts).toHaveProperty(script);
    }
    expect(read(".node-version").trim()).toBe("22.23.3");
    expect(read(".python-version").trim()).toBe("3.11");
    expect(read("pyproject.toml")).toContain('requires-python = ">=3.11,<3.12"');
    expect(read("services/cloud-api/pyproject.toml")).toContain('name = "shuliu-cloud-api"');
    expect(read("runtimes/python-worker/pyproject.toml")).toContain('name = "shuliu-python-runtime"');
  });

  it("keeps release signing credentials out of branch validation", () => {
    const windows = read(".github/workflows/windows-validation.yml");
    expect(windows).not.toContain("secrets.SHULIU_UPDATE_SIGNING_PRIVATE_KEY");
    expect(windows).not.toContain("secrets.SHULIU_UPDATE_SIGNING_KEY_ID");
    expect(windows).toContain('$env:SHULIU_UPDATE_SIGNING_KEY_ID = "ci-ephemeral"');
  });

  it("keeps generic and Windows-only CI validation in separate workflows", () => {
    const generic = read(".github/workflows/ci.yml");
    const windows = read(".github/workflows/windows-validation.yml");
    expect(generic).toContain("ubuntu-latest");
    expect(generic).toContain("macos-latest");
    expect(generic).not.toContain("windows-latest");
    expect(windows).toContain("windows-latest");
    expect(windows).toMatch(/pyinstaller/i);
    expect(windows).toMatch(/nsis/i);
    expect(windows).toContain("installer");
    expect(generic).toContain("pnpm/action-setup@v4");
    expect(generic).not.toMatch(/version:\s*11\.28\.6/);
    expect(generic).toContain("astral-sh/setup-uv@v10");
    expect(generic).toContain("version-file: pyproject.toml");
    expect(read("pyproject.toml")).toContain('required-version = "==0.12.20"');
  });

  it("runs durable model usage integration against both PostgreSQL CI versions", () => {
    const postgresJob = read(".github/workflows/ci.yml").split("  postgres-migration:")[1];
    expect(postgresJob).toContain("postgres: [17, 18]");
    expect(postgresJob).toContain(
      "uv run --locked --all-packages pytest tests/integration/test_model_usage_persistence.py -q",
    );
  });
});
