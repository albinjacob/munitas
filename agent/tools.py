"""Agent tools, each behind a policy decision.

Every tool call is checked, not just the first. Checking once at the start of a
run and trusting the rest is the mistake that makes prompt injection worth
attempting: the attacker does not need to change what the agent is allowed to
do, only to wait until after the check.

Each decision, allow or deny, is written to `access_decision` through the
control plane. A denial that leaves no trace is indistinguishable from a request
that was never made, and the difference matters when someone asks later whether
an agent tried to reach something it should not have.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .identity import AgentIdentity, Budget
from .tracing import record_decision, tool_span
from ports_config import PORTS

API = os.environ.get("MUNITAS_API", f"http://localhost:{PORTS['munitas_api_http']}")
OPA = os.environ.get("MUNITAS_OPA_URL", f"http://localhost:{PORTS['opa']}")

# Where object storage answers *for this process*. The grant from
# `/credentials` carries an endpoint too, but that one is written from the
# control plane's side of the network: inside Compose it is a service name that
# does not resolve on the host, where the agent actually runs. Which bucket and
# which keys are the grant's to say; how to reach the storage is local
# knowledge, so it is read from the environment the same way the worker reads
# its own.
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", f"http://localhost:{PORTS['seaweedfs_s3']}")


class BudgetExceeded(Exception):
    """A ceiling was hit. The run stops here."""


class ToolDenied(Exception):
    """Policy refused this call. Carries the reasons, which go in the trace."""

    def __init__(self, tool: str, reasons: list[str]) -> None:
        super().__init__(f"{tool} denied: {'; '.join(reasons)}")
        self.tool = tool
        self.reasons = reasons


class CredentialRequestFailed(Exception):
    """`/credentials` answered with something that is not one of its three
    documented outcomes (200, 202, 403 -- see `main.py`'s own docstring on
    `request_credential`).

    Deliberately not a `ToolDenied`. A 404 (no such dataset version), a 422
    (a malformed request -- this is exactly how an agent run started with no
    target used to fail: `None` reaching a required `str` field) or a 5xx are
    not policy decisions, and folding one into an ordinary denial is how a
    real bug ends up recorded as a successful, empty triage indistinguishable
    from a genuine refusal. Uncaught here on purpose: `agent/graph.py`'s
    nodes only catch `ToolDenied` and `BudgetExceeded`, so this propagates
    out of `graph.invoke()` and `worker/agent_run_activities.py`'s own
    `run_agent` correctly reports the run as failed, not succeeded.
    """

    def __init__(self, tool: str, status_code: int, body: str) -> None:
        super().__init__(
            f"{tool}: /credentials answered {status_code}, not 200/202/403: "
            f"{body[:500]}"
        )
        self.tool = tool
        self.status_code = status_code


class AwaitingActivation(Exception):
    """Policy allowed this call, but the storage access is not in effect yet.

    Never a refusal. The control plane answered 202: the decision is recorded
    and the platform is updating storage permissions. The graph parks the run
    at a checkpoint on this, and the platform resumes it once the access is
    active, re-running the step that raised.
    """

    def __init__(self, tool: str, reasons: list[str]) -> None:
        super().__init__(f"{tool} allowed, waiting for storage access to take effect")
        self.tool = tool
        self.reasons = reasons


@dataclass
class ToolContext:
    """Runtime state for one agent run. Not part of the graph state."""

    identity: AgentIdentity
    run_id: str
    budget: Budget
    # Off by default so the containment tests run without a model server and
    # stay fast. The defence does not depend on this being on, which is the
    # property worth having.
    use_model: bool = False
    tool_calls: int = 0
    started_at: float = field(default_factory=time.monotonic)
    decisions: list[dict] = field(default_factory=list)

    def charge(self, tool: str) -> None:
        self.tool_calls += 1
        if self.tool_calls > self.budget.max_tool_calls:
            raise BudgetExceeded(
                f"tool call ceiling of {self.budget.max_tool_calls} reached "
                f"while calling {tool}"
            )
        elapsed = time.monotonic() - self.started_at
        if elapsed > self.budget.max_seconds:
            raise BudgetExceeded(
                f"wall clock ceiling of {self.budget.max_seconds}s reached "
                f"after {elapsed:.0f}s"
            )


def _version(ctx: ToolContext, version_id: str, timeout: float = 20.0):
    """Fetch a version's metadata, scoped to the run's own organisation.

    The tenant comes from the runtime identity, which is where every other
    decision about this agent comes from. Without it the agent could describe a
    version belonging to somebody else: its dataset name, its class and its
    release history. Policy would still refuse it a credential, so no data would
    move, but "cannot read the data" and "cannot see that it exists" are
    different claims and V8 is about the second as well.

    A version in another organisation comes back 404, so the caller cannot tell
    it apart from one that was never there.
    """
    # The run's own signed credential, which names the organisation. Without it the control
    # plane answers nobody, because this read returns a version's storage keys.
    headers = {"x-task-credential": ctx.identity.run_secret} if ctx.identity.run_secret else {}
    return httpx.get(
        f"{API}/dataset-versions/{version_id}",
        params={"tenant_id": ctx.identity.tenant},
        headers=headers,
        timeout=timeout,
    )


def _authorise(ctx: ToolContext, tool: str, version_id: str) -> dict:
    """Ask the control plane, record the answer, and refuse on a denial.

    Routed through the credential endpoint rather than straight to OPA, because
    that endpoint is what writes the audit row. An agent that consulted OPA
    directly would get the same decision and leave no record of having asked.

    Returns the grant on success, which carries the prefix-scoped storage
    credential. Tools that only need metadata ignore it; a tool that reads
    objects needs it, and getting it from here rather than asking separately
    keeps one authorisation behind one audit row.
    """
    ctx.charge(tool)

    version_class = None
    try:
        meta = _version(ctx, version_id, timeout=10.0)
        if meta.status_code == 200:
            version_class = meta.json().get("current_class")
    except httpx.HTTPError:
        pass

    with tool_span(tool, principal=ctx.identity.principal, tenant=ctx.identity.tenant,
                   dataset_version=version_id, visibility_class=version_class,
                   agent_run_id=ctx.run_id,
                   agent_version_id=ctx.identity.agent_version_id) as span:
        return _decide(ctx, tool, version_id, span)


def _decide(ctx: ToolContext, tool: str, version_id: str, span) -> dict:
    response = httpx.post(f"{API}/credentials", json={
        "principal": ctx.identity.principal,
        "principal_kind": "workload",
        "roles": list(ctx.identity.roles),
        "tenant_id": ctx.identity.tenant,
        "dataset_version_id": version_id,
        "purpose": ctx.identity.purpose,
        "agent_run_id": ctx.run_id,
        "run_secret": ctx.identity.run_secret,
    }, timeout=20.0)

    if response.status_code == 202:
        reasons = response.json().get("reasons", [])
        ctx.decisions.append({
            "tool": tool,
            "dataset_version": version_id,
            "allowed": True,
            "active": False,
            "reasons": reasons,
        })
        record_decision(span, True, reasons)
        raise AwaitingActivation(tool, reasons)

    allowed = response.status_code == 200
    if response.status_code not in (200, 403):
        # Not one of the three documented outcomes: a malformed request, an
        # unknown dataset version, or a server error, none of which is a
        # policy decision. Raised rather than folded into a denial below --
        # see CredentialRequestFailed's own docstring for why that distinction
        # matters.
        raise CredentialRequestFailed(tool, response.status_code, response.text)

    grant: dict = {}
    if allowed:
        grant = response.json()
        reasons = grant.get("reasons", [])
    else:
        try:
            reasons = response.json().get("detail", {}).get("reasons", [])
        except (json.JSONDecodeError, AttributeError):
            reasons = [f"HTTP {response.status_code}"]

    ctx.decisions.append({
        "tool": tool,
        "dataset_version": version_id,
        "allowed": allowed,
        "reasons": reasons,
    })
    record_decision(span, allowed, reasons)

    if not allowed:
        raise ToolDenied(tool, reasons)

    # Declared but out of scope: refused even though the credential was
    # just granted. Best-effort only, not a security boundary; see
    # AgentIdentity.tool_scope's own comment for why. It exists to catch a
    # mistake in code this platform controls, the same reason a linter
    # catches a typo: useful, and not what stands between an attacker and
    # the data.
    if ctx.identity.tool_scope and tool not in ctx.identity.tool_scope:
        reasons = [f"{tool!r} is outside the tool scope declared for this agent version"]
        ctx.decisions[-1] = {
            "tool": tool, "dataset_version": version_id,
            "allowed": False, "reasons": reasons,
        }
        record_decision(span, False, reasons)
        raise ToolDenied(tool, reasons)

    # A version outside the run's declared scope is refused even when policy
    # would allow it. Policy answers "may this principal read this class"; the
    # scope answers "is this part of the job it was launched to do", and an
    # agent that wanders is a problem even when it wanders somewhere permitted.
    #
    # This is now a fast pre-check backed by a real boundary, not the
    # boundary itself: POST /credentials (main.py) independently checks the
    # same fact, server-side, against agent_run.dataset_version_id, and
    # that check is the one that actually cannot be bypassed by code that
    # skips this module entirely.
    if ctx.identity.allowed_versions and version_id not in ctx.identity.allowed_versions:
        reasons = ["dataset version is outside the scope declared for this run"]
        ctx.decisions[-1] = {
            "tool": tool, "dataset_version": version_id,
            "allowed": False, "reasons": reasons,
        }
        record_decision(span, False, reasons)
        raise ToolDenied(tool, reasons)

    return grant


def read_dataset_version(ctx: ToolContext, version_id: str) -> dict:
    """Read a dataset version's metadata. Policy checked, decision logged."""
    _authorise(ctx, "read_dataset_version", version_id)
    return _version(ctx, version_id).json()


