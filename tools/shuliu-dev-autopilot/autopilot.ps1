param(
  [ValidateSet('start','pause','resume','status','last-failure','scheduled-tick','emergency-stop','request-approval','approve-current','configure-feishu')]
  [string]$Action = 'status'
)
$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Push-Location $RepoRoot
& python (Join-Path $PSScriptRoot 'autopilot.py') $Action
$ExitCode = $LASTEXITCODE
Pop-Location
exit $ExitCode
