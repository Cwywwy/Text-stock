<#
.SYNOPSIS
    盘前选股系统 —— 每日数据任务（增量更新 → 可选发布快照）。

.DESCRIPTION
    供 Windows 计划任务调用，也可手动执行。设计要点：

    - **幂等**：增量更新本身幂等（本地已是最新则跳过且不联网），因此错过一次
      不影响，补跑没有副作用。
    - **UTF-8**：强制 PYTHONUTF8/PYTHONIOENCODING，避免 cmd 默认 GBK 下打印中文
      或 git 输出 UTF-8 时崩溃（历史上踩过 UnicodeDecodeError）。
    - **日志落文件**：输出同时进控制台和 data/logs/daily_job.log，失败可事后排查
      （而不是只剩一个 Last Result: 1）。
    - **不走 cmd 包装**：直接由 powershell.exe 执行本文件，避开含空格/中文路径被
      引号拆错的问题。

.PARAMETER SkipPublish
    只做增量更新，不发布快照。用于盘前兜底那一轮，避免一天两次上传 300MB。

.PARAMETER Rebuild
    先做全历史重建（修正前复权因子回溯偏差），再增量更新与发布。耗时约 10 分钟。

.PARAMETER Workers
    并发线程数，默认 16。

.EXAMPLE
    # 盘后：增量更新 + 发布快照
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\daily_job.ps1

.EXAMPLE
    # 盘前兜底：只更新不发布
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\daily_job.ps1 -SkipPublish

.EXAMPLE
    # 月度维护：全历史重建，修正前复权回溯
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\daily_job.ps1 -Rebuild
#>
[CmdletBinding()]
param(
    [switch]$SkipPublish,
    [switch]$Rebuild,
    [int]$Workers = 16
)

$ErrorActionPreference = 'Continue'   # 单步失败不应吞掉日志，由退出码表达结果

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
# tqdm 进度条写 stderr，会被 PowerShell 5.1 当成错误记录，并把日志刷满几十行
$env:TQDM_DISABLE = '1'

# scripts/ 的上一级即项目根
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$LogDir = Join-Path $ProjectRoot 'data\logs'
$Log = Join-Path $LogDir 'daily_job.log'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log {
    param([string]$Message)
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Host $line
}

function Invoke-Step {
    <#
        执行一步，把 stdout/stderr 都追加进日志，返回退出码。

        用 Start-Process 重定向而不是 `& $Python ... 2>&1`：PowerShell 5.1 会把原生命令
        写到 stderr 的每一行都包装成 ErrorRecord，日志里就凭空多出一段
        "NativeCommandError" 假报错，掩盖真正的问题。
    #>
    param([string]$Label, [string[]]$CommandArgs)
    Write-Log "--- $Label 开始 ---"

    $so = Join-Path $env:TEMP ('dsh_stdout_' + [guid]::NewGuid().ToString('N') + '.txt')
    $se = Join-Path $env:TEMP ('dsh_stderr_' + [guid]::NewGuid().ToString('N') + '.txt')
    try {
        $proc = Start-Process -FilePath $Python -ArgumentList $CommandArgs `
            -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $so -RedirectStandardError $se
        $code = $proc.ExitCode

        foreach ($f in @($so, $se)) {
            if (-not (Test-Path $f)) { continue }
            $body = Get-Content -Path $f -Raw -Encoding UTF8
            if ($body -and $body.Trim()) {
                Add-Content -Path $Log -Value $body.TrimEnd() -Encoding UTF8
                Write-Host $body.TrimEnd()
            }
            Remove-Item $f -Force -ErrorAction SilentlyContinue
        }
    }
    catch {
        Write-Log "启动失败：$($_.Exception.Message)"
        $code = 1
    }

    Write-Log "--- $Label 退出码=$code ---"
    return $code
}

function Limit-LogSize {
    <# 日志只留最近若干行，避免无人值守时无限增长。 #>
    param([int]$Keep = 2000, [int]$Trigger = 6000)
    try {
        if (-not (Test-Path $Log)) { return }
        $lines = @(Get-Content -Path $Log -Encoding UTF8)
        if ($lines.Count -le $Trigger) { return }
        $tail = $lines[-$Keep..-1]
        $head = @('（日志已截断，以下为最近 ' + $Keep + ' 行）')
        ($head + $tail) | Set-Content -Path $Log -Encoding UTF8
    }
    catch { }
}

Write-Log "=== daily_job 开始（Rebuild=$Rebuild SkipPublish=$SkipPublish Workers=$Workers）==="

if (-not (Test-Path $Python)) {
    Write-Log "致命：找不到虚拟环境 Python：$Python（先在本机执行 uv sync）"
    exit 1
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()

try {
    if ($Rebuild) {
        $code = Invoke-Step '全历史重建（修正前复权回溯）' @('-m', 'stock_plan.data.update_daily', '--rebuild', '--workers', "$Workers")
        if ($code -ne 0) { Write-Log "警告：全历史重建有失败项（退出码 $code），继续后续步骤" }
    }

    $code = Invoke-Step '增量数据更新' @('-m', 'stock_plan.data.update_daily', '--workers', "$Workers")
    if ($code -ne 0) {
        # 退出码 2 表示有部分股票失败（常见于停牌/退市/网络抖动），不阻断发布
        Write-Log "警告：增量更新未全部成功（退出码 $code）"
    }

    if ($SkipPublish) {
        Write-Log '已按 -SkipPublish 跳过快照发布'
    } else {
        $code = Invoke-Step '发布数据快照' @('scripts\publish_snapshot.py')
        if ($code -ne 0) {
            Write-Log "错误：快照发布失败（退出码 $code）"
            Write-Log "=== daily_job 结束（失败）总耗时 $([int]$sw.Elapsed.TotalSeconds)s ==="
            exit 1
        }
    }
}
catch {
    Write-Log "异常：$($_.Exception.Message)"
    Write-Log $_.ScriptStackTrace
    Write-Log "=== daily_job 结束（异常）总耗时 $([int]$sw.Elapsed.TotalSeconds)s ==="
    exit 1
}

Write-Log "=== daily_job 结束（成功）总耗时 $([int]$sw.Elapsed.TotalSeconds)s ==="
Limit-LogSize
exit 0
