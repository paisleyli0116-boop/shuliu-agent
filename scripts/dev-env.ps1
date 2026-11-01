#Requires -Version 5.1
<#
.SYNOPSIS
    ShuLiu Agent 开发环境前置脚本（仅影响当前 PowerShell 会话）。

.DESCRIPTION
    集中设置本会话所需的开发环境前置条件，避免每次手动重复。

    设计原则：
      1. 只修改当前进程的会话环境，不改机器级/用户级环境变量。
      2. 不安装依赖、不提交、不推送、不修改仓库文件。
      3. 只做"缺失才补"的动作，不覆盖已正确的配置。
      4. 代理默认不使用（实测直连可用、经代理会 502）；需要时用 -UseProxy 显式开启。

.PARAMETER RepoPath
    仓库根目录。默认 C:\Dev\shuliu-agent。

.PARAMETER UseProxy
    显式启用 HTTP(S)_PROXY。默认关闭。端口用 -ProxyPort 指定，不写死。

.PARAMETER ProxyPort
    代理端口，仅在 -UseProxy 时生效。必须显式提供，脚本不会猜测。

.EXAMPLE
    .\scripts\dev-env.ps1

.EXAMPLE
    .\scripts\dev-env.ps1 -UseProxy -ProxyPort 6894
#>
[CmdletBinding()]
param(
    [string]$RepoPath = 'C:\Dev\shuliu-agent',
    [switch]$UseProxy,
    [int]$ProxyPort = 0
)

$ErrorActionPreference = 'Stop'
$report = [System.Collections.Generic.List[string]]::new()

function Add-Report([string]$Status, [string]$Item, [string]$Detail) {
    $script:report.Add(('[{0,-4}] {1,-26} {2}' -f $Status, $Item, $Detail))
}

