"""U178: the limits on how many uploads one person may start, and how many of their tables may be refused, count what they say they count.

These limits last an hour, so trying them with a real person would stop that person bringing in anything for an hour. This runs inside the API's
container, with the limits lowered for the length of the check, and a person who exists only here: their uploads are rows made for the purpose and
removed at the end.

  * with the limit on uploads open at once at three, three open uploads are allowed and a fourth is turned away, with the reason and a time to wait;
  * with the limit on uploads started in an hour at four, the fifth is turned away, whether the earlier ones were cancelled or not;
  * with the limit on refused tables at two, the third is turned away, a cancelled upload is not counted as refused, and one that finished more than an
    hour ago is not counted;
  * another person's uploads are never counted against this one;
  * an organisation's own limits: the largest file, the bytes kept while they wait and the bytes brought in a day are each refused in words, and a
    table waiting past the number allowed to wait turns away the next one.

    docker compose exec -T munitas-api python /verify/v170_table_upload_limits.py
"""

from __future__ import annotations

import json
import sys
import uuid
from unittest import mock

sys.path.insert(0, "/app")

from common import CANARY, ENGINEER, api, bearer_for, check, db, heading, require_api, summary  # noqa: E402


def main() -> int:
    require_api()
    from app import config, table_uploads
    from app import db as app_db
    from app.person_errors import Refused

    if app_db.pool.closed:
        app_db.pool.open()
    headers = bearer_for(ENGINEER)
    department = api("GET", "/organisation", headers=headers).json()["departments"][0]["id"]
    tag = uuid.uuid4().hex[:8]
    person, other = f"limit-probe-{tag}", f"limit-probe-other-{tag}"
    dataset = api("POST", "/datasets/register", headers=headers, json={"tenant_id": CANARY, "name": f"limits-{tag}", "department_id": department,
                                                                    "registered_by": ENGINEER, "provenance": "internal_regulated"}).json()["id"]
    made: list[str] = []
    counter = [900]

    def row(who: str, *, status: str = "pending", ready: bool = False, upload: bool = True, size: int = 1000, outcome: str | None = None, finished: str | None = None,
            created: str = "now()") -> str:
        counter[0] += 1
        job = str(uuid.uuid4())
        with db() as conn:
            conn.execute(
                f"""insert into table_job (id, tenant_id, dataset_id, version, storage_prefix, storage_backend, request, queue, status, expires_at, version_id,
                                          ready, created_by, upload, outcome, finished_at, created_at)
                    values (%s, %s, %s, %s, %s, 'seaweedfs', %s, 'munitas-table-write', %s, now() + interval '1 day', %s, %s, %s, %s, %s,
                            {finished or 'null'}, {created})""",
                (job, CANARY, dataset, counter[0], f"{CANARY}/{dataset}/v{counter[0]}", json.dumps({"records_key": "x"}), status, str(uuid.uuid4()), ready, who,
                 json.dumps({"file_name": "x.csv", "size_bytes": size, "storage_upload": "x"}) if upload else None, outcome))
        made.append(job)
        return job

    def person_blocked(who: str) -> str | None:
        try:
            table_uploads._check_person({"id": who})
            return None
        except table_uploads.TooBusy as busy:
            return f"{' '.join(busy.reasons)} (wait {busy.retry_after}s)"

    try:
        heading("Uploads open at once")
        with mock.patch.object(config, "TABLE_UPLOAD_OPEN_PER_PERSON", 3):
            for _ in range(3):
                row(person)
            check("three open uploads are allowed", True)
            said = person_blocked(person)
            check("a fourth is turned away, with the reason and a time to wait", said is not None and "3 uploads open" in said and "wait 30s" in said, str(said))
            check("another person is not held up by it", person_blocked(other) is None)

        heading("Uploads started in an hour")
        with mock.patch.object(config, "TABLE_UPLOAD_OPEN_PER_PERSON", 99), mock.patch.object(config, "TABLE_UPLOAD_PER_HOUR", 4):
            said = person_blocked(person)
            check("three started is under a limit of four", said is None, str(said))
            row(person, status="refused", outcome="cancelled", finished="now()")
            said = person_blocked(person)
            check("the fifth is turned away, and the cancelled one still counts as started", said is not None and "4 uploads in the last hour" in said, str(said))
            row(other, created="now() - interval '2 hours'")
            check("an upload started more than an hour ago does not count", person_blocked(other) is None)

        heading("Tables refused or failed in an hour")
        with mock.patch.object(config, "TABLE_UPLOAD_OPEN_PER_PERSON", 99), mock.patch.object(config, "TABLE_UPLOAD_PER_HOUR", 99), mock.patch.object(config, "TABLE_UPLOAD_REFUSED_PER_HOUR", 2):
            who = f"limit-probe-refused-{tag}"
            row(who, status="refused", outcome="cancelled", finished="now()")
            check("a cancelled upload is not counted as refused", person_blocked(who) is None)
            row(who, status="refused", outcome="refused", finished="now()")
            row(who, status="expired", outcome="expired", finished="now() - interval '2 hours'")
            check("one refused in the last hour and one long ago is under a limit of two", person_blocked(who) is None)
            row(who, status="expired", outcome="expired", finished="now()")
            said = person_blocked(who)
            check("the third turns the next away, with the reason", said is not None and "2 of your tables were refused or failed" in said, str(said))

        heading("The organisation's own limits")
        check("the most is the platform's 16 GB until the organisation lowers it", table_uploads.limits(CANARY)["largest_file"] == 16 * 1024 ** 3)
        try:
            table_uploads._check_organisation(CANARY, 17 * 1024 ** 3, opening=True)
            said = None
        except Refused as exc:
            said = " ".join(exc.reasons)
        check("a file past the largest is refused in words", said is not None and "16 GB" in said, str(said))
        with mock.patch.object(table_uploads, "limits", return_value={"largest_file": 10 ** 12, "jobs_at_once": 2, "jobs_waiting": 2, "staged": 5000, "per_day": 10 ** 12}):
            with db() as conn:
                conn.execute("delete from table_job where id = any(%s::uuid[])", ([j for j in made if j],))
            made.clear()
            row(person, size=4000)
            try:
                table_uploads._check_organisation(CANARY, 2000, opening=True)
                said = None
            except table_uploads.TooBusy as busy:
                said = " ".join(busy.reasons)
            check("bytes kept while they wait past the organisation's limit turn the next away, with the reason", said is not None and "waiting to be written" in said, str(said))
            row(person, status="pending", ready=True, size=1)
            row(person, status="pending", ready=True, size=1)
            try:
                table_uploads._check_organisation(CANARY, 10, opening=False)
                said = None
            except table_uploads.TooBusy as busy:
                said = " ".join(busy.reasons)
            check("tables waiting past the number allowed turn the next away, with the reason", said is not None and "already waiting" in said, str(said))
        with mock.patch.object(table_uploads, "limits", return_value={"largest_file": 10 ** 12, "jobs_at_once": 2, "jobs_waiting": 99, "staged": 10 ** 12, "per_day": 5000}):
            try:
                table_uploads._check_organisation(CANARY, 2000, opening=True)
                said = None
            except table_uploads.TooBusy as busy:
                said = " ".join(busy.reasons)
            check("bytes brought in over a day past the limit turn the next away, with the reason", said is not None and "in the last day" in said, str(said))
    finally:
        with db() as conn:
            conn.execute("delete from table_job where id = any(%s::uuid[])", (made,))
    return summary("U178")


if __name__ == "__main__":
    sys.exit(main())
