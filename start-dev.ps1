# Bring the whole platform up: backend services, the ingestion worker, and
# the console.
#
# Three moving parts, not one, because they are not one thing. `docker
# compose up` starts everything that runs in a container: Postgres,
# SeaweedFS, Temporal, OPA, the control plane API. The worker
# (`worker/main.py`) does not: it runs on the host on purpose, so pipeline
# activities reach a GPU without CUDA-in-Docker friction, and HuggingFace
# ingestion jobs ride along on a second task queue in that same process.
# Nothing in Compose starts it, and nothing ever will without changing that
# design. The console is Vite's own dev server, also host-side, also not a
# container.
#
# Skip the worker if you only need the console and the API to look at
# existing data: `.\start-dev.ps1 -NoWorker`. Skip the console the same way
# with `-NoConsole` if you are only driving the API directly.
#
# The containerized part does not start from Windows. Docker Desktop was
# removed after its named-pipe socket broke for the fourth time, and the
# engine now runs inside WSL2. The `docker.exe` still on PATH is Docker
# Desktop's, still aimed at a pipe nothing listens on, so `docker compose`
# has to run inside the distro instead. Everything else in this script stays
# on Windows, because WSL2 forwards the published ports to `localhost`.
# Pass `-WslDistro` if yours is not named Ubuntu-20.04; `wsl -l -v` lists them.
#
# One line per component by default -- "Backend containers: started
# (Postgres, SeaweedFS, ...)", "Control plane: up (http://...)" -- not a
# transcript of every container Compose itself touches or every step along
# the way. Pass `-Detailed` for that, e.g. while debugging a slow or failing
# start.

param(
    [switch]$NoWorker,
    [switch]$NoConsole,
    [string]$WslDistro = "Ubuntu-20.04",
    # Off by default: one line per component (see Write-Component in
    # wsl-docker.ps1) is the normal report. Pass this for docker compose's
    # own streamed per-container output and the previous step-by-step
    # narration, e.g. while debugging a slow or failing start.
    [switch]$Detailed
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

. (Join-Path $PSScriptRoot "wsl-docker.ps1")

# One timed step per component, so a slow start says which part was slow
# instead of leaving that as a guess. Printed as its own report at the end,
# the same shape as the memory report below.
$timings = New-Object System.Collections.Generic.List[PSCustomObject]
function Measure-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $Action
    $sw.Stop()
    $timings.Add([PSCustomObject]@{ Label = $Label; Seconds = $sw.Elapsed.TotalSeconds })
}

Measure-Step "WSL keepalive check" { Confirm-WslStaysUp -WslDistro $WslDistro }
$compose = Get-ComposePrefix -WslDistro $WslDistro -ProjectRoot $PSScriptRoot

# config.json is the one file a person edits to resolve a port conflict;
# this turns it into .env's PORT_* block (docker-compose.yml's own ${VAR}
# substitution) and infra/kratos/kratos.yml (Kratos has no ${VAR} support
# of its own, so that file is rendered fresh from kratos.yml.template every
# time too). Needs only the standard library, so plain `python` works even
# without .venv set up yet.
Measure-Step "Render ports (config.json -> .env, kratos.yml)" {
    $portsPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $portsPython)) { $portsPython = "python" }
    & $portsPython (Join-Path $PSScriptRoot "scripts\render_ports_env.py")
    if ($LASTEXITCODE -ne 0) { throw "scripts\render_ports_env.py failed; fix config.json and try again." }
}
$Ports = Get-Content (Join-Path $PSScriptRoot "config.json") | ConvertFrom-Json

$backendOk = $false
Measure-Step "Backend containers (docker compose up)" {
    # --progress plain, not Compose's default animated redraw: see
    # Invoke-ComposeQuiet's own comment in wsl-docker.ps1 for why an
    # animated UI is worse than the plain-mode text it replaces, even
    # captured rather than streamed. Captured either way now: shown only on
    # failure or with -Detailed, one line per component otherwise.
    $backendOk = Invoke-ComposeQuiet -WslDistro $WslDistro -Command "$compose --progress plain up -d" -Detailed $Detailed
    if (-not $backendOk) {
        throw "docker compose up failed inside '$WslDistro'. If the distro is running but the daemon is not, start it with: wsl -d $WslDistro -- sudo service docker start"
    }
}
Write-Component "Backend containers" "started" "Postgres, SeaweedFS, Temporal, OPA, the API"

