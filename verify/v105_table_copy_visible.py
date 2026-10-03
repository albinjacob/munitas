"""U105: whether a version is also stored as a table, and why not, is on record and readable.

A table copy is what lets a standard tool read a version's rows and what a legal export needs to hand over only the
rows for named people. A version that did not get one used to say so only in a log. This seals four kinds of version, one
that is a table, one that is files, one whose rows could not be read and one sealed before reasons were written down, and
checks what the platform says about each, that a dataset is marked as a table only when it is one, that a table dataset
with a version that has no table copy is marked as missing one, and that nobody can read this about another organisation's
versions.

    docker compose exec -T munitas-api python /verify/v105_table_copy_visible.py
"""

from __future__ import annotations

import sys

from common import api, bearer_for, check, db, fixture_tabular_contract, fixture_tabular_version, heading, require_api, summary
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, finish_org, make_org, seal_data


def main() -> int:
    require_api()
    org, other = make_org(), make_org()
    try:
        member, outsider = org.bearer("member"), other.bearer("member")
        schema = fixture_tabular_contract(org.id)

        heading("Four kinds of version")
        table = fixture_tabular_version(org.id, dataset_name="a-table", schema_id=schema)
        r = api("GET", f"/dataset-versions/{table['id']}/table", headers=member)
        body = r.json()
        check("a version written as a table says so, with its table, rows and snapshot",
              r.status_code == 200 and body["projected"] and body["outcome"] == "projected" and body["rows"] == 3
              and body["table"].endswith(".v1") and body["snapshot_id"] and body["filterable"], str(body)[:160])

        files = seal_data(org)
        r = api("GET", f"/dataset-versions/{files['version_id']}/table", headers=member)
        body = r.json()
        check("a version of files says it is files and was never meant to be a table",
              body["outcome"] == "not_requested" and not body["projected"] and not body["filterable"]
              and "sealed as files" in body["reason"], str(body)[:160])

        broken = api("POST", "/dataset-versions", json={
            "tenant_id": org.id, "dataset_id": table["dataset_id"], "schema_id": schema, "visibility_class": "RAW",
            "object_manifest": [], "record_count": 1, "records_key": f"{org.id}/no/such/records.json"})
        check("a version whose rows cannot be read is still sealed", broken.status_code == 201, str(broken.status_code))
        body = api("GET", f"/dataset-versions/{broken.json()['id']}/table", headers=member).json()
        check("and says the table was not written and why, in a sentence that quotes no row",
              body["outcome"] == "skipped" and not body["projected"] and "could not be read" in body["reason"]
              and "rec-" not in body["reason"], body["reason"][:140])

        # A real error from the real library: a row whose count is text where the contract says integer. The records
        # object is readable and the contract exists. The error comes out of pyarrow itself (ArrowInvalid), which the
        # platform recognises and reports as a skip with that kind of error. A mock could only imitate that.
        import hashlib
        import json as _json
        from common import ADMIN, bucket_for, s3_client
        bad_rows = [{**row, "count": "not a number"} for row in table["rows"]]
        again = api("GET", f"/datasets/{table['dataset_id']}/next-version", params={"tenant_id": org.id}).json()["storage_prefix"]
        payload = _json.dumps(bad_rows).encode()
        key = f"{again}/records.json"
        s3_client(*ADMIN).put_object(Bucket=bucket_for(org.id), Key=key, Body=payload)
        torn = api("POST", "/dataset-versions", json={
            "tenant_id": org.id, "dataset_id": table["dataset_id"], "schema_id": schema, "visibility_class": "RAW",
            "object_manifest": [{"key": key, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}],
            "record_count": len(bad_rows), "records_key": key})
        check("rows the real library cannot write do not stop the seal", torn.status_code == 201, str(torn.status_code))
        body = api("GET", f"/dataset-versions/{torn.json()['id']}/table", headers=member).json()
        check("the note names the kind of error the library raised, and quotes no value",
              body["outcome"] == "skipped" and not body["projected"] and "ArrowInvalid" in body["reason"]
              and "not a number" not in body["reason"], f"{body['outcome']}: {body['reason'][:150]}")

        with db() as conn:
            conn.execute("delete from iceberg_projection_note where dataset_version_id = %s", (files["version_id"],))
        body = api("GET", f"/dataset-versions/{files['version_id']}/table", headers=member).json()
        check("a version sealed before reasons were written down says it cannot say, and does not guess",
              body["outcome"] == "unrecorded" and not body["filterable"] and "cannot say" in body["reason"], str(body)[:140])

        heading("The dataset list marks what is a table")
        listing = {d["name"]: d for d in api("GET", "/datasets", params={"tenant_id": org.id}, headers=member).json()["datasets"]}
        check("a dataset with a table version is marked as a table", listing["a-table"]["is_table"] is True)
        check("two of its versions have no table copy, so it is marked as missing one",
              listing["a-table"]["table_missing"] is True and listing["a-table"]["tabled_versions"] == 1
              and listing["a-table"]["untabled_versions"] == 2)
        check("a dataset of files is not marked as a table", listing["records"]["is_table"] is False and listing["records"]["table_missing"] is False)
        versions = api("GET", f"/datasets/{table['dataset_id']}/versions", headers=member).json()
        check("each version in the list says whether it has a table copy",
              {v["version"]: v["table_copy"] for v in versions} == {1: True, 2: False, 3: False}, str([(v["version"], v["table_copy"]) for v in versions]))

        heading("Only the organisation's own people can ask")
        check("another organisation's member is told there is no such version",
              api("GET", f"/dataset-versions/{table['id']}/table", headers=outsider).status_code == 404)
        check("and a request with no session is refused", api("GET", f"/dataset-versions/{table['id']}/table").status_code == 401)
    finally:
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        finish_org(org, priya, ravi)
        finish_org(other, priya, ravi)
        drop_org(org)
        drop_org(other)
    return summary("U105")


if __name__ == "__main__":
    sys.exit(main())
