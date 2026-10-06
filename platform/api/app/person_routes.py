"""The routes for a table sealed under a privacy contract: sealing it, reading it back, leases on its locked columns, and the audit evidence.

Thin on purpose. Who may do what, what is refused and why, all live in `person_keys`, `locked_leases` and the policy, which the checks call
directly as well. Erasing a person has no route yet: it is an internal operation until the approval flow exists.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from . import auth, group_sizes, locked_leases, person_keys, table_files
from .person_errors import AlreadyDecided, LeaseConflict, NoSuchLease, NoSuchVersion, NotPermitted, Refused

router = APIRouter(tags=["person keys"])


class SealTableIn(BaseModel):
    contract: dict
    rows: list[dict]


class LockedColumnLeaseRequestIn(BaseModel):
    dataset_version_id: str
    purpose: str
    days: int
    rows: int = Field(description="How many rows the lease opens")


class LockedColumnLeaseDecisionIn(BaseModel):
    note: str | None = None


class GroupSizeDecisionIn(BaseModel):
    decision: str = Field(description="'accepted' or 'will_change'")
    note: str = Field(description="Why. Recorded with the decision.")


def _service():
    try:
        return person_keys.key_service()
    except RuntimeError as exc:
        raise HTTPException(503, {"reasons": [f"the key service is not set up: {exc}"]}) from exc


def _refusals(call):
    """Run a lease act and turn what can go wrong into the answer that says so."""
    try:
        return call()
    except NotPermitted as exc:
        raise HTTPException(403, {"reasons": exc.reasons}) from exc
    except (LeaseConflict, AlreadyDecided) as exc:
        raise HTTPException(409, {"reasons": exc.reasons}) from exc
    except Refused as exc:
        raise HTTPException(400, {"reasons": exc.reasons}) from exc
    except (NoSuchVersion, NoSuchLease) as exc:
        raise HTTPException(404, "no such table or lease") from exc


@router.post("/datasets/{dataset_id}/seal-table", status_code=201)
def seal_table(dataset_id: str, body: SealTableIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Seal rows as a version, with the columns that identify a person locked under that person's own key.

    The whole table is refused, with every problem listed, when the contract is wrong or a row names nobody.
    """
    service = _service()
    try:
        return person_keys.seal_table(service, dataset_id=dataset_id, identity=identity, contract=body.contract, rows=body.rows)
    except Refused as exc:
        raise HTTPException(400, {"reasons": exc.reasons}) from exc


@router.post("/datasets/{dataset_id}/seal-table-files", status_code=201)
async def seal_table_files(dataset_id: str, contract: UploadFile = File(...), rows: UploadFile = File(...),
                           identity: dict = Depends(auth.current_session)) -> dict:
    """Seal a table from two files: the privacy contract (YAML or JSON) and the rows (a CSV with a header).

    Pressing this is the check: when anything is wrong in either file, nothing is sealed and every problem is listed together, each naming
    its row and column. Values are read as the types the contract declares. At most 20,000 rows and 5 MB; larger tables are sealed another way.
    """
    service = _service()
    # Who may seal is answered before a byte is read, so a person who may not learns that and nothing about their files.
    person_keys.intake_dataset(dataset_id, identity)
    try:
        doc, parsed = table_files.build(await contract.read(table_files.MAX_CONTRACT_BYTES + 1), await rows.read(table_files.MAX_BYTES + 1))
        return person_keys.seal_table(service, dataset_id=dataset_id, identity=identity, contract=doc, rows=parsed)
    except Refused as exc:
        raise HTTPException(400, {"reasons": exc.reasons}) from exc


@router.get("/dataset-versions/{version_id}/person-rows")
def person_rows(version_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """The rows of a table sealed under a privacy contract, with the locked columns opened for somebody the policy lets.

    The owning department's approvers read every row ("mode": "full"). Anybody else reads only the fixed sample their current lease opens
    ("mode": "sample"). A person who was erased has an erased marker where their locked values were. The decision is recorded either way.
    """
    service = _service()
    result = _refusals(lambda: person_keys.read_locked(service, version_id=version_id, identity=identity))
    return {**result, "count": len(result["rows"])}


@router.get("/dataset-versions/{version_id}/locking")
def locking(version_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """What a screen needs to show a locked table to the caller: its columns and how each is treated, what they may do, their lease, the limits.

    Never a value. Asking records nothing: looking at a screen is not reading what was locked.
    """
    return _refusals(lambda: locked_leases.locking_summary(identity, version_id))


@router.post("/locked-column-leases", status_code=201)
def request_lease(body: LockedColumnLeaseRequestIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Ask for a lease on a table's locked columns: a purpose, up to 7 days, up to 20 rows. A department approver decides it."""
    return _refusals(lambda: locked_leases.request_lease(identity, body.dataset_version_id, body.purpose, body.days, body.rows))


@router.get("/locked-column-leases")
def list_leases(identity: dict = Depends(auth.current_session)) -> dict:
    """The person's own leases, and the requests waiting for them as an approver of the department that owns the data."""
    return locked_leases.list_leases(identity)


@router.post("/locked-column-leases/{lease_id}/approve")
def approve_lease(lease_id: str, body: LockedColumnLeaseDecisionIn | None = None, identity: dict = Depends(auth.current_session)) -> dict:
    """Approve a request. Only an approver of the department that owns the data, and never the person who asked."""
    return _refusals(lambda: locked_leases.decide(identity, lease_id, True, body.note if body else None))


@router.post("/locked-column-leases/{lease_id}/decline")
def decline_lease(lease_id: str, body: LockedColumnLeaseDecisionIn | None = None, identity: dict = Depends(auth.current_session)) -> dict:
    """Decline a request, with a note saying why."""
    return _refusals(lambda: locked_leases.decide(identity, lease_id, False, body.note if body else None))


@router.post("/locked-column-leases/{lease_id}/revoke")
def revoke_lease(lease_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """End a lease that is in force: the department's approver, or the person who holds it."""
    return _refusals(lambda: locked_leases.revoke(identity, lease_id))


@router.get("/audit/locked-columns")
def audit_locked_columns(limit: int = 200, identity: dict = Depends(auth.current_session)) -> dict:
    """How locking was used in the caller's organisation: reads and refusals, key uses, leases, contract treatments and erasures.

    For the privacy auditor and the data protection officer. Never a name, a value or an identifier.
    """
    return _refusals(lambda: locked_leases.audit_evidence(identity, limit))


@router.get("/dataset-versions/{version_id}/group-size-report")
def group_size_report(version_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """How many different people share each combination of the table's readable quasi-identifying columns, and what locking one would leave.

    Counts and column names only, never a value. For the owning department's approvers, the de-identification reviewer, the privacy auditor
    and the data protection officer. Advice: it never changes a treatment and never stopped the seal.
    """
    return _refusals(lambda: group_sizes.get_report(identity, version_id))


@router.post("/dataset-versions/{version_id}/group-size-report/decision")
def decide_group_size_report(version_id: str, body: GroupSizeDecisionIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Record what the owner decided about small groups: accept the risk, or change the contract. Once, with a reason."""
    return _refusals(lambda: group_sizes.decide(identity, version_id, body.decision, body.note))
