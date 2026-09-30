# Stop only what start-dev.ps1 started, and only after confirming what a
# process actually is, not by name alone. `taskkill /IM python.exe` or
# `Stop-Process -Name node` would just as happily take down an unrelated
# Python script or another project's dev server if either happened to be
# running at the same time; this instead reads each candidate process's own
# command line (and, for the sandbox worker inside WSL, its actual working
# directory) and only acts on the ones that match this project by both name
# and argument, exactly the two things start-dev.ps1 itself used to launch
# them.
#
# Four things get started, so four things get looked for: the host worker
# (`worker.main`, matched by its exact venv path and the module name
# together), the sandbox worker (`worker.sandbox_worker`, matched inside WSL
# by reading /proc/<pid>/cwd against this project's own translated path,
# since the module name alone would also match another Munitas checkout on
# the same machine), the console dev server, and the containerized backend
# (`docker compose ... down`, which is Compose's own job and already scoped
# to this project's compose file and project name, so nothing to search for
# there).
#
# The console gets two independent checks, not one: its wrapping PowerShell
# window (matched on this project's own `web` folder path plus `npm run
# dev`), then every process descended from that window, since npm's own
# launcher inserts a layer between the window and the real Vite process --
# and, separately, Vite itself found directly by its own script path inside
# `web`, for the case where that window was already closed on its own,
# leaving Vite running with nothing left connecting it back to a window this
# script would otherwise recognise. Confirmed live: exactly this happened
# once, and the window-only check alone reported "none found" while Vite was
# still running.
#
# Every stop is graceful before it is forceful: `CloseMainWindow()` first (the
# same thing clicking a window's own close button does, so anything a process
# does on exit still runs), a short wait, and only a `Stop-Process -Force` /
# `kill -9` if it is still alive afterward -- an escalation path, not a kill
# dressed up as one.
#
# One line per component while it runs -- "Console dev server: stopped (3
# process(es))", "Backend containers: not running" -- not a line per process
# or per container Compose itself touches. The final report still lists every
# individual thing stopped, one line each, with how much memory each one was
# using right before it was stopped: a process's Windows working set, a WSL
# process's RSS, or a container's own `docker stats` usage. This is what was
# freed by removing the thing, not a promise of how much the OS reclaims the
# instant afterward -- shared pages and cached memory do not behave that
# simply -- but it is the same number Task Manager or `docker stats` would
# already have shown for it a moment before, not a guess. Pass -Detailed for
# the PID-by-PID, command-line-by-command-line narration this used to print
# unconditionally.
#
# Supports the standard -WhatIf (show what would be stopped, stop nothing)
# and -Confirm (ask before every single stop, not just once) since this
# script declares SupportsShouldProcess; -Force skips the one prompt per
# item this script asks on its own, but -WhatIf still wins over -Force.
#
# The WSL keepalive (`wsl -d $WslDistro -- sleep infinity`, started by
# start-dev.ps1's own Confirm-WslStaysUp) is left running by default: the
# distro and the Docker engine inside it are not necessarily this one
# project's alone to stop, since another terminal or another project on the
# same distro may depend on it staying up. Pass -StopKeepalive to also stop
# it once you are sure nothing else needs the distro alive.

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$WslDistro = "Ubuntu-20.04",
    [switch]$NoContainers,
    [switch]$NoWorker,
    [switch]$NoConsole,
    [switch]$StopKeepalive,
    [switch]$Force,
    # Off by default: one line per component (see Write-Component in
    # wsl-docker.ps1) is the normal report. Pass this for the previous
    # PID-by-PID, command-line-by-command-line narration, e.g. while
    # debugging why something was or wasn't matched.
    [switch]$Detailed
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
. (Join-Path $PSScriptRoot "wsl-docker.ps1")

$stopped = New-Object System.Collections.Generic.List[PSCustomObject]