# Fully generated output, not prose a person authors (every page says so in
# its own footer), so unlike ARCHITECTURE.md's former Key URLs table this is
# exactly the .env/kratos.yml shape: nobody hand-edits it, so nothing is lost
# by a start script rewriting it. Run here, right after Postgres is up
# (docker-compose.yml gates the API and everything else behind its own
# healthcheck, so it already is by this point) rather than earlier alongside
# the ports render, because this reads the live database for roles and
# departments -- degrading gracefully, not failing, if that read comes up
# short (see the script's own docstring).
Measure-Step "Service directory (docs/internal/dev/services.html, logins.html)" {
    $docsPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $docsPython)) { $docsPython = "python" }
    & $docsPython (Join-Path $PSScriptRoot "docs\tools\generate_services_page.py") | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Component "Service directory" "could not regenerate" "docs/internal/dev/services.html and logins.html are unchanged; see docs/tools/generate_services_page.py" -Warn
    } else {
        Write-Component "Service directory" "regenerated" "docs/internal/dev/services.html, docs/internal/dev/logins.html"
    }
}

# MLflow is in the 'full' profile, so the line above does not start it, but a
# de-identification run cannot finish without it: verify writes each run's
# score card there and the gate decision cites that card. Named on its own
# rather than the whole profile, which would also bring up Jaeger, Label
# Studio and the collector, none of which a run needs.
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$mlflowOk = Invoke-ComposeQuiet -WslDistro $WslDistro -Command "$compose --progress plain --profile full up -d mlflow" -Detailed $Detailed
if (-not $mlflowOk) {
    # Seen on this machine: a plain `docker compose down` removes the networks
    # but leaves a profiled container behind, still naming networks that no
    # longer exist, and Compose reuses it rather than making a new one. Its
    # state lives in Postgres and SeaweedFS, not the container, so a fresh one
    # loses nothing.
    Write-Component "MLflow container" "existing container failed; recreating once" -Warn
    $mlflowOk = Invoke-ComposeQuiet -WslDistro $WslDistro -Command "$compose --progress plain --profile full up -d --force-recreate mlflow" -Detailed $Detailed
    if (-not $mlflowOk) {
        throw "docker compose could not start MLflow inside '$WslDistro', even as a new container."
    }
}
$sw.Stop()
$timings.Add([PSCustomObject]@{ Label = "MLflow container (docker compose up)"; Seconds = $sw.Elapsed.TotalSeconds })
Write-Component "MLflow container" "started" "where de-identification runs record their scores"

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$deadline = (Get-Date).AddSeconds(90)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:$($Ports.munitas_api_http)/health" -UseBasicParsing -TimeoutSec 3
        if ($response.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        # Not up yet. Tried again next loop rather than treated as fatal:
        # the container can take a few seconds after `docker compose up`
        # reports done.
    }
    Start-Sleep -Seconds 2
}
$sw.Stop()
$timings.Add([PSCustomObject]@{ Label = "API health wait"; Seconds = $sw.Elapsed.TotalSeconds })
if (-not $healthy) {
    Write-Component "Control plane" "did not answer within 90s" "wsl -d $WslDistro -- bash -lc `"$compose logs munitas-api`"" -Warn
} else {
    Write-Component "Control plane" "up" "http://localhost:$($Ports.munitas_api_http)"
}

# MLflow installs its database driver each time it starts, so it answers
# later than the API. Waited for, but not required: everything except a
# de-identification run works without it, and the console says so plainly if
# one is started too early.
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$mlflowDeadline = (Get-Date).AddSeconds(180)
$mlflowUp = $false
while ((Get-Date) -lt $mlflowDeadline) {
    try {
        if ((Invoke-WebRequest -Uri "http://localhost:$($Ports.mlflow)/health" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) {
            $mlflowUp = $true
            break
        }
    } catch {
        # Still installing or starting.
    }
    Start-Sleep -Seconds 3
}
$sw.Stop()
$timings.Add([PSCustomObject]@{ Label = "MLflow health wait"; Seconds = $sw.Elapsed.TotalSeconds })
if ($mlflowUp) {
    Write-Component "MLflow" "up" "http://localhost:$($Ports.mlflow)"
} else {
    Write-Component "MLflow" "did not answer within 3 minutes" "de-identification runs refused until it does; wsl -d $WslDistro -- bash -lc `"$compose --profile full logs mlflow`"" -Warn
}

$workerStarted = $false
$sandboxStarted = $false
$consoleStarted = $false
$workerWindowId = $null
$sandboxWindowId = $null
$consoleWindowId = $null

