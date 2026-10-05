"""The routes for a table sealed under a privacy contract: sealing it, and reading it back.

Thin on purpose. Who may do what, what is refused and why, all live in `person_keys`, which the checks call directly as well. Erasing a
person has no route yet: it is an internal operation until the approval flow exists.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import auth, person_keys

router = APIRouter(tags=["person keys"])


class SealTableIn(BaseModel):
    contract: dict
    rows: list[dict]


def _service():
    try:
        return person_keys.key_service()
    except RuntimeError as exc:
        raise HTTPException(503, {"reasons": [f"the key service is not set up: {exc}"]}) from exc


@router.post("/datasets/{dataset_id}/seal-table", status_code=201)
def seal_table(dataset_id: str, body: SealTableIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Seal rows as a version, with the columns that identify a person locked under that person's own key.

    The whole table is refused, with every problem listed, when the contract is wrong or a row names nobody.
    """
    service = _service()
    try:
        return person_keys.seal_table(service, dataset_id=dataset_id, identity=identity, contract=body.contract, rows=body.rows)
    except person_keys.Refused as exc:
        raise HTTPException(400, {"reasons": exc.reasons}) from exc


@router.get("/dataset-versions/{version_id}/person-rows")
def person_rows(version_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """The rows of a table sealed under a privacy contract, with the locked columns opened for a permitted reader.

    A person who was erased has an erased marker where their locked values were. The decision is recorded either way.
    """
    service = _service()
    try:
        rows = person_keys.read_rows(service, version_id=version_id, identity=identity)
    except person_keys.NoSuchVersion as exc:
        raise HTTPException(404, "no such table") from exc
    except person_keys.NotPermitted as exc:
        raise HTTPException(403, {"reasons": exc.reasons}) from exc
    return {"rows": rows, "count": len(rows)}
