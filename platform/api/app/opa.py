"""Policy decisions, delegated to Open Policy Agent.

The API never decides access itself. It assembles the facts, asks OPA, and
records the answer. That split is the whole point: the rules are testable in
isolation by `opa test`, and V5 exercises them without the API running at all.
"""

from __future__ import annotations

import httpx

from . import config

_DECISION_PATH = "/v1/data/munitas/access/decision"


class PolicyUnavailable(Exception):
    """OPA could not be reached or returned something unusable.

    Raised rather than swallowed. A policy engine that is down must fail closed,
    because the alternative is a service that quietly grants everything the
    moment its authority disappears.
    """


_APPROVAL_PATH = "/v1/data/munitas/access/approval_decision"


def may_approve(payload: dict) -> tuple[bool, list[str]]:
    """May this principal approve this lease request?

    A different question from `evaluate`, which asks whether somebody may read
    data. A custodian decides who reads an asset without necessarily being able
    to read it themselves, so the two answers are independent.
    """
    return _ask(_APPROVAL_PATH, payload)


_GATE_PATH = "/v1/data/munitas/access/gate_decision"


def may_decide_gate(payload: dict) -> tuple[bool, list[str]]:
    """May this principal decide whether a de-identification clears the gate?

    A fourth question, alongside who may read, who may approve a request, and
    what may leave. Whether a de-identification is adequate is a judgement
    about a measurement, not about ownership, so the custodian who answers
    access requests is deliberately not the answer here.
    """
    return _ask(_GATE_PATH, payload)


_PIPELINE_START_PATH = "/v1/data/munitas/access/pipeline_start_decision"


def may_start_pipeline(payload: dict) -> tuple[bool, list[str]]:
    """May this principal start a de-identification run?

    A fifth question, and the only operational one. The other four ask who may
    read data, who may approve a request for it, what may leave, and whether a
    de-identification is adequate. This asks whose job it is to press the
    button, which is deliberately not the same as being allowed to see what the
    button produces.
    """
    return _ask(_PIPELINE_START_PATH, payload)


_EGRESS_APPROVAL_PATH = "/v1/data/munitas/access/egress_approval_decision"


def may_approve_egress_hosts(payload: dict) -> tuple[bool, list[str]]:
    """May this principal approve the hosts an agent version may call?

    Not the same question as whether the agent may currently reach the open
    internet at all (today, only network isolation answers that). This is the
    governance half: who may sign off on the requested list before a version
    is deployable, the same "a named role decides, not a config file" shape
    as every other approval here.
    """
    return _ask(_EGRESS_APPROVAL_PATH, payload)


_ROLE_REQUEST_PATH = "/v1/data/munitas/access/role_request_decision"


def may_request_role(payload: dict) -> tuple[bool, list[str]]:
    """May this principal ask to hold this role?

    Asked at all because the answer is not always yes: `platform_admin` is
    not obtainable from inside the running system, for anybody, which is the
    one refusal this question exists for.
    """
    return _ask(_ROLE_REQUEST_PATH, payload)


_ROLE_APPROVAL_PATH = "/v1/data/munitas/access/role_approval_decision"


def may_approve_role(payload: dict) -> tuple[bool, list[str]]:
    """May this principal decide somebody else's role?

    The seventh question, and the one that keeps `role_floor`'s claim about
    the administrator true: they operate the system that records the
    decision, so they do not also make it.
    """
    return _ask(_ROLE_APPROVAL_PATH, payload)


_ATTEST_PATH = "/v1/data/munitas/access/attest_decision"


def may_attest_role(payload: dict) -> tuple[bool, list[str]]:
    """May this principal confirm somebody else's role is still needed?

    A smaller act than granting one: it renews nothing and can only narrow
    access. What it must not be is self-service, or the overdue list becomes
    a button people press about themselves.
    """
    return _ask(_ATTEST_PATH, payload)


_HOUSEKEEPING_PATH = "/v1/data/munitas/access/housekeeping_decision"


def may_see_housekeeping(payload: dict) -> tuple[bool, list[str]]:
    """May this principal see storage housekeeping, at this scope?

    A sixth question, and the first about telemetry rather than about data.
    Which buckets exist, how full the volume pool is and what could be freed
    say nothing about what any dataset contains, which is why the platform
    roles may see all of it and still hold no standing access to records.
    The tenant scope is a different question with a different answer: one
    organisation's own deletions, which belong to that organisation.
    """
    return _ask(_HOUSEKEEPING_PATH, payload)


_FREE_STORAGE_PATH = "/v1/data/munitas/access/free_storage_decision"


def may_free_storage(payload: dict) -> tuple[bool, list[str]]:
    """May this principal destroy stored bytes?

    Deliberately not the same question as seeing that they could be. Support
    and reliability diagnose that storage needs freeing; the administrator is
    who frees it. The same shape as may_decide_gate refusing whoever started
    the run: reporting a problem is not authority to act on it.
    """
    return _ask(_FREE_STORAGE_PATH, payload)


_RETIRE_PATH = "/v1/data/munitas/access/retire_decision"
_CANCEL_PATH = "/v1/data/munitas/access/cancel_decision"
_PLACE_HOLD_PATH = "/v1/data/munitas/access/place_hold_decision"
_DECIDE_HOLD_PATH = "/v1/data/munitas/access/decide_hold_decision"
_RELEASE_HOLD_PATH = "/v1/data/munitas/access/release_hold_decision"
_SEE_LIFECYCLE_PATH = "/v1/data/munitas/access/see_lifecycle_decision"


def may_retire(payload: dict) -> tuple[bool, list[str]]:
    """May this principal start closing this organisation?

    Starting it begins a countdown that ends in everything inside the organisation
    being deleted, so it belongs to the organisation's own data custodians and to a
    platform administrator acting on its written instruction, and it needs a reason.
    """
    return _ask(_RETIRE_PATH, payload)


