<#
Run a command and keep everything it prints, so a failure is read from a file and never needs a second run to see.

    .\scripts\run-logged.ps1 -Name reseed-health -Command ".\scripts\seed\reseed-tenant.ps1 -Tenant health -Force"
    .\scripts\run-logged.ps1 -Name story -Command ".\.venv\Scripts\python.exe scripts\walkthroughs\capture-health-story.py"

The whole of the output (standard output, errors, warnings, progress) goes to docs/internal/run-logs/<name>-<date and time>.log, which git ignores. What is printed here is only
the path of the log, how the command ended and how long it took, and the last lines of the log. A command that fails is read from that file; it is not run again to find out why.
The first line of the log is the command, and the last is how it ended.

-Command is one quoted string, run in a child PowerShell, so its own quoting reaches it unchanged and an exit in it ends only the child. -Tail 0 prints no lines. The exit code of this script is the command's.
#>
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Command,
    [int]$Tail = 40
)

$ErrorActionPreference = 'Continue'
$folder = Join-Path $PSScriptRoot '..\docs\internal\run-logs'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$log = Join-Path (Resolve-Path $folder) ("{0}-{1}.log" -f $Name, (Get-Date -Format 'yyyyMMdd-HHmmss'))

"# $Command" | Out-File -FilePath $log -Encoding utf8
"# started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -FilePath $log -Encoding utf8 -Append
$watch = [Diagnostics.Stopwatch]::StartNew()
$global:LASTEXITCODE = 0
try {
    # A child process, so that a command which calls exit, or changes folder, cannot end or move this script before the log is finished.
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command $Command *>> $log
    $code = $LASTEXITCODE
} catch {
    $_ | Out-File -FilePath $log -Encoding utf8 -Append
    $code = 1
}
$watch.Stop()
$ended = "# ended with exit {0} after {1:N0} s" -f $code, $watch.Elapsed.TotalSeconds
$ended | Out-File -FilePath $log -Encoding utf8 -Append

Write-Output "log:   $log"
Write-Output $ended.TrimStart('# ')
if ($Tail -gt 0) {
    Write-Output "--- last $Tail lines ---"
    Get-Content -Path $log -Tail $Tail
}
exit $code
