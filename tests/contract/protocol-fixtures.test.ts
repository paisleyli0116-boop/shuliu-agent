import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { validateProtocol, type ProtocolName } from "../../packages/protocols/src/index.js";

const fixtureRoot = join(process.cwd(), "tests/contract/fixtures/protocols");
const protocolNames: ProtocolName[] = [
  "agent",
  "skill",
  "tool",
  "workflow",
  "event",
  "worker",
  "model",
  "runtime-ipc",
];

const readFixture = (protocol: ProtocolName, validity: "valid" | "invalid") =>
  JSON.parse(readFileSync(join(fixtureRoot, `${protocol}.${validity}.json`), "utf8"));

describe("shared protocol fixtures", () => {
  it.each(protocolNames)("accepts the valid %s fixture", (protocol) => {
    expect(validateProtocol(protocol, readFixture(protocol, "valid")).valid).toBe(true);
  });

  it.each(protocolNames)("rejects the invalid %s fixture", (protocol) => {
    const result = validateProtocol(protocol, readFixture(protocol, "invalid"));
    expect(result.valid).toBe(false);
    expect(result.errors.length).toBeGreaterThan(0);
    expect(result.errors.every((error) => error.schema_id === `urn:shuliu:protocol:${protocol}:v1`)).toBe(true);
  });
});
