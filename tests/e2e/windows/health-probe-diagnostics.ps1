$script:HealthReportStatuses = @('healthy', 'degraded', 'unhealthy', 'safe_mode')
$script:HealthReportCodes = @(
  'DATABASE_SCHEMA_UNAVAILABLE',
  'DESKTOP_HEALTH_PROBE_FAILED',
  'HEALTH_REPORT_INVALID',
  'HEALTH_PROBE_TIMEOUT',
  'HEALTH_REPORT_MISSING',
  'PRELOAD_UNAVAILABLE',
  'RUNTIME_SAFE_MODE',
  'RUNTIME_UNHEALTHY'
)
$script:RuntimeStates = @('stopped', 'ready', 'starting', 'safe_mode', 'restarting', 'unhealthy', 'unknown')
$script:RuntimeStartupStages = @('not_started', 'sidecar_spawn', 'ipc_handshake', 'ready', 'sidecar_exit', 'unknown')
$script:RuntimeHandshakeStates = @('not_started', 'pending', 'succeeded', 'failed', 'unknown')
$script:RuntimeFailureStages = @('none', 'sidecar_spawn', 'ipc_handshake', 'sidecar_exit', 'unknown')
$script:RuntimeSignals = @('SIGABRT', 'SIGALRM', 'SIGBUS', 'SIGFPE', 'SIGHUP', 'SIGILL', 'SIGINT', 'SIGKILL', 'SIGPIPE', 'SIGQUIT', 'SIGSEGV', 'SIGTERM', 'SIGTRAP', 'SIGUSR1', 'SIGUSR2', 'unknown')
$script:RuntimeErrorCodes = @(
  'DATABASE_SCHEMA_INCOMPATIBLE', 'IPC_CHANNEL_CLOSED', 'IPC_DIRECTION_INVALID',
  'IPC_FRAME_INVALID', 'IPC_FRAME_TOO_LARGE', 'IPC_FRAME_TRUNCATED', 'IPC_HANDSHAKE_FAILED',
  'IPC_JSON_INVALID', 'IPC_REQUEST_CANCELLED', 'IPC_REQUEST_FAILED', 'IPC_REQUEST_INVALID',
  'IPC_REQUEST_TIMEOUT', 'IPC_RESPONSE_TRACE_MISMATCH', 'IPC_RESPONSE_UNMATCHED',
  'IPC_UTF8_INVALID', 'IPC_WRITE_FAILED', 'MODEL_PROVIDER_CONFIGURATION_INVALID',
  'PROTOCOL_VERSION_UNSUPPORTED', 'RUNTIME_SAFE_MODE', 'SCHEMA_VALIDATION_FAILED',
  'SIDECAR_EXITED', 'SIDECAR_IO_FAILED', 'SIDECAR_SPAWN_FAILED', 'UNKNOWN'
)
$script:RuntimeLogEventCodes = @{
  'runtime_bootstrap_failed' = @('DATABASE_SCHEMA_INCOMPATIBLE')
  'provider_configuration_failed' = @('MODEL_PROVIDER_CONFIGURATION_INVALID')
  'runtime_request_failed' = @('IPC_REQUEST_FAILED')
  'runtime_frame_rejected' = @('IPC_FRAME_TOO_LARGE', 'IPC_FRAME_INVALID', 'IPC_DIRECTION_INVALID')
}

function Get-HealthDiagnosticValue([object]$Value, [string[]]$AllowedValues) {
  if ($Value -is [string] -and $AllowedValues -ccontains $Value) { return $Value }
  return 'unknown'
}

function Get-HealthDiagnosticExitCode([object]$Value) {
  if ($null -eq $Value) { return 'none' }
  $parsed = 0L
  if ([long]::TryParse([string]$Value, [ref]$parsed) -and $parsed -ge -2147483648 -and $parsed -le 4294967295) {
    return [string]$parsed
  }
  return 'unknown'
}

function Get-HealthDiagnosticOptionalValue([object]$Value, [string[]]$AllowedValues) {
  if ($null -eq $Value) { return 'none' }
  if ($Value -is [string] -and $AllowedValues -ccontains $Value) { return $Value }
  return 'unknown'
}

