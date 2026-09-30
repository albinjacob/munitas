"""boundary-prober: proves the real, server-side dataset-scope boundary
holds for a registered user's own uploaded code.

This deliberately does NOT go through agent/tools.py at all -- it calls
POST /credentials directly with plain urllib, the same way an agent
developer's own code would, and the same shape verify/v54_agent_run_scope.py
and verify/v55_sandboxed_agent_run.py's "scope check" fixture already prove
against. The point is that skipping the platform's own helper module does
not help: the server independently remembers, from the moment this run
started, which one dataset version it is for (agent_run.dataset_version_id),
and refuses anything else no matter how the request was made.

Contract (documented in worker/sandbox_run.py, not wrapped in an SDK):
environment variables in, one JSON object printed to stdout before exit.
"""

import json
import os
import urllib.error
import urllib.request


def request_credential(dataset_version_id: str) -> int:
    body = json.dumps({
        "principal": os.environ["MUNITAS_PRINCIPAL_ID"],
        "principal_kind": "workload",
        "roles": [os.environ["MUNITAS_ROLES"]],
        "tenant_id": os.environ["MUNITAS_TENANT_ID"],
        "dataset_version_id": dataset_version_id,
        "purpose": os.environ["MUNITAS_PURPOSE"],
        "agent_run_id": os.environ["MUNITAS_RUN_ID"],
        "run_secret": os.environ.get("MUNITAS_RUN_SECRET"),
    }).encode()
    req = urllib.request.Request(
        os.environ["MUNITAS_API"] + "/credentials", data=body,
        headers={"content-type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def main() -> None:
    own_version = os.environ["MUNITAS_DATASET_VERSION_ID"]
    # There is no way to pass a custom parameter into a sandboxed run today
    # (StartAgentRun only carries purpose/agent_version_id/dataset_version_id/
    # documents/timeout_seconds) -- so the "other" dataset version this agent
    # probes against is baked into the uploaded code itself at build time,
    # the same way verify/v55_sandboxed_agent_run.py's own fixtures do it.
    other_version = "__OTHER_VERSION_ID__"

    own_status = request_credential(own_version)
    other_status = request_credential(other_version) if other_version else None

    result = {
        "marker": "boundary-prober",
        "own_dataset_version_id": own_version,
        "own_status": own_status,
        "own_result": "granted" if own_status == 200 else "refused",
        "other_dataset_version_id": other_version or None,
        "other_status": other_status,
        "other_result": (
            None if other_status is None
            else "refused (correct)" if other_status == 403
            else f"unexpected: {other_status}"
        ),
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
