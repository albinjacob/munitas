"""U111: sealing a version is the platform's own workers' act, and nobody else can do it.

POST /dataset-versions names an organisation in its body and writes a sealed version into it. It had no authentication at all,
so anybody who could reach the API could seal a version into any organisation, and, since table jobs, start jobs that hold a
version number and a storage key. It now takes the worker token the platform's workers already send.

This checks each kind of caller, and that a refused request leaves nothing behind:

  * no token, an empty token, a wrong token: refused, and no version and no job made;
  * a signed-in person, whatever their role (a platform administrator included): refused, because a person is not the worker;
  * the worker token: sealed, which is the control that shows the refusals above are about the caller and not the request.

    docker compose exec -T munitas-api python /verify/v111_seal_requires_worker.py
"""

from __future__ import annotations

import sys

from common import WORKER_HEADERS, api, bearer_for, check, db, fixture_tabular_contract, fixture_tabular_version, heading, require_api, summary
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, finish_org, make_org


def main() -> int:
    require_api()
    org = make_org()
    try:
        schema = fixture_tabular_contract(org.id)
        first = fixture_tabular_version(org.id, dataset_name="sealed-by-worker", schema_id=schema)
        dataset_id = first["dataset_id"]

        def body(**extra) -> dict:
            return {"tenant_id": org.id, "dataset_id": dataset_id, "schema_id": schema, "visibility_class": "RAW",
                    "object_manifest": [], "record_count": 0, **extra}

        def counts() -> tuple[int, int]:
            with db() as conn:
                return (conn.execute("select count(*) as n from dataset_version where dataset_id = %s", (dataset_id,)).fetchone()["n"],
                        conn.execute("select count(*) as n from table_job where dataset_id = %s", (dataset_id,)).fetchone()["n"])

        heading("Nobody but a worker can seal")
        before = counts()
        for label, headers in [
            ("no credential at all", {}),
            ("an empty worker token", {"x-worker-token": ""}),
            ("a worker token that is wrong", {"x-worker-token": "not-the-token"}),
            ("a platform administrator's session", bearer_for(ADMIN_A)),
            ("the organisation's own custodian's session", org.bearer("custodian")),
            ("a member's session", org.bearer("member")),
        ]:
            r = api("POST", "/dataset-versions", json=body(), headers=headers)
            check(f"{label} is refused", r.status_code == 403, f"{r.status_code} {str(r.text)[:100]}")
        check("none of those made a version", counts() == before, f"{before} then {counts()}")

        heading("A refused request starts nothing")
        r = api("POST", "/dataset-versions", json=body(records_key=f"{org.id}/x/records.ndjson", table_mode="background"), headers={})
        check("a table job asked for without a credential is refused", r.status_code == 403)
        check("and no job was made, so no version number or storage key is held", counts() == before)

        heading("The worker can")
        r = api("POST", "/dataset-versions", json=body(), headers=WORKER_HEADERS)
        check("the same request with the worker token seals the version", r.status_code == 201 and r.json().get("sealed") is True, f"{r.status_code} {str(r.text)[:100]}")
        check("and exactly one version was made", counts() == (before[0] + 1, before[1]))
        check("the shared helper in these checks sends the worker token for this call and no other",
              api("GET", f"/dataset-versions/{r.json().get('id')}").status_code == 401)
    finally:
        finish_org(org, bearer_for(ADMIN_A), bearer_for(ADMIN_B))
        drop_org(org)
    return summary("U111")


if __name__ == "__main__":
    sys.exit(main())
