"""V7: an agent on agentnet cannot reach the network.

Run separately from the other agent checks, because it cannot be tested from
the host. The host has a route to the internet, so an egress attempt from here
succeeds and proves nothing except that the machine is online. The claim is
about where the agent runs, not about what its code asks for.

So this launches a container attached to `munitas_agentnet`, which Compose
declares `internal: true` and which therefore has no gateway, and attempts to
leave from inside it.

Three attempts, because a single failure has several possible causes and only
one of them is the guarantee:

  * DNS resolution, which fails without a route
  * a direct connection to a routable IP, which skips DNS entirely
  * a connection to a service on another Compose network, which must also fail,
    since an agent reaching the control plane sideways is the same problem

The control tells the three apart: the same attempts from a container on the
data network must succeed, or the test is measuring a broken Docker install
rather than a policy.

    .venv\\Scripts\\python.exe verify\\v7_egress.py
"""

from __future__ import annotations

import json
import subprocess
import sys

IMAGE = "python:3.12-slim"
NETWORK = "munitas_agentnet"
CONTROL_NETWORK = "munitas_data"

# Runs inside the container. Reports per attempt rather than exiting on the
# first failure, so one result cannot mask another.
PROBE = r"""
import json, socket, urllib.error, urllib.request
out = {}

def reach(url):
    # An HTTPError means the TCP connection succeeded and the server replied,
    # so it counts as reachable. Treating it as a failure because it is an
    # exception would report a working connection as a blocked one.
    try:
        urllib.request.urlopen(url, timeout=6).read(16)
        return "connected"
    except urllib.error.HTTPError as exc:
        return f"connected: server replied {exc.code}"
    except Exception as exc:
        return f"failed: {type(exc).__name__}"

try:
    socket.gethostbyname("example.com")
    out["dns"] = "resolved"
except Exception as exc:
    out["dns"] = f"failed: {type(exc).__name__}"

out["direct_ip"] = reach("http://1.1.1.1")
out["other_network"] = reach("http://seaweedfs:8333")

print(json.dumps(out))
"""

_results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    _results.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def probe(network: str) -> dict:
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", network, IMAGE, "python", "-c", PROBE],
        capture_output=True, text=True, timeout=180,
    )
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    return {"error": (result.stderr or result.stdout)[:300]}


def main() -> int:
    print("\nV7: egress from the agent network")
    print("-" * 33)

    internal = subprocess.run(
        ["docker", "network", "inspect", NETWORK, "--format", "{{.Internal}}"],
        capture_output=True, text=True, timeout=60,
    ).stdout.strip()
    check("agentnet is declared internal", internal == "true", f"Internal={internal}")

    agent = probe(NETWORK)
    if "error" in agent:
        check("the probe ran on agentnet", False, agent["error"])
        return 1

    check("DNS resolution fails", agent["dns"].startswith("failed"), agent["dns"])
    check("a direct connection to a routable IP fails",
          agent["direct_ip"].startswith("failed"), agent["direct_ip"])
    check("a service on another Compose network is unreachable",
          not agent["other_network"].startswith("connected"), agent["other_network"])

    print("\nControl: the same probe from the data network")
    print("-" * 44)
    print("  If these also fail, the test above is measuring a broken network")
    print("  rather than an enforced boundary.")
    control = probe(CONTROL_NETWORK)
    if "error" in control:
        check("the control probe ran", False, control["error"])
    else:
        check("the control container reaches SeaweedFS",
              control["other_network"].startswith("connected"), control["other_network"])
        check("the control container resolves DNS",
              control["dns"] == "resolved", control["dns"])

    print("\nWhat this does and does not show")
    print("-" * 33)
    print("  It shows Docker's internal network has no route out, which is a")
    print("  real boundary and holds against application code that tries to")
    print("  leave. It is weaker than the design's Cilium policy: this is a")
    print("  property of one Docker network, not a kernel-enforced rule that")
    print("  travels with the workload, and anything with control of the")
    print("  container's network configuration is outside what it constrains.")

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"\nV7: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
