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
wsl -d $WslDistro -- bash -lc "$compose exec -T munitas-api python /verify/run_all.py" | Tee-Object -Variable output
$suiteExit = $LASTEXITCODE

# run_all.py prints its structured copy on one marked line, because /verify is
# mounted read-only in that container and it cannot write the history itself.
$marker = "##MUNITAS-VERIFY-RESULTS##"
$resultLine = $output | Where-Object { $_ -is [string] -and $_.StartsWith($marker) } | Select-Object -Last 1

Write-Host ""
if ($resultLine) {
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
& $venvPython (Join-Path $PSScriptRoot "scripts\admin\check-leftover-tenants.py")
$leakExit = $LASTEXITCODE

Write-Host ""
if ($suiteExit -eq 0 -and $leakExit -ne 0) {
    Write-Host "Every check passed, but the run left test tenants behind or could not check for them (see above). Exit 3." -ForegroundColor Red
    exit 3
}
if ($suiteExit -eq 0) {
    Write-Host "Suite passed." -ForegroundColor Green
} else {
    Write-Host "Suite reported failures. The page above shows which, and when each last failed." -ForegroundColor Yellow
}
exit $suiteExit