# Get-ProcessTable, Get-DescendantIds, ConvertFrom-DockerMemString and
# Write-MemoryReport come from wsl-docker.ps1, dot-sourced above: start-dev.ps1
# needs the same four, so they live in the one file both scripts already
# share rather than two copies drifting apart.

function Confirm-Stop {
    param([Parameter(Mandatory = $true)][string]$Description)
    if ($Force) { return $true }
    return $PSCmdlet.ShouldProcess($Description, "Stop")
}

function Stop-ProcessGracefully {
    # CloseMainWindow() first: the same request a click on the window's own
    # close button sends, so a "-NoExit" PowerShell window or a Node process
    # with its own cleanup gets the chance to run it. Escalates to
    # Stop-Process -Force only if the process is still alive once the wait
    # is over, and says so out loud rather than doing it silently.
    param(
        [Parameter(Mandatory = $true)][int]$Id,
        [Parameter(Mandatory = $true)][string]$Label,
        [int]$TimeoutSeconds = 8
    )
    $proc = Get-Process -Id $Id -ErrorAction SilentlyContinue
    if (-not $proc) { return $true }
    $askedNicely = $false
    try { $askedNicely = $proc.CloseMainWindow() } catch { $askedNicely = $false }
    if ($askedNicely) {
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            if (-not (Get-Process -Id $Id -ErrorAction SilentlyContinue)) { return $true }
            Start-Sleep -Milliseconds 300
        }
    }
    if (Get-Process -Id $Id -ErrorAction SilentlyContinue) {
        Write-Detail -Detailed $Detailed -Text "$Label did not close on its own; stopping it directly." -ForegroundColor Yellow
        Stop-Process -Id $Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 300
    }
    return -not (Get-Process -Id $Id -ErrorAction SilentlyContinue)
}

# --- Console dev server -----------------------------------------------------

if (-not $NoConsole) {
    $table = Get-ProcessTable
    $webPath = Join-Path $PSScriptRoot "web"
    $foundConsolePids = New-Object System.Collections.Generic.List[int]
    $consoleStoppedCount = 0
    $windows = @($table.Values | Where-Object {
        $_.Name -eq "powershell.exe" -and $_.CommandLine -and
        $_.CommandLine -match [regex]::Escape($webPath) -and
        $_.CommandLine -match "npm run dev"
    })
    foreach ($w in $windows) {
        $winId = [int]$w.ProcessId
        $children = Get-DescendantIds -ProcessTable $table -RootId $winId
        Write-Detail -Detailed $Detailed -Text "console window: PID $winId, $($children.Count) child process(es) (npm/node/vite)"
        Write-Detail -Detailed $Detailed -Text "$($w.CommandLine)"
        if (Confirm-Stop "console dev server (PID $winId and its children)") {
            $proc = Get-Process -Id $winId -ErrorAction SilentlyContinue
            $mem = 0
            if ($proc) { $mem = $proc.WorkingSet64 }
            $label = "console window (PID $winId)"
            if (Stop-ProcessGracefully -Id $winId -Label $label -TimeoutSeconds 8) {
                $stopped.Add([PSCustomObject]@{ Label = $label; Bytes = $mem })
                $consoleStoppedCount++
            }
            foreach ($c in $children) {
                $cproc = Get-Process -Id $c -ErrorAction SilentlyContinue
                if (-not $cproc) { continue }
                $cmem = $cproc.WorkingSet64
                $clabel = "console child process (PID $c)"
                if (Stop-ProcessGracefully -Id $c -Label $clabel -TimeoutSeconds 3) {
                    $stopped.Add([PSCustomObject]@{ Label = $clabel; Bytes = $cmem })
                    $foundConsolePids.Add($c) | Out-Null
                    $consoleStoppedCount++
                }
            }
            $foundConsolePids.Add($winId) | Out-Null
        }
    }

    # A second, independent check: Vite itself, found directly by its own
    # script path inside this project's `web` folder, not only by walking
    # down from a parent window. If that window was ever closed on its own
    # (the console left running while its terminal was closed, for example)
    # the walk above never reaches Vite at all, because nothing connects it
    # back to a window this script recognises -- confirmed live: exactly
    # this was found orphaned and running after a "none found" report from
    # the window-only check alone.
    $orphanedVite = @($table.Values | Where-Object {
        $_.Name -eq "node.exe" -and $_.CommandLine -and
        $_.CommandLine -match [regex]::Escape($webPath) -and
        $_.CommandLine -match "vite" -and
        -not $foundConsolePids.Contains([int]$_.ProcessId)
    })
    foreach ($v in $orphanedVite) {
        $vId = [int]$v.ProcessId
        Write-Detail -Detailed $Detailed -Text "orphaned Vite process (no parent window found): PID $vId"
        Write-Detail -Detailed $Detailed -Text "$($v.CommandLine)"
        if (Confirm-Stop "orphaned console/Vite process (PID $vId)") {
            $vproc = Get-Process -Id $vId -ErrorAction SilentlyContinue
            $vmem = 0
            if ($vproc) { $vmem = $vproc.WorkingSet64 }
            $vlabel = "orphaned Vite process (PID $vId)"
            if (Stop-ProcessGracefully -Id $vId -Label $vlabel -TimeoutSeconds 3) {
                $stopped.Add([PSCustomObject]@{ Label = $vlabel; Bytes = $vmem })
                $consoleStoppedCount++
            }
        }
    }

    if ($windows.Count -eq 0 -and $orphanedVite.Count -eq 0) {
        Write-Component "Console dev server" "not running"
    } else {
        Write-Component "Console dev server" "stopped" "$consoleStoppedCount process(es)"
    }
}

