"""U40: every run says how it started.

Before this, `action_run.operator` was a hardcoded literal, always
"munitas-worker", so a scheduled nightly run, a data engineer's manual
one-off, and an ad-hoc single training step were indistinguishable after the
fact. `trigger_kind`/`triggered_by`/`schedule_id` close that: a manual run
names the human who ran it, a scheduled one names its schedule instead, and
the two shapes cannot be mixed.

    docker compose exec -T munitas-api python /verify/v40_trigger_provenance.py
"""

from __future__ import annotations

import sys
import uuid

from common import (CANARY, ENGINEER, api, bearer_for, check, db,
                    fixture_contract, fixture_tenant, heading, require_api,
                    summary)


def db_one(sql: str, params: tuple) -> dict | None:
    with db() as conn:
        return conn.execute(sql, params).fetchone()


def start_run(**overrides):
    body = {
        "tenant_id": CANARY,
        "action_id": overrides.pop("action_id"),
        "code_hash": "sha256:verify",
        "image_digest": "sha256:verify",
        "operator": "verify-suite",
        "idempotency_key": f"v40-{uuid.uuid4().hex[:10]}",
        "input_versions": [],
        "params": {},
    }
    body.update(overrides)
    return api("POST", "/action-runs", json=body)


def main() -> int:
    require_api()

    tenant = fixture_tenant()
    fixture_contract(tenant)  # ensures the tenant has something registered

    action_id = db_one(
        "select id from dataset_action where tenant_id = %s limit 1",
        (CANARY,),
    )
    if not action_id:
        # v22/v28/v33 all register actions incidentally via the pipeline
        # fixtures; if none exist yet in this tenant, register one directly
        # rather than depending on script ordering.
        with db() as conn:
            new_id = str(uuid.uuid4())
            conn.execute(
                """insert into dataset_action (id, tenant_id, name, output_class)
                   values (%s, %s, %s, 'RAW') on conflict do nothing""",
                (new_id, CANARY, "verify-trigger-provenance"),
            )
        action_id = {"id": new_id}

    heading("U40: a manual run must name who triggered it")

    no_trigger = start_run(action_id=str(action_id["id"]), trigger_kind="manual")
    check("a manual run with no triggered_by is refused",
          no_trigger.status_code == 400, f"HTTP {no_trigger.status_code}")

    heading("U40: triggered_by must be a registered human")

    unregistered = start_run(
        action_id=str(action_id["id"]), trigger_kind="manual",
        triggered_by="nobody-registered-under-this-name",
    )
    check("an unregistered triggered_by is refused",
          unregistered.status_code == 403, f"HTTP {unregistered.status_code}")

    workload_triggered = start_run(
        action_id=str(action_id["id"]), trigger_kind="manual",
        triggered_by="canary-trainer",  # a workload, not a human
    )
    check("a workload cannot be named as who triggered a run",
          workload_triggered.status_code == 403,
          f"HTTP {workload_triggered.status_code}")

    heading("U40: the two shapes cannot be mixed")

    manual_with_schedule = start_run(
        action_id=str(action_id["id"]), trigger_kind="manual",
        triggered_by=ENGINEER, schedule_id="nightly-deid",
    )
    check("a manual run with a schedule_id is refused",
          manual_with_schedule.status_code == 400,
          f"HTTP {manual_with_schedule.status_code}")

    scheduled_with_person = start_run(
        action_id=str(action_id["id"]), trigger_kind="scheduled",
        triggered_by=ENGINEER,
    )
    check("a scheduled run naming a person is refused",
          scheduled_with_person.status_code == 400,
          f"HTTP {scheduled_with_person.status_code}")

    scheduled_no_id = start_run(action_id=str(action_id["id"]), trigger_kind="scheduled")
    check("a scheduled run with no schedule_id is refused",
          scheduled_no_id.status_code == 400, f"HTTP {scheduled_no_id.status_code}")

    heading("U40: a valid manual run and a valid scheduled run both succeed")

    manual = start_run(
        action_id=str(action_id["id"]), trigger_kind="manual", triggered_by=ENGINEER,
    )
    check("the manual run is accepted", manual.status_code == 201,
          f"HTTP {manual.status_code}")

    scheduled = start_run(
        action_id=str(action_id["id"]), trigger_kind="scheduled",
        schedule_id="nightly-deid",
    )
    check("the scheduled run is accepted", scheduled.status_code == 201,
          f"HTTP {scheduled.status_code}")

    if manual.status_code == 201 and scheduled.status_code == 201:
        listed = api("GET", "/action-runs", params={"tenant_id": tenant, "limit": 500},
                    headers=bearer_for(ENGINEER)).json()["action_runs"]
        by_id = {r["id"]: r for r in listed}

        m = by_id.get(manual.json()["id"])
        s = by_id.get(scheduled.json()["id"])

        check("the manual run is listed as manual, with its triggerer's label",
              m is not None and m["trigger_kind"] == "manual"
              and m["triggered_by"] == ENGINEER
              and m["triggered_by_label"] is not None,
              str(m))
        check("the scheduled run is listed as scheduled, naming its schedule",
              s is not None and s["trigger_kind"] == "scheduled"
              and s["schedule_id"] == "nightly-deid"
              and s["triggered_by"] is None,
              str(s))

        heading("U40: the new fields do not disturb idempotent retry")

        # Same idempotency key, different (and here, invalid) trigger fields.
        # V6's guarantee is that a retry returns the original row untouched;
        # confirming that still holds is what proves these fields did not
        # become part of the identity a retry re-validates.
        original_key = db_one(
            "select idempotency_key from action_run where id = %s",
            (manual.json()["id"],),
        )["idempotency_key"]

        retried = api("POST", "/action-runs", json={
            "tenant_id": tenant,
            "action_id": str(action_id["id"]),
            "code_hash": "sha256:verify",
            "image_digest": "sha256:verify",
            "operator": "verify-suite",
            "idempotency_key": original_key,
            "trigger_kind": "manual",
            "triggered_by": "not-the-original-triggerer",
        })
        check("a retried idempotency key still succeeds",
              retried.status_code == 201, f"HTTP {retried.status_code}")
        check("and returns the original row, not a new one",
              retried.status_code == 201
              and retried.json()["id"] == manual.json()["id"]
              and retried.json()["created"] is False,
              str(retried.json()) if retried.status_code == 201 else "")

    heading("U40: lineage carries trigger provenance in one hop")

    # A run only appears in `lineage` once it produced a sealed version, which
    # these bare action_run fixtures never do. Confirmed instead by reading
    # the view's own definition for the columns, which is what every other
    # lineage consumer in this platform relies on.
    view_cols = db_one(
        """select count(*) as n from information_schema.columns
           where table_name = 'lineage'
             and column_name in ('trigger_kind','triggered_by','schedule_id')""",
        (),
    )
    check("the lineage view exposes all three trigger columns",
          view_cols is not None and view_cols["n"] == 3, str(view_cols))

    return summary("U40")


if __name__ == "__main__":
    sys.exit(main())
