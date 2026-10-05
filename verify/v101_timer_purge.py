"""U101: the platform deletes a due organisation by itself, on its own timer, with nobody asking.

U98 to U100 prove what a purge does, and they start every purge by calling the sweep endpoint, which runs the
same function the timer runs. None of them proves the timer itself: a background loop that wakes every few
minutes inside the API. A loop that stopped, or crashed on its first pass, looks exactly like one with nothing
to do. This makes a throwaway organisation that is due for deletion, never calls the sweep, and waits for the
loop to find it.

It takes up to one sweep interval (5 minutes by default), so it is not part of run_all. Run it alone: any other
sweep that ran meanwhile could do the deleting and the check could not tell.

    docker compose exec -T munitas-api python /verify/v101_timer_purge.py
"""

from __future__ import annotations

import os
import sys
import time

import httpx

from common import api, check, db, heading, require_api, skip, summary
from lifecycle_fixture import KRATOS_ADMIN, bucket_exists, drop_org, make_org, move_dates, seal_data

INTERVAL = int(os.environ.get("MUNITAS_LIFECYCLE_SWEEP_SECONDS", "300"))


def main() -> int:
    require_api()
    if INTERVAL <= 0:
        skip("the timer deletes a due organisation", "MUNITAS_LIFECYCLE_SWEEP_SECONDS is 0, so the timer is off")
        return summary("U101")

    org = make_org("verify-timer-")
    try:
        data = seal_data(org)
        api("POST", "/lifecycle/organisation/retire", headers=org.bearer("custodian"),
            json={"reason": "Checking that the timer deletes it"}).raise_for_status()
        move_dates(org.id, retiring_ended=True, closing_ended=True)
        with db() as conn:
            row = conn.execute("select tenant_phase(%s) as p, tenant_purge_allowed(%s) as a", (org.id, org.id)).fetchone()
        heading("Due, unheld, and nobody calls the sweep")
        check("the organisation is due for deletion and nothing stands in the way", (row["p"], row["a"]) == ("purge_due", True), str(row))
        check("it holds sealed data, files and sign-in accounts to delete",
              bucket_exists(data["bucket"]) and len(org.identities) == 3)

        deadline = time.monotonic() + INTERVAL + 90
        started = time.monotonic()
        record = None
        while time.monotonic() < deadline:
            with db() as conn:
                record = conn.execute("select * from tenant_deletion_record where original_tenant_id = %s", (org.id,)).fetchone()
            if record:
                break
            time.sleep(10)
        waited = int(time.monotonic() - started)

        heading("What the timer did")
        check(f"the organisation was deleted without a sweep being asked for ({waited} s, interval {INTERVAL} s)", record is not None)
        if record:
            check("the record says the scheduled sweep did it", record["purged_by"] == "the scheduled sweep", record["purged_by"])
            with db() as conn:
                left = conn.execute("select count(*) as n from tenant where id = %s", (org.id,)).fetchone()["n"]
                versions = conn.execute("select count(*) as n from dataset_version where tenant_id = %s", (org.id,)).fetchone()["n"]
            check("its organisation row and its sealed data are gone", left == 0 and versions == 0)
            check("its bucket is gone", not bucket_exists(data["bucket"]))
            gone = [httpx.get(f"{KRATOS_ADMIN}/admin/identities/{i}", timeout=10.0).status_code for i in org.identities]
            check("its three sign-in accounts are gone", gone == [404, 404, 404], str(gone))
            check("and the record counts them", record["identities_removed"] == 3, str(record["identities_removed"]))
    finally:
        drop_org(org)
    return summary("U101")


if __name__ == "__main__":
    sys.exit(main())
