param(
  [ValidateSet('install','status','remove')]
  [string]$Action = 'status'
)
$ErrorActionPreference = 'Stop'
$TaskName = 'ShuLiuDevAutopilot'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$StateDir = Join-Path $env:LOCALAPPDATA 'ShuLiuAgent\DevAutopilot\Task28'
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Action -eq 'status') {
  if ($null -eq $Task) { Write-Output '后台计划任务尚未安装。'; exit 0 }
  $Info = Get-ScheduledTaskInfo -TaskName $TaskName
  Write-Output ("后台计划任务状态：{0}；上次运行：{1}；上次结果：{2}" -f $Task.State,$Info.LastRunTime,$Info.LastTaskResult)
  exit 0
}
if ($Action -eq 'remove') {
  if ($null -ne $Task) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; Write-Output '后台计划任务已移除。' }
  else { Write-Output '后台计划任务原本未安装。' }
  exit 0
}
$Acceptance = Join-Path $StateDir 'AUTOPILOT_ACCEPTED.json'
if (-not (Test-Path -LiteralPath $Acceptance -PathType Leaf)) {
  throw '自动化验收尚未完成；为避免启动未验证的后台开发，未安装计划任务。'
}
$Python = (Get-Command python.exe -ErrorAction Stop).Source
$ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot 'autopilot.py')).Path
$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Arguments = '"{0}" scheduled-tick' -f $ScriptPath
$ScheduledAction = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At '12:00AM'
$Trigger.Repetition.Interval = 'PT15M'
$Trigger.Repetition.Duration = 'P1D'
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 100) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$Principal = New-ScheduledTaskPrincipal -UserId $Identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $ScheduledAction -Trigger $Trigger -Settings $Settings -Principal $Principal -Description 'ShuLiu Dev Autopilot: limited, user-scoped, feature-branch work only.' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output '已安装当前用户权限下的计划任务；每 15 分钟检查一次，重复实例会被忽略。'
