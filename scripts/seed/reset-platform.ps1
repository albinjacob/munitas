# Start the whole platform over from an empty database.
#
# This is not a reset of one organisation. It removes the Postgres volume,
# and that volume holds every database the stack uses: the platform's own
# (every organisation, dataset, version, lease and audit record), Kratos's
# (every login), Temporal's (every workflow's history), MLflow's and Label
# Studio's. To rebuild a single organisation and leave the rest alone, use
# scripts/seed/reseed-tenant.ps1 instead.
#
# Before anything is changed it prints exactly what will be deleted, counted
# from the live database, what comes back on its own, and what it does not
# touch, then asks for the word reset. There is deliberately no flag to skip
# that question.
#
# Why a reset is the only way to clear some things: sealed dataset versions
# cannot be deleted. `DELETE` against one returns `DELETE 0` and changes
# nothing, because a rewrite rule in the schema turns it into a no-op. That is
# the guarantee V1 verifies: a version that could be deleted by whoever could
# reach the database is not an immutable version. Directory entries that
# approved a lease cannot be deleted either, for the same reason one layer up.
#
# You almost certainly do not need this. The seeded organisations already keep
# their data apart:
#
#   health   the hospital, infra/postgres/seed-organisation.sql
#   finance  the card-payments company, infra/postgres/seed-finance.sql
#   canary   every verification run, infra/postgres/seed-canary.sql
#
# Any other organisation (t1 from before the split, anything made with
# scripts/admin/create-tenant.py, leftover probe tenants) is not in a seed
# file, so a reset removes it for good.
#
# So instead of starting over:
#
#   * To rebuild one worked example, run scripts/seed/reseed-tenant.ps1.
#   * To free space, run scripts/admin/reclaim-storage.py. It deletes the
#     stored objects of old canary versions and keeps every row, which is
#     where the space actually goes.
#   * To remove leftover probe tenants, run scripts/admin/tidy-probes.py.
#
# What happens, in order, stopping at the first step that fails:
#
#   1. docker compose down
#   2. remove the Postgres volume
#   3. docker compose up -d, which recreates every database and runs the
#      schema and all three seed files automatically
#   4. wait for the API to answer
#   5. reconnect every seeded person to a login (infra/kratos/seed-identities.py),
#      because the logins were in the volume too
#   6. count what came back and print it
#
# Docker runs inside WSL2 on this machine, so every docker command goes
# through wsl-docker.ps1, the same as start-dev.ps1.
#
#   .\scripts\seed\reset-platform.ps1

param(
    [string]$WslDistro = "Ubuntu-20.04"
)

$ErrorActionPreference = "Stop"
# This file lives in scripts\seed; every path below is from the repo root.
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Ports = Get-Content (Join-Path $RepoRoot "config.json") | ConvertFrom-Json
Set-Location $RepoRoot

. (Join-Path $RepoRoot "wsl-docker.ps1")
$compose = Get-ComposePrefix -WslDistro $WslDistro -ProjectRoot $RepoRoot
$volume = "munitas_pgdata"

$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "No .venv at $venvPython. Reconnecting the logins runs on the host and needs it. Nothing was changed."
}
if (-not $env:PG_DSN) { $env:PG_DSN = "postgresql://munitas:munitas@localhost:$($Ports.postgres)/platform" }

# Runs one step inside the distro and stops the script if it fails. Native
# commands do not throw in Windows PowerShell, so without this check every
# step could fail and the script would still reach "Done".
function Invoke-InWsl([string]$Command, [string]$Step) {
    wsl -d $WslDistro -- bash -lc $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Step failed: $Step (exit $LASTEXITCODE). Stopped here; the steps after it were not run."
    }
}

# SQL goes in on stdin, never on the command line, for the reason
# reseed-tenant.ps1 gives: quotes do not survive PowerShell, wsl, bash and
# docker in a row.
function Get-Scalar([string]$Sql) {
    $out = $Sql | wsl -d $WslDistro -- bash -lc "$compose exec -T postgres psql -U munitas -d platform -t -A -f -"
    if ($null -eq $out) { return "" }
    return ([string]($out | Select-Object -Last 1)).Trim()
}

