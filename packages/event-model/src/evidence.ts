import type { RiskLevel } from "@shuliu/shared-types";
import type { RecordMetadata } from "./event.js";

export interface EvidenceRecord extends RecordMetadata {
  evidence_id: string;
  risk_level: RiskLevel;
  evidence_kind: string;
  artifact_ref: string;
  retention_policy_id: string;
  metadata: Record<string, unknown>;
}
