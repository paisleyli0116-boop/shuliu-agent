import type { ApprovalSummary, ProviderCatalog, ProviderCredentialResult, ProviderTestResult, RuntimeHealth, TaskSummary } from "../../preload/api";
import { DESKTOP_IPC_CHANNELS } from "../../preload/api";

export class DesktopIpcError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "DesktopIpcError";
  }
}

export type DesktopTaskService = {
  list(): Promise<TaskSummary[]>;
  get(taskId: string): Promise<TaskSummary | null>;
  create(input: { title: string }): Promise<TaskSummary>;
};
export type DesktopApprovalService = {
  list(): Promise<ApprovalSummary[]>;
  decide(input: { approvalId: string; decision: "approve" | "deny" }): Promise<ApprovalSummary>;
};
export type DesktopProviderService = {
  list(): Promise<ProviderCatalog>;
  testConnection(input: { providerId: string; modelId: string }): Promise<ProviderTestResult>;
  saveProfile(input: { providerId: string; modelId: string; apiKey: string }): Promise<ProviderCredentialResult>;
  replaceCredential(input: { profileId: string; apiKey: string }): Promise<ProviderCredentialResult>;
};
export type DesktopServices = {
  tasks: DesktopTaskService;
  approvals: DesktopApprovalService;
  providers: DesktopProviderService;
};
export type DesktopIpcMainLike = {
  handle(channel: string, listener: (event: unknown, ...args: unknown[]) => unknown): void;
  removeHandler?(channel: string): void;
};

const validIdentifier = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/;
const validModelIdentifier = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, expected: string[]): boolean {
  return Object.keys(value).length === expected.length && expected.every((key) => key in value);
}

function onePayload(args: unknown[], keys: string[]): Record<string, unknown> {
  if (args.length !== 1 || !record(args[0]) || !exactKeys(args[0], keys)) {
    throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
  }
  return args[0];
}

function safeFailure(error: unknown): DesktopIpcError {
  if (error instanceof DesktopIpcError) return error;
  return new DesktopIpcError("DESKTOP_OPERATION_FAILED");
}

export function registerDesktopIpc(
  ipcMain: DesktopIpcMainLike,
  services: DesktopServices,
  isTrustedSender: (event: unknown) => boolean,
): () => void {
  const handlers: Array<[string, (args: unknown[]) => Promise<unknown>]> = [
    [DESKTOP_IPC_CHANNELS.tasksList, (args) => {
      if (args.length) throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
      return services.tasks.list();
    }],
    [DESKTOP_IPC_CHANNELS.tasksGet, (args) => {
      const payload = onePayload(args, ["taskId"]);
      if (typeof payload.taskId !== "string" || !validIdentifier.test(payload.taskId)) {
        throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
      }
      return services.tasks.get(payload.taskId);
    }],
    [DESKTOP_IPC_CHANNELS.tasksCreate, (args) => {
      const payload = onePayload(args, ["title"]);
      if (typeof payload.title !== "string" || payload.title.trim().length < 2 || payload.title.length > 120) {
        throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
      }
      return services.tasks.create({ title: payload.title.trim() });
    }],
    [DESKTOP_IPC_CHANNELS.approvalsList, (args) => {
      if (args.length) throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
      return services.approvals.list();
    }],
    [DESKTOP_IPC_CHANNELS.approvalsDecide, (args) => {
      const payload = onePayload(args, ["approvalId", "decision"]);
      if (typeof payload.approvalId !== "string" || !validIdentifier.test(payload.approvalId) ||
        (payload.decision !== "approve" && payload.decision !== "deny")) {
        throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
      }
      return services.approvals.decide({ approvalId: payload.approvalId, decision: payload.decision });
    }],
    [DESKTOP_IPC_CHANNELS.providersList, (args) => {
      if (args.length) throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
      return services.providers.list();
    }],
    [DESKTOP_IPC_CHANNELS.providersTestConnection, (args) => {
      const payload = onePayload(args, ["providerId", "modelId"]);
      if (typeof payload.providerId !== "string" || !validIdentifier.test(payload.providerId) ||
        typeof payload.modelId !== "string" || !validModelIdentifier.test(payload.modelId)) {
        throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
      }
      return services.providers.testConnection({ providerId: payload.providerId, modelId: payload.modelId });
    }],
    [DESKTOP_IPC_CHANNELS.providersSaveProfile, (args) => {
      const payload = onePayload(args, ["providerId", "modelId", "apiKey"]);
      if (typeof payload.providerId !== "string" || !validIdentifier.test(payload.providerId) ||
        typeof payload.modelId !== "string" || !validModelIdentifier.test(payload.modelId) ||
        typeof payload.apiKey !== "string" || payload.apiKey.trim().length < 8 || payload.apiKey.length > 4096) {
        throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
      }
      return services.providers.saveProfile({
        providerId: payload.providerId,
        modelId: payload.modelId,
        apiKey: payload.apiKey,
      });
    }],
    [DESKTOP_IPC_CHANNELS.providersReplaceCredential, (args) => {
      const payload = onePayload(args, ["profileId", "apiKey"]);
      if (typeof payload.profileId !== "string" || !validIdentifier.test(payload.profileId) ||
        typeof payload.apiKey !== "string" || payload.apiKey.trim().length < 8 || payload.apiKey.length > 4096) {
        throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
      }
      return services.providers.replaceCredential({ profileId: payload.profileId, apiKey: payload.apiKey });
    }],
  ];

  for (const [channel, handler] of handlers) {
    ipcMain.handle(channel, async (event, ...args) => {
      if (!isTrustedSender(event)) throw new DesktopIpcError("IPC_SENDER_FORBIDDEN");
      try {
        return await handler(args);
      } catch (error) {
        throw safeFailure(error);
      }
    });
  }

  return () => {
    for (const [channel] of handlers) ipcMain.removeHandler?.(channel);
  };
}

export function runtimeHealthForIpc(health: RuntimeHealth): RuntimeHealth {
  return health;
}
