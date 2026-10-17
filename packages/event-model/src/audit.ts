import type { RecordMetadata } from "./event.js";

export interface AuditRecord extends RecordMetadata {
  audit_id: string;
  actor_id: string;
  action: string;
  target_ref?: string;
  details?: Record<string, unknown>;
}

export interface AuditRepository {
  append_audit(record: AuditRecord): string | Promise<string>;
}
