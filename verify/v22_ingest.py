"""U22 to U27: bringing data in, and the two ways it can go wrong.

The claim this whole change rests on is U25: **sensitivity says who may read
something, provenance says whether it may leave.** Two datasets at the same
class get different answers, and the one people expect to be allowed is the one
that must be refused.

Per assertion, never in aggregate.

    docker compose exec -T munitas-api python /verify/v22_ingest.py
"""

from __future__ import annotations

import io
import sys
import uuid

from common import (CANARY, ENGINEER, RESEARCHER, api, bearer_for, check, db,
                    heading, require_api, summary, tiny_wav)

TENANT = CANARY


def department(name: str) -> tuple[str, str]:
    """A seeded department and its custodian."""
    with db() as conn:
        row = conn.execute(
            "select id, custodian from department where tenant_id = %s and name = %s",
            (TENANT, name),
        ).fetchone()
    if not row:
        raise RuntimeError(
            f"department {name!r} is missing from tenant {TENANT!r}. Apply "
            "infra/postgres/seed-canary.sql."
        )
    return str(row["id"]), row["custodian"]


def register(**over) -> "object":
    body = {
        "tenant_id": TENANT,
        "name": f"upload-{uuid.uuid4().hex[:8]}",
        "department_id": over.pop("department_id", None),
        "registered_by": ENGINEER,
        "provenance": "internal_regulated",
        "declared_class": "RAW",
        "source_kind": "upload",
    }
    body.update(over)
    return api("POST", "/datasets/register", json=body)


def upload_a_file(dataset_id: str, name: str = "recording.wav") -> "object":
    # A real wav when the name says wav. The upload endpoint reads what it is
    # given and refuses a file that contradicts its own name, so the words
    # "pretend audio bytes" under a .wav name would now be refused, correctly.
    payload = tiny_wav() if name.endswith(".wav") else b"pretend file contents"
    return api(
        "POST",
        f"/datasets/{dataset_id}/files",
        files={"file": (name, io.BytesIO(payload), "application/octet-stream")},
    )


