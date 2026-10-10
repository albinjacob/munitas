"""U152: a result says who it is waiting on, and a person's departments are shown only while they are true.

Two things a person reads on a screen, each of which must come from what is true right now and never from what was once true:

  * a de-identification result lists the people who may decide it (those who hold a deciding role now, other than whoever started the run), says
    whether the person asking is one of them, and stops listing a person the moment their role is revoked or runs out;
  * the sign-in panel's "approver for" list is each department the person can act for now: listed, not removed, not past the end of their cover,
    and still holding the data custodian role. A role that lapses takes the department off the list without anybody editing the listing.

Everything is made up in an organisation of its own, and removed afterwards.

    docker compose exec -T munitas-api python /verify/v148_waiting_on.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")

from types import SimpleNamespace  # noqa: E402

import httpx  # noqa: E402

from common import CANARY, api, bearer_for, check, db, fixture_contract, fixture_tenant, fixture_version, heading, require_api, summary  # noqa: E402
from lifecycle_fixture import KRATOS_ADMIN, give_login  # noqa: E402
from v127_department_approvers import add_person  # noqa: E402


DEPT = ""


def gate_rows(who: str, **query) -> list[dict]:
    r = api("GET", "/gate-decisions", headers=bearer_for(who), params=query)
    assert r.status_code == 200, r.text
    return r.json()["gate_decisions"]


def approver_of(org: str, person: str) -> list[str]:
    r = api("GET", "/directory", headers=bearer_for(person), params={"kind": "human", "purpose": "all"})
    assert r.status_code == 200, r.text
    return next(p["approver_of"] for p in r.json() if p["id"] == person)


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    tag = uuid.uuid4().hex[:6]
    global DEPT
    DEPT = f"U152 {tag}"
    # The people are made up here, in the verification organisation, with names of their own so nothing else is touched.
    org = SimpleNamespace(id=CANARY, people={}, identities=[])
    try:
        engineer = add_person(org, f"u152-{tag}-engineer", "pipeline_operator", "Engineer")
        member = add_person(org, f"u152-{tag}-analyst", "analyst", "Member")
        custodian_key = f"u152-{tag}-custodian"
        add_person(org, custodian_key, "data_custodian", "Custodian")
        r1 = add_person(org, f"u152-{tag}-r1", "deid_reviewer", "First reviewer")
        r2 = add_person(org, f"u152-{tag}-r2", "deid_reviewer", "Second reviewer")
        version = fixture_version(CANARY, fixture_contract(CANARY))["id"]
        ours = {engineer, member, r1, r2, org.people[custodian_key]}

        def names(row: dict) -> list[str]:
            """Who a row waits on, among the people made up here: the verification organisation has reviewers of its own that this check does not own."""
            return sorted(p["label"] for p in row["waiting_on"] if p["id"] in ours)

        pending, own = str(uuid.uuid4()), str(uuid.uuid4())
        with db() as conn:
            for gate, who in ((pending, engineer), (own, r1)):
                conn.execute(
                    """insert into gate_decision (id, tenant_id, dataset_version_id, to_class, score_card_id, metrics, recommendation, recommendation_reason, state, triggered_by)
                       values (%s, %s, %s, 'OPEN_FOR_ANNOTATION', 'u152', '{}', 'pass', 'a check of who it waits on', 'pending', %s)""",
                    (gate, org.id, version, who))

        heading("A result of a run that was not started from an uploaded pipeline version")
        orphan_run, orphan = str(uuid.uuid4()), str(uuid.uuid4())
        with db() as conn:
            conn.execute("insert into pipeline_run (id, tenant_id, dataset, workflow_id, triggered_by, source_version_id) values (%s, %s, 'u152', %s, %s, %s)",
                         (orphan_run, org.id, f"u152-{uuid.uuid4()}", engineer, version))
            conn.execute(
                """insert into gate_decision (id, tenant_id, pipeline_run_id, dataset_version_id, to_class, score_card_id, metrics, recommendation, recommendation_reason, state, triggered_by)
                   values (%s, %s, %s, %s, 'OPEN_FOR_ANNOTATION', 'u152', '{}', 'pass', 'a run with no pipeline version', 'pending', %s)""",
                (orphan, org.id, orphan_run, version, engineer))
        seen = api("GET", f"/gate-decisions/{orphan}", headers=bearer_for(engineer))
        check("it opens: there is nobody who uploaded the pipeline, and that is not an error", seen.status_code == 200 and seen.json()["can_decide"] is False, f"{seen.status_code} {seen.text[:120]}")
        run = seen.json()["run"] or {}
        check("it still says which run made it, and that no pipeline version is behind it", run.get("id") == orphan_run and run.get("pipeline_name") is None and run.get("triggered_by_label") == "Engineer", str(run)[:200])
        check("and it is listed with who it waits on", names(seen.json()) != [] or len(seen.json()["waiting_on"]) >= 1, str(seen.json().get("waiting_on")))

        heading("A result started by the engineer waits on both reviewers")
        mine = {g["id"]: g for g in gate_rows(engineer, started_by=engineer)}
        check("the engineer's runs are listed for them, and only theirs", sorted(mine) == sorted([pending, orphan]), str(list(mine)))
        row = mine[pending]
        check("it says it is waiting on the two reviewers, by name", names(row) == ["First reviewer", "Second reviewer"], str(row["waiting_on"]))
        check("and that the engineer, who started it, may not decide it", row["can_decide"] is False)
        check("a reviewer is told they may decide it", {g["id"]: g for g in gate_rows(r2)}[pending]["can_decide"] is True)
        check("an analyst, who holds no deciding role, is told they may not, and sees the same people it waits on",
              (lambda g: g["can_decide"] is False and names(g) == ["First reviewer", "Second reviewer"])({g["id"]: g for g in gate_rows(member)}[pending]))
        detail = api("GET", f"/gate-decisions/{pending}", headers=bearer_for(engineer)).json()
        check("the detail page says the same", detail["can_decide"] is False and names(detail) == ["First reviewer", "Second reviewer"])

        heading("A reviewer's own run waits on the other reviewer, never on themselves")
        r1_view = {g["id"]: g for g in gate_rows(r1)}[own]
        check("it lists only the second reviewer", names(r1_view) == ["Second reviewer"] and r1_view["can_decide"] is False, str(r1_view["waiting_on"]))

        heading("The list follows the roles as they change")
        with db() as conn:
            conn.execute("update directory set roles = '{}' where id = %s", (r2,))
        after = {g["id"]: g for g in gate_rows(engineer, started_by=engineer)}[pending]
        check("when the second reviewer loses the role, it waits on the first alone", names(after) == ["First reviewer"], str(after["waiting_on"]))
        own_after = {g["id"]: g for g in gate_rows(r1)}[own]
        check("and a reviewer's own run then waits on nobody", names(own_after) == [])
        grant = str(uuid.uuid4())
        with db() as conn:
            conn.execute(
                "insert into role_grant (id, tenant_id, principal, role, approved_by, expires_at) values (%s, %s, %s, 'deid_reviewer', %s, now() + interval '1 hour')",
                (grant, org.id, member, org.people[custodian_key]))
        granted = {g["id"]: g for g in gate_rows(engineer, started_by=engineer)}[pending]
        check("a temporary grant of the role counts, so the analyst is listed while it lasts", names(granted) == ["First reviewer", "Member"], str(granted["waiting_on"]))
        with db() as conn:
            conn.execute("update role_grant set expires_at = now() - interval '1 minute' where id = %s", (grant,))
        check("and is not listed once it has run out", names({g["id"]: g for g in gate_rows(engineer, started_by=engineer)}[pending]) == ["First reviewer"])
        with db() as conn:
            conn.execute("update directory set roles = '{}' where id = %s", (r1,))
        nobody = {g["id"]: g for g in gate_rows(engineer, started_by=engineer)}[pending]
        check("with nobody holding the role, the list is empty and the screen can say so", names(nobody) == [] and nobody["can_decide"] is False)

        heading("A decided result lists nobody")
        with db() as conn:
            conn.execute("update directory set roles = %s where id = %s", (["deid_reviewer"], r2))
            conn.execute("update gate_decision set state = 'promoted', decided_by = %s, decided_at = now(), decision_reason = 'fine' where id = %s", (r2, pending))
        done = {g["id"]: g for g in gate_rows(engineer, started_by=engineer)}[pending]
        check("nobody is waited on, and nobody may decide it again", done["waiting_on"] == [] and done["can_decide"] is False)

        heading("The departments a person approves for are only those they can act for now")
        custodian = org.people[custodian_key]
        dept = str(uuid.uuid4())
        with db() as conn:
            conn.execute("insert into department (id, tenant_id, name, custodian) values (%s, %s, %s, %s)", (dept, org.id, DEPT, custodian))
        listed = approver_of(org.id, custodian)
        check("a custodian listed for the department is shown as its approver", listed == [DEPT], str(listed))
        with db() as conn:
            conn.execute("update directory set roles = '{}' where id = %s", (custodian,))
        check("when the custodian role lapses, the department is no longer shown, though the listing is untouched", approver_of(org.id, custodian) == [])
        with db() as conn:
            conn.execute("update directory set roles = %s where id = %s", (["data_custodian"], custodian))
        check("and it comes back when the role does", approver_of(org.id, custodian) == [DEPT])
        permanent = add_person(org, f"u152-{tag}-c3", "data_custodian", "Permanent custodian")
        with db() as conn:
            conn.execute("insert into department_approver (tenant_id, department_id, person_id, added_by, reason) values (%s, %s, %s, %s, 'a second permanent approver')",
                         (org.id, dept, permanent, custodian))
            row = conn.execute("select id from department_approver where department_id = %s and person_id = %s and removed_at is null", (dept, custodian)).fetchone()
            conn.execute("update department_approver set removed_by = %s, removed_at = now(), removal_reason = 'left the team' where id = %s", (custodian, row["id"]))
        check("a removed approver is not shown", approver_of(org.id, custodian) == [])
        second = add_person(org, f"u152-{tag}-c2", "data_custodian", "Cover custodian")
        with db() as conn:
            conn.execute("insert into department_approver (tenant_id, department_id, person_id, added_by, reason, valid_until) values (%s, %s, %s, %s, 'cover', now() + interval '1 day')",
                         (org.id, dept, second, custodian))
        check("a person with cover is shown while it lasts", approver_of(org.id, second) == [DEPT])
        with db() as conn:
            conn.execute("alter table department_approver disable trigger user")
            conn.execute("update department_approver set valid_until = now() - interval '1 minute', added_at = now() - interval '2 days' where person_id = %s", (second,))
            conn.execute("alter table department_approver enable trigger user")
        check("and not after it has run out", approver_of(org.id, second) == [])
    finally:
        # What can be taken away is; what a rule keeps (an approver's history, a decided gate) is left for the canary tidy. What the people made here
        # held is taken back, so a later run does not find them as reviewers or approvers.
        with db() as conn:
            conn.execute("update directory set roles = '{}' where id like %s", (f"canary-u152-{tag}-%",))
        for identity in org.identities:
            httpx.delete(f"{KRATOS_ADMIN}/admin/identities/{identity}", timeout=10.0)
    return summary("v148")


if __name__ == "__main__":
    sys.exit(main())
