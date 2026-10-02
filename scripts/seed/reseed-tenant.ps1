# Rebuild one worked example from scratch, in the order that actually works.
#
# Not scripts/seed/reset-platform.ps1, which drops the whole volume and takes every tenant
# with it. This rebuilds a single tenant and leaves the others alone.
#
# The order is the entire point of this file. Rebuilding `finance` by hand
# got it wrong twice, each time in a way that looked fine and was not:
#
#   1. Re-applying the SQL seed restores the people but not
#      `directory.kratos_identity_id`, the column that connects a login to a
#      person. Without it every human-decision endpoint answers 401, so the
#      seeded access requests are never created. The seed printed them as
#      pending anyway, which is now a hard failure rather than a sentence
#      (see scripts/seed/seed_common.py's expect()).
#
#   2. Seeding data before anything resolves the tenant's bucket leaves it
#      grandfathered onto the shared bucket forever. seed-*-example.py
#      registers versions through the API with a declared manifest, so a
#      dataset_version row exists before the first bucket lookup, and
#      seaweed.py's resolver reads "has data, no provisioning row" as "this
#      tenant predates per-tenant buckets". Resolving once while the tenant
#      is still empty is what makes it provision instead of infer.
#
# So: delete, restore the people, link the logins, provision the bucket,
# then seed the data. Every step verified at the end rather than assumed.
#
#   .\scripts\seed\reseed-tenant.ps1 -Tenant finance          # dry run, changes nothing
#   .\scripts\seed\reseed-tenant.ps1 -Tenant finance -Force   # actually rebuild
#
# scripts/admin/nuke-tenant.py refuses a tenant whose purpose is `production`, which both
# worked examples are. This script sets the purpose to `canary` for the
# deletion, and the SQL seed sets it back to `production` on insert, so the
# flip is undone by the rebuild rather than by remembering to undo it. That
# is a guard being deliberately stepped around, which is why it is written
# here in full rather than buried in a flag.

param(
    [Parameter(Mandatory = $true)][ValidateSet("health", "finance", "harbour")][string]$Tenant,
    [switch]$Force,
    [string]$WslDistro = "Ubuntu-20.04"
)

$ErrorActionPreference = "Stop"
# This file lives in scripts\seed; every path below is from the repo root.
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Ports = Get-Content (Join-Path $RepoRoot "config.json") | ConvertFrom-Json
Set-Location $RepoRoot

. (Join-Path $RepoRoot "wsl-docker.ps1")
$compose = Get-ComposePrefix -WslDistro $WslDistro -ProjectRoot $RepoRoot

# Which files build which tenant. Adding a third worked example means one
# entry here, not a new script.
$Recipes = @{
    "health"    = @{ Sql = "infra\postgres\seed-organisation.sql"; Example = "scripts\seed\seed-health-example.py" }
    "finance" = @{ Sql = "infra\postgres\seed-finance.sql";      Example = "scripts\seed\seed-finance-example.py" }
    "harbour" = @{ Sql = "infra\postgres\seed-harbour.sql";      Example = "scripts\seed\seed-harbour-example.py" }
}
$recipe = $Recipes[$Tenant]

$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "No .venv at $venvPython. Every step below runs on the host and needs it."
}

if (-not $env:PG_DSN) { $env:PG_DSN = "postgresql://munitas:munitas@localhost:$($Ports.postgres)/platform" }
if (-not $env:S3_ENDPOINT) { $env:S3_ENDPOINT = "http://localhost:$($Ports.seaweedfs_s3)" }

# SQL goes in on stdin, never on the command line. It travels through
# PowerShell, then wsl, then bash, then docker, and every one of those has an
# opinion about quotes; a statement containing 'finance' does not survive the
# trip intact. Nothing quotes stdin.
function Get-Scalar([string]$Sql) {
    $out = $Sql | wsl -d $WslDistro -- bash -lc "$compose exec -T postgres psql -U munitas -d platform -t -A -f -"
    if ($null -eq $out) { return "" }
    return ([string]($out | Select-Object -Last 1)).Trim()
}

Write-Host "Rebuilding '$Tenant' from $($recipe.Sql) and $($recipe.Example)."
Write-Host ""
Write-Host "Before:" -ForegroundColor Cyan
Write-Host ("  datasets        " + (Get-Scalar "select count(*) from dataset where tenant_id = '$Tenant'"))
Write-Host ("  versions        " + (Get-Scalar "select count(*) from dataset_version where tenant_id = '$Tenant'"))
Write-Host ("  lease requests  " + (Get-Scalar "select count(*) from lease_request where tenant_id = '$Tenant'"))
Write-Host ("  bucket          " + (Get-Scalar "select coalesce(max(bucket), '(none)') from tenant_storage_provision where tenant_id = '$Tenant' and backend = 'seaweedfs'"))

if (-not $Force) {
    Write-Host ""
    Write-Host "Dry run. Nothing changed. Pass -Force to rebuild, which permanently"
    Write-Host "deletes everything above, including sealed versions and their audit rows."
    exit 0
}