function Get-Rows([string]$Sql) {
    $out = $Sql | wsl -d $WslDistro -- bash -lc "$compose exec -T postgres psql -U munitas -d platform -t -A -F '|' -f -"
    if ($LASTEXITCODE -ne 0 -or $null -eq $out) { return @() }
    return @($out | Where-Object { $_ -and $_.Trim() })
}

Invoke-InWsl "docker volume inspect $volume > /dev/null" "find the database volume '$volume'"

# The scope, from the live database, before anything is touched.
# Which organisations come back is read, not listed: the seed files Compose
# mounts into the database's first-start folder, and the tenant each one
# inserts. Adding or renaming a seed file changes this without an edit here.
$seedFiles = @(Select-String -Path (Join-Path $RepoRoot "docker-compose.yml") `
        -Pattern '\./(\S+?\.sql):/docker-entrypoint-initdb\.d/' |
    ForEach-Object { $_.Matches[0].Groups[1].Value })
$seeded = @($seedFiles | ForEach-Object {
        $sql = Get-Content (Join-Path $RepoRoot $_) -Raw
        [regex]::Matches($sql, "(?is)insert\s+into\s+tenant\b[^;]*?values\s*\(\s*'([^']+)'") |
            ForEach-Object { $_.Groups[1].Value }
    } | Sort-Object -Unique)
if ($seeded.Count -eq 0) {
    throw "Could not find which organisations the seed files create, so the warning below could not be accurate. Nothing was changed."
}
# @(...) around each call: a query with one row comes back from a PowerShell
# function as a bare string, and indexing a string gives its first character.
$orgs = @(Get-Rows @"
select t.id, t.purpose,
  (select count(*) from dataset d where d.tenant_id = t.id),
  (select count(*) from dataset_version v where v.tenant_id = t.id),
  (select count(*) from lease_request l where l.tenant_id = t.id),
  (select count(*) from directory p where p.tenant_id = t.id and p.kind = 'human')
from tenant t order by t.id
"@)
$totals = @(Get-Rows @"
select (select count(*) from access_decision), (select count(*) from role_grant),
       (select count(*) from agent), (select count(*) from pipeline),
       (select count(*) from directory where kratos_identity_id is not null)
"@)

Write-Host ""
Write-Host "RESET THE WHOLE PLATFORM" -ForegroundColor Red
Write-Host ""
Write-Host "WHAT THIS MEANS" -ForegroundColor Red
if ($orgs.Count -eq 0) {
    Write-Host "  The database is not answering, so nothing could be counted. Everything in"
    Write-Host "  it is still deleted: every organisation, dataset, version and audit record."
} else {
    $rows = $orgs | ForEach-Object { , ($_ -split '\|') }
    $datasets = ($rows | ForEach-Object { [int]$_[2] } | Measure-Object -Sum).Sum
    $versions = ($rows | ForEach-Object { [int]$_[3] } | Measure-Object -Sum).Sum
    $lost = @($rows | Where-Object { $seeded -notcontains $_[0] } | ForEach-Object { $_[0] })
    Write-Host "  * All $datasets datasets and $versions sealed versions, in all $($orgs.Count) organisations,"
    Write-Host "    are deleted permanently. There is no undo and no backup."
    Write-Host "  * The full audit trail goes with them: who asked for what, who approved it,"
    Write-Host "    and every access decision ever made."
    if ($lost.Count -gt 0) {
        $noun = if ($lost.Count -eq 1) { "organisation is" } else { "organisations are" }
        Write-Host "  * $($lost.Count) $noun not in a seed file and will not come back."
        Write-Host "    They are marked (not recreated) below."
    }
}
Write-Host "  * Everyone is logged out. Seeded people get their logins back; any login"
Write-Host "    added by hand is gone."
Write-Host "  * Any pipeline or agent run still in progress is lost with Temporal's history."
Write-Host "  * The console will show organisations and people but no data until you"
Write-Host "    load a worked example again."
Write-Host ""
Write-Host "WILL BE DELETED, IN DETAIL" -ForegroundColor Yellow
if ($orgs.Count -gt 0) {
    Write-Host ("  {0,-34} {1,-11} {2,8} {3,9} {4,9} {5,7}" -f "Organisation", "Purpose", "Datasets", "Versions", "Requests", "People")
    foreach ($c in $rows) {
        $back = if ($seeded -contains $c[0]) { "" } else { "  (not recreated)" }
        Write-Host ("  {0,-34} {1,-11} {2,8} {3,9} {4,9} {5,7}{6}" -f $c[0], $c[1], $c[2], $c[3], $c[4], $c[5], $back)
    }
    if ($totals.Count -gt 0) {
        $t = $totals[0] -split '\|'
        Write-Host ""
        Write-Host "  Across all of them: $($t[0]) access decisions, $($t[1]) role grants,"
        Write-Host "  $($t[2]) registered agents, $($t[3]) pipelines, and $($t[4]) people's links to their logins."
    }
}
Write-Host "  Also in the same volume: every login (Kratos), every workflow's history"
Write-Host "  (Temporal), and all MLflow and Label Studio data."
Write-Host ""
Write-Host "COMES BACK ON ITS OWN" -ForegroundColor Green
Write-Host "  The organisations $($seeded -join ', '), with their departments and people,"
Write-Host "  and a login for each seeded person. No datasets: load a worked example"
Write-Host "  afterwards with scripts\seed\reseed-tenant.ps1."
Write-Host ""
Write-Host "NOT TOUCHED" -ForegroundColor Cyan
Write-Host "  Stored files in SeaweedFS (they become orphaned and cannot be reclaimed"
Write-Host "  afterwards; run scripts\admin\reclaim-storage.py first if space matters),"
Write-Host "  this repository, .env, and anything outside Docker."
Write-Host ""
Write-Host "Sealed versions cannot be deleted any other way, which is the point of them."
Write-Host "To rebuild just one organisation instead, stop here and use scripts\seed\reseed-tenant.ps1."
Write-Host ""
$answer = Read-Host "Type the word reset to delete all of the above"
if ($answer -ne "reset") {
    Write-Host "Nothing was changed."
    exit 0
}

Write-Host "1/6 stopping the platform..."
Invoke-InWsl "$compose down" "docker compose down"

Write-Host "2/6 removing the database volume '$volume'..."
Invoke-InWsl "docker volume rm $volume" "remove the database volume"

Write-Host "3/6 starting again, which recreates the databases and runs the schema and seeds..."
Invoke-InWsl "$compose up -d" "docker compose up"

Write-Host "4/6 waiting for the API to answer..."
$deadline = (Get-Date).AddSeconds(120)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    try {
        if ((Invoke-WebRequest -Uri "http://localhost:$($Ports.munitas_api_http)/health" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        # Not up yet: the database is still running its first-start scripts.
    }
    Start-Sleep -Seconds 2
}
if (-not $healthy) {
    throw "The API did not answer within 120 seconds, so the logins were not reconnected. Check: wsl -d $WslDistro -- bash -lc `"$compose logs munitas-api`""
}

