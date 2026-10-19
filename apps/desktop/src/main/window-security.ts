export function createSecureWindowWebPreferences(preload: string) {
  return {
    preload,
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    webSecurity: true,
  } as const;
}

export function isTrustedRendererRequest(
  event: unknown,
  trustedSender: unknown,
  rendererUrl: string,
): boolean {
  const candidate = event as { sender?: unknown; senderFrame?: { url?: string } } | null;
  if (!candidate) return false;
  return candidate.sender === trustedSender && candidate.senderFrame?.url === rendererUrl;
}
