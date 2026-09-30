"""U70: disposability is declared before an organisation holds anything.

A sealed version cannot be changed or deleted. That is the platform's central
claim and `verify/v1_immutability.py` proves it. This proves the one exemption
to it, and proves the exemption cannot be reached by anything that already
holds data.

The exemption exists because the verification suite mints a throwaway tenant
per run for the checks that can only be proved once about an organisation, and
something has to remove them or the storage engine runs out of volumes.
Removing one means deleting its sealed versions, which used to mean turning
the immutability rules off for the duration. A guarantee switched off by
whatever tidies up is not a guarantee, so disposability moved to a declaration
made at creation: purpose `scratch`, said while the tenant is empty and the
claim is honest.

The claim being tested is therefore two-sided, and the second side is the one
that matters:

    a scratch tenant's sealed versions can be deleted, and
    nothing that already holds sealed versions can become scratch.

Without the second, the first is an open door: relabel a customer, delete
their history. `canary` is checked here too, because exempting canary rather
than adding a purpose would have left v1_immutability's own fixture exempt and
the platform's central claim untested by the only kind of tenant the suite can
create.

    docker compose exec -T munitas-api python /verify/v70_declared_disposable.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import (CANARY, check, db, fixture_contract,  # noqa: E402
                    fixture_tenant, fixture_version, heading, require_api,
                    summary)

SCRATCH = f"scratch-probe-{uuid.uuid4().hex[:8]}"
CANARY_PROBE = CANARY


def declare(tenant_id: str, purpose: str) -> None:
    with db() as conn:
        conn.execute(
            "insert into tenant (id, isolation_level, key_ref, purpose) "
            "values (%s, 'shared', %s, %s) on conflict (id) do nothing",
            (tenant_id, f"key/{tenant_id}", purpose),
        )


def sealed_version(tenant_id: str) -> dict:
    return fixture_version(tenant_id, fixture_contract(tenant_id), "RAW")


def delete_version(version_id: str) -> int:
    with db() as conn:
        return conn.execute(
            "delete from dataset_version where id = %s", (version_id,)
        ).rowcount


def main() -> int:
    require_api()

    heading("U70: a tenant declared disposable can be deleted outright")

    declare(SCRATCH, "scratch")
    scratch_version = sealed_version(SCRATCH)
    check("the scratch tenant holds a sealed version",
          scratch_version.get("sealed") is not False, scratch_version["id"])

    removed = delete_version(scratch_version["id"])
    check("deleting that sealed version affects one row", removed == 1,
          f"rowcount={removed}")

    with db() as conn:
        left = conn.execute(
            "select count(*) as n from dataset_version where id = %s",
            (scratch_version["id"],),
        ).fetchone()["n"]
    check("and it is actually gone", left == 0, f"{left} row(s) remain")

    heading("U70: canary keeps full immutability, so v1 still proves the rule")

    # The reason this is a separate purpose rather than an exemption for
    # canary. v1_immutability builds its fixture with fixture_tenant(), which
    # creates a canary tenant; had canary been exempted, that check would have
    # started passing for the wrong reason or been quietly changed.
    #
    # The shared canary tenant rather than a fresh one, because what this
    # seals cannot be deleted afterwards: that is the property being proved.
    # A new tenant per run would leave one behind every run, permanently,
    # which is the accumulation this whole area exists to stop.
    fixture_tenant(CANARY_PROBE)
    canary_version = sealed_version(CANARY_PROBE)
    removed = delete_version(canary_version["id"])
    check("deleting a canary tenant's sealed version affects zero rows",
          removed == 0, f"rowcount={removed}")

    with db() as conn:
        still = conn.execute(
            "select count(*) as n from dataset_version where id = %s",
            (canary_version["id"],),
        ).fetchone()["n"]
    check("and the version is still there", still == 1, f"{still} row(s)")

    heading("U70: disposability cannot be acquired after the fact")

    # The open door this closes: relabel an organisation that already holds
    # sealed history, and the exemption above would make that history
    # deletable. Refused at the database, not by whoever is asking.
    refused = False
    message = ""
    try:
        with db() as conn:
            conn.execute(
                "update tenant set purpose = 'scratch' where id = %s",
                (CANARY_PROBE,),
            )
    except Exception as exc:  # noqa: BLE001 - the refusal is the result
        refused = True
        message = str(exc).splitlines()[0]

    check("a tenant holding sealed versions cannot be declared disposable",
          refused, message or "the update was allowed")
    check("and the refusal says disposability is declared beforehand",
          "declared before" in message.lower()
          or "disposab" in message.lower(), message)

    with db() as conn:
        purpose = conn.execute(
            "select purpose from tenant where id = %s", (CANARY_PROBE,)
        ).fetchone()["purpose"]
    check("so the tenant keeps the purpose it had", purpose == "canary", purpose)

    heading("U70: an empty tenant may still be declared disposable")

    empty = f"scratch-empty-{uuid.uuid4().hex[:8]}"
    fixture_tenant(empty)
    allowed = True
    # Cleared, not carried: `message` still holds the refusal from the block
    # above, and reusing it would print that refusal beside a passing check,
    # which reads as the opposite of the result it is reporting.
    message = ""
    try:
        with db() as conn:
            conn.execute(
                "update tenant set purpose = 'scratch' where id = %s", (empty,)
            )
    except Exception as exc:  # noqa: BLE001
        allowed = False
        message = str(exc).splitlines()[0]
    check("a tenant with nothing sealed can be relabelled while it is empty",
          allowed, message or "declared while empty")

    # Cleans up after itself, which this check of all checks has no excuse
    # not to do: the tenants it made are disposable by declaration, so
    # removing them needs nothing switched off. Leaving them would add two
    # organisations per run to the very pile this area exists to prevent.
    with db() as conn:
        for tenant_id in (SCRATCH, empty):
            for table in ("dataset_version", "dataset", "schema_contract",
                          "tenant_storage_provision"):
                conn.execute(
                    f"delete from {table} where tenant_id = %s", (tenant_id,)
                )
            conn.execute("delete from tenant where id = %s", (tenant_id,))

    with db() as conn:
        left = conn.execute(
            "select count(*) as n from tenant where id = any(%s)",
            ([SCRATCH, empty],),
        ).fetchone()["n"]
    check("and removes the disposable tenants it created", left == 0,
          f"{left} left behind")

    return summary("U70")


if __name__ == "__main__":
    sys.exit(main())