Write-Host "5/6 reconnecting every seeded person to a login..."
& $venvPython infra/kratos/seed-identities.py
if ($LASTEXITCODE -ne 0) {
    throw "infra/kratos/seed-identities.py failed, so nobody can log in yet. It is safe to run again on its own."
}

Write-Host "6/6 counting what came back..."
$tenants = Get-Scalar "select string_agg(id, ', ' order by id) from tenant"
$people = Get-Scalar "select count(*) from directory where kind = 'human'"
$linked = Get-Scalar "select count(*) from directory where kratos_identity_id is not null"
$versions = Get-Scalar "select count(*) from dataset_version"

Write-Host ""
Write-Host "Reset complete." -ForegroundColor Green
Write-Host "  organisations     $tenants"
Write-Host "  people            $people"
Write-Host "  with a login      $linked"
Write-Host "  dataset versions  $versions"
Write-Host ""
Write-Host "The organisations and their people are back, with no datasets yet. To load a"
Write-Host "worked example, run .\scripts\seed\reseed-tenant.ps1 -Tenant health -Force"
Write-Host "(or -Tenant finance). It provisions the storage bucket before loading data,"
Write-Host "which is the order that works."
Write-Host ""
Write-Host "Object storage was not touched: files from before are still in SeaweedFS,"
Write-Host "orphaned, because the versions that referenced them are gone. Nothing can"
Write-Host "reclaim them now, because scripts\admin\reclaim-storage.py works from the"
Write-Host "version rows this script just deleted. Reclaim first, reset second."
Write-Host ""
Write-Host "Console: http://localhost:$($Ports.console_dev)"
