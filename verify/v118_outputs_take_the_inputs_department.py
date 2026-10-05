"""U118: a dataset made from a version belongs to the department of the dataset that version is in.

A pipeline step names its dataset and the platform made it with no owning department, so nobody was accountable for it and nobody could
approve access to it. Now the step names the version it read, and the dataset takes that dataset's department. This checks, one item at a
time, on the route the steps use:

  * a dataset made from a version takes that version's dataset's department;
  * what is made from that in turn takes the same one, so a chain of steps keeps its owner;
  * a dataset made with no version is as it was (no department), which is what the corpus ingest and the checks' own fixtures do;
  * a version of another organisation, or one that does not exist, is refused and nothing is made;
  * asking again for a name that exists returns that dataset and changes nothing.

    docker compose exec -T munitas-api python /verify/v118_outputs_take_the_inputs_department.py
"""

from __future__ import annotations

import sys
import uuid

sys.path.insert(0, "/app")

from common import CANARY, api, bearer_for, check, db, fixture_department, fixture_tabular_contract, fixture_tabular_version, fixture_tenant, heading, require_api, summary  # noqa: E402
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, finish_org, make_org  # noqa: E402


def department_of(dataset_id: str) -> str | None:
    with db() as conn:
        row = conn.execute("select department_id::text as d from dataset where id = %s", (dataset_id,)).fetchone()
    return row["d"] if row else None


def count_named(name: str) -> int:
    with db() as conn:
        return conn.execute("select count(*) as n from dataset where tenant_id = %s and name = %s", (CANARY, name)).fetchone()["n"]


def main() -> int:
    require_api()
    tenant = fixture_tenant(CANARY)
    other = make_org()
    try:
        source = fixture_tabular_version(tenant)
        fixture_department(tenant, source["dataset_id"])
        owner = department_of(source["dataset_id"])
        check("the source dataset belongs to a department", owner is not None, str(owner))

        heading("A step's dataset takes the department of what it read")
        name = f"u118-out-{uuid.uuid4().hex[:8]}"
        made = api("POST", "/datasets", json={"tenant_id": tenant, "name": name, "derived_from_version_id": source["id"]})
        check("the dataset is made", made.status_code == 201 and made.json()["created"] is True, f"{made.status_code} {made.text[:120]}")
        out_id = made.json()["id"]
        check("and belongs to the department of the dataset the version is in", department_of(out_id) == owner, f"{department_of(out_id)} vs {owner}")

        heading("The next step in a chain keeps the owner")
        out_version = fixture_tabular_version(tenant, dataset_id=out_id, dataset_name=name)
        name2 = f"u118-next-{uuid.uuid4().hex[:8]}"
        next_made = api("POST", "/datasets", json={"tenant_id": tenant, "name": name2, "derived_from_version_id": out_version["id"]})
        check("a dataset made from that output belongs to the same department", next_made.status_code == 201 and department_of(next_made.json()["id"]) == owner,
              f"{next_made.status_code} {department_of(next_made.json()['id']) if next_made.status_code == 201 else ''}")

        heading("Without a version it is as it was")
        bare_name = f"u118-bare-{uuid.uuid4().hex[:8]}"
        bare = api("POST", "/datasets", json={"tenant_id": tenant, "name": bare_name})
        check("a dataset made with no version has no department", bare.status_code == 201 and department_of(bare.json()["id"]) is None)

        heading("A version that is not this organisation's is refused")
        theirs = fixture_tabular_version(other.id, dataset_name="theirs", schema_id=fixture_tabular_contract(other.id))
        foreign_name = f"u118-foreign-{uuid.uuid4().hex[:8]}"
        foreign = api("POST", "/datasets", json={"tenant_id": tenant, "name": foreign_name, "derived_from_version_id": theirs["id"]})
        check("another organisation's version is refused", foreign.status_code == 422, f"{foreign.status_code} {foreign.text[:120]}")
        check("and no dataset was made", count_named(foreign_name) == 0)
        missing_name = f"u118-missing-{uuid.uuid4().hex[:8]}"
        missing = api("POST", "/datasets", json={"tenant_id": tenant, "name": missing_name, "derived_from_version_id": str(uuid.uuid4())})
        check("a version that does not exist is refused, and nothing is made", missing.status_code == 422 and count_named(missing_name) == 0, f"{missing.status_code}")

        heading("Asking again changes nothing")
        again = api("POST", "/datasets", json={"tenant_id": tenant, "name": name, "derived_from_version_id": source["id"]})
        check("the same name returns the same dataset, not a second", again.status_code == 201 and again.json() == {"id": out_id, "created": False}, again.text[:100])
        check("and its department is unchanged", department_of(out_id) == owner)
    finally:
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        finish_org(other, priya, ravi)
        drop_org(other)
    return summary("U118")


if __name__ == "__main__":
    sys.exit(main())
