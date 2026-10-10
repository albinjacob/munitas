# Test the policy, build it into one signed package, and put it in the policy store for the engine to pick up (MUNITAS_OPA_POLICY_SOURCE=bundle).
#
#   .\publish-policy.ps1
#
# The package is built from platform/policy without the tests (*_test.rego). The steps are in platform/policy-bundle/publish.sh, which this runs inside the WSL
# distro, and each stops the script when it fails, so a policy that does not pass its tests or does not compile is never published:
#
#   1. `opa test` runs every test in the folder.
#   2. `opa build` compiles the policy into platform/policy-bundle/published/policy.tar.gz.tmp and signs it with the private key. A policy that does not compile
#      produces nothing.
#   3. The finished file is put in the policy store (a small object store of its own, started if it is not running) by a command run in the store's container, so no
#      write key exists. A copy of what was published stays in platform/policy-bundle/published.
#
# The package is signed. The private key stays in this machine's WSL distro (~/.munitas/policy-signing/private.pem) and is never in the repository, in a container
# the engine runs in, or in the store. The matching public key is platform/policy-bundle/signing-public.pem, which the engine trusts. A package that anybody could
# produce without the private key is refused by the engine, so who may reach the store does not decide who may change policy.
#
# The first time on a machine, run it with -NewSigningKey. That also makes the store's read-only key, in the data folder; the engine is started again after it so
# that it uses it (RUNBOOK.md, "The policy engine").
#
# The engine polls every few seconds, checks the signature and that the package compiles, and switches to it all at once. A package it cannot load is reported in
# its log and the last good one keeps answering. Nothing here touches the running engine; in folder mode (the default) there is nothing to publish, the engine reads
# the folder.
#
# Everything runs in the same images Compose uses, inside the WSL distro, so it needs no tools on this machine.
#
# Pass -WslDistro if yours is not named Munitas; `wsl -l -v` lists them.

param(
    [string]$WslDistro = "Munitas",
    # Makes the signing key pair. Needed once, on the first publish on a machine; refused when a key already exists, because a new key means the engine must be
    # started again to trust it (RUNBOOK.md, "The policy engine").
    [switch]$NewSigningKey
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "wsl-docker.ps1")

Confirm-WslStaysUp -WslDistro $WslDistro | Out-Null

$repo = ConvertTo-WslPath -WslDistro $WslDistro -WindowsPath $PSScriptRoot
$data = ConvertTo-WslPath -WslDistro $WslDistro -WindowsPath (Get-DataRoot -ProjectRoot $PSScriptRoot)
$revision = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$newKey = if ($NewSigningKey) { "yes" } else { "no" }

# The image is the one Compose runs, read from docker-compose.yml rather than written twice.
$image = (Select-String -Path (Join-Path $PSScriptRoot "docker-compose.yml") -Pattern '^\s*image:\s*(openpolicyagent/opa:\S+)\s*$' | Select-Object -First 1).Matches[0].Groups[1].Value
if (-not $image) { throw "docker-compose.yml names no openpolicyagent/opa image." }

$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"   # a native command's stderr is not a failure here; its exit code is
try {
    wsl.exe -d $WslDistro -- env "REPO=$repo" "DATA=$data" "IMAGE=$image" "REVISION=$revision" "NEW_KEY=$newKey" bash "$repo/platform/policy-bundle/publish.sh"
    $code = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previous
}
if ($code -ne 0) {
    throw "The policy was not published (exit $code). Nothing was changed; the engine keeps the package it has."
}
Write-Line "Published revision $revision. A policy engine in bundle mode picks it up within a few seconds."
