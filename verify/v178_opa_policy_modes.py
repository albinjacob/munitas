r"""U189: the policy engine's two modes, its token, its signature and its store are wired consistently, and the folder mode cannot be stopped by a test file.

Runs on the host and needs no database and no running stack:

    .venv\Scripts\python.exe verify\v178_opa_policy_modes.py

Bundle mode is held together by files that nobody would think to compare: the Compose override that starts the engine on a config file and starts the store, the
config that names the package, its signing key and where to fetch it, the script that builds, signs and publishes it, the helper that puts it in the store, and the
function that adds the override to every Compose command. If one of them is renamed the engine starts, finds nothing, and refuses every decision. This reads them
and checks that they name the same things, that the engine's own interface needs the platform's token in both modes, that the store is on a network of its own
and holds no write key, and that the folder mode loads no test file (a test that did not compile once stopped the engine for everybody).

The live behaviour is tried against the running stack, not here: verify/policy_signing_attacks.sh (an unsigned, wrongly signed or altered package is refused and
the last good policy keeps answering), verify/policy_persistence_check.sh (the engine starts from its saved package with the store down), and
verify/policy_store_check.sh, which is U190 (who can reach the store and what they can do there).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

# The shared helpers read these at import; this check uses neither a database nor a service.
os.environ.setdefault("MUNITAS_PG_DSN", "postgresql://munitas:munitas@localhost:5432/platform")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS", "http://localhost:4433")
os.environ.setdefault("MUNITAS_S3_ENDPOINT", "http://localhost:8333")

from common import check, heading, summary  # noqa: E402


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def section(text: str, start: str, end: str | None = None) -> str:
    a = text.index(start)
    return text[a:text.index(end, a + len(start))] if end else text[a:]


def main() -> int:
    compose = read("docker-compose.yml")
    override = read("docker-compose.opa-bundle.yml")
    config = read("platform/policy-bundle/opa.yaml")
    claims = read("platform/policy-bundle/signing-claims.json")
    publish_ps = read("publish-policy.ps1")
    publish = read("platform/policy-bundle/publish.sh")
    store = read("platform/policy-bundle/store.sh")
    wsl = read("wsl-docker.ps1")
    authz = read("platform/policy/system_authz.rego")
    api_config = read("platform/api/app/config.py")

    opa = section(compose, "\n  opa:", "\n  step-gateway:")
    engine = section(override, "\n  opa:", "\n  policy-store:")
    policy_store = section(override, "\n  policy-store:", "\nnetworks:")
    url = re.search(r"url: http://([\w-]+):(\d+)", config)
    resource = re.search(r"resource: (\S+)", config)
    key_id = re.search(r"keyid: (\w+)", config)
    scope = re.search(r"scope: (\S+)", config)
    claimed = re.search(r'"scope":\s*"([^"]+)"', claims)
    mount = re.search(r"\}/opa-bundles:(\S+)", override)
    directory = re.search(r"persistence_directory: (\S+)", config)
    image = re.search(r"image: (openpolicyagent/opa:\S+)", opa)

    heading("U189: folder mode")
    check("it reloads when a file changes", '- "--watch"' in opa)
    check("it loads no test file", '- "--ignore=*_test.rego"' in opa)
    check("it reads the policy folder", '- "/policy"' in opa and "./platform/policy:/policy:ro" in opa)
    check("the running mode is readable from the container", "munitas.policy-source: ${MUNITAS_OPA_POLICY_SOURCE:-folder}" in opa)

    heading("The engine's own interface, in both modes")
    for name, text in (("folder mode", opa), ("bundle mode", engine)):
        check(f"{name} needs a token and an authorization policy", '"--authentication=token"' in text and '"--authorization=basic"' in text)
    check("the engine and the API are given the same token setting",
          compose.count("MUNITAS_OPA_TOKEN: ${MUNITAS_OPA_TOKEN:-dev-opa-token-not-for-production}") == 2)
    check("the authorization policy reads the token from the engine's environment and refuses by default",
          "opa.runtime().env.MUNITAS_OPA_TOKEN" in authz and "default allow := false" in authz)
    check("the authorization policy allows no change of policy", "policies" not in authz and 'input.method in {"GET", "POST"}' in authz)
    check("a production start refuses the published token", '("MUNITAS_OPA_TOKEN", not OPA_TOKEN or OPA_TOKEN == "dev-opa-token-not-for-production")' in api_config)

    heading("Bundle mode: the engine")
    check("the engine starts on the config file", "--config-file=/etc/opa/config.yaml" in engine)
    check("the engine's policy folder is replaced by the config", "volumes: !override" in engine and "./platform/policy-bundle/opa.yaml:/etc/opa/config.yaml:ro" in engine)
    check("the engine waits for the store", re.search(r"depends_on:\n\s+- policy-store", engine) is not None)
    check("the engine is on its own network and the store's, and nothing else is added", "networks: !override [control, policy]" in engine)

    heading("Bundle mode: the store")
    check("the store is on a network of its own and on no other", "networks: [policy]" in policy_store and "\nnetworks:\n  policy:" in override)
    check("the store publishes no port", "ports:" not in policy_store)
    check("the store is an object store that reads its identities from a file", "-s3.config=/etc/seaweedfs/s3.json" in policy_store)
    check("the store's identity file and the engine's key file are in the same folder of the data folder",
          "/policy-store/s3.json:/etc/seaweedfs/s3.json:ro" in policy_store and "/policy-store/opa.env" in engine)
    check("the engine fetches from the store by its service name and port", url is not None and url.group(1) == "policy-store" and url.group(2) == "8333")
    check("the engine signs its requests with the key in its environment", "s3_signing:" in config and "environment_credentials" in config)
    check("the package is where the helper puts it", resource is not None and resource.group(1) == "policy-bundles/policy.tar.gz"
          and "http://policy-store:8888/buckets/policy-bundles/" in store and "/tmp/policy.tar.gz" in store)
    check("the key made for the store may read that bucket and do nothing else",
          '"actions":["Read:policy-bundles"]' in publish and "AWS_ACCESS_KEY_ID=%s" in publish and publish.count('"actions"') == 1)
    check("the store's keys are made in the data folder, never in the repository", '"$DATA/policy-store"' in publish and "umask 077" in publish)
    check("nothing writes with a key: the package goes in through the store's own container", "docker exec" in store and "weed filer.copy" in store and "secretKey" not in store)

    heading("The signature")
    check("the config verifies with a named key and a scope", key_id is not None and scope is not None)
    check("the key is named in the config and its file is given on the command line",
          key_id is not None and f"keys:\n  {key_id.group(1)}:" in config
          and f"--set-file=keys.{key_id.group(1)}.key=/etc/opa/signing-public.pem" in engine
          and "./platform/policy-bundle/signing-public.pem:/etc/opa/signing-public.pem:ro" in engine)
    check("the scope the config checks is the scope the script signs", scope is not None and claimed is not None and scope.group(1) == claimed.group(1))
    check("the script signs with the private key and the claims file",
          "--signing-key /keys/private.pem" in publish and "--claims-file /keys/claims.json" in publish
          and "platform/policy-bundle/signing-claims.json:/keys/claims.json:ro" in publish)
    check("the script checks that the public key in the repository belongs to the private key", "openssl rsa -in" in publish and "is not the public half" in publish)
    check("the private key lives outside the repository", '"$HOME/.munitas/policy-signing"' in publish and not (ROOT / "platform/policy-bundle/private.pem").exists())

    heading("Keeping the last good package")
    check("the engine saves the packages it accepts", "persist: true" in config and directory is not None)
    check("the saved packages are on a mount", mount is not None and directory is not None and mount.group(1) == directory.group(1))

    heading("Publishing")
    check("the script tests before it builds, and builds without the tests",
          publish.index(" test /policy") < publish.index(" build -b /policy") and "--ignore '*_test.rego'" in publish)
    check("the script puts the package in the store through the helper", "store.sh ready" in publish and "store.sh upload" in publish)
    check("the Windows script runs these steps and passes only paths and a label", "platform/policy-bundle/publish.sh" in publish_ps and "NEW_KEY=" in publish_ps)
    check("the script uses the engine's own image, read from Compose, not written twice",
          image is not None and "openpolicyagent/opa:" not in publish and "openpolicyagent/opa:\\S+" in publish_ps)

    heading("Choosing the mode")
    check("Get-ComposePrefix adds the override in bundle mode", "-f docker-compose.yml -f docker-compose.opa-bundle.yml" in wsl and "Get-OpaPolicySource" in wsl)
    check("a value other than folder or bundle is refused", '$value -notin @("folder", "bundle")' in wsl)
    check("a production start refuses folder mode", "MUNITAS_ENV is production, so MUNITAS_OPA_POLICY_SOURCE must be bundle" in wsl)
    return summary("U189")


if __name__ == "__main__":
    sys.exit(main())
