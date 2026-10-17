export const ERROR_CATEGORIES = [
  "VALIDATION",
  "AUTH",
  "PERMISSION",
  "POLICY",
  "APPROVAL",
  "MODEL",
  "TOOL",
  "BROWSER",
  "COMPUTER",
  "NETWORK",
  "WORKER",
  "WORKFLOW",
  "STORAGE",
  "SYSTEM",
] as const;

export type ErrorCategory = (typeof ERROR_CATEGORIES)[number];

export const ERROR_CODES = {
  MODEL_RATE_LIMIT: "MODEL_RATE_LIMIT",
  MODEL_TIMEOUT: "MODEL_TIMEOUT",
  BROWSER_NAVIGATION_TIMEOUT: "BROWSER_NAVIGATION_TIMEOUT",
  WORKER_LEASE_EXPIRED: "WORKER_LEASE_EXPIRED",
  POLICY_DENIED: "POLICY_DENIED",
  APPROVAL_EXPIRED: "APPROVAL_EXPIRED",
  WORKFLOW_STEP_FAILED: "WORKFLOW_STEP_FAILED",
} as const;

export type KnownErrorCode = (typeof ERROR_CODES)[keyof typeof ERROR_CODES];
export type ErrorCode = KnownErrorCode | (string & {});

// The Architecture Spec requires severity but does not define its value set yet.
export type ErrorSeverity = string;

export interface ShuLiuError {
  code: ErrorCode;
  category: ErrorCategory;
  message: string;
  retryable: boolean;
  recoverable: boolean;
  severity: ErrorSeverity;
  details: Record<string, unknown>;
  cause?: unknown;
  trace_id: string;
}

export type ShuLiuErrorIssueCode =
  | "INVALID_SHULIU_ERROR"
  | "MISSING_REQUIRED_FIELD"
  | "INVALID_ERROR_CODE"
  | "INVALID_ERROR_CATEGORY"
  | "INVALID_FIELD_TYPE"
  | "INVALID_FIELD_VALUE"
  | "INVALID_JSON";

export interface ShuLiuErrorIssue {
  code: ShuLiuErrorIssueCode;
  path: string;
}

export type ShuLiuErrorParseResult =
  | { valid: true; value: ShuLiuError }
  | { valid: false; errors: [ShuLiuErrorIssue] };

const categorySet: ReadonlySet<string> = new Set(ERROR_CATEGORIES);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const invalid = (code: ShuLiuErrorIssueCode, path: string): ShuLiuErrorParseResult => ({
  valid: false,
  errors: [{ code, path }],
});

const hasOwn = (value: Record<string, unknown>, key: string): boolean =>
  Object.prototype.hasOwnProperty.call(value, key);

const isNonEmptyString = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0;

export const parseShuLiuError = (input: unknown): ShuLiuErrorParseResult => {
  if (!isRecord(input)) {
    return invalid("INVALID_SHULIU_ERROR", "");
  }

  const requiredFields = [
    "code",
    "category",
    "message",
    "retryable",
    "recoverable",
    "severity",
    "details",
    "trace_id",
  ];
  for (const field of requiredFields) {
    if (!hasOwn(input, field)) {
      return invalid("MISSING_REQUIRED_FIELD", `/${field}`);
    }
  }

  if (!isNonEmptyString(input.code)) {
    return invalid("INVALID_ERROR_CODE", "/code");
  }
  if (typeof input.category !== "string" || !categorySet.has(input.category)) {
    return invalid("INVALID_ERROR_CATEGORY", "/category");
  }
  if (!isNonEmptyString(input.message)) {
    return invalid("INVALID_FIELD_VALUE", "/message");
  }
  if (typeof input.retryable !== "boolean") {
    return invalid("INVALID_FIELD_TYPE", "/retryable");
  }
  if (typeof input.recoverable !== "boolean") {
    return invalid("INVALID_FIELD_TYPE", "/recoverable");
  }
  if (!isNonEmptyString(input.severity)) {
    return invalid("INVALID_FIELD_VALUE", "/severity");
  }
  if (!isRecord(input.details)) {
    return invalid("INVALID_FIELD_TYPE", "/details");
  }
  if (!isNonEmptyString(input.trace_id)) {
    return invalid("INVALID_FIELD_VALUE", "/trace_id");
  }

  return { valid: true, value: input as unknown as ShuLiuError };
};

export const createShuLiuError = (input: unknown): ShuLiuError => {
  const result = parseShuLiuError(input);
  if (!result.valid) {
    throw new TypeError(result.errors[0].code);
  }
  return result.value;
};

export const serializeShuLiuError = (error: unknown): string =>
  JSON.stringify(createShuLiuError(error));

export const parseShuLiuErrorJson = (serialized: string): ShuLiuErrorParseResult => {
  try {
    return parseShuLiuError(JSON.parse(serialized) as unknown);
  } catch {
    return invalid("INVALID_JSON", "");
  }
};
