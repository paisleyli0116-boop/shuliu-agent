import type { RendererApi } from "../../preload/api";

export function createProviderSettingsClient(api: RendererApi) {
  return {
    list: () => api.providers.list(),
    testConnection: (input: { providerId: string; capability: string }) => api.providers.testConnection(input),
    saveProfile: (input: { providerId: string; modelId: string; apiKey: string }) => api.providers.saveProfile(input),
    replaceCredential: (input: { profileId: string; apiKey: string }) => api.providers.replaceCredential(input),
  };
}
