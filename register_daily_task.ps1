# One-time registration of the daily ingest in Windows Task Scheduler.
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\register_daily_task.ps1 [-At "07:00"]
# Re-running updates the existing task (-Force). Runs as the current user
# while logged on; missed triggers start as soon as the machine is available.
param(
    [string]$At = "07:00",
    [string]$TaskName = "LoLDraftTool Daily Ingest"
)

$runner = Join-Path $PSScriptRoot "daily_run.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`"" `
    -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
# AllowStartIfOnBatteries/DontStopIfGoingOnBatteries: this runs on a laptop
# that is usually unplugged at 07:00 — the defaults would silently skip the
# run until it next went on AC. StartWhenAvailable still catches missed runs.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Force `
    -Description "LoL soloq meta pipeline: daily ladder/match ingest + stats rebuild" | Out-Null
Write-Host "Registered '$TaskName' daily at $At for user $env:USERNAME."
Write-Host "Reminder: a dev key still needs a fresh .riot_key every 24h or the run no-ops with a clear log line."
