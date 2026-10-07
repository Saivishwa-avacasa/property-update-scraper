<#
Registers five Windows Task Scheduler jobs, one per state, two hours apart.
Run once from an elevated or normal PowerShell in this folder:

    powershell -ExecutionPolicy Bypass -File .\register_tasks.ps1

Re-running replaces the tasks. Remove them with:
    Get-ScheduledTask -TaskPath "\99acres\" | Unregister-ScheduledTask -Confirm:$false

Settings that matter:
  * Runs only while you are logged on (interactive). The captcha warm-up opens a
    real Chrome window, which needs an interactive desktop. Lock the screen if
    you like; do not log off.
  * StartWhenAvailable: a missed run (PC was off) starts as soon as it is on.
  * One instance at a time, 3 hour time limit, wake the PC to run.
#>
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $here "run_daily.cmd"

$schedule = @(
  @{ State = "TN"; Time = "10:00" },
  @{ State = "KA"; Time = "12:00" },
  @{ State = "MH"; Time = "14:00" },
  @{ State = "GA"; Time = "16:00" },
  @{ State = "UK"; Time = "17:00" }
)

$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -WakeToRun `
  -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
  -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 30)

foreach ($job in $schedule) {
  $name = "99acres New Launch - $($job.State)"
  $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"`"$runner`" $($job.State)`"" -WorkingDirectory $here
  $trigger = New-ScheduledTaskTrigger -Daily -At $job.Time
  $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $name -TaskPath "\99acres\" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
  Write-Host "registered '$name' daily at $($job.Time)"
}
Write-Host "`nTest one now:  Start-ScheduledTask -TaskPath '\99acres\' -TaskName '99acres New Launch - TN'"
Write-Host "Log:           $here\data\scheduler.log"
