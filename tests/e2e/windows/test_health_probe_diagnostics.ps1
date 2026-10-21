$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'health-probe-diagnostics.ps1')

$report = [pscustomobject]@{
  status = 'safe_mode'
  code = 'RUNTIME_SAFE_MODE'
  preload_available = $true
  database_schema = '1.0.0'
  runtime_diagnostics = [pscustomobject]@{
    safe_error_code = 'SIDECAR_EXITED'
    raw_error = 'sk-test-secret-marker C:\Users\person\profile'
  }
}

$summary = Format-HealthProbeSummary -Name 'initial-health' -ExitCode 2 -Report $report
$expected = 'INSTALLED_APP_HEALTH_PROBE stage=initial-health status=safe_mode process_exit_code=2 report_code=RUNTIME_SAFE_MODE preload_available=true database_schema=1.0.0'
if ($summary -cne $expected) { throw 'SAFE_HEALTH_DIAGNOSTIC_SUMMARY_INVALID' }
if ($summary -match '(?i)sk-test-secret-marker|C:\\Users|raw_error|SIDECAR_EXITED') {
  throw 'HEALTH_DIAGNOSTIC_SECRET_LEAK'
}

$untrusted = [pscustomobject]@{
  status = 'token=must-not-print'
  code = 'sk-test-credential-marker'
  runtime_diagnostics = [pscustomobject]@{
    state = 'unexpected path'
    startup_stage = 'C:\Users\person'
    failure_stage = 'Bearer token'
    sidecar_exit_code = 'secret'
    safe_error_code = 'secret-value'
    sidecar_log_code = 'secret-value'
  }
}
$rejected = Format-HealthProbeSummary -Name 'unexpected-stage' -ExitCode 'not-an-exit-code' -Report $untrusted
if ($rejected -match '(?i)token=|sk-test-credential-marker|Bearer|C:\\Users|secret-value') {
  throw 'UNTRUSTED_HEALTH_DIAGNOSTIC_LEAK'
}
if ($rejected -cne 'INSTALLED_APP_HEALTH_PROBE stage=unknown status=unknown process_exit_code=unknown report_code=unknown preload_available=unknown database_schema=unknown') {
  throw 'UNTRUSTED_HEALTH_DIAGNOSTIC_NOT_REDACTED'
}

$crash = Format-HealthProbeSummary -Name 'initial-health' -ExitCode -1073741819 -Report $report
if (-not $crash.Contains('process_exit_code=-1073741819')) {
  throw 'NATIVE_CRASH_EXIT_CODE_NOT_PRESERVED'
}

$invalid = Format-HealthProbeSummary -Name 'initial-health' -ExitCode 3 -Report ([pscustomobject]@{
  status = 'unhealthy'
  code = 'HEALTH_REPORT_INVALID'
})
if (-not $invalid.Contains('report_code=HEALTH_REPORT_INVALID')) {
  throw 'INVALID_HEALTH_REPORT_NOT_CLASSIFIED'
}

Write-Output 'HEALTH_DIAGNOSTIC_SANITIZATION_TEST=passed'