# --- Host worker -------------------------------------------------------------

if (-not $NoWorker) {
    $venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    $table = Get-ProcessTable
    $windows = @($table.Values | Where-Object {
        $_.Name -eq "powershell.exe" -and $_.CommandLine -and
        $_.CommandLine -match [regex]::Escape($PSScriptRoot) -and
        $_.CommandLine -match "worker\.main"
    })
    $hostWorkerStoppedCount = 0
    foreach ($w in $windows) {
        $winId = [int]$w.ProcessId
        $children = Get-DescendantIds -ProcessTable $table -RootId $winId
        Write-Detail -Detailed $Detailed -Text "host worker window: PID $winId"
        Write-Detail -Detailed $Detailed -Text "$($w.CommandLine)"
        if (Confirm-Stop "host worker (PID $winId and its python.exe child)") {
            $proc = Get-Process -Id $winId -ErrorAction SilentlyContinue
            $mem = 0
            if ($proc) { $mem = $proc.WorkingSet64 }
            $label = "host worker window (PID $winId)"
            if (Stop-ProcessGracefully -Id $winId -Label $label -TimeoutSeconds 8) {
                $stopped.Add([PSCustomObject]@{ Label = $label; Bytes = $mem })
                $hostWorkerStoppedCount++
            }
            foreach ($c in $children) {
                $cp = $table[$c]
                # Matched on both name AND its exact venv path, not just
                # "python.exe": a different Python process this same window
                # happened to spawn should not be assumed to be the worker.
                if ($cp -and $cp.Name -eq "python.exe" -and $cp.ExecutablePath -eq $venvPython) {
                    $cproc = Get-Process -Id $c -ErrorAction SilentlyContinue
                    if ($cproc) {
                        $cmem = $cproc.WorkingSet64
                        $clabel = "host worker process (PID $c)"
                        if (Stop-ProcessGracefully -Id $c -Label $clabel -TimeoutSeconds 5) {
                            $stopped.Add([PSCustomObject]@{ Label = $clabel; Bytes = $cmem })
                            $hostWorkerStoppedCount++
                        }
                    }
                }
            }
        }
    }
    if ($windows.Count -eq 0) {
        Write-Component "Host worker" "not running"
    } else {
        Write-Component "Host worker" "stopped" "$hostWorkerStoppedCount process(es)"
    }
}

# --- Sandbox worker (inside WSL) ---------------------------------------------

