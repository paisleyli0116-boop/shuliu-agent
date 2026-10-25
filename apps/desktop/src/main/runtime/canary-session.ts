const CANARY_ENVIRONMENT_VARIABLE = "SHULIU_MODEL_CONNECTION_TEST";

/**
 * Consume the Desktop process' one-shot canary launch opt-in. This only deletes
 * the variable from this process environment; it never changes Windows settings.
 */
export function consumeProviderCanarySession(environment: NodeJS.ProcessEnv): boolean {
  const enabled = environment[CANARY_ENVIRONMENT_VARIABLE] === "1";
  delete environment[CANARY_ENVIRONMENT_VARIABLE];
  return enabled;
}