function Get-HealthDiagnosticSafeCode([object]$Value) {
  if ($null -eq $Value) { return 'none' }
  if ($Value -is [string] -and $script:RuntimeErrorCodes -ccontains $Value) { return $Value }
  return 'UNKNOWN'
}

function Get-HealthDiagnosticLogEvent([object]$Value) {
  if ($null -eq $Value) { return 'none' }
  if ($Value -is [string] -and $script:RuntimeLogEventCodes.ContainsKey($Value)) { return $Value }
  return 'UNKNOWN'
}

function Get-HealthDiagnosticLogCode([object]$Event, [object]$Code) {
  if ($null -eq $Event -and $null -eq $Code) { return 'none' }
  if ($Event -is [string] -and $Code -is [string] -and
      $script:RuntimeLogEventCodes.ContainsKey($Event) -and
      $script:RuntimeLogEventCodes[$Event] -ccontains $Code) { return $Code }
  return 'UNKNOWN'
}

function Get-HealthDiagnosticBoolean([object]$Value) {
  if ($Value -is [bool]) { return ([string]$Value).ToLowerInvariant() }
  return 'unknown'
}

function Get-HealthDiagnosticSchema([object]$Value) {
  if ($null -eq $Value) { return 'none' }
  if ($Value -is [string] -and $Value -match '^\d{1,3}(?:\.\d{1,3}){0,3}$') { return $Value }
  return 'unknown'
}

function Format-HealthProbeSummary([string]$Name, [object]$ExitCode, [object]$Report) {
  $stage = Get-HealthDiagnosticValue $Name @('initial-health', 'safe-mode-health')
  $processExit = Get-HealthDiagnosticExitCode $ExitCode
  $status = Get-HealthDiagnosticValue $Report.status $script:HealthReportStatuses
  $reportCode = Get-HealthDiagnosticOptionalValue $Report.code $script:HealthReportCodes
  $preloadAvailable = Get-HealthDiagnosticBoolean $Report.preload_available
  $databaseSchema = Get-HealthDiagnosticSchema $Report.database_schema
  $runtime = $Report.runtime_diagnostics
  $runtimeState = Get-HealthDiagnosticValue $runtime.runtime_state $script:RuntimeStates
  $startupStage = Get-HealthDiagnosticValue $runtime.startup_stage $script:RuntimeStartupStages
  $handshake = Get-HealthDiagnosticValue $runtime.ipc_handshake_status $script:RuntimeHandshakeStates
  $failureStage = Get-HealthDiagnosticValue $runtime.failure_stage $script:RuntimeFailureStages
  $sidecarExit = Get-HealthDiagnosticExitCode $runtime.sidecar_exit_code
  $sidecarSignal = Get-HealthDiagnosticOptionalValue $runtime.sidecar_signal $script:RuntimeSignals
  $safeErrorCode = Get-HealthDiagnosticSafeCode $runtime.safe_error_code
  $logEvent = Get-HealthDiagnosticLogEvent $runtime.sidecar_log_event
  $logCode = Get-HealthDiagnosticLogCode $runtime.sidecar_log_event $runtime.sidecar_log_code

  return "INSTALLED_APP_HEALTH_PROBE stage=$stage status=$status process_exit_code=$processExit report_code=$reportCode preload_available=$preloadAvailable database_schema=$databaseSchema runtime_state=$runtimeState startup_stage=$startupStage ipc_handshake_status=$handshake failure_stage=$failureStage sidecar_exit_code=$sidecarExit sidecar_signal=$sidecarSignal safe_error_code=$safeErrorCode sidecar_log_event=$logEvent sidecar_log_code=$logCode"
}

function Save-HealthProbeSummary([string]$Directory, [string]$Name, [object]$ExitCode, [object]$Report) {
  $stage = Get-HealthDiagnosticValue $Name @('initial-health', 'safe-mode-health')
  if ($stage -eq 'unknown') { throw 'HEALTH_DIAGNOSTIC_STAGE_INVALID' }
  $summary = Format-HealthProbeSummary -Name $stage -ExitCode $ExitCode -Report $Report
  $path = Join-Path ([IO.Path]::GetFullPath($Directory)) "$stage.diagnostics.log"
  [IO.File]::WriteAllText($path, $summary + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
  return $summary
}