if (-not $NoWorker) {
    $repoInWsl = (ConvertTo-WslPath -WslDistro $WslDistro -WindowsPath $PSScriptRoot).Trim()
    $listing = @(wsl -d $WslDistro -- bash -lc "pgrep -af 'worker.sandbox_worker' 2>/dev/null")
    $matchedIds = @()
    foreach ($line in $listing) {
        if ($line -match '^(\d+)\s') {
            $candidateId = $Matches[1]
            # The module name alone is not proof it is THIS project's worker
            # -- another Munitas checkout on the same machine would match it
            # too. The bash wrapper `cd`s into this project's own path before
            # exec-ing python, so the running process's real cwd is the one
            # fact that actually distinguishes them.
            $cwd = (wsl -d $WslDistro -- bash -lc "readlink -f /proc/$candidateId/cwd 2>/dev/null" | Select-Object -First 1)
            if ($cwd -and $cwd.Trim() -eq $repoInWsl) {
                $matchedIds += $candidateId
            }
        }
    }
    if ($matchedIds.Count -eq 0) {
        Write-Component "Sandbox worker (WSL)" "not running"
    } else {
        foreach ($procId in $matchedIds) {
            Write-Detail -Detailed $Detailed -Text "sandbox worker: PID $procId inside $WslDistro (cwd confirmed as this project)"
            if (Confirm-Stop "sandbox worker (PID $procId inside $WslDistro)") {
                $rssKb = (wsl -d $WslDistro -- bash -lc "ps -o rss= -p $procId 2>/dev/null" | Select-Object -First 1)
                $bytes = 0
                if ($rssKb -and ($rssKb.Trim() -match '^\d+$')) { $bytes = [int64]$rssKb.Trim() * 1024 }
                wsl -d $WslDistro -- bash -lc "kill $procId 2>/dev/null" | Out-Null
                $deadline = (Get-Date).AddSeconds(8)
                $gone = $false
                while ((Get-Date) -lt $deadline) {
                    $check = wsl -d $WslDistro -- bash -lc "kill -0 $procId 2>/dev/null && echo alive || echo gone"
                    if ($check -match "gone") { $gone = $true; break }
                    Start-Sleep -Milliseconds 500
                }
                if (-not $gone) {
                    Write-Detail -Detailed $Detailed -Text "did not stop on SIGTERM; sending SIGKILL." -ForegroundColor Yellow
                    wsl -d $WslDistro -- bash -lc "kill -9 $procId 2>/dev/null" | Out-Null
                    Start-Sleep -Milliseconds 300
                }
                $stopped.Add([PSCustomObject]@{ Label = "sandbox worker (PID $procId in $WslDistro)"; Bytes = $bytes })
            }
        }
        # Best-effort tidy-up: the Windows-side wsl.exe window that launched
        # it is left waiting at "press Enter to close" once the python
        # process above exits (harmless, but this script's job is not to
        # leave litter behind either). Not counted in the memory total: it
        # is an idle shell, not the workload that mattered.
        $table = Get-ProcessTable
        $wslWindows = @($table.Values | Where-Object {
            $_.Name -eq "wsl.exe" -and $_.CommandLine -and
            $_.CommandLine -match [regex]::Escape($WslDistro) -and
            $_.CommandLine -match "sandbox_worker"
        })
        foreach ($w in $wslWindows) {
            Stop-ProcessGracefully -Id ([int]$w.ProcessId) -Label "sandbox worker window (PID $($w.ProcessId))" -TimeoutSeconds 3 | Out-Null
        }
        Write-Component "Sandbox worker (WSL)" "stopped" "$($matchedIds.Count) process(es)"
    }
}

# --- Containerized backend ----------------------------------------------------

