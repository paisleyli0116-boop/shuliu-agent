import { SecretNotFoundError } from "../security/secure-storage";
import type { ProviderCatalog, ProviderCredentialResult, ProviderOption, ProviderTestResult } from "../../preload/api";

export type ProviderCredentialStore = {
  get(reference: string): Promise<string>;
  put(reference: string, value: string): Promise<void>;
};

const providerDefinitions: Array<Omit<ProviderOption, "configured" | "selected_model_id"> & { secretRef: string }> = [
  {
    id: "openai-compatible",
    name: "OpenAI Compatible",
    secretRef: "secret://os/providers/openai-compatible/credential",
    models: [
      { id: "fast-chat-demo", name: "Fast Chat · Demo", capabilities: ["fast_chat"] },
      { id: "reasoning-demo", name: "Reasoning · Demo", capabilities: ["reasoning_high"] },
      { id: "vision-demo", name: "Vision · Demo", capabilities: ["vision"] },
    ],
  },
];

export class ProviderSettingsService {
  private readonly selectedModels = new Map<string, string>();

  constructor(private readonly credentialStore: ProviderCredentialStore) {}

  async list(): Promise<ProviderCatalog> {
    const providers = await Promise.all(providerDefinitions.map(async (definition) => ({
      id: definition.id,
      name: definition.name,
      models: definition.models,
      configured: await this.hasCredential(definition.secretRef),
      selected_model_id: this.selectedModels.get(definition.id) ?? definition.models[0]?.id,
    })));
    return { providers };
  }

  async testConnection(input: { providerId: string; modelId: string }): Promise<ProviderTestResult> {
    this.assertModel(input.providerId, input.modelId);
    return {
      simulated: true,
      outcome: "success",
      message: "模拟测试已完成；没有发起真实 Provider 请求。真实连接验证将在 Task 28 接入。",
    };
  }

  async saveProfile(input: { providerId: string; modelId: string; apiKey: string }): Promise<ProviderCredentialResult> {
    const definition = this.assertModel(input.providerId, input.modelId);
    await this.credentialStore.put(definition.secretRef, input.apiKey);
    this.selectedModels.set(input.providerId, input.modelId);
    return { profile_id: input.providerId, configured: true, storage: "OS_SECURE_STORAGE" };
  }

  async replaceCredential(input: { profileId: string; apiKey: string }): Promise<ProviderCredentialResult> {
    const definition = providerDefinitions.find((item) => item.id === input.profileId);
    if (!definition) throw new Error("provider profile is not available");
    await this.credentialStore.put(definition.secretRef, input.apiKey);
    return { profile_id: input.profileId, configured: true, storage: "OS_SECURE_STORAGE" };
  }

  private async hasCredential(reference: string): Promise<boolean> {
    try {
      await this.credentialStore.get(reference);
      return true;
    } catch (error) {
      if (error instanceof SecretNotFoundError) return false;
      throw error;
    }
  }

  private assertModel(providerId: string, modelId: string) {
    const definition = providerDefinitions.find((item) => item.id === providerId);
    if (!definition || !definition.models.some((model) => model.id === modelId)) {
      throw new Error("provider or model is not available");
    }
    return definition;
  }
}
