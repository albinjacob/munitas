# The quick loop: run only the checks a change needs, in about a minute instead of thirteen.
#
#     .\quick-verify.ps1                  picks the checks from what changed since the last full run that passed
#     .\quick-verify.ps1 -Only U128,U129  runs exactly those checks
#     .\quick-verify.ps1 -List            says what it would run, and why, and runs nothing
#
# This is a speed-up for the loop and never a substitute for run-verification.ps1. A quick run is not recorded on the history page, because a run
# of six checks must never be read as a run of all of them, and it ends by saying so. The whole suite is what CI runs and what must pass before
# anything is pushed. When no quick set is known for a change (a file nobody has said what covers), this falls back to the whole suite itself
# and says that it did.
#
# Chosen by verify/affects.py, which explains each choice. The cheap checks that a change anywhere can break by rule (the retired-organisation
# guard, the API reference, every route knowing its caller, no tenant left behind) are always added: they are about fifteen seconds, and they
# are the ones that caught the surprises.

param(
    [string[]]$Only = @(),
    [switch]$List,
    [string]$WslDistro = "Munitas"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

. (Join-Path $PSScriptRoot "wsl-docker.ps1")

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { throw "No .venv at $venvPython." }

$ids = @()
$reasons = @()
if ($Only.Count -gt 0) {
    # Commas and spaces both separate, so -Only U128,U129 and -Only "U128 U129" mean the same.
    $ids = @($Only | ForEach-Object { $_ -split "[,\s]+" } | ForEach-Object { $_.Trim().ToUpper() } | Where-Object { $_ })
    $reasons = @("# asked for by name: " + ($ids -join ", "))
} else {
    $picked = @(& $venvPython (Join-Path $PSScriptRoot "verify\affects.py") | ForEach-Object { "$_" })
    $reasons = @($picked | Where-Object { $_.StartsWith("#") })
    $ids = @($picked | Where-Object { $_ -and -not $_.StartsWith("#") })
}

Write-Host ""
$reasons | ForEach-Object { Write-Host $_ -ForegroundColor DarkGray }
Write-Host ""

if ($ids -contains "FULL") {
    Write-Host "No quick set is known for this change, so this is a full run." -ForegroundColor Yellow
    if ($List) { exit 0 }
    & (Join-Path $PSScriptRoot "run-verification.ps1") -NoReclaim -WslDistro $WslDistro
    exit $LASTEXITCODE
}

$policyChanged = [bool]($reasons | Where-Object { $_ -match "platform/policy/" })
if ($ids.Count -eq 0 -and -not $policyChanged) {
    Write-Host "Nothing the back-end checks cover has changed since the last full run that passed. Nothing to run." -ForegroundColor Green
    exit 0
}

Write-Host ("Checks: " + ($ids -join " ") + $(if ($policyChanged) { "  and the policy tests" } else { "" }))
if ($List) { exit 0 }

Confirm-WslStaysUp -WslDistro $WslDistro
$compose = Get-ComposePrefix -WslDistro $WslDistro -ProjectRoot $PSScriptRoot

# The same guard the full run has: exactly one API container, or the checks may run against the wrong one.
$apiContainers = @(wsl -d $WslDistro -- bash -lc "docker ps --filter label=com.docker.compose.service=munitas-api --format '{{.Names}}'" | Where-Object { $_ })
if ($apiContainers.Count -ne 1) {
    throw "Expected exactly one munitas-api container and found $($apiContainers.Count): $($apiContainers -join ', ')."
}

$exit = 0
$timer = [System.Diagnostics.Stopwatch]::StartNew()
$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"

if ($policyChanged) {
    Write-Host ""
    Write-Host "Policy tests..."
    $policy = @(wsl -d $WslDistro -- bash -lc "$compose run --rm --no-deps --entrypoint /opa opa test /policy" 2>&1 | ForEach-Object { "$_" })
    $policy | Select-Object -Last 3 | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) { $exit = 1 }
}

if ($ids.Count -gt 0) {
    Write-Host ""
    Write-Host "Running $($ids.Count) check(s) inside the munitas-api container..."
    $log = Join-Path ([System.IO.Path]::GetTempPath()) "munitas-quick-verify.log"
    $lines = @(wsl -d $WslDistro -- bash -lc "$compose exec -T munitas-api python /verify/run_all.py --only $($ids -join ',')" 2>&1 | ForEach-Object { "$_" })
    if ($LASTEXITCODE -ne 0) { $exit = 1 }
    [System.IO.File]::WriteAllLines($log, $lines)
    # What a person needs: each failing assertion, then the per-script lines. The whole output is in the log.
    $lines | Where-Object { $_ -match "\[FAIL\]" } | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    $lines | Where-Object { $_ -match "^\s+(PASS|FAIL|SKIP)\s+\d" -or $_ -match "PARTIAL RUN|Total .* across" -or $_ -match "^No such check" } | ForEach-Object {
        $color = if ($_ -match "FAIL") { "Red" } elseif ($_ -match "PARTIAL") { "Yellow" } else { "Gray" }
        Write-Host $_ -ForegroundColor $color
    }
    Write-Host "  Full output: $log" -ForegroundColor DarkGray
    if (-not ($lines | Where-Object { $_ -match "Total .* across" })) {
        Write-Host "The runner printed no summary, so it died before finishing. The log says why." -ForegroundColor Red
        $exit = 1
    }
}
$ErrorActionPreference = $previous

# Cleaned up as the full run cleans up: probe tenants and their buckets, then a look for tenants left behind.
Write-Host ""
& $venvPython (Join-Path $PSScriptRoot "scripts\admin\tidy-probes.py") --apply | Out-Null
$leak = @(& $venvPython (Join-Path $PSScriptRoot "scripts\admin\check-leftover-tenants.py") 2>&1 | ForEach-Object { "$_" })
if ($LASTEXITCODE -ne 0) {
    $leak | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    Write-Host "This run left test tenants behind, or could not look." -ForegroundColor Red
    if ($exit -eq 0) { $exit = 3 }
}

$timer.Stop()
Write-Host ""
Write-Host ("Quick run took {0:N0}s. It was NOT recorded and does not replace the full suite: run .\run-verification.ps1 before pushing." -f $timer.Elapsed.TotalSeconds) -ForegroundColor Yellow
if ($exit -eq 0) { Write-Host "Every check in this quick run passed." -ForegroundColor Green } else { Write-Host "Something in this quick run failed (exit $exit)." -ForegroundColor Red }
exit $exit
