# Rebuild one service's image and restart just that service, keeping the current image and exactly one older copy.
#
# Docker never deletes the old image when a name moves to a new one: it becomes an unnamed leftover and stays until it is pruned, so repeated rebuilds
# fill the disk. This keeps one way back and nothing else. Before building, the image now in use is tagged `previous` (which retires the older
# `previous` to an unnamed leftover); after the build, unnamed leftovers are removed; the build cache is held to a fixed size.
#
#     .\scripts\admin\rebuild-image.ps1                          # the API
#     .\scripts\admin\rebuild-image.ps1 -Service table-worker
#
# To go back one version: `docker tag <image>:previous <image>:latest`, then `up -d --no-deps <service>` (see RUNBOOK, "Rebuilding an image").
#
# Only the one service is restarted (`--no-deps`), because starting its dependencies again would recreate Postgres.

param(
    [string]$Service = "munitas-api",
    [string]$WslDistro = "Munitas",
    [int]$CacheGB = 5,
    [int]$HealthSeconds = 120
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $root "wsl-docker.ps1")
$compose = Get-ComposePrefix -WslDistro $WslDistro -ProjectRoot $root

function Invoke-Wsl([string]$Command) {
    # The command is sent as base64 and decoded inside the distro. Windows PowerShell 5.1 drops the double quotes inside an argument it passes to a program, and text
    # piped to one starts with a byte-order mark, so neither the command nor its quotes can go through as they are.
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Command))
    # Errors are merged into the output inside the distro: Docker prints its progress on the error stream, and under Stop PowerShell 5.1 treats any line there as a failure.
    $out = wsl -d $WslDistro -- bash -c "echo $encoded | base64 -d | bash 2>&1"
    if ($LASTEXITCODE -ne 0) { throw "failed: $Command`n$out" }
    return $out
}

# The image name Compose gives this service, read from its own configuration rather than guessed.
$image = (Invoke-Wsl "$compose config --format json | python3 -c ""import json,sys; c=json.load(sys.stdin)['services']['$Service']; print(c.get('image') or ('munitas-$Service'))""").Trim() | Select-Object -Last 1
if (-not $image) { throw "no service called $Service in docker-compose.yml" }
$repo = $image.Split(":")[0]

$had = (Invoke-Wsl "docker image inspect $repo`:latest >/dev/null 2>&1 && echo yes || echo no") | Select-Object -Last 1
if ($had -eq "yes") { Invoke-Wsl "docker tag $repo`:latest $repo`:previous" | Out-Null }

# The API image carries the client library people install in a notebook (GET /client). It is built from the library's own source first, so the image
# always holds the current version, not whatever was built last time.
if ($Service -eq "munitas-api") {
    $python = Join-Path $root ".venv\Scripts\python.exe"
    Write-Host "Building the client library ..."
    & $python (Join-Path $root "scripts\build-client-wheel.py")
    if ($LASTEXITCODE -ne 0) { throw "the client library did not build" }
}

Write-Host "Building $Service ($repo) ..."
Invoke-Wsl "$compose build $Service" | Select-Object -Last 2 | ForEach-Object { Write-Host "  $_" }
Invoke-Wsl "$compose up -d --no-deps $Service" | Select-Object -Last 2 | ForEach-Object { Write-Host "  $_" }

Write-Host "Clearing leftovers ..."
Invoke-Wsl "docker image prune -f" | Select-Object -Last 1 | ForEach-Object { Write-Host "  $_" }
Invoke-Wsl "docker builder prune -f --keep-storage ${CacheGB}GB" | Select-Object -Last 1 | ForEach-Object { Write-Host "  $_" }

$kept = (Invoke-Wsl "docker images $repo --format '{{.Tag}}'") -join ", "
Write-Host "Kept for ${repo}: $kept"
