param(
  [Parameter(Mandatory = $true)][string]$InstallerPath,
  [Parameter(Mandatory = $true)][string]$EvidenceDirectory
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'health-probe-diagnostics.ps1')
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
if ([IO.Path]::GetExtension($installer) -ne '.exe') { throw 'INSTALLER_FILE_TYPE_INVALID' }
$base = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$testRoot = Join-Path $base ('shuliu-task26-smoke-' + [guid]::NewGuid().ToString('N'))
$installDirectory = Join-Path $testRoot 'install'
$userDataDirectory = Join-Path $testRoot 'user-data'
New-Item -ItemType Directory -Path $testRoot, $installDirectory, $userDataDirectory -Force | Out-Null
$evidence = [IO.Path]::GetFullPath($EvidenceDirectory)
New-Item -ItemType Directory -Path $evidence -Force | Out-Null

function Install-Package([string]$Path, [string]$Destination) {
  $resolvedDestination = [IO.Path]::GetFullPath($Destination)
  $resolvedRoot = [IO.Path]::GetFullPath($testRoot)
  if (-not $resolvedDestination.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'INSTALL_TARGET_OUTSIDE_TEST_ROOT'
  }
  $process = Start-Process -FilePath $Path -ArgumentList @('/S', "/D=$resolvedDestination") -PassThru -Wait
  if ($process.ExitCode -ne 0) { throw 'INSTALLER_EXIT_NONZERO' }
}

function Invoke-HealthProbe([string]$Executable, [string]$DataDirectory, [string]$Name) {
  $startInfo = [Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = $Executable
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $startInfo.ArgumentList.Add('--shuliu-health-check')
  $startInfo.ArgumentList.Add("--user-data-dir=$DataDirectory")
  $process = [Diagnostics.Process]::new()
  $process.StartInfo = $startInfo
  [void]$process.Start()
  $stdoutTask = $process.StandardOutput.ReadToEndAsync()
  $stderrTask = $process.StandardError.ReadToEndAsync()
  if (-not $process.WaitForExit(90000)) {
    $process.Kill($true)
    $summary = Save-HealthProbeSummary -Directory $evidence -Name $Name -ExitCode $null -Report ([pscustomobject]@{ status = 'unhealthy'; code = 'HEALTH_PROBE_TIMEOUT' })
    Write-Host $summary
    throw 'INSTALLED_APP_HEALTH_TIMEOUT'
  }
  $stdout = $stdoutTask.GetAwaiter().GetResult()
  $stderr = $stderrTask.GetAwaiter().GetResult()
  $line = ($stdout -split "`r?`n") | Where-Object { $_.TrimStart().StartsWith('{') } | Select-Object -Last 1
  if (-not $line) {
    $missingReport = [pscustomobject]@{ status = 'unhealthy'; code = 'HEALTH_REPORT_MISSING' }
    $summary = Save-HealthProbeSummary -Directory $evidence -Name $Name -ExitCode $process.ExitCode -Report $missingReport
    Write-Host $summary
    throw 'INSTALLED_APP_HEALTH_REPORT_MISSING'
  }
  try {
    $report = $line | ConvertFrom-Json
  } catch {
    $invalidReport = [pscustomobject]@{ status = 'unhealthy'; code = 'HEALTH_REPORT_INVALID' }
    $summary = Save-HealthProbeSummary -Directory $evidence -Name $Name -ExitCode $process.ExitCode -Report $invalidReport
    Write-Host $summary
    throw 'INSTALLED_APP_HEALTH_REPORT_INVALID'
  }
  $summary = Save-HealthProbeSummary -Directory $evidence -Name $Name -ExitCode $process.ExitCode -Report $report
  Write-Host $summary
  return [pscustomobject]@{ ExitCode = $process.ExitCode; Report = $report }
}

try {
  Install-Package $installer $installDirectory
  $app = Join-Path $installDirectory 'ShuLiu Agent.exe'
  $sidecar = Join-Path $installDirectory 'resources\python\ShuLiuRuntime.exe'
  if (-not (Test-Path -LiteralPath $app -PathType Leaf)) { throw 'INSTALLED_ELECTRON_ENTRY_MISSING' }
  if (-not (Test-Path -LiteralPath $sidecar -PathType Leaf)) { throw 'INSTALLED_SIDECAR_MISSING' }

  $healthy = Invoke-HealthProbe $app $userDataDirectory 'initial-health'
  if ($healthy.ExitCode -ne 0 -or $healthy.Report.status -ne 'healthy') { throw 'INSTALLED_APP_HEALTH_FAILED' }
  if ($healthy.Report.electron_version -ne '44.9.0' -or $healthy.Report.node_version -ne '24.22.0') {
    throw 'PACKAGED_ELECTRON_VERSION_MISMATCH'
  }
  if ($healthy.Report.preload_available -ne $true -or $healthy.Report.database_schema -ne '1.2.0') {
    throw 'PACKAGED_RUNTIME_HEALTH_INCOMPLETE'
  }
  $markerPath = Join-Path $userDataDirectory 'task26-preservation.marker'
  [IO.File]::WriteAllText($markerPath, 'Task 26 install smoke data')
  $databasePath = Join-Path $userDataDirectory 'foundation.sqlite'
  if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf)) { throw 'DATABASE_NOT_CREATED_ON_FIRST_START' }
  $installLogPath = Join-Path $installDirectory 'install.log'
  if (-not (Select-String -LiteralPath $installLogPath -Pattern 'sidecar_payload=installed' -Quiet)) { throw 'INSTALL_LOG_MISSING' }
  Copy-Item -LiteralPath $installLogPath -Destination (Join-Path $evidence 'nsis-install.log')

  $disabledSidecar = "$sidecar.disabled"
  Move-Item -LiteralPath $sidecar -Destination $disabledSidecar
  try {
    $safeMode = Invoke-HealthProbe $app $userDataDirectory 'safe-mode-health'
    if ($safeMode.ExitCode -ne 2 -or $safeMode.Report.status -ne 'safe_mode' -or $safeMode.Report.preload_available -ne $true) {
      throw 'SAFE_MODE_NOT_REPORTED'
    }
  } finally {
    if (Test-Path -LiteralPath $disabledSidecar) { Move-Item -LiteralPath $disabledSidecar -Destination $sidecar }
  }

  $uninstaller = Join-Path $installDirectory 'Uninstall ShuLiu Agent.exe'
  if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) { throw 'UNINSTALLER_MISSING' }
  $uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList '/S' -PassThru -Wait
  if ($uninstallProcess.ExitCode -ne 0) { throw 'UNINSTALL_EXIT_NONZERO' }
  if (-not (Test-Path -LiteralPath $markerPath) -or -not (Test-Path -LiteralPath $databasePath)) {
    throw 'UNINSTALL_REMOVED_USER_DATA'
  }

  [ordered]@{
    status = 'passed'
    installed_path_under_temp = $true
    installer_opened = $true
    electron_version = $healthy.Report.electron_version
    node_version = $healthy.Report.node_version
    preload_available = $healthy.Report.preload_available
    sidecar_health = $healthy.Report.status
    database_schema = $healthy.Report.database_schema
    safe_mode_verified = $safeMode.Report.status -eq 'safe_mode'
    uninstaller_ran = $true
    user_data_preserved = $true
  } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $evidence 'installer-smoke.json') -Encoding utf8
  Write-Output 'Task 26 installer smoke passed.'
} catch {
  [ordered]@{ status = 'failed'; code = 'INSTALLER_SMOKE_FAILED' } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'installer-smoke.json') -Encoding utf8
  throw
}
