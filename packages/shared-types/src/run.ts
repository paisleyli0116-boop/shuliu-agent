import type { OrganizationId } from "./identity.js";
import type { TaskId } from "./task.js";

export type AgentRunId = string & { readonly __agentRunId: unique symbol };

export interface AgentRunRef {
  agent_run_id: AgentRunId;
  task_id: TaskId;
  organization_id: OrganizationId;
}