def may_cancel_retirement(payload: dict) -> tuple[bool, list[str]]:
    """May this principal stop an organisation's closing while it can still be stopped?"""
    return _ask(_CANCEL_PATH, payload)


def may_place_hold(payload: dict) -> tuple[bool, list[str]]:
    """May this principal record a legal hold, and does the notice say enough?

    A hold overrides the organisation's wishes, so no member of the organisation
    places one. The refusal names every part of the notice that is missing.
    """
    return _ask(_PLACE_HOLD_PATH, payload)


def may_decide_hold(payload: dict) -> tuple[bool, list[str]]:
    """May this principal approve or decline a hold somebody else placed?

    A different platform administrator from the one who placed it, the same
    shape as a lease, where nobody approves their own.
    """
    return _ask(_DECIDE_HOLD_PATH, payload)


def may_release_hold(payload: dict) -> tuple[bool, list[str]]:
    """May this principal end a hold that is in force, with a reason on record?"""
    return _ask(_RELEASE_HOLD_PATH, payload)


def may_see_lifecycle(payload: dict) -> tuple[bool, list[str]]:
    """May this principal see where an organisation is in its closing?"""
    return _ask(_SEE_LIFECYCLE_PATH, payload)


_EXPORT_REQUEST_PATH = "/v1/data/munitas/access/export_request_decision"
_EXPORT_APPROVAL_PATH = "/v1/data/munitas/access/export_approval_decision"
_EXPORT_CONFIRMATION_PATH = "/v1/data/munitas/access/export_confirmation_decision"
_EXPORT_LINK_PATH = "/v1/data/munitas/access/export_link_decision"
_EXPORT_PASSPHRASE_PATH = "/v1/data/munitas/access/export_passphrase_decision"


def may_request_export(payload: dict) -> tuple[bool, list[str]]:
    """May this principal ask for an organisation's records to be produced for a legal matter?

    Only a platform administrator, only while a legal hold is in force, and only with the demand and the
    recipient written down. The refusal names what is missing."""
    return _ask(_EXPORT_REQUEST_PATH, payload)


def may_approve_export(payload: dict) -> tuple[bool, list[str]]:
    """May this principal approve an export? A different platform administrator from the one who asked."""
    return _ask(_EXPORT_APPROVAL_PATH, payload)


def may_confirm_export(payload: dict) -> tuple[bool, list[str]]:
    """May this principal confirm what an export holds? Only the custodian the hold names."""
    return _ask(_EXPORT_CONFIRMATION_PATH, payload)


def may_link_export(payload: dict) -> tuple[bool, list[str]]:
    """May this principal make a download link for a ready package?"""
    return _ask(_EXPORT_LINK_PATH, payload)


def may_read_passphrase(payload: dict) -> tuple[bool, list[str]]:
    """May this principal be given a package's passphrase? The hold's custodian, once."""
    return _ask(_EXPORT_PASSPHRASE_PATH, payload)


_EXPORT_PATH = "/v1/data/munitas/access/export_decision"


def may_export(payload: dict) -> tuple[bool, list[str]]:
    """May this data leave the platform?

    A third question, alongside whether somebody may read it and who may approve
    a request. Answered by provenance rather than by class, so that a public
    corpus and a de-identified clinical dataset at the same class get different
    answers.
    """
    return _ask(_EXPORT_PATH, payload)


def evaluate(payload: dict) -> tuple[bool, list[str]]:
    """Return (allowed, reasons). Reasons are present either way."""
    return _ask(_DECISION_PATH, payload)


_PREVIEW_PATH = "/v1/data/munitas/access/preview"


def preview(payload: dict) -> dict:
    """What `decision` would say about each of many versions, for one person.

    Returns the policy's answer keyed by version id. Raises PolicyUnavailable
    rather than returning nothing, because an empty answer would read as
    "nothing is readable" and a missing engine is not that.
    """
    try:
        response = httpx.post(
            f"{config.OPA_URL}{_PREVIEW_PATH}", json={"input": payload}, timeout=5.0
        )
        response.raise_for_status()
        result = response.json().get("result")
    except httpx.HTTPError as exc:
        raise PolicyUnavailable(f"policy engine unreachable: {exc}") from exc
    if not isinstance(result, dict):
        raise PolicyUnavailable(
            "policy engine returned no preview; the bundle may have failed to load"
        )
    return result


def read_document(path: str) -> object:
    """Read a plain document out of the policy bundle.

    Used so the console can render the class floors and the approving roles as
    the policy actually defines them, rather than as a copy in TypeScript. A
    screen that describes the rules and disagrees with them is worse than no
    screen, and this project has already had one drift.
    """
    try:
        response = httpx.get(f"{config.OPA_URL}/v1/data/{path}", timeout=5.0)
        response.raise_for_status()
        return response.json().get("result")
    except httpx.HTTPError as exc:
        raise PolicyUnavailable(f"policy engine unreachable: {exc}") from exc


def _ask(path: str, payload: dict) -> tuple[bool, list[str]]:
    try:
        response = httpx.post(
            f"{config.OPA_URL}{path}",
            json={"input": payload},
            timeout=5.0,
        )
        response.raise_for_status()
        result = response.json().get("result")
    except httpx.HTTPError as exc:
        raise PolicyUnavailable(f"policy engine unreachable: {exc}") from exc

    if not isinstance(result, dict) or "allow" not in result:
        raise PolicyUnavailable(
            "policy engine returned no decision; the bundle may have failed to load"
        )

    return bool(result["allow"]), list(result.get("reasons", []))
