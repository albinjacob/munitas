# Run the verification suite, record the result, then reclaim old canary bytes.
#
# Three steps that belong together, which is why they are one script rather
# than three things to remember:
#
#   1. Run verify/run_all.py inside the munitas-api container, where the
#      scripts can sign S3 requests properly and see the platform's own network.
#   2. Append the run to verify/history/runs.jsonl and rewrite
#      verify/history/index.html, the searchable record of every run.
#   3. Free the storage of sealed canary versions older than -ReclaimOlderThanDays.
#
# Step 3 runs whether the suite passed or failed, and that is deliberate. A
# one-day floor means a failing run's data is still there tomorrow to look at,
# while yesterday's leftovers go. Gating it on a clean run instead would need
# the reclaimer to know which versions this run created, because a pass today
# is no reason to wipe the evidence under a failure you are still reading.
#
# Pass -ReclaimOlderThanDays 0 to sweep everything eligible right now, or
# -NoReclaim to record the run and free nothing.
#
# A fourth step removes the canary tenant's old fixtures, rows and files, with scripts/admin/tidy-canary.py. It is test-harness
# housekeeping for the one tenant the suite writes into, guarded at every point to touch that tenant and nothing else, and it only
# removes what is older than -TidyCanaryOlderThanHours (2), so the run just made is still there to look at. -NoTidyCanary skips it.

param(
    [string]$WslDistro = "Ubuntu-20.04",
    [int]$ReclaimOlderThanDays = 1,
    [switch]$NoReclaim,
    [int]$TidyCanaryOlderThanHours = 2,
    [switch]$NoTidyCanary
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

. (Join-Path $PSScriptRoot "wsl-docker.ps1")
Confirm-WslStaysUp -WslDistro $WslDistro
$compose = Get-ComposePrefix -WslDistro $WslDistro -ProjectRoot $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "No .venv at $venvPython. The history page and the reclaimer both run on the host and need it."
}

Write-Host "Running the verification suite inside the munitas-api container..."
Write-Host ""
# Anything the suite writes to stderr (a check's traceback, a library warning) must not stop this script. With the error preference at Stop and the
# caller redirecting streams, which any automation does, one line on stderr became a terminating error here: the run was neither recorded nor
# cleaned up, and its test tenants were left behind. The suite's own exit code is what says whether it passed.
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
wsl -d $WslDistro -- bash -lc "$compose exec -T munitas-api python /verify/run_all.py" | Tee-Object -Variable output
$suiteExit = $LASTEXITCODE
$ErrorActionPreference = $previousPreference

# run_all.py prints its structured copy on one marked line, because /verify is
# mounted read-only in that container and it cannot write the history itself.
$marker = "##MUNITAS-VERIFY-RESULTS##"
$resultLine = $output | Where-Object { $_ -is [string] -and $_.StartsWith($marker) } | Select-Object -Last 1

Write-Host ""
if ($resultLine) {
    # The run's start time exactly as written, taken from the text and not parsed: Windows PowerShell turns an ISO date in JSON into a DateTime
    # and would print it differently, and the check recorded after the cleanup is tied to this run by that exact string.
    $runStartedAt = [regex]::Match($resultLine, '"started_at"\s*:\s*"([^"]+)"').Groups[1].Value
    $runJson = Join-Path ([System.IO.Path]::GetTempPath()) "munitas-verify-run.json"
    # WriteAllText, not Set-Content -Encoding utf8: Windows PowerShell's utf8
    # means utf8 with a byte order mark, and a mark at the front of a JSON file
    # is a parse error rather than whitespace.
    [System.IO.File]::WriteAllText($runJson, $resultLine.Substring($marker.Length).Trim())
    & $venvPython (Join-Path $PSScriptRoot "verify\report.py") --add $runJson
    $reportExit = $LASTEXITCODE
    Remove-Item $runJson -ErrorAction SilentlyContinue
    if ($reportExit -ne 0) {
        Write-Host "The run finished but could not be recorded (see above). Nothing was reclaimed," -ForegroundColor Yellow
        Write-Host "because reclaiming without a record would remove data no page can account for." -ForegroundColor Yellow
        exit $(if ($suiteExit) { $suiteExit } else { 1 })
    }
    Write-Host "  file:///$(((Join-Path $PSScriptRoot 'verify\history\index.html')).Replace('\','/'))"
} else {
    Write-Host "The suite printed no results line, so this run was not recorded." -ForegroundColor Yellow
    Write-Host "That means it died before finishing rather than failing a check. The" -ForegroundColor Yellow
    Write-Host "output above is the whole story; nothing was reclaimed." -ForegroundColor Yellow
    exit $(if ($suiteExit) { $suiteExit } else { 1 })
}

if (-not $NoReclaim) {
    Write-Host ""
    Write-Host "Freeing sealed canary storage older than $ReclaimOlderThanDays day(s)..."
    & $venvPython (Join-Path $PSScriptRoot "scripts\admin\reclaim-storage.py") `
        --older-than $ReclaimOlderThanDays `
        --reason "canary verification leftovers, reclaimed by run-verification.ps1"
}

