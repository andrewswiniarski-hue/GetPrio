# KR match backfill loop. Run in your own PowerShell from the repo folder:
#   $env:RIOT_API_KEY = "RGAPI-..."   # current window
#   setx RIOT_API_KEY "RGAPI-..."     # future windows
#   .\backfill.ps1
$target = 10000
foreach ($i in 1..50) {
    python fetch_matches.py --limit 25
    if ($LASTEXITCODE -ne 0) {
        Write-Host "BACKFILL STOPPED on pass $i (expired key or network issue)"
        break
    }
    $count = python -c "import psycopg2, config; c = psycopg2.connect(config.DATABASE_URL); cur = c.cursor(); cur.execute('SELECT count(*) FROM matches_raw'); print(cur.fetchone()[0]); c.close()"
    Write-Host "pass $i complete - matches_raw total: $count"
    if ([int]$count -ge $target) {
        Write-Host "TARGET REACHED: $count raw matches"
        break
    }
}
