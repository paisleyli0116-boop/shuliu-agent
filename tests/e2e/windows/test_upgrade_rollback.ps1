param(
  [Parameter(Mandatory = $true)][string]$PreviousInstallerPath,
  [Parameter(Mandatory = $true)][string]$NextInstallerPath,
  [Parameter(Mandatory = $true)][string]$EvidenceDirectory
)

$ErrorActionPreference = 'Stop'
$previousInstaller = (Resolve-Path -LiteralPath $PreviousInstallerPath).Path
$nextInstaller = (Resolve-Path -LiteralPath $NextInstallerPath).Path
$base = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$testRoot = Join-Path $base ('shuliu-task26-upgrade-' + [guid]::NewGuid().ToString('N'))
$installDirectory = Join-Path $testRoot 'install'
$userDataDirectory = Join-Path $testRoot 'user-data'
New-Item -ItemType Directory -Path $testRoot, $installDirectory, $userDataDirectory -Force | Out-Null
$evidence = [IO.Path]::GetFullPath($EvidenceDirectory)
New-Item -ItemType Directory -Path $evidence -Force | Out-Null

function Install-Package([string]$Path) {
  $resolvedDestination = [IO.Path]::GetFullPath($installDirectory)
  $resolvedRoot = [IO.Path]::GetFullPath($testRoot)
  if (-not $resolvedDestination.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'INSTALL_TARGET_OUTSIDE_TEST_ROOT'
  }
  $process = Start-Process -FilePath $Path -ArgumentList @('/S', "/D=$resolvedDestination") -PassThru -Wait
  if ($process.ExitCode -ne 0) { throw 'INSTALLER_EXIT_NONZERO' }
}

function Invoke-HealthProbe([string]$Name) {
  $app = Join-Path $installDirectory 'ShuLiu Agent.exe'
  if (-not (Test-Path -LiteralPath $app -PathType Leaf)) { throw 'INSTALLED_ELECTRON_ENTRY_MISSING' }
  $startInfo = [Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = $app
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $startInfo.ArgumentList.Add('--shuliu-health-check')
  $startInfo.ArgumentList.Add("--user-data-dir=$userDataDirectory")
  $process = [Diagnostics.Process]::new()
  $process.StartInfo = $startInfo
  [void]$process.Start()
  $stdoutTask = $process.StandardOutput.ReadToEndAsync()
  $stderrTask = $process.StandardError.ReadToEndAsync()
  if (-not $process.WaitForExit(90000)) {
    $process.Kill($true)
    throw 'INSTALLED_APP_HEALTH_TIMEOUT'
  }
  $stdout = $stdoutTask.GetAwaiter().GetResult()
  $stderr = $stderrTask.GetAwaiter().GetResult()
  [IO.File]::WriteAllText((Join-Path $evidence "$Name.stdout.log"), $stdout)
  [IO.File]::WriteAllText((Join-Path $evidence "$Name.stderr.log"), $stderr)
  $line = ($stdout -split "`r?`n") | Where-Object { $_.TrimStart().StartsWith('{') } | Select-Object -Last 1
  if (-not $line) { throw 'INSTALLED_APP_HEALTH_REPORT_MISSING' }
  return [pscustomobject]@{ ExitCode = $process.ExitCode; Report = ($line | ConvertFrom-Json) }
}

try {
  Install-Package $previousInstaller
  $previousHealth = Invoke-HealthProbe 'before-upgrade'
  if ($previousHealth.ExitCode -ne 0 -or $previousHealth.Report.status -ne 'healthy' -or $previousHealth.Report.app_version -ne '0.1.0') {
    throw 'PREVIOUS_VERSION_HEALTH_FAILED'
  }
  $markerPath = Join-Path $userDataDirectory 'task26-preservation.marker'
  [IO.File]::WriteAllText($markerPath, 'configuration and local data survive upgrade and rollback')
  $databasePath = Join-Path $userDataDirectory 'foundation.sqlite'
  if (-not (Test-Path -LiteralPath $databasePath)) { throw 'DATABASE_NOT_CREATED_ON_FIRST_START' }

  Install-Package $nextInstaller
  $upgradedHealth = Invoke-HealthProbe 'after-upgrade'
  if ($upgradedHealth.ExitCode -ne 0 -or $upgradedHealth.Report.status -ne 'healthy' -or $upgradedHealth.Report.app_version -ne '0.1.1') {
    throw 'UPGRADED_VERSION_HEALTH_FAILED'
  }
  if (-not (Test-Path -LiteralPath $markerPath) -or -not (Test-Path -LiteralPath $databasePath)) {
    throw 'UPGRADE_REMOVED_USER_DATA'
  }

  $sidecar = Join-Path $installDirectory 'resources\python\ShuLiuRuntime.exe'
  if (-not (Test-Path -LiteralPath $sidecar -PathType Leaf)) { throw 'UPGRADED_SIDECAR_MISSING' }
  $disabledSidecar = "$sidecar.disabled"
  Move-Item -LiteralPath $sidecar -Destination $disabledSidecar
  try {
    $failedUpdateHealth = Invoke-HealthProbe 'failed-update'
    if ($failedUpdateHealth.ExitCode -ne 2 -or $failedUpdateHealth.Report.status -ne 'safe_mode') {
      throw 'FAILED_UPDATE_DID_NOT_ENTER_SAFE_MODE'
    }
  } finally {
    if (Test-Path -LiteralPath $disabledSidecar) { Move-Item -LiteralPath $disabledSidecar -Destination $sidecar }
  }

  Install-Package $previousInstaller
  $rollbackHealth = Invoke-HealthProbe 'after-rollback'
  if ($rollbackHealth.ExitCode -ne 0 -or $rollbackHealth.Report.status -ne 'healthy' -or $rollbackHealth.Report.app_version -ne '0.1.0') {
    throw 'ROLLBACK_HEALTH_FAILED'
  }
  if (-not (Test-Path -LiteralPath $markerPath) -or -not (Test-Path -LiteralPath $databasePath)) {
    throw 'ROLLBACK_REMOVED_USER_DATA'
  }

  [ordered]@{
    status = 'passed'
    previous_version = $previousHealth.Report.app_version
    upgraded_version = $upgradedHealth.Report.app_version
    safe_mode_after_failed_runtime = $failedUpdateHealth.Report.status -eq 'safe_mode'
    rollback_version = $rollbackHealth.Report.app_version
    user_data_preserved_through_upgrade_and_rollback = $true
    database_preserved_through_upgrade_and_rollback = $true
  } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $evidence 'upgrade-rollback.json') -Encoding utf8
  Write-Output 'Task 26 installer upgrade and rollback passed.'
} catch {
  [ordered]@{ status = 'failed'; code = 'UPGRADE_ROLLBACK_FAILED' } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'upgrade-rollback.json') -Encoding utf8
  throw
}