def main() -> int:
    require_api()
    owning, custodian = department("Verification")
    _, another_custodian = department("Elsewhere")

    # ------------------------------------------------------------ U26 --
    heading("U26: nothing arrives without an owner")

    orphan = register(department_id=None)
    check("registering without a department is refused",
          orphan.status_code in (400, 422), f"HTTP {orphan.status_code}")

    unknown = register(department_id=str(uuid.uuid4()))
    check("registering to a department that does not exist is refused",
          unknown.status_code == 400, f"HTTP {unknown.status_code}")

    owned = register(department_id=owning)
    check("registering with a real department succeeds",
          owned.status_code == 201, f"HTTP {owned.status_code} {owned.text[:120]}")
    dataset_id = owned.json()["id"] if owned.status_code == 201 else None
    if not dataset_id:
        return summary("U22 to U27")

    # ------------------------------------------------------------ U23 --
    heading("U23: a claim is recorded as a claim")

    safe = owned.json()
    check("asking for the safest class is not treated as a claim",
          safe["declaration_basis"] is None, str(safe["declaration_basis"]))
    check("and so nobody has to confirm it",
          safe["needs_confirmation"] is False)

    claimed = register(department_id=owning, declared_class="PUBLISHED",
                       provenance="external_public")
    check("declaring data less sensitive is accepted",
          claimed.status_code == 201, f"HTTP {claimed.status_code}")
    claim = claimed.json()
    check("and recorded as somebody's assertion",
          claim["declaration_basis"] == "asserted", str(claim["declaration_basis"]))
    check("which somebody else has to confirm",
          claim["needs_confirmation"] is True)

    with db() as conn:
        row = conn.execute(
            "select declared_by, declared_at, declared_class from dataset where id = %s",
            (claim["id"],),
        ).fetchone()
    check("the claim carries the name of whoever made it",
          row["declared_by"] == ENGINEER, str(row["declared_by"]))
    check("and when they made it", row["declared_at"] is not None)

    # Naming HuggingFace as the source is not, by itself, verification of
    # anything: it is a label the caller supplied, the exact shape of claim
    # this project keeps refusing to trust elsewhere (identity from the
    # request body, an approver from any string). So this starts out exactly
    # like any other claim, asserted and unconfirmed, and only becomes
    # `verified_source` once `fetch-huggingface` has actually made the
    # request and can show what it found. See v43_huggingface_fetch.py.
    labelled = register(department_id=owning, declared_class="PUBLISHED",
                        provenance="external_public", source_kind="huggingface",
                        locator="hf://example/corpus")
    check("naming huggingface as the source is still an assertion, not a fact",
          labelled.json().get("declaration_basis") == "asserted",
          str(labelled.json().get("declaration_basis")))
    check("so it still needs confirmation",
          labelled.json().get("needs_confirmation") is True)

    # ------------------------------------------------------------ U22 --
    heading("U22: registering exposes nothing")

    up = upload_a_file(dataset_id)
    check("a file can be uploaded", up.status_code == 201,
          f"HTTP {up.status_code} {up.text[:120]}")
    check("the upload records a checksum",
          bool(up.json().get("checksum")), str(up.json().get("checksum"))[:20])

    sealed = api("POST", f"/datasets/{dataset_id}/seal")
    check("the upload can be sealed into a version", sealed.status_code == 201,
          f"HTTP {sealed.status_code} {sealed.text[:120]}")
    version_id = sealed.json()["id"] if sealed.status_code == 201 else None

    if version_id:
        reader = api("POST", "/credentials", json={
            "principal": RESEARCHER,
            "principal_kind": "human",
            "roles": ["notebook_explore"],
            "tenant_id": TENANT,
            "dataset_version_id": version_id,
            "purpose": "curiosity",
        })
        check("a researcher cannot read it just because it exists",
              reader.status_code == 403, f"HTTP {reader.status_code}")

        with db() as conn:
            logged = conn.execute(
                """select count(*) as n from access_decision
                   where dataset_version_id = %s and principal = %s""",
                (version_id, RESEARCHER),
            ).fetchone()
        check("and the attempt is in the audit log", logged["n"] > 0,
              f"{logged['n']} decisions")

    # ------------------------------------------------------------ U27 --
    heading("U27: the way in is not a way out")

    readback = api("GET", f"/datasets/{dataset_id}/files")
    check("the upload endpoint has no way to read files back",
          readback.status_code in (404, 405), f"HTTP {readback.status_code}")

    # ------------------------------------------------------------ U24 --
    heading("U24: one person cannot release their own upload")

    claim_id = claim["id"]
    wrong = api("POST", f"/datasets/{claim_id}/confirm-classification",
                json={"confirmed_by": another_custodian})
    check("a custodian of another department cannot confirm",
          wrong.status_code == 403, f"HTTP {wrong.status_code}")

    self_confirm = api("POST", f"/datasets/{claim_id}/confirm-classification",
                       json={"confirmed_by": ENGINEER})
    check("the person who made the claim cannot confirm it",
          self_confirm.status_code == 403, f"HTTP {self_confirm.status_code}")

    right = api("POST", f"/datasets/{claim_id}/confirm-classification",
                json={"confirmed_by": custodian})
    check("the owning custodian can confirm", right.status_code == 200,
          f"HTTP {right.status_code} {right.text[:120]}")

    already = api("POST", f"/datasets/{dataset_id}/confirm-classification",
                  json={"confirmed_by": custodian})
    check("there is nothing to confirm when no claim was made",
          already.status_code == 409, f"HTTP {already.status_code}")

    # ------------------------------------------------------------ U25 --
    heading("U25: whether data may leave follows provenance, not sensitivity")

    public = register(department_id=owning, declared_class="PUBLISHED",
                      provenance="external_public")
    regulated = register(department_id=owning, declared_class="PUBLISHED",
                         provenance="internal_regulated")

    for name, registered in (("public", public), ("regulated", regulated)):
        did = registered.json()["id"]
        upload_a_file(did, f"{name}.wav")
        api("POST", f"/datasets/{did}/seal")

    public_export = api(
        "GET", f"/datasets/{public.json()['id']}/export",
        params={"purpose": "share the benchmark"},
        headers=bearer_for(ENGINEER),
    )
    regulated_export = api(
        "GET", f"/datasets/{regulated.json()['id']}/export",
        params={"purpose": "share the benchmark"},
        headers=bearer_for(ENGINEER),
    )

    check("data that was already public may leave",
          public_export.status_code == 200,
          f"HTTP {public_export.status_code} {public_export.text[:110]}")

    # The case people expect to be allowed. Same class as the one above.
    check("data that started out regulated may not, at the same sensitivity",
          regulated_export.status_code == 403,
          f"HTTP {regulated_export.status_code}")

    if regulated_export.status_code == 403:
        reasons = regulated_export.json().get("detail", {}).get("reasons", [])
        check("and the refusal says it is about where the data came from",
              any("came from" in r for r in reasons), "; ".join(reasons)[:110])

    with db() as conn:
        exports = conn.execute(
            """select count(*) as n from access_decision
               where principal = %s and purpose = 'share the benchmark'""",
            (ENGINEER,),
        ).fetchone()
    check("both the permitted and the refused export are recorded",
          exports["n"] >= 2, f"{exports['n']} decisions")

    return summary("U22 to U27")


if __name__ == "__main__":
    sys.exit(main())
