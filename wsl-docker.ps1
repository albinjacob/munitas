# Reaching Docker from Windows, shared by start-dev.ps1, stop-dev.ps1 and
# run-verification.ps1.
#
# Docker Desktop was removed after its named-pipe socket broke for the fourth
# time, and the engine now runs inside WSL2. The `docker.exe` still on PATH is
# Docker Desktop's, still aimed at a pipe nothing listens on, so every Compose
# command has to run inside the distro. All three scripts need the same
# things: a path translation, the MUNITAS_DATA override, the Compose prefix
# built from them, and (start-dev.ps1 and stop-dev.ps1 specifically) a way to
# find this project's own processes by what they actually are, not just their
# name, and to read what a process or container is using right now. They
# live here so none of this can drift apart between the two.
#
# Dot-source it: . (Join-Path $PSScriptRoot "wsl-docker.ps1")

function Write-Line {
    # Every `wsl.exe` call in start-dev.ps1/stop-dev.ps1 can leave this
    # console's cursor at a stray column, not just the ones running
    # docker compose's own animated progress UI -- confirmed live, it also
    # happened around the plain `pgrep`/`readlink` calls used to detect an
    # existing worker, which never print anything to the console
    # themselves. A leading carriage return forces column 0 before
    # anything is printed, regardless of what left the cursor wherever it
    # was, so both scripts route every line through this instead of calling
    # Write-Host directly. `@args` forwards positional and named
    # parameters (including -ForegroundColor) exactly as given.
    [Console]::Out.Write("`r")
    Write-Host @args
}

function Write-Component {
    # The one line both scripts print per component by default: what it is,
    # what happened to it, and (optionally) a short parenthetical -- "Console
    # dev server: stopped (3 process(es))". Everything else either script
    # knows about that component (which PID, its command line, whether a
    # window had to be force-closed) is real and sometimes worth having, but
    # printing it unconditionally is what made both scripts' output a
    # blow-by-blow transcript instead of a status report; it now goes out
    # through Write-Detail below instead, shown only with -Detailed.
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$Status,
        [string]$Detail = "",
        [switch]$Warn
    )
    $line = "${Label}: $Status"
    if ($Detail) { $line += " ($Detail)" }
    if ($Warn) { Write-Line $line -ForegroundColor Yellow } else { Write-Line $line }
}

function Write-Detail {
    # The narration Write-Component replaced as the default: which PID,
    # which command line, "did not close on its own", and so on. Only
    # printed when the caller passed -Detailed, so it is still one flag away
    # rather than gone.
    param(
        [Parameter(Mandatory = $true)][bool]$Detailed,
        [Parameter(Mandatory = $true)][string]$Text,
        [string]$ForegroundColor = ""
    )
    if (-not $Detailed) { return }
    if ($ForegroundColor) { Write-Line "    $Text" -ForegroundColor $ForegroundColor }
    else { Write-Line "    $Text" }
}

