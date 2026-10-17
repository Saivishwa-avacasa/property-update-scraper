<#
Registers the Windows Task Scheduler jobs (folder \99acres\):
  * 99acres:     one daily task at 11:00, all five states in a row (60 project pages each)
  * Magicbricks: one daily task at 11:00, the all-India MagicHomes feed (1,500 project pages)
  * optionally a one-time FULL run of all states for either source

Run from an Administrator PowerShell in this folder:

    powershell -ExecutionPolicy Bypass -File .\register_tasks.ps1
    powershell -ExecutionPolicy Bypass -File .\register_tasks.ps1 -FullRunAt 22:20
    powershell -ExecutionPolicy Bypass -File .\register_tasks.ps1 -MbFullRunAt 19:00

-FullRunAt / -MbFullRunAt take HH:mm and mean today (tomorrow if that time
has passed). Re-running replaces the tasks. Remove them all with:
    Get-ScheduledTask -TaskPath "\99acres\" | Unregister-ScheduledTask -Confirm:$false

Settings that matter:
  * Runs only while you are logged on (interactive). The 99acres captcha
    warm-up opens a real Chrome window, which needs an interactive desktop.
    Lock the screen if you like; do not log off.
  * Runs through run_hidden.vbs, so there is no console window to close.
  * StartWhenAvailable: a missed run (PC was off) starts as soon as it is on.
    If two runs of the same source start together, the second waits for the
    first (lock in the scraper). 99acres and Magicbricks have separate locks.
  * Runs on battery too (Windows skips tasks on battery by default, which
    silently skipped runs on this laptop). One instance at a time, wake the PC
    to run. Daily: 5 h limit. 99acres full: 12 h. Magicbricks full: 48 h.
#>
param(
  [string]$FullRunAt = "",
  [string]$MbFullRunAt = ""
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $here "run_hidden.vbs"   # no console window; it calls run_daily.cmd
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

function New-Settings($hours, [switch]$Restart) {
  $s = @{
    StartWhenAvailable = $true; AllowStartIfOnBatteries = $true; DontStopIfGoingOnBatteries = $true
    WakeToRun = $true; MultipleInstances = "IgnoreNew"; ExecutionTimeLimit = (New-TimeSpan -Hours $hours)
  }
  if ($Restart) { $s.RestartCount = 1; $s.RestartInterval = (New-TimeSpan -Minutes 30) }
  New-ScheduledTaskSettingsSet @s
}

function Register-Job($name, $arguments, $trigger, $settings, $when) {
  $action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$runner`" $arguments" -WorkingDirectory $here
  Register-ScheduledTask -TaskName $name -TaskPath "\99acres\" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
  Write-Host "registered '$name' $when"
}

function Get-OnceTime($hhmm) {
  $at = [datetime]::ParseExact($hhmm, "HH:mm", $null)
  if ($at -lt (Get-Date)) { $at = $at.AddDays(1) }
  $at
}

$daily = New-Settings 5 -Restart

# Both sources start at 11:00 every day and run side by side (separate locks).
$dailyAt = "11:00"

# 99acres New Launch: one task, all five states one after another (~1-1.5 h)
foreach ($old in "TN", "KA", "MH", "GA", "UK") {   # per-state tasks from the first version
  Get-ScheduledTask -TaskPath "\99acres\" -TaskName "99acres New Launch - $old" -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false
}
Register-Job "99acres New Launch - All states" "all" (New-ScheduledTaskTrigger -Daily -At $dailyAt) $daily "daily at $dailyAt"

# Magicbricks MagicHomes: one daily run of the all-India feed (~2.5 h)
foreach ($old in "TN", "KA", "MH", "GA", "UK") {   # per-state tasks from the first version
  Get-ScheduledTask -TaskPath "\99acres\" -TaskName "Magicbricks MagicHomes - $old" -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false
}
Register-Job "Magicbricks MagicHomes - India" "mb IN" (New-ScheduledTaskTrigger -Daily -At $dailyAt) $daily "daily at $dailyAt"

if ($FullRunAt) {
  $at = Get-OnceTime $FullRunAt
  Register-Job "99acres Full Run - ALL" "all full" (New-ScheduledTaskTrigger -Once -At $at) (New-Settings 12) "once at $($at.ToString('dd MMM yyyy HH:mm'))"
}
if ($MbFullRunAt) {
  $at = Get-OnceTime $MbFullRunAt
  Register-Job "Magicbricks Full Run - India" "mb IN full" (New-ScheduledTaskTrigger -Once -At $at) (New-Settings 48) "once at $($at.ToString('dd MMM yyyy HH:mm'))"
}

Write-Host "`nStart both now:"
Write-Host "  Start-ScheduledTask -TaskPath '\99acres\' -TaskName '99acres New Launch - All states'"
Write-Host "  Start-ScheduledTask -TaskPath '\99acres\' -TaskName 'Magicbricks MagicHomes - India'"
Write-Host "Logs:  $here\data\scheduler_all.log  and  $here\data\scheduler_mb_IN.log"
