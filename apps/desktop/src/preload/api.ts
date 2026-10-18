export const RUNTIME_HEALTH_CHANNEL = "shuliu:runtime:health:get";

export type RendererApi = {
  runtime: {
    health: {
      get(): Promise<{ status: string; [key: string]: unknown }>;
    };
  };
};

export type ContextBridgePort = { exposeInMainWorld(key: string, api: RendererApi): void };
export type IpcRendererPort = { invoke(channel: string): Promise<{ status: string; [key: string]: unknown }> };

export function createRendererApi(invoke: IpcRendererPort["invoke"]): RendererApi {
  return Object.freeze({
    runtime: Object.freeze({
      health: Object.freeze({
        get: () => invoke(RUNTIME_HEALTH_CHANNEL),
      }),
    }),
  });
}

export function exposeRendererApi(contextBridge: ContextBridgePort, ipcRenderer: IpcRendererPort): void {
  contextBridge.exposeInMainWorld("shuliu", createRendererApi((channel) => ipcRenderer.invoke(channel)));
}
