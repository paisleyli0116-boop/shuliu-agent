import type { RendererApi } from "../../preload/api";

export function getRendererApi(): RendererApi {
  const api = window.shuliu;
  if (!api) throw new Error("DESKTOP_API_UNAVAILABLE");
  return api;
}

export function safeUiError(error: unknown, fallback: string): string {
  const code = typeof error === "object" && error !== null && "code" in error && typeof error.code === "string"
    ? error.code
    : "";
  const messages: Record<string, string> = {
    IPC_SENDER_FORBIDDEN: "窗口身份校验失败，请重启应用。",
    DESKTOP_REQUEST_INVALID: "提交内容不符合要求，请检查后重试。",
    PROVIDER_CREDENTIAL_STORE_UNAVAILABLE: "系统安全存储当前不可用，凭据没有保存。",
  };
  return messages[code] ?? fallback;
}
