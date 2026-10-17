import type { OrganizationId } from "./identity.js";

export type TaskId = string & { readonly __taskId: unique symbol };

export interface TaskRef {
  task_id: TaskId;
  organization_id: OrganizationId;
}
