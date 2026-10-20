export type RollbackCompatibility = {
  protocol: boolean;
  database: boolean;
};

export type RollbackResult = {
  state: "ROLLED_BACK" | "SAFE_MODE" | "FAILED";
  error_code?: string;
};

export async function rollbackUpdate(input: {
  compatibility: RollbackCompatibility;
  restorePrevious(): Promise<void>;
  enterSafeMode(): Promise<void>;
}): Promise<RollbackResult> {
  if (!input.compatibility.protocol || !input.compatibility.database) {
    try {
      await input.enterSafeMode();
      return { state: "SAFE_MODE", error_code: "ROLLBACK_INCOMPATIBLE" };
    } catch {
      return { state: "FAILED", error_code: "SAFE_MODE_ENTRY_FAILED" };
    }
  }

  try {
    await input.restorePrevious();
    return { state: "ROLLED_BACK" };
  } catch {
    try {
      await input.enterSafeMode();
      return { state: "SAFE_MODE", error_code: "ROLLBACK_FAILED" };
    } catch {
      return { state: "FAILED", error_code: "SAFE_MODE_ENTRY_FAILED" };
    }
  }
}
