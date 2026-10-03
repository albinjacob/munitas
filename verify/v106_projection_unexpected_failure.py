"""U106: when writing a table fails in a way nobody anticipated, the version is still sealed and the note says only what kind of error.

U105 feeds the real library data it rejects, which the platform recognises and reports as a skip. What it cannot reach is
the last branch: an error of a kind the platform does not recognise, such as a storage fault in the middle of a write or an
exception nobody foresaw. That branch has one job. The seal must not stop, and the note written beside the version may name
the kind of error and nothing else, because a library's message can quote a row.

This reaches it by replacing the table writer, for the length of one seal, with a stand-in that raises an error whose message
contains a value from a row. Everything else is the platform's own code, run for real: the sealing, the register, the note.
What it cannot show is what the real library would raise in the field, only what the platform does with an error it did not
expect. U105 holds the other half.

    docker compose exec -T munitas-api python /verify/v106_projection_unexpected_failure.py
"""

from __future__ import annotations

import sys
from unittest import mock

sys.path.insert(0, "/app")

from common import api, bearer_for, check, db, fixture_tabular_contract, fixture_tabular_version, heading, require_api, summary  # noqa: E402
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, finish_org, make_org  # noqa: E402

SECRET = "rec-0-the-value-a-library-message-might-quote"


def main() -> int:
    require_api()
    from app import db as app_db
    from app import iceberg, versions

    if app_db.pool.closed:
        app_db.pool.open()
    org = make_org()
    try:
        member = org.bearer("member")
        schema = fixture_tabular_contract(org.id)
        first = fixture_tabular_version(org.id, dataset_name="a-table", schema_id=schema)

        heading("An error nobody anticipated")
        where = api("GET", f"/datasets/{first['dataset_id']}/next-version", params={"tenant_id": org.id}).json()
        boom = RuntimeError(f"storage said no while writing {SECRET}")
        with mock.patch.object(iceberg, "project", side_effect=boom):
            sealed = versions.seal(
                tenant_id=org.id, dataset_id=first["dataset_id"], schema_id=schema, visibility_class="RAW",
                storage_backend="seaweedfs", object_manifest=[], record_count=1, records_key=f"{where['storage_prefix']}/records.json")
        check("the version is sealed all the same", bool(sealed.get("id")), str(sealed)[:80])

        body = api("GET", f"/dataset-versions/{sealed['id']}/table", headers=member).json()
        check("the note says the writing failed, which is the branch for errors it did not recognise",
              body["outcome"] == "failed" and not body["projected"], f"{body['outcome']}: {body['reason'][:120]}")
        check("it names the kind of error, which is RuntimeError here", "RuntimeError" in body["reason"], body["reason"][:120])
        check("and it quotes nothing from the message, not even a value that was in it",
              SECRET not in body["reason"] and "storage said" not in body["reason"], body["reason"][:120])
        with db() as conn:
            notes = conn.execute("select count(*) as n from iceberg_projection_note where dataset_version_id = %s", (sealed["id"],)).fetchone()["n"]
            refs = conn.execute("select count(*) as n from iceberg_table_ref where dataset_version_id = %s", (sealed["id"],)).fetchone()["n"]
        check("one note was written, and no pointer to a table that is not there", notes == 1 and refs == 0, f"{notes} note, {refs} pointer")

        heading("The log says the same, and no more")
        with mock.patch.object(iceberg, "project", side_effect=boom), mock.patch.object(iceberg.log, "error") as logged:
            again = versions.seal(
                tenant_id=org.id, dataset_id=first["dataset_id"], schema_id=schema, visibility_class="RAW",
                storage_backend="seaweedfs", object_manifest=[], record_count=1, records_key=f"{where['storage_prefix']}/records.json")
        check("a second version sealed the same way is sealed too", bool(again.get("id")) and again["id"] != sealed["id"])
        text = str(logged.call_args)
        check("the log entry names the error kind and the identifiers, and quotes no value",
              "RuntimeError" in text and SECRET not in text, text[:160])
    finally:
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        finish_org(org, priya, ravi)
        drop_org(org)
    return summary("U106")


if __name__ == "__main__":
    sys.exit(main())
