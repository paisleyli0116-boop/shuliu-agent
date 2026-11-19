import type { ApprovalSummary, ChatCompletionInput, ChatCompletionResult, ComputerCapability, ComputerObservation, ProviderCatalog, ProviderCredentialResult, ProviderTestResult, RuntimeHealth, TaskSummary } from "../../preload/api";
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
  testConnection(input: { providerId: string; capability: string; traceId?: string }): Promise<ProviderTestResult>;
  saveProfile(input: { providerId: string; modelId: string; apiKey: string }): Promise<ProviderCredentialResult>;
  replaceCredential(input: { profileId: string; apiKey: string }): Promise<ProviderCredentialResult>;
};
export type DesktopChatService = { complete(input: ChatCompletionInput): Promise<ChatCompletionResult> };
export type DesktopComputerService = {
  capability(): Promise<ComputerCapability>;
  observeForeground(): Promise<ComputerObservation>;
};
export type DesktopServices = {
  tasks: DesktopTaskService;
  approvals: DesktopApprovalService;
  providers: DesktopProviderService;
  chat: DesktopChatService;
  computer?: DesktopComputerService;
};
export type DesktopIpcMainLike = {
  handle(channel: string, listener: (event: unknown, ...args: unknown[]) => unknown): void;
  removeHandler?(channel: string): void;
};

const validIdentifier = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/;
const validModelIdentifier = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
const validTraceId = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const validUuid = validTraceId;
const validModelCapabilities = new Set(["fast_chat", "reasoning_high", "vision", "embedding"]);

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
      if (args.length !== 1 || !record(args[0])) throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
      const payload = args[0];
      const allowedShape = exactKeys(payload, ["providerId", "capability"]) ||
        exactKeys(payload, ["providerId", "capability", "traceId"]);
      if (!allowedShape ||
        typeof payload.providerId !== "string" || !validIdentifier.test(payload.providerId) ||
        typeof payload.capability !== "string" || !validModelCapabilities.has(payload.capability) ||
        (payload.traceId !== undefined && (typeof payload.traceId !== "string" || !validTraceId.test(payload.traceId)))) {
        throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
      }
      return services.providers.testConnection({
        providerId: payload.providerId,
        capability: payload.capability,
        ...(typeof payload.traceId === "string" ? { traceId: payload.traceId } : {}),
      });
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
    [DESKTOP_IPC_CHANNELS.chatComplete, (args) => {
      const payload = onePayload(args, ["providerId", "modelId", "messages", "requestId", "traceId", "externalProcessingConfirmed"]);
      if (typeof payload.providerId !== "string" || !validIdentifier.test(payload.providerId) ||
        typeof payload.modelId !== "string" || !validModelIdentifier.test(payload.modelId) ||
        typeof payload.requestId !== "string" || !validUuid.test(payload.requestId) ||
        typeof payload.traceId !== "string" || !validTraceId.test(payload.traceId) ||
        payload.externalProcessingConfirmed !== true || !Array.isArray(payload.messages) ||
        payload.messages.length < 1 || payload.messages.length > 20) {
        throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
      }
      let total = 0;
      const messages: Array<{ role: "user" | "assistant"; content: string }> = [];
      for (let index = 0; index < payload.messages.length; index += 1) {
        const message = payload.messages[index];
        if (!record(message) || !exactKeys(message, ["role", "content"]) ||
          message.role !== (index % 2 === 0 ? "user" : "assistant") || typeof message.content !== "string" ||
          message.content.trim().length === 0 || message.content.length > 4_000) {
          throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
        }
        total += message.content.length;
        if (total > 16_000) throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
        messages.push({ role: message.role as "user" | "assistant", content: message.content });
      }
      if (messages[messages.length - 1]?.role !== "user") throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
      return services.chat.complete({
        providerId: payload.providerId,
        modelId: payload.modelId,
        messages,
        requestId: payload.requestId,
        traceId: payload.traceId,
        externalProcessingConfirmed: true,
      });
    }],
  ];

  if (services.computer) {
    handlers.push([
      DESKTOP_IPC_CHANNELS.computerCapabilityGet,
      (args) => {
        if (args.length) throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
        return services.computer!.capability();
      },
    ]);
    handlers.push([
      DESKTOP_IPC_CHANNELS.computerObserveForeground,
      (args) => {
        if (args.length) throw new DesktopIpcError("DESKTOP_REQUEST_INVALID");
        return services.computer!.observeForeground();
      },
    ]);
  }

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