# The canary tenant's old fixtures. After the record was kept, like the reclaim above, and for the same reason. A failure here is
# reported and does not change the suite's own result: the suite passed or failed on its checks, and housekeeping is not one of them.
if (-not $NoTidyCanary) {
    Write-Host ""
    Write-Host "Removing canary fixtures older than $TidyCanaryOlderThanHours hour(s)..."
    & $venvPython (Join-Path $PSScriptRoot "scripts\admin\tidy-canary.py") --apply --older-than-hours $TidyCanaryOlderThanHours
    if ($LASTEXITCODE -ne 0) {
        Write-Host "The canary tidy did not finish (exit $LASTEXITCODE). The suite's result is unchanged; the output above says why." -ForegroundColor Yellow
    }
}

# The probe tenants this run minted, and their buckets. Here rather than left
# to somebody's memory because the cost of forgetting is not untidiness: each
# bucket is a SeaweedFS collection holding its own volumes out of a fixed
# pool, whether it stores anything or not, and a pool with none left fails
# every write with an InternalError that names nothing about volumes. Twenty-
# two buckets holding well under a megabyte between them was enough to stop
# U65 dead, and it read like broken storage rather than a full one.
#
# Unconditional, unlike the reclaim above: it runs whether the suite passed
# or failed, because a failing run leaves the same buckets behind as a
# passing one. It never touches a tenant that is not probe-named.
Write-Host ""
Write-Host "Sweeping the probe tenants this run left behind..."
& $venvPython (Join-Path $PSScriptRoot "scripts\admin\tidy-probes.py") --apply

# Last, after every cleanup above, so it sees only what none of them could remove: a check that made a tenant and never cleaned it up. Finding
# these used to be luck. A leak makes an otherwise passing run exit 3, and so does not being able to look (exit 2 from the script), because a
# run that cannot show it left nothing behind has not shown it.
Write-Host ""
Write-Host "Checking that no test tenants were left behind..."
$leakTimer = [System.Diagnostics.Stopwatch]::StartNew()
$leakOutput = @(& $venvPython (Join-Path $PSScriptRoot "scripts\admin\check-leftover-tenants.py") 2>&1 | ForEach-Object { "$_" })
$leakExit = $LASTEXITCODE
$leakTimer.Stop()
$leakOutput | ForEach-Object { Write-Host $_ }

# The run was recorded before any cleanup ran (cleanup deletes data, so nothing is deleted that no page can account for), which is why this result
# cannot be in the run's own line. The record is append-only, so it goes in a second line tied to that run, and the history page shows it with the
# run. If it cannot be recorded, that is a failure of the run in the same way an unrecorded run is: a leak the page does not show is a leak nobody sees.
$leakOk = ($leakExit -eq 0)
$leakDetail = if ($leakOk) { "" } elseif ($leakExit -eq 2) { "could not look for leftover tenants, so nothing is known" } else { (($leakOutput | Select-Object -First 8) -join " | ") }
$afterRecorded = $false
if ($runStartedAt) {
    $afterRecord = @{
        run_started_at = $runStartedAt
        recorded_at    = (Get-Date).ToUniversalTime().ToString("o")
        results        = @(@{
            check   = "LEAK"
            label   = "no test tenants left behind"
            script  = "scripts/admin/check-leftover-tenants.py"
            status  = $(if ($leakOk) { "pass" } else { "fail" })
            passed  = $(if ($leakOk) { 1 } else { 0 })
            failed  = $(if ($leakOk) { 0 } else { 1 })
            skipped = 0
            seconds = [math]::Round($leakTimer.Elapsed.TotalSeconds, 2)
            detail  = $leakDetail
        })
    }
    $afterJson = Join-Path ([System.IO.Path]::GetTempPath()) "munitas-verify-after.json"
    [System.IO.File]::WriteAllText($afterJson, ($afterRecord | ConvertTo-Json -Depth 5 -Compress))
    & $venvPython (Join-Path $PSScriptRoot "verify\report.py") --add-after $afterJson
    $afterRecorded = ($LASTEXITCODE -eq 0)
    Remove-Item $afterJson -ErrorAction SilentlyContinue
}
if (-not $afterRecorded) {
    Write-Host "The leftover-tenant result could not be recorded on the history page (see above)." -ForegroundColor Yellow
}

Write-Host ""
if ($suiteExit -eq 0 -and (-not $leakOk -or -not $afterRecorded)) {
    Write-Host "Every check passed, but the run left test tenants behind, could not check for them, or could not record the result (see above). Exit 3." -ForegroundColor Red
    exit 3
}
if ($suiteExit -eq 0) {
    Write-Host "Suite passed." -ForegroundColor Green
} else {
    Write-Host "Suite reported failures. The page above shows which, and when each last failed." -ForegroundColor Yellow
}
exit $suiteExit
