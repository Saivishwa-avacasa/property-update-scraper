<#
Registers a Task Scheduler job (folder \99acres\) that runs scrape_goa_pages.py
five times a day, one batch of 10 pages each time (~250 listings, ~1,250 a day),
until 99acres' last Goa page.

Run from PowerShell in this folder:

    powershell -ExecutionPolicy Bypass -File .\register_goa_pages_task.ps1
    powershell -ExecutionPolicy Bypass -File .\register_goa_pages_task.ps1 -Times "08:00,14:00,17:00,20:00,23:00"

Re-running replaces the task (including the old every-70-minutes version).
Progress:  data\goa_pages.log   Results:  goa_pages.json
Stop it:
    Unregister-ScheduledTask -TaskPath "\99acres\" -TaskName "99acres Goa pages" -Confirm:$false

Notes:
  * Default times keep 3 hours between runs and stay clear of the daily
    11:00 scrape_99acres.py run (same connection; the run lock would also
    make a batch skip itself while that run is going).
  * After a security check the script waits 3 hours by itself; a run inside
    that window just logs "wait N more minutes" and exits.
  * Uses pythonw.exe, so no window opens; output goes to data\goa_pages.log.
  * Runs only while you are logged on, like the other tasks.
#>
param(
  [string]$Times = "08:00,14:00,17:00,20:00,23:00"   # daily start times, HH:mm
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $here ".venv\Scripts\pythonw.exe"
$script = Join-Path $here "scrape_goa_pages.py"
if (-not (Test-Path $pythonw)) { throw "Not found: $pythonw" }

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$triggers = foreach ($t in $Times.Split(",")) {
  New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($t.Trim(), "HH:mm", $null))
}
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $here

Register-ScheduledTask -TaskName "99acres Goa pages" -TaskPath "\99acres\" -Action $action -Trigger $triggers `
  -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "registered '99acres Goa pages': daily at $Times (10 pages per run)"
Write-Host "Watch progress:  Get-Content '$here\data\goa_pages.log' -Wait"
