export const DESKTOP_IPC_CHANNELS = Object.freeze({
  runtimeHealthGet: "shuliu:runtime:health:get",
  tasksList: "shuliu:tasks:list",
  tasksGet: "shuliu:tasks:get",
  tasksCreate: "shuliu:tasks:create",
  approvalsList: "shuliu:approvals:list",
  approvalsDecide: "shuliu:approvals:decide",
  providersList: "shuliu:providers:list",
  providersTestConnection: "shuliu:providers:test-connection",
  providersSaveProfile: "shuliu:providers:save-profile",
  providersReplaceCredential: "shuliu:providers:replace-credential",
  chatComplete: "shuliu:chat:complete",
  computerCapabilityGet: "shuliu:computer:capability:get",
  computerObserveForeground: "shuliu:computer:observe-foreground",
});

export type RuntimeHealth = {
  status: "healthy" | "degraded" | "unhealthy" | "safe_mode";
  code?: string;
  checked_at?: string;
};

export type TaskSummary = {
  task_id: string;
  title: string;
  status: "QUEUED" | "RUNNING" | "WAITING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  created_at: string;
};

export type ApprovalSummary = {
  approval_id: string;
  action: string;
  risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  status: "PENDING" | "APPROVED" | "DENIED" | "EXPIRED";
};

export type ProviderModel = { id: string; name: string; capabilities: string[] };
export type ProviderOption = {
  id: string;
  name: string;
  models: ProviderModel[];
  configured?: boolean;
  enabled?: boolean;
  selected_model_id?: string;
};
export type ProviderCatalog = { providers: ProviderOption[] };
export type ProviderRequestStatus = "not_sent" | "http_response_received" | "unknown";
export type ProviderFailureStage =
  | "none" | "preflight" | "canary_gate" | "provider_profile" | "secret_resolution" | "gateway" | "policy"
  | "budget" | "provider_setup" | "provider_transport" | "provider_http" | "provider_response" | "sidecar_ipc" | "unknown";
export type ProviderTestResult = {
  status: "connected" | "blocked" | "failed";
  provider_id: string;
  model: string;
  capability: string;
  latency_ms: number;
  trace_id: string;
  safe_error_code: string | null;
  request_status: ProviderRequestStatus;
  failure_stage: ProviderFailureStage;
  http_status: number | null;
};
export type ProviderCredentialResult = {
  profile_id: string;
  configured: true;
  storage: "OS_SECURE_STORAGE" | "CLOUD_SECRET_MANAGER";
};
export type ChatMessage = { role: "user" | "assistant"; content: string };
export type ChatCompletionInput = {
  providerId: string;
  modelId: string;
  messages: ChatMessage[];
  requestId: string;
  traceId: string;
  externalProcessingConfirmed: true;
};
export type ChatCompletionResult = {
  status: "succeeded" | "blocked" | "failed";
  provider_id: string;
  model: string;
  trace_id: string;
  safe_error_code: string | null;
  request_status: ProviderRequestStatus;
  failure_stage: ProviderFailureStage;
  http_status: number | null;
  response: string | null;
  usage: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
  cost: number | null;
  latency_ms: number;
};
export type ComputerCapability = {
  status: "available" | "unavailable" | "unknown";
  safe_error_code: string | null;
  trace_id: string;
};
export type ComputerObservation = {
  status: "observed" | "blocked";
  title: string | null;
  elements: Array<{ role: string; name: string; sensitive: boolean; enabled: boolean }>;
  safe_error_code: string | null;
  trace_id: string;
};

export type RendererApi = {
  runtime: { health: { get(): Promise<RuntimeHealth> } };
  tasks: {
    list(): Promise<TaskSummary[]>;
    get(taskId: string): Promise<TaskSummary | null>;
    create(input: { title: string }): Promise<TaskSummary>;
  };
  approvals: {
    list(): Promise<ApprovalSummary[]>;
    decide(input: { approvalId: string; decision: "approve" | "deny" }): Promise<ApprovalSummary>;
  };
  providers: {
    list(): Promise<ProviderCatalog>;
    testConnection(input: { providerId: string; capability: string; traceId: string }): Promise<ProviderTestResult>;
    saveProfile(input: { providerId: string; modelId: string; apiKey: string }): Promise<ProviderCredentialResult>;
    replaceCredential(input: { profileId: string; apiKey: string }): Promise<ProviderCredentialResult>;
  };
  chat: { complete(input: ChatCompletionInput): Promise<ChatCompletionResult> };
  computer: {
    capability(): Promise<ComputerCapability>;
    observeForeground(): Promise<ComputerObservation>;
  };
};

export type ContextBridgePort = { exposeInMainWorld(key: string, api: RendererApi): void };
export type IpcRendererPort = {
  invoke(channel: string, ...args: unknown[]): Promise<unknown>;
};

export function createRendererApi(invoke: IpcRendererPort["invoke"]): RendererApi {
  const call = <T>(channel: string, ...args: unknown[]) => invoke(channel, ...args) as Promise<T>;
  return Object.freeze({
    runtime: Object.freeze({
      health: Object.freeze({
        get: () => call<RuntimeHealth>(DESKTOP_IPC_CHANNELS.runtimeHealthGet),
      }),
    }),
    tasks: Object.freeze({
      list: () => call<TaskSummary[]>(DESKTOP_IPC_CHANNELS.tasksList),
      get: (taskId: string) => call<TaskSummary | null>(DESKTOP_IPC_CHANNELS.tasksGet, { taskId }),
      create: (input: { title: string }) => call<TaskSummary>(DESKTOP_IPC_CHANNELS.tasksCreate, input),
    }),
    approvals: Object.freeze({
      list: () => call<ApprovalSummary[]>(DESKTOP_IPC_CHANNELS.approvalsList),
      decide: (input: { approvalId: string; decision: "approve" | "deny" }) =>
        call<ApprovalSummary>(DESKTOP_IPC_CHANNELS.approvalsDecide, input),
    }),
    providers: Object.freeze({
      list: () => call<ProviderCatalog>(DESKTOP_IPC_CHANNELS.providersList),
      testConnection: (input: { providerId: string; capability: string }) =>
        call<ProviderTestResult>(DESKTOP_IPC_CHANNELS.providersTestConnection, input),
      saveProfile: (input: { providerId: string; modelId: string; apiKey: string }) =>
        call<ProviderCredentialResult>(DESKTOP_IPC_CHANNELS.providersSaveProfile, input),
      replaceCredential: (input: { profileId: string; apiKey: string }) =>
        call<ProviderCredentialResult>(DESKTOP_IPC_CHANNELS.providersReplaceCredential, input),
    }),
    chat: Object.freeze({
      complete: (input: ChatCompletionInput) =>
        call<ChatCompletionResult>(DESKTOP_IPC_CHANNELS.chatComplete, input),
    }),
    computer: Object.freeze({
      capability: () => call<ComputerCapability>(DESKTOP_IPC_CHANNELS.computerCapabilityGet),
      observeForeground: () => call<ComputerObservation>(DESKTOP_IPC_CHANNELS.computerObserveForeground),
    }),
  });
}

export function exposeRendererApi(contextBridge: ContextBridgePort, ipcRenderer: IpcRendererPort): void {
  contextBridge.exposeInMainWorld("shuliu", createRendererApi((channel, ...args) => ipcRenderer.invoke(channel, ...args)));
}
