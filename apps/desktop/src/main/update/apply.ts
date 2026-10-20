import { UpdateVerificationError, verifyUpdatePackage, type RuntimeVersionManifest, type SignedUpdatePackage, type VerifiedUpdatePackage } from "./verify";
import { rollbackUpdate, type RollbackCompatibility } from "./rollback";

export type UpdateState =
  | "DOWNLOADED"
  | "VERIFIED"
  | "STAGED"
  | "MIGRATED"
  | "HEALTH_CHECKED"
  | "ACTIVE"
  | "ROLLED_BACK"
  | "SAFE_MODE"
  | "FAILED";

export type UpdateHealthResult = { passed: boolean; code?: string };

export type UpdateDependencies<TStaged> = {
  download(): Promise<SignedUpdatePackage>;
  installedVersions: RuntimeVersionManifest;
  trustedKeys: Readonly<Record<string, string>>;
  stage(update: VerifiedUpdatePackage): Promise<TStaged>;
  preflightMigration(staged: TStaged): Promise<{ compatible: boolean }>;
  migrate(staged: TStaged): Promise<void>;
  healthGate(staged: TStaged): Promise<UpdateHealthResult>;
  activate(staged: TStaged): Promise<void>;
  rollbackCompatibility(): Promise<RollbackCompatibility>;
  rollback(): Promise<void>;
  enterSafeMode(): Promise<void>;
};

export type UpdateOutcome = {
  state: UpdateState;
  history: UpdateState[];
  error_code?: string;
};

function safeCode(error: unknown, fallback: string): string {
  if (error instanceof UpdateVerificationError) return error.code;
  return fallback;
}

async function failClosed<TStaged>(
  dependencies: UpdateDependencies<TStaged>,
  history: UpdateState[],
  errorCode: string,
): Promise<UpdateOutcome> {
  try {
    const compatibility = await dependencies.rollbackCompatibility();
    const rollback = await rollbackUpdate({
      compatibility,
      restorePrevious: () => dependencies.rollback(),
      enterSafeMode: () => dependencies.enterSafeMode(),
    });
    history.push(rollback.state);
    return { state: rollback.state, history, error_code: rollback.error_code ?? errorCode };
  } catch {
    try {
      await dependencies.enterSafeMode();
      history.push("SAFE_MODE");
      return { state: "SAFE_MODE", history, error_code: "ROLLBACK_CHECK_FAILED" };
    } catch {
      history.push("FAILED");
      return { state: "FAILED", history, error_code: "SAFE_MODE_ENTRY_FAILED" };
    }
  }
}

export async function applyUpdate<TStaged>(
  dependencies: UpdateDependencies<TStaged>,
): Promise<UpdateOutcome> {
  const history: UpdateState[] = [];
  let downloadedPackage: SignedUpdatePackage;
  try {
    downloadedPackage = await dependencies.download();
    history.push("DOWNLOADED");
  } catch {
    history.push("FAILED");
    return { state: "FAILED", history, error_code: "DOWNLOAD_FAILED" };
  }

  const verified = verifyUpdatePackage(
    downloadedPackage,
    dependencies.trustedKeys,
    dependencies.installedVersions,
  );
  history.push("VERIFIED");

  let staged: TStaged;
  try {
    staged = await dependencies.stage(verified);
    history.push("STAGED");
  } catch {
    history.push("FAILED");
    return { state: "FAILED", history, error_code: "STAGING_FAILED" };
  }

  try {
    const migration = await dependencies.preflightMigration(staged);
    if (!migration.compatible) return await failClosed(dependencies, history, "MIGRATION_INCOMPATIBLE");

    await dependencies.migrate(staged);
    history.push("MIGRATED");

    const health = await dependencies.healthGate(staged);
    if (!health.passed) return await failClosed(dependencies, history, health.code ?? "HEALTH_GATE_FAILED");
    history.push("HEALTH_CHECKED");

    await dependencies.activate(staged);
    history.push("ACTIVE");
    return { state: "ACTIVE", history };
  } catch (error) {
    const failedStep = history.at(-1);
    const errorCode = failedStep === "STAGED"
      ? safeCode(error, "MIGRATION_FAILED")
      : failedStep === "MIGRATED"
        ? safeCode(error, "HEALTH_GATE_FAILED")
        : "ACTIVATION_FAILED";
    return await failClosed(dependencies, history, errorCode);
  }
}
