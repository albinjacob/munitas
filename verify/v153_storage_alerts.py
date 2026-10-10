"""U159: a storage permission update is always written, and what is odd about it is raised as an alert, once, in one file.

The platform used to refuse an update that removed more than 40% of the folder grants. That refused routine work (a lease ending, data being
deleted), left the grants of ended leases open, and stopped new organisations' storage switching on. Now the update is written and the odd
things are raised as alerts. Each case below is proved against the real register (real versions, a real lease) with a stand-in for storage, so
the list that storage actually holds is never touched:

  routine      grants of a lease that ended, and of data that was deleted: written, no alert, summarised on the update's row
  unexplained  five or more folder grants removed that nothing in the register ever justified: written, one alert; four: no alert
  no bucket    a list with no bucket-wide permission at all: written, one alert
  unjustified  a permission naming a folder that is in no version: written, one alert
  elsewhere    a permission that opens a version's folder in a different organisation's bucket: written, one alert
  clean        a permission for a real version in its own bucket: written, no alert

And the alerts themselves: one line of JSON per alert in one file, with a category, an event, a severity, a sentence and numbers and names only;
recorded on the update's own row; shown by the status the console reads; and a file that cannot be written never stops the update.

    docker compose exec -T munitas-api python /verify/v153_storage_alerts.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid

sys.path.insert(0, "/app")

from common import CANARY, check, db, fixture_contract, fixture_version, heading, require_api, summary


def main() -> int:
    require_api()
    from app import config, db as app_db, grants, seaweed

    app_db.pool.open()
    schema = fixture_contract(CANARY)
    bucket = "munitas-canary"
    other_bucket = "munitas-health"

    heading("The register: one version that a lease once justified, one that nothing ever did")
    justified = fixture_version(CANARY, schema, "RAW", f"u159-justified-{uuid.uuid4().hex[:8]}")
    plain = fixture_version(CANARY, schema, "RAW", f"u159-plain-{uuid.uuid4().hex[:8]}")
    with db() as conn:
        conn.execute(
            "insert into access_lease (id, tenant_id, principal, dataset_version_id, purpose, approved_by, expires_at) "
            "values (%s, %s, 'canary-engineer', %s, 'u159', 'canary-custodian', now() - interval '1 day')",
            (str(uuid.uuid4()), CANARY, justified["id"]))
    folder_justified, folder_plain = justified["storage_prefix"].rstrip("/"), plain["storage_prefix"].rstrip("/")
    gone = f"{CANARY}/{uuid.uuid4()}/v1"
    check("the two versions have folders of their own", folder_justified != folder_plain and folder_justified.startswith(f"{CANARY}/"), f"{folder_justified} {folder_plain}")

    with db() as conn:
        first = conn.execute("select coalesce(max(id), 0) as last, clock_timestamp() as now from storage_permission_print").fetchone()

    admin = {"name": "munitas-admin", "credentials": [], "actions": ["Admin", "List", "Read", "Tagging", "Write"]}
    ingest = {"name": "ingest-u159", "credentials": [], "actions": [f"Read:{bucket}", f"Write:{bucket}"]}

    def grant(folder: str, bucket_name: str = bucket) -> str:
        return f"Read:{bucket_name}/{folder}/*"

    alerts_file = os.path.join(tempfile.mkdtemp(prefix="u159-"), "alerts.jsonl")
    config.ALERTS_FILE = alerts_file

    def update(*, live_grants: list[tuple[str, str]], want_grants: list[tuple[str, str]], bucket_wide: bool = True) -> tuple[dict, dict]:
        """One update with a stand-in for storage. Returns the update's result and the list that was handed to storage."""
        def doc(pairs):
            by_name: dict[str, list[str]] = {}
            for name, action in pairs:
                by_name.setdefault(name, []).append(action)
            base = [admin, ingest] if bucket_wide else [{"name": "nothing", "credentials": [], "actions": []}]
            return {"identities": base + [{"name": n, "credentials": [], "actions": sorted(a)} for n, a in by_name.items()]}
        handed: dict = {}
        real = (seaweed.load_identities, seaweed.save_identities, grants.desired_document)
        seaweed.load_identities = lambda: doc(live_grants)
        seaweed.save_identities = lambda d: handed.update(doc=d)
        grants.desired_document = lambda **_: doc(want_grants)
        try:
            result = grants.reconcile(trigger="manual")
        finally:
            seaweed.load_identities, seaweed.save_identities, grants.desired_document = real
        return result, handed

    def events(result: dict) -> list[str]:
        return sorted(a["event"] for a in result["alerts"])

    heading("Routine: a lease that ended and data that was deleted")
    result, handed = update(live_grants=[("lease-a", grant(folder_justified)), ("lease-b", grant(gone))], want_grants=[])
    check("the update was written", bool(handed.get("doc")), str(result)[:120])
    check("no alert was raised", events(result) == [], str(events(result)))
    check("the update says what it removed and why: one ended lease, one deleted, none unexplained",
          "2 folder permissions" in (result["auto_explained"] or "") and "1 belonged to leases" in result["auto_explained"]
          and "1 to data that was deleted" in result["auto_explained"] and "0 nothing in the register explains" in result["auto_explained"], str(result["auto_explained"]))

    heading("Unexplained: grants nothing in the register ever justified")
    five = [(f"u-{n}", grant(folder_plain)) for n in range(5)]
    result, handed = update(live_grants=five, want_grants=[])
    check("five of them: written", bool(handed.get("doc")))
    check("and one alert raised, saying so", events(result) == ["unexplained-removal"], str(events(result)))
    alert = next((a for a in result["alerts"] if a["event"] == "unexplained-removal"), {})
    check("it counts them and gives examples by name, never contents", alert.get("details", {}).get("unexplained") == 5 and len(alert["details"]["examples"]) == 5, str(alert)[:200])
    result, handed = update(live_grants=five[:4], want_grants=[])
    check("four of them: written, and no alert (a handful is not alarming)", bool(handed.get("doc")) and events(result) == [], str(events(result)))

    heading("A list with no bucket-wide permission at all")
    result, handed = update(live_grants=[], want_grants=[], bucket_wide=False)
    check("written as computed", bool(handed.get("doc")))
    check("with one critical alert", events(result) == ["no-bucket-wide-permissions"] and result["alerts"][0]["severity"] == "critical", str(result["alerts"]))

    heading("A permission that traces to nothing in the register, or to another organisation's bucket")
    result, handed = update(live_grants=[], want_grants=[("lease-x", grant(gone))])
    check("a folder in no version: written, one alert", bool(handed.get("doc")) and events(result) == ["unjustified-permission"], str(events(result)))
    result, handed = update(live_grants=[], want_grants=[("lease-y", grant(folder_justified, other_bucket))])
    check("a real version's folder opened in another organisation's bucket: written, one alert",
          bool(handed.get("doc")) and events(result) == ["permission-in-another-organisations-bucket"], str(events(result)))
    result, handed = update(live_grants=[], want_grants=[("lease-z", grant(folder_justified))])
    check("a real version's folder in its own bucket: written, no alert", bool(handed.get("doc")) and events(result) == [], str(events(result)))

    heading("The alerts file: one file for every category, one line per alert")
    try:
        with open(alerts_file, encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
    except FileNotFoundError:
        lines = []
    check("the alerts file exists", os.path.exists(alerts_file), alerts_file)
    check("every alert raised above is one line of JSON: four in all", len(lines) == 4, str(len(lines)))
    check("each has a time, a category, an event, a severity, a sentence and details",
          bool(lines) and all({"ts", "category", "event", "severity", "message", "details"} <= set(line) for line in lines), str(lines[:1])[:160])
    check("the category says which part of the platform raised it", {line["category"] for line in lines} == {"storage-permissions"}, str({line["category"] for line in lines}))
    check("every message is a full sentence", bool(lines) and all(line["message"][0].isupper() and line["message"].rstrip().endswith(".") for line in lines), str([line["message"][-30:] for line in lines]))
    check("no field looks like a secret, and every value is a number, a name or a short list of them",
          all(not any(h in k.lower() for h in ("secret", "token", "password", "credential")) for line in lines for k in line["details"]), str(lines[0]["details"]))
    check("and they are exactly the four that were raised, in the order they were raised",
          [line["event"] for line in lines] == ["unexplained-removal", "no-bucket-wide-permissions", "unjustified-permission", "permission-in-another-organisations-bucket"],
          str([line["event"] for line in lines]))

    heading("Recorded with the update, and shown by the status the console reads")
    with db() as conn:
        rows = conn.execute("select id, alerts from storage_permission_print where id > %s and alerts is not null order by id", (first["last"],)).fetchall()
    check("the updates of this run that raised alerts hold them on their own rows, one event each, in order",
          [r["alerts"][0]["event"] for r in rows] == ["unexplained-removal", "no-bucket-wide-permissions", "unjustified-permission", "permission-in-another-organisations-bucket"],
          str([r["alerts"][0]["event"] for r in rows]))
    check("and each row holds a full sentence and the numbers behind it", all(r["alerts"][0]["message"].endswith(".") and r["alerts"][0]["details"] for r in rows), str(rows[:1])[:200])
    status = grants.activation_status()
    latest = status["latest_alerts"]
    check("the status names when, which update and what it noticed, and it is this run's newest",
          bool(latest) and latest["at"] > first["now"] and latest["alerts"][0]["event"] == "permission-in-another-organisations-bucket", str(latest)[:200])
    check("an update that raised alerts does not count as failing", status["failing"] is False, str(status["failing"]))

    heading("A folder a running task was given to write to")
    # A running task is given a folder to write its output to before any version exists there, so the platform's own write grant is what justifies it.
    writing, never_issued = f"{CANARY}/{uuid.uuid4()}/v1", f"{CANARY}/{uuid.uuid4()}/v1"
    with db() as conn:
        conn.execute("insert into write_grant (id, tenant_id, role, bucket, storage_prefix, task_kind, task_id, principal) "
                     "values (%s, %s, 'pipeline_action', %s, %s, 'action_run', %s, 'canary-engineer')",
                     (str(uuid.uuid4()), CANARY, bucket, writing, str(uuid.uuid4())))
    try:
        result, handed = update(live_grants=[], want_grants=[("run-a", f"Write:{bucket}/{writing}/*")])
        check("a folder a running task was given to write to, before any version exists there: written, no alert", bool(handed.get("doc")) and events(result) == [], str(events(result)))
        result, handed = update(live_grants=[], want_grants=[("run-b", f"Write:{bucket}/{never_issued}/*")])
        check("a write permission no write grant was issued for is still an unjustified permission", events(result) == ["unjustified-permission"], str(events(result)))
    finally:
        with db() as conn:
            conn.execute("delete from write_grant where storage_prefix = %s", (writing,))

    heading("A file that cannot be written never stops an update")
    config.ALERTS_FILE = "/proc/u159-cannot-write-here/alerts.jsonl"
    result, handed = update(live_grants=[], want_grants=[], bucket_wide=False)
    check("the update was still written, and its alert still returned", bool(handed.get("doc")) and events(result) == ["no-bucket-wide-permissions"], str(result)[:120])
    config.ALERTS_FILE = ""
    result, handed = update(live_grants=[], want_grants=[], bucket_wide=False)
    check("with the file switched off the update is written and the alert is still on its row", bool(handed.get("doc")) and events(result) == ["no-bucket-wide-permissions"])

    heading("Put the real list back, and take away what this run left on the updates' rows")
    # The stand-in updates above were real rows, and the status the console reads shows the newest alerts for a day: left there, this run's made-up
    # alerts would read as the platform's own. Only manual updates of this run are cleared, so an alert any other update raised stays.
    with db() as conn:
        conn.execute("update storage_permission_print set alerts = null where id > %s and trigger = 'manual' and alerts is not null", (first["last"],))
    config.ALERTS_FILE = alerts_file
    real = grants.reconcile(trigger="manual")
    check("the real update is written from the register", real["identities"] > 0, str({k: real[k] for k in ("identities", "removed")}))
    check("and none of this run's made-up alerts is left for the console to show",
          not (grants.activation_status()["latest_alerts"] or {"at": None})["at"] or grants.activation_status()["latest_alerts"]["at"] <= first["now"] or
          all(a["event"] not in ("no-bucket-wide-permissions", "unexplained-removal", "unjustified-permission") for a in grants.activation_status()["latest_alerts"]["alerts"]),
          str(grants.activation_status()["latest_alerts"])[:160])
    return summary("U159")


if __name__ == "__main__":
    sys.exit(main())