# 把目录放到会话 PATH 最前面，且保证不重复（脚本可反复执行而不膨胀）。
function Add-PathPrefix([string]$Dir) {
    $norm = $Dir.TrimEnd('\')
    $kept = $env:Path -split ';' | Where-Object {
        $_ -and ($_.TrimEnd('\') -ne $norm)
    }
    $env:Path = (@($Dir) + $kept) -join ';'
}

# ---------- 1. 仓库根目录 ----------
if (-not (Test-Path -LiteralPath $RepoPath)) {
    throw "仓库路径不存在: $RepoPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $RepoPath '.git'))) {
    throw "不是 Git 仓库: $RepoPath"
}
Set-Location -LiteralPath $RepoPath
Add-Report 'OK' '仓库' $RepoPath

# ---------- 2. 清掉会破坏 Electron 的继承变量 ----------
# WorkBuddy 宿主会注入 ELECTRON_RUN_AS_NODE=1，使 electron.exe 退化成纯 Node，
# 表现为 "bad option: --no-sandbox"。仅清除本会话，不动系统设置。
if ($env:ELECTRON_RUN_AS_NODE) {
    Remove-Item Env:\ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    Add-Report 'FIX' 'ELECTRON_RUN_AS_NODE' '已从当前会话移除'
} else {
    Add-Report 'OK' 'ELECTRON_RUN_AS_NODE' '未设置'
}

# ---------- 3. Node ----------
# 宿主（如 WorkBuddy / Electron 壳）可能在自己 PATH 前部注入别的 Node 版本。
# 这里以仓库 .node-version 为准：当前 node 版本不符时，优先切换到系统级安装。
$expectedNode = (Get-Content -LiteralPath (Join-Path $RepoPath '.node-version') -Raw).Trim()
$systemNodeDir = 'C:\Program Files\nodejs'
$systemNodeExe = Join-Path $systemNodeDir 'node.exe'

# 用绝对路径取版本，避免 Get-Command 的会话缓存导致误判。
$currentVersion = ''
if (Get-Command node -ErrorAction SilentlyContinue) {
    $currentVersion = (& node --version).Trim()
}

if ($currentVersion -ne "v$expectedNode" -and (Test-Path -LiteralPath $systemNodeExe)) {
    $systemVersion = (& $systemNodeExe --version).Trim()
    if ($systemVersion -eq "v$expectedNode") {
        Add-PathPrefix $systemNodeDir
        $currentVersion = (& $systemNodeExe --version).Trim()
        $nodeStatus = 'FIX'
        $nodeDetail = "已切换到 $systemNodeDir -> $currentVersion"
    } else {
        $nodeStatus = 'WARN'
        $nodeDetail = "$currentVersion；系统级为 $systemVersion，仓库要求 v$expectedNode"
    }
}

if ([string]::IsNullOrWhiteSpace($currentVersion)) { throw '未找到 node。请检查 C:\Program Files\nodejs' }
if (-not $nodeStatus) {
    if ($currentVersion -eq "v$expectedNode") {
        $nodeStatus = 'OK'
        $nodeDetail = $currentVersion
    } else {
        $nodeStatus = 'WARN'
        $nodeDetail = "$currentVersion (仓库要求 v$expectedNode)"
    }
}
Add-Report $nodeStatus 'Node' $nodeDetail

# ---------- 4. pnpm（经 corepack，版本由 package.json 的 packageManager 决定）----------
$pnpmCmd = Get-Command pnpm -ErrorAction SilentlyContinue
if (-not $pnpmCmd) {
    Add-Report 'WARN' 'pnpm' '当前会话不可用；如需要请手动执行 corepack enable（脚本不代做）'
} else {
    $pnpmVersion = (& pnpm --version 2>$null | Select-Object -Last 1)
    Add-Report 'OK' 'pnpm' $pnpmVersion
}

# ---------- 5. uv ----------
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCmd) {
    $uvDir = Join-Path $env:USERPROFILE '.local\bin'
    if (Test-Path -LiteralPath (Join-Path $uvDir 'uv.exe')) {
        Add-PathPrefix $uvDir
        $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
        Add-Report 'FIX' 'uv' "已加入会话 PATH: $uvDir"
    }
}
if (-not $uvCmd) { throw '未找到 uv。请检查 %USERPROFILE%\.local\bin' }
$uvVersion = ((& uv --version) -split '\s+')[1]
Add-Report 'OK' 'uv' $uvVersion

# ---------- 6. Python 3.11 ----------
# 宿主的裸 python 可能指向 3.13；这里优先使用仓库 .venv，再用 py -3.11 兜底。
$venvPython = Join-Path $RepoPath '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
    $venvVersion = (& $venvPython --version 2>&1) -join ' '
    $env:VIRTUAL_ENV = Join-Path $RepoPath '.venv'
    Add-PathPrefix (Join-Path $RepoPath '.venv\Scripts')
    Add-Report 'OK' 'Python (.venv)' $venvVersion
} else {
    Add-Report 'WARN' 'Python (.venv)' '未找到 .venv/Scripts/python.exe（不自动创建）'
}

$py311 = Get-Command py -ErrorAction SilentlyContinue
if ($py311) {
    $py311Version = ((& py -3.11 --version) -join ' ').Trim()
    Add-Report 'OK' 'py -3.11' $py311Version
} else {
    Add-Report 'WARN' 'py -3.11' 'py launcher 不可用'
}

# ---------- 7. 编码 ----------
if (-not $env:PYTHONUTF8) {
    $env:PYTHONUTF8 = '1'
    Add-Report 'FIX' 'PYTHONUTF8' '设为 1'
} else {
    Add-Report 'OK' 'PYTHONUTF8' $env:PYTHONUTF8
}

# ---------- 8. 代理（默认直连）----------
# 实测：直连 github 成功；经本地代理 git 返回 502。故默认清空代理变量。
if ($UseProxy) {
    if ($ProxyPort -le 0) {
        throw '使用 -UseProxy 时必须同时指定 -ProxyPort（脚本不猜测端口）'
    }
    $proxyUrl = "http://127.0.0.1:$ProxyPort"
    $env:HTTP_PROXY = $proxyUrl
    $env:HTTPS_PROXY = $proxyUrl
    $env:NO_PROXY = 'localhost,127.0.0.1,::1'
    Add-Report 'SET' '代理' "$proxyUrl （显式启用）"
} else {
    foreach ($name in 'HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy') {
        if (Test-Path "Env:\$name") { Remove-Item "Env:\$name" -ErrorAction SilentlyContinue }
    }
    $env:NO_PROXY = 'localhost,127.0.0.1,::1'
    Add-Report 'SET' '代理' '直连（未启用代理）'
}

# ---------- 输出 ----------
Write-Host ''
Write-Host 'ShuLiu Agent 开发环境（当前会话）' -ForegroundColor Cyan
Write-Host ('-' * 72)
$report | ForEach-Object { Write-Host $_ }
Write-Host ('-' * 72)
Write-Host '提示: 用 `. .\scripts\dev-env.ps1` 以点号方式执行，环境才能保留在当前会话。' -ForegroundColor DarkGray
Write-Host '      直接 & 脚本 执行时，脚本跑在自己的子进程里，父会话不受影响。' -ForegroundColor DarkGray
Write-Host '      本脚本不安装依赖、不提交、不推送、不修改任何系统级配置。' -ForegroundColor DarkGray
