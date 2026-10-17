function normalizeKey(key: string): string {
  return key.toLowerCase().replace(/[-_\s]/g, "");
}

function isSensitiveKey(key: string): boolean {
  const normalized = normalizeKey(key);
  if (normalized === "secretref") return false;
  return normalized === "token" ||
    /(authorization|cookie|password|secret|apikey|accesstoken|refreshtoken)/i.test(normalized);
}

export function redact(payload: unknown): unknown {
  if (Array.isArray(payload)) {
    return payload.map((item) => redact(item));
  }
  if (payload === null || typeof payload !== "object") {
    return payload;
  }

  const safe: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(payload)) {
    if (isSensitiveKey(key)) continue;
    safe[key] = redact(value);
  }
  return safe;
}
