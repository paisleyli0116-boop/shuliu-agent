export const RISK_LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;

export type RiskLevel = (typeof RISK_LEVELS)[number];

export type RiskLevelParseResult =
  | { valid: true; value: RiskLevel }
  | { valid: false; error: { code: "UNKNOWN_RISK_LEVEL"; path: "" } };

const riskLevelSet: ReadonlySet<string> = new Set(RISK_LEVELS);

export const parseRiskLevel = (value: unknown): RiskLevelParseResult => {
  if (typeof value === "string" && riskLevelSet.has(value)) {
    return { valid: true, value: value as RiskLevel };
  }

  return { valid: false, error: { code: "UNKNOWN_RISK_LEVEL", path: "" } };
};
