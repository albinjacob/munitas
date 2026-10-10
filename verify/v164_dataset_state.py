"""U172: a dataset is registered, active or deleted, worked out from its versions, and deleted ones stay out of the list unless asked for.

The datasets list used to offer every dataset whose data had been deleted beside the ones that could still be read, and over time they would pile up.
Every dataset now has a state:

  * registered: it has no version yet;
  * active:     at least one of its versions still has data that can be read (files or a table);
  * deleted:    it has versions and the data of every one of them was deleted; its record, its versions and its history are kept.

Checked here one item at a time, against the live platform, with four datasets made up for the run in the second test organisation (the datasets are named
with a tag of their own, so earlier runs cannot be mistaken for this one):

  * a dataset with no version is registered, one with a version is active, one whose only version was deleted is deleted;
  * a dataset with two versions, one of them deleted, is still active;
  * the default list leaves the deleted one out, and says how many deleted datasets match;
  * asking to include deleted datasets, or asking for the deleted state, lists it with its state;
  * each state filter returns exactly its own datasets;
  * an unknown state is refused;
  * the deleted dataset's versions are still listed, and the deleted one says when its data was deleted;
  * another organisation sees none of them.

    docker compose exec -T munitas-api python /verify/v164_dataset_state.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    CUSTODIAN, NEIGHBOUR, NEIGHBOUR_CUSTODIAN, api, bearer_for, check, db, fixture_contract, fixture_tenant, fixture_version, heading, require_api, summary,
)


def delete_data(version_id: str, tenant: str) -> None:
    """Record that a version's data was deleted, the same record the storage cleanup and the end of a development sample write."""
    with db() as conn:
        conn.execute(
            """insert into storage_reclamation (dataset_version_id, tenant_id, bytes_freed, object_count, reclaimed_by, reason)
               values (%s, %s, 0, 0, 'platform', 'made up for a verification run') on conflict do nothing""",
            (version_id, tenant),
        )


def add_version(dataset_id: str, tenant: str, schema_id: str) -> dict:
    r = api("POST", "/dataset-versions", json={
        "tenant_id": tenant, "dataset_id": dataset_id, "schema_id": schema_id, "visibility_class": "RAW",
        "object_manifest": [{"key": "part-1.json", "bytes": 64}], "record_count": 1,
    })
    r.raise_for_status()
    return r.json()


def listing(asker: str, tag: str, **params) -> dict:
    r = api("GET", "/datasets", headers=bearer_for(asker), params={"q": tag, "limit": 100, **params})
    assert r.status_code == 200, f"{asker}: HTTP {r.status_code} {r.text[:160]}"
    return r.json()


def main() -> int:
    require_api()
    tenant = fixture_tenant(NEIGHBOUR)
    schema = fixture_contract(tenant)
    tag = f"state-{uuid.uuid4().hex[:8]}"

    # A: registered. B: active. C: deleted. D: two versions, the first deleted.
    r = api("POST", "/datasets", json={"tenant_id": tenant, "name": f"{tag}-a-registered"})
    r.raise_for_status()
    a = r.json()["id"]
    b = fixture_version(tenant, schema, dataset_name=f"{tag}-b-active")
    c = fixture_version(tenant, schema, dataset_name=f"{tag}-c-deleted")
    d = fixture_version(tenant, schema, dataset_name=f"{tag}-d-mixed")
    d2 = add_version(d["dataset_id"], tenant, schema)
    delete_data(c["id"], tenant)
    delete_data(d["id"], tenant)
    by_name = {f"{tag}-a-registered": a, f"{tag}-b-active": b["dataset_id"], f"{tag}-c-deleted": c["dataset_id"], f"{tag}-d-mixed": d["dataset_id"]}

    def names(page: dict) -> dict[str, str]:
        return {row["name"]: row["state"] for row in page["datasets"]}

    heading("U172: the state follows from the versions")
    everything = names(listing(NEIGHBOUR_CUSTODIAN, tag, include_deleted="true"))
    check("a dataset with no version is registered", everything.get(f"{tag}-a-registered") == "registered", str(everything.get(f"{tag}-a-registered")))
    check("a dataset with a readable version is active", everything.get(f"{tag}-b-active") == "active", str(everything.get(f"{tag}-b-active")))
    check("a dataset whose only version was deleted is deleted", everything.get(f"{tag}-c-deleted") == "deleted", str(everything.get(f"{tag}-c-deleted")))
    check("a dataset with one deleted version and one readable one is active", everything.get(f"{tag}-d-mixed") == "active", str(everything.get(f"{tag}-d-mixed")))
    check("the second version of that dataset was not touched", d2["id"] != d["id"], "two versions")

    heading("U172: deleted datasets stay out of the list unless asked for")
    default = listing(NEIGHBOUR_CUSTODIAN, tag)
    check("the default list leaves the deleted dataset out", f"{tag}-c-deleted" not in names(default), ", ".join(sorted(names(default))))
    check("and keeps the other three", len(names(default)) == 3, f"{len(names(default))} datasets")
    check("it says how many deleted datasets match", default["deleted"] == 1, str(default["deleted"]))
    check("and its total is the three that are listed", default["total"] == 3, str(default["total"]))
    included = listing(NEIGHBOUR_CUSTODIAN, tag, include_deleted="true")
    check("asking to include deleted datasets lists all four", included["total"] == 4 and f"{tag}-c-deleted" in names(included), str(included["total"]))
    check("and still says how many are deleted", included["deleted"] == 1, str(included["deleted"]))

    heading("U172: each state filter returns exactly its own datasets")
    for state, expected in (("registered", {f"{tag}-a-registered"}), ("active", {f"{tag}-b-active", f"{tag}-d-mixed"}), ("deleted", {f"{tag}-c-deleted"})):
        got = set(names(listing(NEIGHBOUR_CUSTODIAN, tag, state=state)))
        check(f"state {state} returns {len(expected)} dataset(s) and no other", got == expected, ", ".join(sorted(got)) or "none")
    refused = api("GET", "/datasets", headers=bearer_for(NEIGHBOUR_CUSTODIAN), params={"state": "archived"})
    check("a state that does not exist is refused", refused.status_code == 422, f"HTTP {refused.status_code}")

    heading("U172: a deleted dataset's record is kept")
    versions = api("GET", f"/datasets/{by_name[f'{tag}-c-deleted']}/versions", headers=bearer_for(NEIGHBOUR_CUSTODIAN))
    rows = versions.json() if versions.status_code == 200 else []
    check("its version is still listed", len(rows) == 1, f"HTTP {versions.status_code}, {len(rows)} versions")
    check("and says when its data was deleted", bool(rows) and rows[0].get("reclaimed_at") is not None, str(rows[0].get("reclaimed_at")) if rows else "no row")
    mixed = api("GET", f"/datasets/{by_name[f'{tag}-d-mixed']}/versions", headers=bearer_for(NEIGHBOUR_CUSTODIAN)).json()
    check("the mixed dataset shows one deleted version and one readable one",
          sorted(v["reclaimed_at"] is None for v in mixed) == [False, True], f"{len(mixed)} versions")

    heading("U172: another organisation sees none of them")
    elsewhere = listing(CUSTODIAN, tag, include_deleted="true")
    check("the first test organisation's custodian finds none of the four", elsewhere["total"] == 0 and elsewhere["deleted"] == 0, f"{elsewhere['total']} found")

    return summary("U172")


if __name__ == "__main__":
    sys.exit(main())