function Invoke-ComposeQuiet {
    # Runs a `docker compose` line inside WSL without letting its own
    # streamed per-container "Stopping/Stopped/Removing/Removed" output (or
    # the animated --progress UI) reach this console, which is most of what
    # made both scripts' output long. The full output is captured either
    # way, so a failure -- or -Detailed -- can still show exactly what
    # Compose said; only the successful, undetailed case stays silent.
    #
    # $ErrorActionPreference is set to "Stop" by both callers, and `2>&1` on
    # a native command under that preference wraps every stderr line (which
    # is where Compose's own --progress plain output actually goes) in a
    # terminating NativeCommandError -- confirmed live, this stopped
    # start-dev.ps1 outright the first time this ran, before Compose had
    # done anything wrong at all. Set to "Continue" for the one call that
    # needs to merge streams, and restored immediately after regardless of
    # how it exits.
    param(
        [Parameter(Mandatory = $true)][string]$WslDistro,
        [Parameter(Mandatory = $true)][string]$Command,
        [bool]$Detailed = $false
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = wsl -d $WslDistro -- bash -lc "$Command" 2>&1
        $ok = ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($Detailed -or -not $ok) {
        foreach ($line in $output) { Write-Line "    $line" }
    }
    return $ok
}

function ConvertTo-WslPath {
    # wslpath is the authority on this translation, so ask it rather than
    # rewriting drive letters here. It has to be handed forward slashes:
    # wsl.exe strips backslashes out of its arguments before wslpath ever
    # sees them, turning C:\a\b into the silently wrong C:ab.
    param(
        [Parameter(Mandatory = $true)][string]$WslDistro,
        [Parameter(Mandatory = $true)][string]$WindowsPath
    )

    $translated = (wsl -d $WslDistro -- wslpath -a $WindowsPath.Replace('\', '/')) | Select-Object -First 1
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($translated)) {
        throw "Could not translate '$WindowsPath' for WSL distro '$WslDistro'. Run 'wsl -l -v' to see the distro names on this machine, then pass -WslDistro with the right one."
    }
    return $translated.Trim()
}

function Get-ProcessTable {
    # Every live Windows process, indexed by PID, fetched once so callers can
    # walk parent links without re-querying WMI per candidate. Shared by
    # start-dev.ps1 (to find what it just started, once the window it
    # launched has spawned the real worker underneath it) and stop-dev.ps1
    # (to find what to stop).
    $table = @{}
    foreach ($p in (Get-CimInstance Win32_Process)) { $table[[int]$p.ProcessId] = $p }
    return $table
}

function Get-DescendantIds {
    # PIDs descended from $RootId at any depth, not just direct children:
    # `npm run dev` on Windows inserts at least one layer (npm's own
    # launcher) between the window that started it and the real Vite
    # process, and both starting and stopping need to see all of it, not
    # just the window.
    param(
        [Parameter(Mandatory = $true)][hashtable]$ProcessTable,
        [Parameter(Mandatory = $true)][int]$RootId
    )
    $found = New-Object System.Collections.Generic.List[int]
    $frontier = @($RootId)
    while ($frontier.Count -gt 0) {
        $next = @()
        foreach ($proc in $ProcessTable.Values) {
            $ppid = [int]$proc.ParentProcessId
            $cid = [int]$proc.ProcessId
            if (($frontier -contains $ppid) -and -not $found.Contains($cid)) {
                $found.Add($cid)
                $next += $cid
            }
        }
        $frontier = $next
    }
    return $found
}

function ConvertFrom-DockerMemString {
    # docker stats' MemUsage reads like "123.4MiB / 2GiB"; only the used side
    # (before the slash) is what a container is actually consuming, which is
    # the half both scripts report.
    param([string]$Text)
    if (-not $Text) { return 0 }
    $used = ($Text -split '/')[0].Trim()
    if ($used -match '^([\d.]+)\s*([KMGT]?i?B)$') {
        $value = [double]$Matches[1]
        $unit = $Matches[2]
        if ($unit -eq "B") { return [int64]$value }
        if ($unit -eq "KiB") { return [int64]($value * 1KB) }
        if ($unit -eq "MiB") { return [int64]($value * 1MB) }
        if ($unit -eq "GiB") { return [int64]($value * 1GB) }
        if ($unit -eq "TiB") { return [int64]($value * 1TB) }
    }
    return 0
}

function Write-MemoryReport {
    # The one table both scripts print: what is stopped/started, and how
    # much memory each one is using (Windows working set, WSL RSS, or a
    # container's own docker stats usage), plus a total. $Items is a list of
    # {Label, Bytes}. $Heading names what this particular report is of, since
    # start-dev.ps1 and stop-dev.ps1 mean opposite things by the same table.
    param(
        [Parameter(Mandatory = $true)][string]$Heading,
        [Parameter(Mandatory = $true)]$Items,
        [Parameter(Mandatory = $true)][string]$NoneMessage
    )
    Write-Line ""
    Write-Line "=== $Heading ==="
    if ($Items.Count -eq 0) {
        Write-Line $NoneMessage
        return
    }
    $totalBytes = [int64]0
    foreach ($item in $Items) {
        $mb = [math]::Round($item.Bytes / 1MB, 1)
        Write-Line ("  {0,-55} {1,10} MB" -f $item.Label, $mb)
        $totalBytes += [int64]$item.Bytes
    }
    $totalMb = [math]::Round($totalBytes / 1MB, 1)
    $totalGb = [math]::Round($totalBytes / 1GB, 2)
    Write-Line ""
    Write-Line ("Total: {0} MB ({1} GB)" -f $totalMb, $totalGb)
    Write-Line "Each figure is a snapshot taken at report time (Windows working set /"
    Write-Line "Linux RSS / docker stats usage), not a hard reservation or a guarantee"
    Write-Line "of what the OS does with it a moment later."
}

function Write-TimingReport {
    # How long each step of a start-dev.ps1 run actually took, sorted slowest
    # first, so "it took a while" turns into "which part" without guessing.
    # $Items is a list of {Label, Seconds}.
    param(
        [Parameter(Mandatory = $true)][string]$Heading,
        [Parameter(Mandatory = $true)]$Items
    )
    Write-Line ""
    Write-Line "=== $Heading ==="
    $total = 0.0
    foreach ($item in ($Items | Sort-Object -Property Seconds -Descending)) {
        Write-Line ("  {0,-40} {1,8:N1}s" -f $item.Label, $item.Seconds)
        $total += $item.Seconds
    }
    Write-Line ""
    Write-Line ("Total measured: {0:N1}s" -f $total)
    Write-Line "Steps run one after another, so this is close to the script's own real"
    Write-Line "wall-clock time, not the sum of independent work done in parallel."
}

function Import-WslEnvPassthrough {
    # Forwards named Windows environment variables into the distro, so
    # Compose sees them when it substitutes ${VAR} into a service.
    #
    # Nothing crosses that boundary on its own. A variable set in Windows,
    # at user scope or any other, is simply absent inside WSL unless it is
    # named in WSLENV, and the failure is silent: Compose substitutes an
    # empty string and the container comes up believing it was never
    # configured. WSLENV is how wsl.exe is told which ones to carry.
    #
    # Read from user scope explicitly rather than from $env:, because a
    # PowerShell process inherits its parent's copy of the environment and
    # a variable added after that parent started is not in it. Reading the
    # user scope goes to the real value, so a fresh terminal is not needed.
    #
    # Values are passed as environment entries, never interpolated into the
    # command string, so a secret does not end up in a process listing.
    param([Parameter(Mandatory = $true)][string[]]$Names)

    $carried = @()
    foreach ($name in $Names) {
        $value = [Environment]::GetEnvironmentVariable($name, "Process")
        if ([string]::IsNullOrEmpty($value)) {
            $value = [Environment]::GetEnvironmentVariable($name, "User")
        }
        if ([string]::IsNullOrEmpty($value)) {
            $value = [Environment]::GetEnvironmentVariable($name, "Machine")
        }
        if (-not [string]::IsNullOrEmpty($value)) {
            Set-Item -Path "env:$name" -Value $value
            $carried += "$name/u"
        }
    }
    if ($carried.Count -gt 0) {
        $existing = if ($env:WSLENV) { @($env:WSLENV) } else { @() }
        $env:WSLENV = (($existing + $carried) | Where-Object { $_ }) -join ":"
    }
    return $carried.Count
}

function Get-DataRoot {
    # The one folder that holds the platform's data, from `.env`'s
    # MUNITAS_DATA. No fallback: a drive letter that suits one machine is not
    # a default for anyone else, and guessing would put data somewhere
    # nobody chose.
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    $envFile = Join-Path $ProjectRoot ".env"
    if (Test-Path $envFile) {
        $declared = Select-String -Path $envFile -Pattern '^\s*MUNITAS_DATA\s*=\s*(.+?)\s*$' | Select-Object -First 1
        if ($declared) { return $declared.Matches[0].Groups[1].Value.TrimEnd('/', '\') }
    }
    throw "MUNITAS_DATA is not set in $envFile. Copy .env.example to .env and set it to the folder that should hold the platform's data."
}

function Get-ComposePrefix {
    # Returns the shell fragment every Compose command starts with, ready to
    # have `up -d`, `logs`, `exec` and so on appended.
    #
    # `.env` holds MUNITAS_DATA as a Windows path, which is right for anything
    # reading it on this side and meaningless inside the distro. Compose is the
    # only thing that reads it, and Compose now runs over there, so translate it
    # and pass it in: a shell variable beats the `.env` file's value. Without
    # the override Compose joins the raw Windows path onto the project directory and
    # the daemon rejects the result outright, which is loud rather than silent,
    # but still a failure to start.
    param(
        [Parameter(Mandatory = $true)][string]$WslDistro,
        [Parameter(Mandatory = $true)][string]$ProjectRoot
    )

    $dataRoot = Get-DataRoot -ProjectRoot $ProjectRoot
    $envFile = Join-Path $ProjectRoot ".env"

    # Settings this machine keeps in Windows environment variables rather
    # than in `.env`. Either place works; both are read, and `.env` wins
    # for anything declared twice, because Compose applies the file after
    # these arrive.
    Import-WslEnvPassthrough -Names @(
        "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
        "R2_API_TOKEN", "R2_BUCKET"
    ) | Out-Null

    # docker-compose.yml bind-mounts MUNITAS_HOST_WORK_DIR, and Compose warns
    # on every single call when it is not defined at all. Passed through as an
    # empty string when `.env` does not declare it, which is the value Compose
    # was substituting anyway: same behaviour, without two lines of warning on
    # stderr per command. That noise is not cosmetic. Under
    # `$ErrorActionPreference = "Stop"`, Windows PowerShell turns a native
    # command's stderr into a terminating NativeCommandError the moment its
    # output is captured, so a script that reads a value back from Compose
    # dies on the warning rather than on anything being wrong.
    $workDir = ""
    if (Test-Path $envFile) {
        $declaredWork = Select-String -Path $envFile -Pattern '^\s*MUNITAS_HOST_WORK_DIR\s*=\s*(.*?)\s*$' | Select-Object -First 1
        if ($declaredWork) { $workDir = $declaredWork.Matches[0].Groups[1].Value }
    }

    $repoInWsl = ConvertTo-WslPath -WslDistro $WslDistro -WindowsPath $ProjectRoot
    $dataInWsl = ConvertTo-WslPath -WslDistro $WslDistro -WindowsPath $dataRoot
    return "cd '$repoInWsl' && MUNITAS_DATA='$dataInWsl' MUNITAS_HOST_WORK_DIR='$workDir' docker compose"
}


function Confirm-WslStaysUp {
    # The Docker engine runs inside the distro, so it stops whenever the
    # distro does, and WSL stops a distro that nothing on the Windows side is
    # attached to. Every container then loses power at once: Postgres comes
    # back with "not properly shut down", Temporal restarts, and a running
    # test fails for no reason of its own. Seen on this machine repeatedly,
    # most recently with `.wslconfig` correct and no keepalive running.
    #
    # Two things prevent it, and this checks both rather than leaving them to
    # memory. `.wslconfig` is machine-wide, so it is checked and never edited
    # here: changing it affects every WSL project on the machine and needs a
    # `wsl --shutdown`, which is for the person to choose. The keepalive is
    # this project's own concern, so it is started when missing. See
    # RUNBOOK.md, "Troubleshooting: containers stuck in a crash-recovery loop".
    param([Parameter(Mandatory = $true)][string]$WslDistro)

    $config = Join-Path $env:USERPROFILE ".wslconfig"
    $text = if (Test-Path $config) { Get-Content $config -Raw } else { "" }
    $missing = @()
    if ($text -notmatch '(?m)^\s*vmIdleTimeout\s*=\s*-1\s*$') {
        $missing += "vmIdleTimeout=-1 under [wsl2]"
    }
    if ($text -notmatch '(?m)^\s*autoMemoryReclaim\s*=\s*disabled\s*$') {
        $missing += "autoMemoryReclaim=disabled under [experimental]"
    }
    if ($missing.Count -gt 0) {
        throw ("$config is missing: " + ($missing -join "; ") +
            ". Add what is missing, run 'wsl --shutdown', then start again. RUNBOOK.md explains why.")
    }

    $pattern = "-d\s+$([regex]::Escape($WslDistro))\s+--\s+sleep\s+infinity"
    $find = {
        Get-CimInstance Win32_Process -Filter "name='wsl.exe'" |
            Where-Object { $_.CommandLine -match $pattern } |
            Select-Object -First 1
    }
    $keepalive = & $find
    if ($keepalive) {
        Write-Line "WSL keepalive already running (process $($keepalive.ProcessId))."
        return
    }

    Start-Process wsl -ArgumentList "-d $WslDistro -- sleep infinity" -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(15)
    while (-not $keepalive -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $keepalive = & $find
    }
    if (-not $keepalive) {
        throw "Could not start the WSL keepalive. Start it by hand in its own terminal: wsl -d $WslDistro -- sleep infinity"
    }
    Write-Line "Started the WSL keepalive (process $($keepalive.ProcessId)), so '$WslDistro' and the Docker engine in it stay up. A reboot ends it; this script starts it again."
}
