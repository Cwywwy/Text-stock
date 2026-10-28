<#
.SYNOPSIS
    注册（或重新注册）盘前选股系统的 Windows 计划任务。

.DESCRIPTION
    把任务定义固化成脚本 + XML，替换原来手工在「任务计划程序」里填表的方式。
    换机器或重装系统时只要跑一次本脚本，不会再出现"参数填错、路径含空格被拆错、
    引号套引号"这类问题。

    相比旧任务 \StockPlan\Snapshot_2130 的关键修正：
    - WakeToRun / StartWhenAvailable：睡眠中会唤醒，错过的会补跑
    - DisallowStartIfOnBatteries=false：笔记本用电池也跑
    - RunOnlyIfNetworkAvailable：没网时不空跑
    - ExecutionTimeLimit=PT3H：卡死会被强制结束，不会永远挂着
    - 动作直接调 powershell.exe -NoProfile -ExecutionPolicy Bypass -File，
      不再用 cmd /c ""..."" 包装含空格与中文的路径
    - 全程输出落 data/logs/daily_job.log，失败有据可查

    注意：本机注册任务需要**管理员权限**（非提权时 schtasks 报 Access is denied）。
    不方便提权时可加 -ExportOnly 只生成 XML，再用「任务计划程序 → 导入任务」
    手工导入，效果等价。

.PARAMETER LogonType
    S4U（默认）      不需要保存密码，且不要求用户已登录（注册需要管理员）
    InteractiveToken 不需要管理员，但只在用户已登录时运行

.PARAMETER ExportOnly
    只把 XML 写到 scripts\tasks\，不调用 schtasks 注册。

.PARAMETER KeepLegacy
    保留旧任务 \StockPlan\Snapshot_2130（默认先备份其 XML 再删除，因为它的动作
    仍指向旧目录 D:\Vscode\stock plan\Text，每天必然失败）。

.EXAMPLE
    # 管理员 PowerShell 里执行
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_tasks.ps1

.EXAMPLE
    # 不提权，只生成 XML 供 GUI 导入
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -ExportOnly
#>
[CmdletBinding()]
param(
    [ValidateSet('S4U', 'InteractiveToken')]
    [string]$LogonType = 'S4U',
    [switch]$ExportOnly,
    [switch]$KeepLegacy
)

$ErrorActionPreference = 'Continue'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ScriptPath = Join-Path $PSScriptRoot 'daily_job.ps1'
$XmlDir = Join-Path $PSScriptRoot 'tasks'
$UserId = (whoami)
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path $ScriptPath)) { throw "找不到 $ScriptPath" }

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host "项目根    : $ProjectRoot"
Write-Host "执行用户  : $UserId"
Write-Host "登录类型  : $LogonType"
Write-Host "已提权    : $elevated"
Write-Host ''

New-Item -ItemType Directory -Force -Path $XmlDir | Out-Null

function New-TaskXml {
    param(
        [string]$Description,
        [string]$TriggerXml,
        [string]$ExtraArgs = ''
    )
    $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"{1}' -f $ScriptPath, $ExtraArgs
    @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$Description</Description>
  </RegistrationInfo>
  <Triggers>
$TriggerXml
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$UserId</UserId>
      <LogonType>$LogonType</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT3H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$PowerShellExe</Command>
      <Arguments>$arguments</Arguments>
      <WorkingDirectory>$ProjectRoot</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
}

$weekdayTrigger = @'
    <CalendarTrigger>
      <StartBoundary>__TIME__</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek>
          <Monday /><Tuesday /><Wednesday /><Thursday /><Friday />
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
'@

$monthlyTrigger = @'
    <CalendarTrigger>
      <StartBoundary>__TIME__</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByMonth>
        <DaysOfMonth><Day>1</Day></DaysOfMonth>
        <Months>
          <January /><February /><March /><April /><May /><June />
          <July /><August /><September /><October /><November /><December />
        </Months>
      </ScheduleByMonth>
    </CalendarTrigger>
'@

$definition = [ordered]@{}

