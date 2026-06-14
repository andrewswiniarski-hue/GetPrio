# Daily ingest: ladder -> matches -> parse -> stats.
# Registered with Task Scheduler by register_daily_task.ps1; run manually with:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\daily_run.ps1 [-MatchLimit 200]
#
# Secrets come from gitignored files next to this script:
#   .riot_key       current Riot API key (dev keys expire every 24h - refresh daily!)
#   .database_url   postgresql://user:pass@localhost:5432/lol_draft_tool
param([int]$MatchLimit = 200)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$logDir = Join-Path $PSScriptRoot "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory $logDir | Out-Null }
$log = Join-Path $logDir ("daily_{0}.log" -f (Get-Date).ToString("yyyyMMdd_HHmmss"))
Get-ChildItem $logDir -Filter "daily_*.log" |
    Sort-Object Name -Descending | Select-Object -Skip 14 |
    ForEach-Object { try { Remove-Item $_.FullName -Force -ErrorAction Stop } catch {} }

function Log([string]$Message) {
    $line = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss") + " RUNNER " + $Message
    Add-Content -Path $log -Value $line
    Write-Host $line
}

# Heartbeat: every exit path records a glanceable status so a failed or
# stale run is visible without reading the log. A FAILED marker file is
# created on failure and cleared on success (easy to spot / script against).
$statusFile = Join-Path $logDir "last_run.json"
$failMarker = Join-Path $logDir "LAST_RUN_FAILED.txt"
function Write-Status([string]$Result, [string]$Detail) {
    $obj = [ordered]@{
        finished_at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        result      = $Result
        detail      = $Detail
        log         = (Split-Path $log -Leaf)
    }
    $obj | ConvertTo-Json | Set-Content -Path $statusFile -Encoding UTF8
    if ($Result -eq "OK") {
        if (Test-Path $failMarker) { Remove-Item $failMarker -Force }
    } else {
        Set-Content -Path $failMarker -Encoding UTF8 -Value `
            "$($obj.finished_at)  $Result  $Detail  (see $($obj.log))"
    }
}
function Fail([string]$Detail) {
    Log "FATAL: $Detail"
    Write-Status "FAILED" $Detail
    exit 1
}

# ---- secrets ----
$keyFile = Join-Path $PSScriptRoot ".riot_key"
if (Test-Path $keyFile) {
    $env:RIOT_API_KEY = (Get-Content $keyFile -Raw).Trim()
}
if (-not $env:RIOT_API_KEY) { Fail "no RIOT_API_KEY (.riot_key missing or empty)" }
$dbFile = Join-Path $PSScriptRoot ".database_url"
if (Test-Path $dbFile) {
    $env:DATABASE_URL = (Get-Content $dbFile -Raw).Trim()
}
if (-not $env:DATABASE_URL) { Fail "no DATABASE_URL (.database_url missing or empty)" }

# ---- key pre-flight: fail loudly on an expired dev key ----
try {
    Invoke-RestMethod -Uri "https://kr.api.riotgames.com/lol/status/v4/platform-data" `
        -Headers @{ "X-Riot-Token" = $env:RIOT_API_KEY } -TimeoutSec 15 | Out-Null
} catch {
    $code = 0
    if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
    if ($code -eq 401 -or $code -eq 403) {
        Fail "Riot API key rejected ($code). Dev key expired - refresh .riot_key and re-run."
    }
    Log "WARNING: key pre-flight inconclusive ($($_.Exception.Message)); continuing, fetch has its own retry"
}

# ---- pipeline ----
# Steps run through cmd so python's stderr lands in the log without
# PowerShell 5.1 wrapping each line in an ErrorRecord.
$env:PYTHONIOENCODING = "utf-8"
function Invoke-Step([string]$Name, [string]$CommandLine) {
    Log "step start: $Name"
    cmd /c "$CommandLine >> `"$log`" 2>&1"
    if ($LASTEXITCODE -ne 0) {
        Fail "step '$Name' failed with exit code $LASTEXITCODE - see log above"
    }
    Log "step ok: $Name"
}

Invoke-Step "fetch_ladder"  "python fetch_ladder.py"
Invoke-Step "fetch_matches" "python fetch_matches.py --limit $MatchLimit"
Invoke-Step "parse_matches" "python parse_matches.py"
Invoke-Step "compute_stats" "python compute_stats.py"

# ---- freshness guard: a green run that ingested nothing is still a problem ----
$freshness = "freshness unknown"
try {
    $maxDay = (cmd /c "python -c ""import os,psycopg2; c=psycopg2.connect(os.environ['DATABASE_URL']); cur=c.cursor(); cur.execute('SELECT max(game_creation)::date FROM matches'); print(cur.fetchone()[0]); c.close()""" 2>$null).Trim()
    $freshness = "newest game $maxDay"
    $age = (New-TimeSpan -Start ([datetime]$maxDay) -End (Get-Date)).Days
    if ($age -ge 2) {
        Log "WARNING: newest match is $age days old ($maxDay) - ingest may be stalling"
    }
} catch {
    Log "WARNING: freshness check failed ($($_.Exception.Message))"
}

Log "daily run complete ($freshness)"
Write-Status "OK" "ingest + stats complete; $freshness"