if (-not $NoWorker) {
    # Two worker processes now, not one. The host worker runs the pipeline,
    # HuggingFace ingestion and native agent runs on Windows, next to the GPU.
    # The sandbox worker runs inside the distro, next to the Docker socket,
    # because sandboxed agent runs need Docker and the engine has no TCP
    # listener the Windows side could reach. See worker/sandbox_worker.py.
    $venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    # Checked first, the same reason Confirm-WslStaysUp checks before starting
    # a keepalive: running this script a second time without stopping the
    # first used to launch a second host worker silently, both then polling
    # Temporal for the same work. Matched on both name and its exact venv
    # path, the same pair stop-dev.ps1 checks, so "already running" means the
    # same thing in both scripts.
    $existingWorker = Get-CimInstance Win32_Process -Filter "name='python.exe'" |
        Where-Object { $_.ExecutablePath -eq $venvPython -and $_.CommandLine -match "worker\.main" } |
        Select-Object -First 1
    if ($existingWorker) {
        Write-Component "Host worker" "already running" "process $($existingWorker.ProcessId), not starting a second one"
    } elseif (-not (Test-Path $venvPython)) {
        Write-Component "Host worker" "skipped, no .venv at $venvPython" "see worker/requirements.txt" -Warn
    } else {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        # The worker reads no .env of its own, so the data folder is handed
        # to it here: its corpus and work folders sit under it.
        $dataRoot = Get-DataRoot -ProjectRoot $PSScriptRoot
        $workerProc = Start-Process powershell -PassThru -ArgumentList @(
            "-NoExit", "-Command",
            "Set-Location '$PSScriptRoot'; `$env:MUNITAS_DATA = '$dataRoot'; & '$venvPython' -m worker.main"
        )
        $workerWindowId = $workerProc.Id
        $workerStarted = $true
        $sw.Stop()
        # Only the window's own launch, not how long the worker takes to
        # finish importing once inside it -- that part is covered by the
        # "give it a few seconds" wait and its own memory report below.
        $timings.Add([PSCustomObject]@{ Label = "Host worker window launch"; Seconds = $sw.Elapsed.TotalSeconds })
        Write-Component "Host worker" "started" "pipeline + HuggingFace fetch + native agent runs, new window"
    }

    # The sandbox worker lives in the distro. Its Python is a provisioned venv
    # (the distro's own Python is 3.8, too old); worker/requirements-sandbox.txt
    # documents the one-time setup. MUNITAS_WORK must be a distro path: the run
    # directory is bind-mounted into the sandbox containers by the distro's own
    # daemon, which cannot make sense of a Windows drive path.
    # Checked first, the same reason as the host worker above: this script
    # run twice used to start a second sandbox worker inside the distro
    # silently, both then competing for the same Temporal task queue.
    # Matched by real cwd, not just the module name, the same way
    # stop-dev.ps1 tells this project's own sandbox worker apart from
    # another Munitas checkout's on the same distro.
    $repoInWsl = (ConvertTo-WslPath -WslDistro $WslDistro -WindowsPath $PSScriptRoot).Trim()
    $existingSandboxWorker = $null
    # Anchored on the interpreter, so it matches the worker process and not the
    # wrapper shell whose command line merely contains the same words. Matching
    # the wrapper made a worker that had died under a live wrapper look like one
    # that was still running, and it was never started again.
    foreach ($line in @(wsl -d $WslDistro -- bash -lc "pgrep -af '^[^ ]*python[0-9.]* -m worker\.sandbox_worker' 2>/dev/null")) {
        if ($line -match '^(\d+)\s') {
            $candidateId = $Matches[1]
            $cwd = (wsl -d $WslDistro -- bash -lc "readlink -f /proc/$candidateId/cwd 2>/dev/null" | Select-Object -First 1)
            if ($cwd -and $cwd.Trim() -eq $repoInWsl) {
                $existingSandboxWorker = $candidateId
                break
            }
        }
    }
    wsl -d $WslDistro -- bash -c 'test -x "$HOME/.munitas/sandbox-venv/bin/python"'
    if ($existingSandboxWorker) {
        Write-Component "Sandbox worker (WSL)" "already running" "process $existingSandboxWorker, not starting a second one"
    } elseif ($LASTEXITCODE -ne 0) {
        Write-Component "Sandbox worker (WSL)" "skipped, no venv in '$WslDistro'" "sandboxed agent runs will not run" -Warn
        # Actionable fix-it steps, not step-by-step narration -- shown
        # unconditionally, the same as any other error this script surfaces,
        # not gated behind -Detailed.
        Write-Line "  Provision it once, inside the distro (see worker/requirements-sandbox.txt):" -ForegroundColor Yellow
        Write-Line "    curl -LsSf https://astral.sh/uv/install.sh | sh" -ForegroundColor Yellow
        Write-Line "    `$HOME/.local/bin/uv venv --python 3.12 `$HOME/.munitas/sandbox-venv" -ForegroundColor Yellow
        Write-Line "    `$HOME/.local/bin/uv pip install --python `$HOME/.munitas/sandbox-venv/bin/python -r worker/requirements-sandbox.txt" -ForegroundColor Yellow
    } else {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $workInWsl = ConvertTo-WslPath -WslDistro $WslDistro -WindowsPath "$(Get-DataRoot -ProjectRoot $PSScriptRoot)/work"
        # Its log file sits beside the host worker's, under the data folder,
        # so a closed window does not take the evidence with it.
        $logsInWsl = ConvertTo-WslPath -WslDistro $WslDistro -WindowsPath "$(Get-DataRoot -ProjectRoot $PSScriptRoot)/logs"
        # wsl.exe is launched directly, not through a second PowerShell. A
        # PowerShell window re-parses the command: Start-Process drops the
        # inner quotes, so it read bash's && as its own syntax, and it expanded
        # $HOME to the Windows profile path. Either one stopped the worker
        # before it started. The trailing read keeps the window open after the
        # worker exits, so a refusal stays on screen to be read.
        $sandboxCmd = "cd '$repoInWsl' && MUNITAS_WORK='$workInWsl' MUNITAS_LOG_DIR='$logsInWsl' `$HOME/.munitas/sandbox-venv/bin/python -m worker.sandbox_worker; echo; read -p 'The sandbox worker has stopped. Press Enter to close this window.'"
        $sandboxProc = Start-Process wsl -PassThru -ArgumentList "-d $WslDistro -- bash -c `"$sandboxCmd`""
        $sandboxWindowId = $sandboxProc.Id
        $sandboxStarted = $true
        $sw.Stop()
        $timings.Add([PSCustomObject]@{ Label = "Sandbox worker window launch"; Seconds = $sw.Elapsed.TotalSeconds })
        Write-Component "Sandbox worker (WSL)" "started" "new window inside $WslDistro"
    }
}

if (-not $NoConsole) {
    # Checked first, the same reason as the host worker and sandbox worker
    # above: running this script a second time without stopping the first
    # used to start a second Vite dev server silently, on the next free
    # port (5174, 5175, ...) rather than refusing -- confirmed live, after
    # running this script twice left two independent console windows up,
    # one per port, neither aware of the other. Matched by command line
    # against this project's own `web` path, the same way stop-dev.ps1's
    # own orphaned-Vite check already finds it, so "already running" means
    # the same thing in both scripts.
    $webPath = Join-Path $PSScriptRoot "web"
    $existingVite = Get-CimInstance Win32_Process -Filter "name='node.exe'" |
        Where-Object {
            $_.CommandLine -and $_.CommandLine -match [regex]::Escape($webPath) -and
            $_.CommandLine -match "vite"
        } |
        Select-Object -First 1
    if ($existingVite) {
        Write-Component "Console dev server" "already running" "process $($existingVite.ProcessId), not starting a second one"
    } else {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $consoleProc = Start-Process powershell -PassThru -ArgumentList @(
            "-NoExit", "-Command",
            "Set-Location '$PSScriptRoot\web'; npm run dev"
        )
        $consoleWindowId = $consoleProc.Id
        $consoleStarted = $true
        $sw.Stop()
        $timings.Add([PSCustomObject]@{ Label = "Console window launch"; Seconds = $sw.Elapsed.TotalSeconds })
        Write-Component "Console dev server" "started" "new window"
    }
}

Write-Line ""
Write-Line "Console:     http://localhost:$($Ports.console_dev) (a few seconds behind this script)"
Write-Line "API:         http://localhost:$($Ports.munitas_api_http)"
Write-Line "Temporal UI: http://localhost:$($Ports.temporal_ui)"
Write-Line "MLflow:      http://localhost:$($Ports.mlflow)"
if ($workerStarted -or $sandboxStarted) {
    Write-Line "Worker logs: $(Get-DataRoot -ProjectRoot $PSScriptRoot)/logs"
}
Write-Line ""
$opened = @()
if ($workerStarted) { $opened += "host worker" }
if ($sandboxStarted) { $opened += "sandbox worker" }
if ($consoleStarted) { $opened += "console" }
if ($opened.Count -gt 1) {
    Write-Line ("The " + ($opened -join ", ") + " each opened in their own window.")
} elseif ($opened.Count -eq 1) {
    Write-Line "The $opened opened in its own window."
}
Write-Line "Stop everything this script started with:"
Write-Line ""
Write-Line "  .\stop-dev.ps1"

# --- What's now running, and what it's using ---------------------------------

# A python.exe just launched (or npm's own launcher spawning node/vite) has
# not finished importing its own modules yet; measuring immediately would
# understate what it settles at. This is the same reason a fresh process
# looks lighter in Task Manager for the first few seconds after it starts.
if ($workerStarted -or $sandboxStarted -or $consoleStarted) {
    Write-Line ""
    Write-Line "Giving the worker and console a few seconds to finish starting before measuring..."
    Start-Sleep -Seconds 6
}

$running = New-Object System.Collections.Generic.List[PSCustomObject]

if ($workerStarted -and $workerWindowId) {
    $table = Get-ProcessTable
    $children = Get-DescendantIds -ProcessTable $table -RootId $workerWindowId
    foreach ($c in $children) {
        $cp = $table[$c]
        # Matched on both name AND its exact venv path, the same pair
        # stop-dev.ps1 checks, so both scripts agree on what "the host
        # worker process" means.
        if ($cp -and $cp.Name -eq "python.exe" -and $cp.ExecutablePath -eq $venvPython) {
            $cproc = Get-Process -Id $c -ErrorAction SilentlyContinue
            if ($cproc) {
                $running.Add([PSCustomObject]@{ Label = "host worker (PID $c)"; Bytes = $cproc.WorkingSet64 })
            }
        }
    }
}

if ($sandboxStarted) {
    $repoInWsl = (ConvertTo-WslPath -WslDistro $WslDistro -WindowsPath $PSScriptRoot).Trim()
    $listing = @(wsl -d $WslDistro -- bash -lc "pgrep -af 'worker.sandbox_worker' 2>/dev/null")
    foreach ($line in $listing) {
        if ($line -match '^(\d+)\s') {
            $candidateId = $Matches[1]
            $cwd = (wsl -d $WslDistro -- bash -lc "readlink -f /proc/$candidateId/cwd 2>/dev/null" | Select-Object -First 1)
            if ($cwd -and $cwd.Trim() -eq $repoInWsl) {
                $rssKb = (wsl -d $WslDistro -- bash -lc "ps -o rss= -p $candidateId 2>/dev/null" | Select-Object -First 1)
                $bytes = 0
                if ($rssKb -and ($rssKb.Trim() -match '^\d+$')) { $bytes = [int64]$rssKb.Trim() * 1024 }
                $running.Add([PSCustomObject]@{ Label = "sandbox worker (PID $candidateId in $WslDistro)"; Bytes = $bytes })
            }
        }
    }
}

if ($consoleStarted -and $consoleWindowId) {
    $table = Get-ProcessTable
    $children = Get-DescendantIds -ProcessTable $table -RootId $consoleWindowId
    foreach ($c in $children) {
        $cproc = Get-Process -Id $c -ErrorAction SilentlyContinue
        if ($cproc) {
            $running.Add([PSCustomObject]@{ Label = "console dev server (PID $c)"; Bytes = $cproc.WorkingSet64 })
        }
    }
}

# Containers have been up since the health-check waits above, so there is no
# extra warm-up needed here, unlike the host-side processes just started.
$containerIds = @(wsl -d $WslDistro -- bash -lc "$compose --profile full ps -q" | Where-Object { $_ -and $_.Trim() })
if ($containerIds.Count -gt 0) {
    $idsArg = ($containerIds -join " ")
    $statsLines = @(wsl -d $WslDistro -- bash -lc "docker stats --no-stream --format '{{.Name}}|{{.MemUsage}}' $idsArg 2>/dev/null")
    foreach ($line in $statsLines) {
        $parts = $line -split '\|', 2
        if ($parts.Count -eq 2) {
            $running.Add([PSCustomObject]@{ Label = "container: $($parts[0].Trim())"; Bytes = (ConvertFrom-DockerMemString $parts[1]) })
        }
    }
}

Write-TimingReport -Heading "How long each step took" -Items $timings
Write-MemoryReport -Heading "What's running now, and what it's using" -Items $running -NoneMessage "Nothing was started."
