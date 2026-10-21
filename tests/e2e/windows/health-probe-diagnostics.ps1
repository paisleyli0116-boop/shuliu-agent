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

function Get-HealthDiagnosticValue([object]$Value, [string[]]$AllowedValues) {
  if ($Value -is [string] -and $AllowedValues -ccontains $Value) { return $Value }
  return 'unknown'
}

function Get-HealthDiagnosticExitCode([object]$Value) {
  $parsed = 0L
  if ([long]::TryParse([string]$Value, [ref]$parsed) -and $parsed -ge -2147483648 -and $parsed -le 4294967295) {
    return [string]$parsed
  }
  return 'unknown'
}

function Get-HealthDiagnosticBoolean([object]$Value) {
  if ($Value -is [bool]) { return ([string]$Value).ToLowerInvariant() }
  return 'unknown'
}

function Get-HealthDiagnosticSchema([object]$Value) {
  if ($Value -is [string] -and $Value -match '^\d{1,3}(?:\.\d{1,3}){0,3}$') { return $Value }
  return 'unknown'
}

function Format-HealthProbeSummary([string]$Name, [object]$ExitCode, [object]$Report) {
  $stage = Get-HealthDiagnosticValue $Name @('initial-health', 'safe-mode-health')
  $processExit = Get-HealthDiagnosticExitCode $ExitCode
  $status = Get-HealthDiagnosticValue $Report.status $script:HealthReportStatuses
  $reportCode = Get-HealthDiagnosticValue $Report.code $script:HealthReportCodes
  $preloadAvailable = Get-HealthDiagnosticBoolean $Report.preload_available
  $databaseSchema = Get-HealthDiagnosticSchema $Report.database_schema

  return "INSTALLED_APP_HEALTH_PROBE stage=$stage status=$status process_exit_code=$processExit report_code=$reportCode preload_available=$preloadAvailable database_schema=$databaseSchema"
}
