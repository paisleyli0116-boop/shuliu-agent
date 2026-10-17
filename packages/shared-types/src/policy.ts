export const POLICY_DECISIONS = ["ALLOW", "DENY", "REQUIRE_APPROVAL"] as const;

export type PolicyDecision = (typeof POLICY_DECISIONS)[number];

export const APPROVAL_STATES = ["PENDING", "APPROVED", "REJECTED", "EXPIRED"] as const;

export type ApprovalState = (typeof APPROVAL_STATES)[number];