$t = $weekdayTrigger.Replace('__TIME__', '2026-01-01T16:30:00')
$definition['\StockPlan\Daily_Publish'] = @{
    File = 'Daily_Publish.xml'
    Xml  = New-TaskXml -Description '盘前选股系统：每交易日 16:30 增量更新行情并发布数据快照到 GitHub' -TriggerXml $t
}

$t = $weekdayTrigger.Replace('__TIME__', '2026-01-01T08:00:00')
$definition['\StockPlan\Daily_Update'] = @{
    File = 'Daily_Update.xml'
    Xml  = New-TaskXml -Description '盘前选股系统：每交易日 08:00 盘前兜底更新（不发布快照，幂等）' -TriggerXml $t -ExtraArgs ' -SkipPublish'
}

$t = $monthlyTrigger.Replace('__TIME__', '2026-01-01T17:00:00')
$definition['\StockPlan\Monthly_Rebuild'] = @{
    File = 'Monthly_Rebuild.xml'
    Xml  = New-TaskXml -Description '盘前选股系统：每月 1 日全历史重建，修正前复权因子回溯偏差' -TriggerXml $t -ExtraArgs ' -Rebuild'
}

# 1) 始终导出 XML（UTF-16 是任务计划程序要求的格式，中文路径也必须靠它保真）
Write-Host "导出 XML → $XmlDir"
foreach ($name in $definition.Keys) {
    $file = Join-Path $XmlDir $definition[$name].File
    $definition[$name].Xml | Out-File -FilePath $file -Encoding Unicode
    Write-Host "  [OK]   $($definition[$name].File)"
}
Write-Host ''

if ($ExportOnly) {
    Write-Host '已按 -ExportOnly 跳过注册。请用「任务计划程序 → 操作 → 导入任务」逐个导入上述 XML。'
    exit 0
}

# 2) 注册
Write-Host '注册任务：'
$anyFail = $false
foreach ($name in $definition.Keys) {
    $file = Join-Path $XmlDir $definition[$name].File
    $result = schtasks /create /tn $name /xml $file /f 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK]   $name"
    } else {
        Write-Host "  [FAIL] $name → $result"
        $anyFail = $true
    }
}
Write-Host ''

if ($anyFail -and -not $elevated) {
    Write-Host '------------------------------------------------------------'
    Write-Host '注册失败：本机 schtasks 需要管理员权限，当前进程未提权。'
    Write-Host '两种解决办法，任选其一：'
    Write-Host ''
    Write-Host '  1) 用【管理员】身份打开 PowerShell，执行：'
    Write-Host "       powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    Write-Host ''
    Write-Host '  2) 不提权：打开「任务计划程序」→ 操作 → 导入任务，'
    Write-Host "     逐个导入 $XmlDir 下的 3 个 XML。"
    Write-Host '------------------------------------------------------------'
    exit 1
}

# 3) 旧任务：动作仍指向 D:\Vscode\stock plan\Text，每天必然失败，默认备份后移除
$legacy = '\StockPlan\Snapshot_2130'
$exists = (schtasks /query /tn $legacy 2>$null)
if ($exists -and -not $KeepLegacy) {
    $backupDir = Join-Path $PSScriptRoot 'backup'
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    $backupFile = Join-Path $backupDir 'legacy_Snapshot_2130.xml'
    schtasks /query /tn $legacy /xml > $backupFile 2>$null
    Write-Host "旧任务 $legacy 的 XML 已备份到：$backupFile"
    $r = schtasks /delete /tn $legacy /f 2>&1
    if ($LASTEXITCODE -eq 0) { Write-Host "  [OK]   已删除 $legacy（功能由 Daily_Publish 取代）" }
    else { Write-Host "  [FAIL] 删除 $legacy 失败 → $r" }
} elseif ($exists) {
    Write-Host "已按 -KeepLegacy 保留旧任务 $legacy（注意它每天仍会失败）"
}

Write-Host ''
Write-Host '当前 StockPlan 任务：'
schtasks /query /tn '\StockPlan\' /fo TABLE 2>&1 | Select-Object -Skip 1
Write-Host ''
Write-Host '建议立刻手动验证一次（不必等第二天）：'
Write-Host '    schtasks /run /tn "\StockPlan\Daily_Update"'
