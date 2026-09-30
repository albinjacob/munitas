"""V10: deletion works without breaking immutability.

Not in the slice 1 list, but the crypto module now exists and this is the claim
that justifies it: a record can be erased while every dataset version that held
it stays sealed and its lineage stays answerable.

Destroying the key is the deletion. The ciphertext is never touched, so it does
not matter how many sealed versions contain it.
"""

from __future__ import annotations

import sys
import uuid

from common import (OVERSIGHT, api, check, db, fixture_tenant, heading,
                    require_api, summary)


def main() -> int:
    require_api()
    tenant = fixture_tenant()
    record_id = f"rec-{uuid.uuid4().hex[:12]}"
    secret = "Patient Aoife Brennan, DOB 1971-03-04, MRN 88213."

    heading("V10: a record is deleted by destroying its key")

    sealed = api("POST", "/records", json={
        "tenant_id": tenant, "record_id": record_id, "plaintext": secret,
    })
    check("record sealed", sealed.status_code == 201, f"HTTP {sealed.status_code}")
    ciphertext = sealed.json()["ciphertext"]

    opened = api("POST", f"/records/{record_id}/open", params={"ciphertext_b64": ciphertext})
    check("record is readable before deletion",
          opened.status_code == 200 and opened.json().get("plaintext") == secret,
          f"HTTP {opened.status_code}")

    gone = api("DELETE", f"/records/{record_id}", json={
        "tenant_id": tenant,
        "reason": "erasure request",
        "requested_by": OVERSIGHT,
    })
    check("deletion accepted", gone.status_code == 200, f"HTTP {gone.status_code}")
    check("the key was destroyed", gone.json().get("key_destroyed") is True)

    # The same ciphertext, which still sits in every sealed version that held it.
    after = api("POST", f"/records/{record_id}/open", params={"ciphertext_b64": ciphertext})
    check("the identical ciphertext is now unreadable", after.status_code == 410,
          f"HTTP {after.status_code}")

    with db() as conn:
        key = conn.execute(
            "select wrapped_key, destroyed_at from record_key where record_id = %s",
            (record_id,),
        ).fetchone()
        tomb = conn.execute(
            "select * from tombstone where record_id = %s", (record_id,)
        ).fetchone()

    check("no key material remains in the database", key["wrapped_key"] is None)
    check("the destruction is timestamped", key["destroyed_at"] is not None,
          str(key["destroyed_at"]))
    check("a tombstone survives the record", tomb is not None)
    check("the tombstone records who asked and why",
          bool(tomb) and tomb["requested_by"] == OVERSIGHT and tomb["reason"] == "erasure request",
          f"{tomb['requested_by']}: {tomb['reason']}" if tomb else "missing")

    repeat = api("DELETE", f"/records/{record_id}", json={
        "tenant_id": tenant, "reason": "erasure request", "requested_by": OVERSIGHT,
    })
    check("deleting twice is safe and reports honestly",
          repeat.status_code == 200 and repeat.json().get("already_destroyed") is True)

    return summary("V10")


if __name__ == "__main__":
    sys.exit(main())
