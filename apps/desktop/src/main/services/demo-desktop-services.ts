import { randomUUID } from "node:crypto";
import type { ApprovalSummary, TaskSummary } from "../../preload/api";
import type { ProviderCredentialStore } from "./provider-settings";
import { ProviderSettingsService } from "./provider-settings";

const DEMO_APPROVAL_ID = "approval-demo-1";

export function createDemoDesktopServices(credentialStore: ProviderCredentialStore) {
  const tasks: TaskSummary[] = [];
  const approvals: ApprovalSummary[] = [
    {
      approval_id: DEMO_APPROVAL_ID,
      action: "读取受限 Knowledge 资源",
      risk_level: "HIGH",
      status: "PENDING",
    },
  ];

  return {
    tasks: {
      async list() {
        return tasks.map((task) => ({ ...task }));
      },
      async get(taskId: string) {
        return tasks.find((task) => task.task_id === taskId) ?? null;
      },
      async create(input: { title: string }) {
        const task: TaskSummary = {
          task_id: randomUUID(),
          title: input.title,
          status: "QUEUED",
          created_at: new Date().toISOString(),
        };
        tasks.unshift(task);
        return { ...task };
      },
    },
    approvals: {
      async list() {
        return approvals.map((approval) => ({ ...approval }));
      },
      async decide(input: { approvalId: string; decision: "approve" | "deny" }) {
        const approval = approvals.find((item) => item.approval_id === input.approvalId);
        if (!approval || approval.status !== "PENDING") throw new Error("approval is not pending");
        approval.status = input.decision === "approve" ? "APPROVED" : "DENIED";
        return { ...approval };
      },
    },
    providers: new ProviderSettingsService(credentialStore),
  };
}
