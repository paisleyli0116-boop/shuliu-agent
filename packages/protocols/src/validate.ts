import Ajv2020 from "ajv/dist/2020.js";
import type { ErrorObject, ValidateFunction } from "ajv";
import { readFileSync } from "node:fs";

export const protocolNames = [
  "agent",
  "skill",
  "tool",
  "workflow",
  "event",
  "worker",
  "model",
  "runtime-ipc",
] as const;

export type ProtocolName = (typeof protocolNames)[number];

export type ProtocolErrorCode =
  | "PROTOCOL_UNKNOWN"
  | "PROTOCOL_VERSION_UNSUPPORTED"
  | "SCHEMA_VALIDATION_FAILED";

export interface ProtocolValidationError {
  code: ProtocolErrorCode;
  path: string;
  schema_id: string;
}

export interface ValidationResult {
  valid: boolean;
  schema_id: string;
  errors: ProtocolValidationError[];
}

type JsonSchema = {
  $id: string;
  [key: string]: unknown;
};

const readSchema = (fileName: string): JsonSchema =>
  JSON.parse(readFileSync(new URL(`../schemas/${fileName}`, import.meta.url), "utf8")) as JsonSchema;

const commonSchema = readSchema("common-v1.schema.json");
const ajv = new Ajv2020({ allErrors: true, strict: true });
ajv.addSchema(commonSchema);

const validators = new Map<ProtocolName, ValidateFunction>();
for (const protocolName of protocolNames) {
  const schema = readSchema(`${protocolName}-v1.schema.json`);
  ajv.addSchema(schema);
  const validator = ajv.getSchema(schema.$id);
  if (!validator) {
    throw new Error(`Protocol schema did not compile: ${schema.$id}`);
  }
  validators.set(protocolName, validator);
}

const schemaIdFor = (protocolName: string): string =>
  `urn:shuliu:protocol:${encodeURIComponent(protocolName)}:v1`;

const escapeJsonPointerSegment = (value: string): string =>
  value.replaceAll("~", "~0").replaceAll("/", "~1");

const errorPath = (error: ErrorObject): string => {
  const params = error.params as { missingProperty?: unknown; additionalProperty?: unknown };
  if (typeof params.missingProperty === "string") {
    return `${error.instancePath}/${escapeJsonPointerSegment(params.missingProperty)}`;
  }
  if (typeof params.additionalProperty === "string") {
    return `${error.instancePath}/${escapeJsonPointerSegment(params.additionalProperty)}`;
  }
  return error.instancePath;
};

const failure = (
  schemaId: string,
  code: ProtocolErrorCode,
  path: string,
): ValidationResult => ({
  valid: false,
  schema_id: schemaId,
  errors: [{ code, path, schema_id: schemaId }],
});

export const validateProtocol = (protocolName: string, payload: unknown): ValidationResult => {
  const schemaId = schemaIdFor(protocolName);
  const validator = validators.get(protocolName as ProtocolName);
  if (!validator) {
    return failure(schemaId, "PROTOCOL_UNKNOWN", "/protocol_name");
  }

  if (
    payload !== null &&
    typeof payload === "object" &&
    !Array.isArray(payload) &&
    Number.isInteger((payload as Record<string, unknown>).protocol_version) &&
    (payload as Record<string, unknown>).protocol_version !== 1
  ) {
    return failure(schemaId, "PROTOCOL_VERSION_UNSUPPORTED", "/protocol_version");
  }

  if (validator(payload)) {
    return { valid: true, schema_id: schemaId, errors: [] };
  }

  return {
    valid: false,
    schema_id: schemaId,
    errors: (validator.errors ?? []).map((error) => ({
      code: "SCHEMA_VALIDATION_FAILED",
      path: errorPath(error),
      schema_id: schemaId,
    })),
  };
};