if (-not $NoContainers) {
    $compose = Get-ComposePrefix -WslDistro $WslDistro -ProjectRoot $PSScriptRoot
    $containerIds = @(wsl -d $WslDistro -- bash -lc "$compose --profile full ps -q" | Where-Object { $_ -and $_.Trim() })
    $containerMem = @{}
    if ($containerIds.Count -gt 0) {
        $idsArg = ($containerIds -join " ")
        $statsLines = @(wsl -d $WslDistro -- bash -lc "docker stats --no-stream --format '{{.Name}}|{{.MemUsage}}' $idsArg 2>/dev/null")
        foreach ($line in $statsLines) {
            $parts = $line -split '\|', 2
            if ($parts.Count -eq 2) {
                $containerMem[$parts[0].Trim()] = ConvertFrom-DockerMemString $parts[1]
            }
        }
    }
    if ($containerIds.Count -eq 0) {
        Write-Component "Backend containers" "not running"
    } elseif (Confirm-Stop "docker compose --profile full down (this project's own compose project only, $($containerIds.Count) container(s))") {
        # Compose's own streamed "Stopping/Stopped/Removing/Removed" lines
        # (or its animated --progress UI) are captured, not streamed to this
        # console; Invoke-ComposeQuiet shows them anyway on failure or with
        # -Detailed. --progress plain still matters for the captured case:
        # see start-dev.ps1's own comment on its `up` call for why an
        # animated redraw is worse than the plain-mode text it replaces.
        $ok = Invoke-ComposeQuiet -WslDistro $WslDistro -Command "$compose --progress plain --profile full down" -Detailed $Detailed
        if (-not $ok) {
            Write-Component "Backend containers" "down reported a problem" "see output above" -Warn
        } else {
            foreach ($name in $containerMem.Keys) {
                $stopped.Add([PSCustomObject]@{ Label = "container: $name"; Bytes = $containerMem[$name] })
            }
            if ($containerMem.Keys.Count -eq 0 -and $containerIds.Count -gt 0) {
                $stopped.Add([PSCustomObject]@{ Label = "docker compose services ($($containerIds.Count) container(s), usage not read)"; Bytes = 0 })
            }
            Write-Component "Backend containers" "stopped" "$($containerIds.Count) container(s)"
        }
    }
}

# --- WSL keepalive, off by default -------------------------------------------

if ($StopKeepalive) {
    $table = Get-ProcessTable
    $pattern = "-d\s+$([regex]::Escape($WslDistro))\s+--\s+sleep\s+infinity"
    $keepalives = @($table.Values | Where-Object { $_.Name -eq "wsl.exe" -and $_.CommandLine -match $pattern })
    $keepaliveStoppedCount = 0
    foreach ($k in $keepalives) {
        $kId = [int]$k.ProcessId
        Write-Detail -Detailed $Detailed -Text "keepalive: PID $kId"
        if (Confirm-Stop "WSL keepalive (PID $kId) -- this may be relied on by other terminals using $WslDistro") {
            $proc = Get-Process -Id $kId -ErrorAction SilentlyContinue
            $mem = 0
            if ($proc) { $mem = $proc.WorkingSet64 }
            # Started with -WindowStyle Hidden, so it has no window to close
            # gracefully; it holds no state of its own, so a direct stop
            # here is not a shortcut around anything, it is simply correct.
            Stop-Process -Id $kId -Force -ErrorAction SilentlyContinue
            $stopped.Add([PSCustomObject]@{ Label = "WSL keepalive (PID $kId)"; Bytes = $mem })
            $keepaliveStoppedCount++
        }
    }
    if ($keepalives.Count -eq 0) {
        Write-Component "WSL keepalive" "not running"
    } else {
        Write-Component "WSL keepalive" "stopped" "$keepaliveStoppedCount process(es)"
    }
} else {
    Write-Line ""
    Write-Line "Leaving the WSL keepalive running (shared by anything else using $WslDistro)."
    Write-Line "Pass -StopKeepalive to also stop it once nothing else needs the distro alive."
}

# --- Summary ------------------------------------------------------------------

Write-MemoryReport -Heading "What this stopped, and what it was using" -Items $stopped -NoneMessage "Nothing was stopped."
Write-Line ""
Write-Line "Done."