# http/https only. Not a formality: an unrestricted scheme lets a URL read
# a local file (file://) or speak a raw protocol most HTTP clients will
# happily proxy (gopher://, ftp://) instead of making an HTTP request at
# all, a documented class of SSRF bypass this check exists to close.
ALLOWED_SCHEMES = {"http", "https"}


def _resolved_ips(hostname: str) -> tuple[list[str], str | None]:
    """Every address this hostname resolves to right now, and a refusal
    reason if resolution itself failed.

    Never a hostname the URL text is trusted on its own: a resolver can
    answer differently moment to moment (DNS rebinding), so the check has
    to run against a fresh answer, not against what the caller claims.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        return [], f"could not resolve {hostname!r}: {exc}"
    return sorted({info[4][0] for info in infos}), None


def _non_public_ips(ips: list[str]) -> list[str]:
    """Which of these addresses are private, loopback, or link-local.

    `ipaddress.ip_address(...).is_global` is what a real public internet
    address means: not `10.0.0.0/8` or `172.16.0.0/12` or `192.168.0.0/16`
    (the private ranges), not `127.0.0.0/8` (loopback), and not
    `169.254.0.0/16` (link-local, the range a cloud metadata service like
    `169.254.169.254` sits in and hands out real credentials from, no
    login required, to anything that can reach it from inside the host).
    Every resolved address is checked, not just the first, since a
    resolver can hand back a mix and a client may connect to any of them.
    """
    return [ip for ip in ips if not ipaddress.ip_address(ip).is_global]


def _egress_status(ctx: ToolContext) -> tuple[set[str], str | None]:
    """This agent version's approved hosts, and its approval state.

    Asked fresh on every call, the same "checked every time, not just
    once" posture every other tool in this module already takes (see this
    module's own docstring). `GET /agent-versions/{id}/egress-status`
    takes no session, because the caller is a workload, not a person.
    (`GET /dataset-versions/{id}` is different: it carries storage keys, so
    `_version` presents the run's own signed credential.)
    Treated as "nothing approved" if the call fails, the same fail-closed
    posture `opa.py`'s `PolicyUnavailable` takes when the policy engine
    itself cannot be reached -- an egress check that quietly allowed
    everything the moment its own dependency was unreachable would be
    worse than no check.
    """
    try:
        response = httpx.get(
            f"{API}/agent-versions/{ctx.identity.agent_version_id}/egress-status",
            timeout=10.0,
        )
        response.raise_for_status()
        status = response.json()
    except httpx.HTTPError:
        return set(), None
    return {h.lower() for h in status.get("requested_hosts", [])}, status.get("state")


def fetch_url(ctx: ToolContext, url: str) -> str:
    """Fetch something from the network, if this version was approved to.

    Three checks, every call, in order: the host is one this version's
    developer actually declared and a `network_architect` actually
    approved (`docs/internal/diagrams/agent-egress-allowlist`); the host's freshly-
    resolved address is a real public one, not a private, loopback, or
    link-local address a URL could otherwise use to reach something on
    this machine's own network instead of the open internet (server-side
    request forgery); and the response is not a redirect to somewhere
    else, since a first hop that passed both checks can still answer with
    a second address that never would have. A redirect is refused outright
    rather than followed and re-checked -- simpler, and strictly more
    conservative than re-validating each hop.

    Sandboxed runs have no route off `agentnet` at all regardless of this
    check (V7); this is what makes the same guarantee hold for a native
    run too, which has real network access and no network-level boundary
    of its own to fall back on.
    """
    ctx.charge("fetch_url")

    parts = urlsplit(url)
    reasons: list[str] = []
    hostname = (parts.hostname or "").lower()

    if parts.scheme not in ALLOWED_SCHEMES:
        reasons.append(f"scheme {parts.scheme!r} is not http or https")
    if not hostname:
        reasons.append("that URL has no host")

    if not reasons:
        approved_hosts, state = _egress_status(ctx)
        if state != "approved" or hostname not in approved_hosts:
            reasons.append(
                f"{hostname!r} is not an approved host for this agent version"
            )

    if not reasons:
        ips, dns_error = _resolved_ips(hostname)
        if dns_error:
            reasons.append(dns_error)
        elif bad := _non_public_ips(ips):
            reasons.append(
                f"{hostname!r} resolves to a non-public address ({', '.join(bad)})"
            )

    allowed = not reasons
    with tool_span("fetch_url", principal=ctx.identity.principal, tenant=ctx.identity.tenant,
                   agent_run_id=ctx.run_id, agent_version_id=ctx.identity.agent_version_id) as span:
        record_decision(span, allowed, reasons)
    ctx.decisions.append({"tool": "fetch_url", "allowed": allowed, "reasons": reasons})
    if not allowed:
        raise ToolDenied("fetch_url", reasons)

    response = httpx.get(url, timeout=10.0, follow_redirects=False)
    if response.is_redirect:
        raise ToolDenied("fetch_url", [
            "the response redirected to a different address; refused rather "
            "than followed to an address never checked"
        ])
    return response.text[:2000]


def summarise_counts(ctx: ToolContext, version_id: str) -> dict:
    """Return record counts for a version. Also policy checked."""
    _authorise(ctx, "summarise_counts", version_id)
    data = _version(ctx, version_id).json()
    return {
        "dataset_version_id": version_id,
        "current_class": data.get("current_class"),
        "version": data.get("version"),
    }


# The queue file the de-identification pipeline's handoff step seals. Matched by
# suffix against the version's own manifest rather than rebuilt from the storage
# prefix, so a version that does not hold one is an empty queue rather than a
# guessed key that 404s.
REVIEW_QUEUE_FILE = "label-studio-tasks.json"

# A ceiling on documents, because `Budget` does not have one. Its limits are
# tool calls, tokens and wall clock, and a single call returning ten thousand
# transcripts passes all three while still being the thing that exhausts memory
# and the model's context.
MAX_REVIEW_DOCUMENTS = 200


def list_review_queue(ctx: ToolContext, version_id: str,
                      limit: int = MAX_REVIEW_DOCUMENTS) -> list[dict]:
    """Read the transcripts waiting for human review in this version.

    The one tool that reads objects rather than metadata, so it is the one that
    needs the storage credential `_authorise` returns. The authorisation is the
    same one every other tool makes: this reads the queue under whatever lease
    the run holds, and holds no standing access of its own. A run against a RAW
    version with no lease is refused here and the refusal is logged, which is
    the correct outcome rather than a failure to handle.

    Returns the shape the graph's triage node reads: an id, the text, and the
    version it came from. The machine-proposed highlights in the file are
    deliberately not returned. The agent's job is to rank how urgently a human
    should look at a transcript, and handing it the detector's own guesses would
    have it grade the detector's confidence instead of reading the document.
    """
    grant = _authorise(ctx, "list_review_queue", version_id)

    meta = _version(ctx, version_id).json()
    keys = [
        entry.get("key")
        for entry in (meta.get("object_manifest") or [])
        if isinstance(entry, dict) and str(entry.get("key", "")).endswith(REVIEW_QUEUE_FILE)
    ]
    if not keys:
        return []

    import boto3  # imported here so the containment tests need no S3 client
    from botocore.config import Config

    # `s3v4` and a region are not optional against SeaweedFS: without them the
    # request is signed in a way it rejects, and it reports that as AccessDenied,
    # which reads like a missing grant rather than a malformed signature. The
    # same settings are what `verify/common.py` uses.
    #
    # The endpoint choice preserves the deliberate host-vs-container
    # distinction above this function's own module docstring: a SeaweedFS
    # grant's own endpoint is a container-internal address that does not
    # resolve on the host, where this native-path code runs, so that case
    # still uses this module's own S3_ENDPOINT override. R2's endpoint is
    # a real public address, valid from anywhere, so it is always safe to
    # use directly. aws_session_token=None is accepted by boto3 identically
    # to omitting the argument, so this is safe for SeaweedFS's grants,
    # which never carry one.
    endpoint = grant["endpoint"] if grant.get("backend") == "r2" else S3_ENDPOINT
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=grant["access_key"],
        aws_secret_access_key=grant["secret_key"],
        aws_session_token=grant.get("session_token"),
        config=Config(signature_version="s3v4", retries={"max_attempts": 2}),
        region_name="us-east-1",
    )
    body = s3.get_object(Bucket=grant["bucket"], Key=keys[0])["Body"].read()
    tasks = json.loads(body)

    documents = []
    for task in tasks[:limit]:
        data = task.get("data", {}) if isinstance(task, dict) else {}
        documents.append({
            "id": data.get("record_id"),
            "text": data.get("text", ""),
            "dataset_version": version_id,
        })
    return documents