Write-Host ""
# A tenant that does not exist yet has nothing to delete: this is the first
# build rather than a rebuild. nuke-tenant.py refuses a missing tenant, so the
# step is skipped here rather than let that refusal stop the build.
if ((Get-Scalar "select count(*) from tenant where id = '$Tenant'") -eq "0") {
Write-Host "1/5 '$Tenant' does not exist yet, so there is nothing to delete."
} else {
Write-Host "1/5 deleting '$Tenant'..." -ForegroundColor Yellow
Get-Scalar "update tenant set purpose = 'canary' where id = '$Tenant'" | Out-Null
# scripts/admin/nuke-tenant.py asks for the id to be typed back even under --force, and
# that prompt is the guard working. It is answered here rather than removed,
# through cmd's echo: piping a bare string from PowerShell to a native
# process sends no trailing newline, so `input()` reads an empty line and the
# deletion is refused. The script is not weakened, only answered.
cmd /c "echo $Tenant| `"$venvPython`" scripts\admin\nuke-tenant.py --tenant $Tenant --force"
if ($LASTEXITCODE -ne 0) { throw "scripts/admin/nuke-tenant.py failed; nothing further was done." }
}

# Harbour is deleted by the closing walkthrough, and a deletion leaves a record that nothing may delete.
# Rebuilding the demonstration organisation under the same name is a deliberate reset, so the earlier
# record of its deletion is cleared inside one transaction, which puts the rule back whatever happens.
# Only this organisation: any other record, and its audit rows, stay exactly as written.
if ($Tenant -eq "harbour") {
    Write-Host "    clearing the earlier record that harbour was deleted..."
    Get-Scalar "begin; alter table tenant_deletion_record disable rule tenant_deletion_record_no_delete; delete from access_decision where tenant_id in (select tenant_id from tenant_deletion_record where original_tenant_id = 'harbour' or tenant_id = 'harbour'); delete from tenant_deletion_record where original_tenant_id = 'harbour' or tenant_id = 'harbour'; alter table tenant_deletion_record enable rule tenant_deletion_record_no_delete; commit; select count(*) from tenant_deletion_record where original_tenant_id = 'harbour'" | Out-Null
}

Write-Host "2/5 restoring the people from $($recipe.Sql)..."
Get-Content (Join-Path $RepoRoot $recipe.Sql) -Raw |
    wsl -d $WslDistro -- bash -lc "$compose exec -T postgres psql -U munitas -d platform -f -" | Out-Null

Write-Host "3/5 linking the logins (directory.kratos_identity_id)..."
& $venvPython infra/kratos/seed-identities.py | Out-Null
if ($LASTEXITCODE -ne 0) { throw "seed-identities.py failed; the logins are not connected." }

Write-Host "4/5 provisioning the bucket while the tenant is still empty..."
$provision = @"
import sys
sys.path.insert(0, "/app")
from app import db, seaweed
db.pool.open()
print(seaweed.bucket("$Tenant"))
"@
$bucket = ($provision | wsl -d $WslDistro -- bash -lc "$compose exec -T munitas-api python -" |
    Select-Object -Last 1).Trim()
Write-Host "    $bucket"

Write-Host "5/5 seeding the worked example..."
& $venvPython $recipe.Example
if ($LASTEXITCODE -ne 0) { throw "$($recipe.Example) failed. The tenant is half-built; run this script again." }

# Verified, not assumed. Every count below was wrong at least once during the
# rebuild this script exists to make repeatable.
Write-Host ""
Write-Host "After:" -ForegroundColor Cyan
$datasets = Get-Scalar "select count(*) from dataset where tenant_id = '$Tenant'"
$versions = Get-Scalar "select count(*) from dataset_version where tenant_id = '$Tenant'"
$pending = Get-Scalar "select count(*) from lease_request where tenant_id = '$Tenant' and state = 'pending'"
$linked = Get-Scalar "select count(*) from directory where tenant_id = '$Tenant' and kratos_identity_id is not null"
$resolved = Get-Scalar "select coalesce(max(bucket), '(none)') from tenant_storage_provision where tenant_id = '$Tenant' and backend = 'seaweedfs'"

Write-Host "  datasets           $datasets"
Write-Host "  versions           $versions"
Write-Host "  pending requests   $pending"
Write-Host "  linked logins      $linked"
Write-Host "  bucket             $resolved"

$problems = @()
if ([int]$datasets -eq 0) { $problems += "no datasets were created" }
if ([int]$versions -eq 0) { $problems += "no versions were sealed" }
# Harbour is shut down, not worked in, so it has no request waiting by design.
if ($Tenant -ne "harbour" -and [int]$pending -eq 0) { $problems += "no access request is pending, so no custodian has anything to decide" }
if ([int]$linked -eq 0) { $problems += "no login is connected to a person, so nobody can sign in as this tenant" }
if ($resolved -eq "(none)") {
    $problems += "this tenant resolves to '$resolved' rather than a bucket of its own"
}

if ($problems.Count -gt 0) {
    Write-Host ""
    Write-Host "Rebuilt, but not correctly:" -ForegroundColor Red
    foreach ($p in $problems) { Write-Host "  - $p" -ForegroundColor Red }
    exit 1
}

Write-Host ""
Write-Host "'$Tenant' rebuilt and checked. Console: http://localhost:$($Ports.console_dev)" -ForegroundColor Green
