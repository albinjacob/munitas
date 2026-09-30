"""U72: a role is asked for by one person and granted by another, or not at all.

Roles used to change only by editing Postgres by hand. The obvious fix was an
administration screen, and it is the wrong fix: `role_floor` says the
administrator "runs the system, holds no standing access to its contents", and
that stops being true the moment one account can add `data_custodian` to a row.
An audit entry does not restore it, because an audit says what happened
afterwards and the claim is about what is possible.

So this proves the claim rather than the feature. Five things, and the last
three matter more than the first two:

    a role is asked for, decided by somebody else, and held for a period
    nobody approves their own ask
    the administrator cannot grant a role to anybody, including themselves
    `platform_admin` cannot be obtained from the running system at all,
        refused by the database as well as by the policy
    an ended appointment stops somebody acting, while everything they
        approved stays attributable

Run against a live stack, as real people with real sessions, because a check
that asserts the console's own code proves nothing about what the platform
would refuse.

    docker compose exec -T munitas-api python /verify/v72_role_administration.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import (api, bearer_for, check, db, heading,  # noqa: E402
                    require_api, summary)

# The demonstration organisation's people, because this needs real roles that
# differ from each other: somebody who may decide, somebody who may not, and
# the administrator who deliberately may not either.
ASKER = "sam-researcher"       # notebook_explore
CUSTODIAN = "cust-hartley"     # data_custodian, may decide
ADMIN = "ops-priya"            # platform_admin + hybridops, may not decide
OVERSIGHT = "dpo-nakamura"     # dpo, may confirm a role is still needed

WANTED = "deid_reviewer"


def clean() -> None:
    """Leave nothing behind, so a second run proves the same things."""
    with db() as conn:
        conn.execute(
            "delete from role_grant where principal = %s and role = %s",
            (ASKER, WANTED),
        )
        conn.execute(
            "delete from role_request where principal = %s and role = %s",
            (ASKER, WANTED),
        )
        conn.execute(
            "update directory set ended_at = null where id = %s", (ASKER,)
        )


def ask(who: str, role: str, justification: str):
    return api("POST", "/people/role-requests",
               json={"role": role, "justification": justification},
               headers=bearer_for(who))


def decide(who: str, request_id: str, outcome: str, reason: str):
    return api("POST", f"/people/role-requests/{request_id}/decide",
               json={"outcome": outcome, "reason": reason},
               headers=bearer_for(who))


def main() -> int:
    require_api()
    clean()

    heading("U72: a role is asked for, with a reason")

    asked = ask(ASKER, WANTED, "covering de-identification reviews for a fortnight")
    check("a person may ask to hold a role", asked.status_code == 201,
          f"HTTP {asked.status_code} {asked.text[:120]}")
    request_id = asked.json().get("id") if asked.status_code == 201 else None

    empty = ask(ASKER, WANTED, "   ")
    check("asking without a reason is refused", empty.status_code == 403,
          f"HTTP {empty.status_code}")

    heading("U72: the role that administers roles is not obtainable here")

    # The refusal that matters most. Asked for by the administrator, who is
    # the one person who might plausibly be allowed it.
    admin_ask = ask(ADMIN, "platform_admin", "a second administrator for cover")
    check("nobody may ask for the administering role",
          admin_ask.status_code == 403, f"HTTP {admin_ask.status_code}")
    reasons = (admin_ask.json().get("detail", {}).get("reasons", [])
               if admin_ask.status_code == 403 else [])
    check("and the refusal says it is not obtainable from the running system",
          any("not obtainable" in r for r in reasons), "; ".join(reasons)[:140])

    # Refused by the database too, not only by the policy, so a bug in the
    # API could not write the row either.
    refused_in_db = False
    detail = ""
    try:
        with db() as conn:
            conn.execute(
                """insert into role_request
                     (id, tenant_id, principal, role, justification, requested_days)
                   values (gen_random_uuid(), 'health', %s, 'platform_admin',
                           'straight past the API', 30)""",
                (ASKER,),
            )
    except Exception as exc:  # noqa: BLE001 - the refusal is the result
        refused_in_db = True
        detail = str(exc).splitlines()[0]
    check("and the database refuses it even with the API bypassed",
          refused_in_db, detail or "the row was written")

    heading("U72: nobody decides their own ask")

    if request_id:
        own = decide(ASKER, request_id, "approve", "I need it")
        check("the asker cannot approve their own request", own.status_code == 403,
              f"HTTP {own.status_code}")

        # The combination an auditor tests for: the account that operates the
        # system that records the decision does not also make it.
        by_admin = decide(ADMIN, request_id, "approve", "seems reasonable")
        check("nor can the administrator, who records the decision",
              by_admin.status_code == 403, f"HTTP {by_admin.status_code}")

        heading("U72: somebody else decides, and the role is held for a period")

        granted = decide(CUSTODIAN, request_id, "approve",
                         "Imani is away and reviews cannot wait")
        check("a custodian may decide somebody else's request",
              granted.status_code == 200, f"HTTP {granted.status_code} {granted.text[:120]}")

        with db() as conn:
            grant = conn.execute(
                "select * from role_grant where principal = %s and role = %s",
                (ASKER, WANTED),
            ).fetchone()
        check("the grant records who approved it", grant is not None
              and grant["approved_by"] == CUSTODIAN,
              grant["approved_by"] if grant else "(no grant)")
        check("and lapses rather than lasting forever",
              grant is not None and grant["expires_at"] is not None,
              str(grant["expires_at"]) if grant else "(no grant)")

        again = decide(CUSTODIAN, request_id, "reject", "changed my mind")
        check("a decided request cannot be decided again",
              again.status_code == 409, f"HTTP {again.status_code}")

    heading("U72: an ended appointment stops somebody acting")

    before = api("GET", "/people/roles", headers=bearer_for(ASKER))
    check("the person can act while appointed", before.status_code == 200,
          f"HTTP {before.status_code}")

    ended = api("POST", f"/people/{ASKER}/appointment",
                json={"ended": True, "reason": "left the organisation"},
                headers=bearer_for(OVERSIGHT))
    check("somebody else may end an appointment", ended.status_code == 200,
          f"HTTP {ended.status_code} {ended.text[:120]}")

    after = api("GET", "/people/roles", headers=bearer_for(ASKER))
    check("and the person can no longer act", after.status_code == 403,
          f"HTTP {after.status_code}")
    why = (after.json().get("detail", {}).get("reasons", [])
           if after.status_code == 403 else [])
    check("the refusal names the ended appointment rather than failing obscurely",
          any("appointment ended" in r for r in why), "; ".join(why)[:140])

    # Nobody is deleted, which is the property the whole design turns on: an
    # approval naming somebody unresolvable is not an audit trail.
    with db() as conn:
        still = conn.execute(
            "select label, ended_at from directory where id = %s", (ASKER,)
        ).fetchone()
    check("their record survives, so what they approved stays attributable",
          still is not None and still["ended_at"] is not None,
          still["label"] if still else "(gone)")

    restored = api("POST", f"/people/{ASKER}/appointment",
                   json={"ended": False, "reason": "came back"},
                   headers=bearer_for(OVERSIGHT))
    check("and ending is reversible, because people come back",
          restored.status_code == 200, f"HTTP {restored.status_code}")

    clean()
    return summary("U72")


if __name__ == "__main__":
    sys.exit(main())
