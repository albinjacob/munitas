"""U126: who may bring data in, register code and confirm a sensitivity claim, and the second approver when the owner made the claim.

These acts used to ask only that the caller was the person they named, in their own organisation, so a researcher could register an agent or
a data protection officer could register a dataset. Now the policy decides, and refuses with a reason. This checks, one item at a time, with
real sign-ins as each role:

  * bringing data in (registering a dataset): a data engineer and a data custodian may; a data protection officer, a researcher, a reviewer, a
    network architect and a platform administrator may not, and nothing is created for a refusal;
  * the steps after registering (a file, sealing, withdrawing an upload, fetching, cancelling a fetch) are refused to the same roles;
  * registering code (an agent, an agent version, an agent upload, a pipeline, a pipeline version): only a data engineer;
  * confirming a claim made by an engineer: only the owning department's custodian; another department's custodian, an engineer and a data
    protection officer are refused, each with its reason;
  * a claim made by the owning department's only approver cannot be confirmed by that approver, nor by a custodian of another department; it
    waits, in nobody's queue, until the department has a second approver, who then sees it and confirms it;
  * sealing or withdrawing on another organisation's dataset is answered as if the dataset did not exist, which these two routes did not do.

    docker compose exec -T munitas-api python /verify/v126_roles_on_person_routes.py
"""

from __future__ import annotations

import io
import sys
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import CANARY, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402

ENGINEER, CUSTODIAN, ELSEWHERE = "canary-engineer", "canary-custodian", "canary-elsewhere"
DPO, RESEARCHER, REVIEWER, ARCHITECT = "canary-dpo", "canary-researcher", "canary-reviewer", "canary-architect"
ADMIN, OTHER_ORG_ENGINEER = "ops-priya", "eng-devi"  # a platform administrator and a data engineer, both of the health organisation

INTAKE_REASON = "this person holds no role that may bring data in (a data engineer or a data custodian)"
CODE_REASON = "this person holds no role that may register an agent or a pipeline (a data engineer)"

NOT_INTAKE = [DPO, RESEARCHER, REVIEWER, ARCHITECT]
NOT_CODE = [CUSTODIAN, DPO, RESEARCHER, REVIEWER, ARCHITECT]


def department(name: str) -> str:
    with db() as conn:
        return str(conn.execute("select id from department where tenant_id = %s and name = %s", (CANARY, name)).fetchone()["id"])


def reasons(response) -> list[str]:
    try:
        return response.json().get("detail", {}).get("reasons", [])
    except Exception:  # noqa: BLE001
        return []


def call(person: str, method: str, path: str, **kw):
    return api(method, path, headers=bearer_for(person), **kw)


def register_dataset(person: str, dept: str, *, claim: bool = False, tenant: str = CANARY):
    body = {"tenant_id": tenant, "name": f"u126-{uuid.uuid4().hex[:8]}", "department_id": dept, "registered_by": person,
            "provenance": "external_public" if claim else "internal_regulated", "declared_class": "PUBLISHED" if claim else "RAW", "source_kind": "upload"}
    return call(person, "POST", "/datasets/register", json=body)


def count(table: str, column: str, value: str) -> int:
    with db() as conn:
        return conn.execute(f'select count(*) as n from "{table}" where "{column}" = %s', (value,)).fetchone()["n"]


def tiny_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("main.py", "print('x')\n")
    return buffer.getvalue()


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    verification, elsewhere = department("Verification"), department("Elsewhere")
    waiting: list[tuple[str, str]] = []  # claims left unconfirmed, cleared at the end as (dataset id, the custodian who may confirm)
    added: list[str] = []  # people made a second approver of Verification for one check, removed again at the end
    try:
        heading("Bringing data in: registering a dataset")
        for who in (ENGINEER, CUSTODIAN):
            r = register_dataset(who, verification)
            check(f"{who} may register a dataset", r.status_code == 201, f"{r.status_code} {r.text[:100]}")
        for who in NOT_INTAKE:
            name_before = count("dataset", "registered_by", who)
            r = register_dataset(who, verification)
            check(f"{who} may not, and is told why", r.status_code == 403 and INTAKE_REASON in reasons(r), f"{r.status_code} {reasons(r)}")
            check(f"nothing was registered for {who}", count("dataset", "registered_by", who) == name_before)
        fake = register_dataset(ADMIN, str(uuid.uuid4()), tenant="health")
        check("a platform administrator may not either", fake.status_code == 403 and INTAKE_REASON in reasons(fake), f"{fake.status_code} {reasons(fake)}")

        heading("The steps after registering are refused to the same roles")
        base = register_dataset(ENGINEER, verification).json()["id"]
        zero = str(uuid.uuid4())
        for who in NOT_INTAKE:
            steps = {
                "putting a file in": call(who, "POST", f"/datasets/{base}/files", files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")}),
                "sealing the uploads as audio": call(who, "POST", f"/datasets/{base}/seal-audio"),
                "withdrawing an upload": call(who, "POST", f"/datasets/{base}/uploads/{zero}/withdraw"),
                "fetching from HuggingFace": call(who, "POST", f"/datasets/{base}/fetch-huggingface", json={"repo_id": "x/y", "fetched_by": who}),
                "cancelling a fetch": call(who, "POST", f"/datasets/{base}/huggingface-fetch-jobs/{zero}/cancel"),
                "sealing": call(who, "POST", f"/datasets/{base}/seal"),
            }
            check(f"{who} is refused all six with the reason",
                  all(r.status_code == 403 and INTAKE_REASON in reasons(r) for r in steps.values()),
                  "; ".join(f"{k}: {r.status_code}" for k, r in steps.items() if r.status_code != 403))

        heading("Another organisation's dataset is not found, not sealed or withdrawn")
        for label, r in (("sealing as audio", call(OTHER_ORG_ENGINEER, "POST", f"/datasets/{base}/seal-audio")),
                         ("withdrawing an upload", call(OTHER_ORG_ENGINEER, "POST", f"/datasets/{base}/uploads/{zero}/withdraw"))):
            check(f"{label} on a dataset of another organisation is answered 404", r.status_code == 404, f"{r.status_code} {r.text[:80]}")

        heading("Registering code: only a data engineer")
        agent = call(ENGINEER, "POST", "/agents/register", json={
            "tenant_id": CANARY, "name": f"u126-agent-{uuid.uuid4().hex[:6]}", "department_id": verification, "registered_by": ENGINEER, "purpose": "role check"})
        check("a data engineer may register an agent", agent.status_code == 201, f"{agent.status_code} {agent.text[:100]}")
        agent_id = agent.json()["id"]
        pipeline = call(ENGINEER, "POST", "/pipelines/register", json={
            "tenant_id": CANARY, "name": f"u126-pipeline-{uuid.uuid4().hex[:6]}", "department_id": verification, "registered_by": ENGINEER})
        check("and a pipeline", pipeline.status_code == 201, f"{pipeline.status_code} {pipeline.text[:100]}")
        pipeline_id = pipeline.json()["id"]
        version = call(ENGINEER, "POST", f"/agents/{agent_id}/versions", json={
            "code_hash": "u126", "source_path": "https://example.test/x", "model_id": "none", "tool_scope": [], "registered_by": ENGINEER})
        check("and an agent version", version.status_code == 201, f"{version.status_code} {version.text[:100]}")

        for who in NOT_CODE:
            agents_before, pipelines_before = count("agent", "registered_by", who), count("pipeline", "registered_by", who)
            tries = {
                "an agent": call(who, "POST", "/agents/register", json={
                    "tenant_id": CANARY, "name": f"u126-no-{uuid.uuid4().hex[:6]}", "department_id": verification, "registered_by": who, "purpose": "x"}),
                "an agent version": call(who, "POST", f"/agents/{agent_id}/versions", json={
                    "code_hash": "x", "source_path": "https://example.test/x", "model_id": "none", "tool_scope": [], "registered_by": who}),
                "an agent upload": call(who, "POST", f"/agents/{agent_id}/versions/upload", files={"zip": ("a.zip", tiny_zip(), "application/zip")},
                                        data={"model_id": "none", "registered_by": who}),
                "a pipeline": call(who, "POST", "/pipelines/register", json={
                    "tenant_id": CANARY, "name": f"u126-no-{uuid.uuid4().hex[:6]}", "department_id": verification, "registered_by": who}),
                "a pipeline version": call(who, "POST", f"/pipelines/{pipeline_id}/versions/upload", files={
                    "config_file": ("p.yaml", b"name: x\nsteps: []\n", "text/yaml"), "scripts": ("s.zip", tiny_zip(), "application/zip")},
                    data={"registered_by": who}),
            }
            check(f"{who} is refused all five, with the reason",
                  all(r.status_code == 403 and CODE_REASON in reasons(r) for r in tries.values()),
                  "; ".join(f"{k}: {r.status_code}" for k, r in tries.items() if r.status_code != 403 or CODE_REASON not in reasons(r)))
            check(f"and nothing was registered for {who}",
                  count("agent", "registered_by", who) == agents_before and count("pipeline", "registered_by", who) == pipelines_before)
        admin_try = call(ADMIN, "POST", "/agents/register", json={"tenant_id": "health", "name": "u126-admin", "registered_by": ADMIN, "purpose": "x"})
        check("a platform administrator is refused too", admin_try.status_code == 403 and CODE_REASON in reasons(admin_try), f"{admin_try.status_code} {reasons(admin_try)}")

        heading("Confirming a claim made by an engineer")
        claim = register_dataset(ENGINEER, verification, claim=True).json()["id"]
        waiting.append((claim, CUSTODIAN))
        wrong = call(ELSEWHERE, "POST", f"/datasets/{claim}/confirm-classification", json={"confirmed_by": ELSEWHERE})
        check("another department's custodian may not, and the department approvers are named",
              wrong.status_code == 403 and any("whose approvers are canary-custodian" in x for x in reasons(wrong)), f"{wrong.status_code} {reasons(wrong)}")
        for who in (ENGINEER, DPO, REVIEWER):
            r = call(who, "POST", f"/datasets/{claim}/confirm-classification", json={"confirmed_by": who})
            check(f"{who} may not confirm", r.status_code == 403 and "only a data custodian may confirm a sensitivity claim" in reasons(r), f"{r.status_code} {reasons(r)}")
        ok = call(CUSTODIAN, "POST", f"/datasets/{claim}/confirm-classification", json={"confirmed_by": CUSTODIAN})
        check("the owning department's custodian may", ok.status_code == 200, f"{ok.status_code} {ok.text[:100]}")
        waiting.pop()

        heading("A claim the department's only approver made waits for a second approver")
        own = register_dataset(CUSTODIAN, verification, claim=True)
        check("a custodian registers a dataset with a claim", own.status_code == 201, f"{own.status_code} {own.text[:100]}")
        own_id = own.json()["id"]
        waiting.append((own_id, ELSEWHERE))
        mine_q = call(CUSTODIAN, "GET", "/datasets/awaiting-confirmation", params={"tenant_id": CANARY, "custodian": CUSTODIAN, "limit": 500}).json()
        theirs_q = call(ELSEWHERE, "GET", "/datasets/awaiting-confirmation", params={"tenant_id": CANARY, "custodian": ELSEWHERE, "limit": 500}).json()
        check("the maker's queue does not list it", own_id not in [d["id"] for d in mine_q["items"]])
        check("nor does another department's custodian's", own_id not in [d["id"] for d in theirs_q["items"]])
        self_confirm = call(CUSTODIAN, "POST", f"/datasets/{own_id}/confirm-classification", json={"confirmed_by": CUSTODIAN})
        check("the maker may not confirm it", self_confirm.status_code == 403 and "this person made the claim, so somebody else must confirm it" in reasons(self_confirm),
              f"{self_confirm.status_code} {reasons(self_confirm)}")
        not_a_custodian = call(ENGINEER, "POST", f"/datasets/{own_id}/confirm-classification", json={"confirmed_by": ENGINEER})
        check("an engineer may not confirm it either", not_a_custodian.status_code == 403, f"{not_a_custodian.status_code}")
        outsider = call(ELSEWHERE, "POST", f"/datasets/{own_id}/confirm-classification", json={"confirmed_by": ELSEWHERE})
        check("a custodian of another department may not, and the reason says it waits for a second approver",
              outsider.status_code == 403 and any("waits until the department has a second approver" in x for x in reasons(outsider)), f"{outsider.status_code} {reasons(outsider)}")
        join = call(CUSTODIAN, "POST", f"/departments/{verification}/approvers", json={"person_id": ELSEWHERE, "reason": "second approver, so claims can be checked"})
        check("the approver adds a second approver, with a reason", join.status_code == 201, f"{join.status_code} {join.text[:100]}")
        added.append(ELSEWHERE)
        theirs_q = call(ELSEWHERE, "GET", "/datasets/awaiting-confirmation", params={"tenant_id": CANARY, "custodian": ELSEWHERE, "limit": 500}).json()
        check("the second approver's queue now lists it", own_id in [d["id"] for d in theirs_q["items"]])
        second = call(ELSEWHERE, "POST", f"/datasets/{own_id}/confirm-classification", json={"confirmed_by": ELSEWHERE})
        check("and the second approver may confirm it", second.status_code == 200, f"{second.status_code} {second.text[:100]}")
        waiting.pop()
        back = call(CUSTODIAN, "POST", f"/departments/{verification}/approvers/{ELSEWHERE}/remove", json={"reason": "the check is finished"})
        check("the department goes back to one approver", back.status_code == 200, f"{back.status_code} {back.text[:100]}")
        added.pop()
        with db() as conn:
            row = conn.execute("select declared_by, classification_confirmed_by from dataset where id = %s", (own_id,)).fetchone()
        check("and the record shows two different people", row["declared_by"] == CUSTODIAN and row["classification_confirmed_by"] == ELSEWHERE, str(dict(row)))

        heading("An engineer's claim is in the owning custodian's queue and not the other's")
        again = register_dataset(ENGINEER, verification, claim=True).json()["id"]
        waiting.append((again, CUSTODIAN))
        q_owner = call(CUSTODIAN, "GET", "/datasets/awaiting-confirmation", params={"tenant_id": CANARY, "custodian": CUSTODIAN, "limit": 500}).json()
        q_other = call(ELSEWHERE, "GET", "/datasets/awaiting-confirmation", params={"tenant_id": CANARY, "custodian": ELSEWHERE, "limit": 500}).json()
        check("the owning custodian sees it", again in [d["id"] for d in q_owner["items"]])
        check("another department's custodian does not", again not in [d["id"] for d in q_other["items"]])
    finally:
        # Leave no claim waiting and no extra approver behind.
        for person in added:
            call(CUSTODIAN, "POST", f"/departments/{verification}/approvers/{person}/remove", json={"reason": "the check is finished"})
        for dataset_id, confirmer in waiting:
            call(confirmer, "POST", f"/datasets/{dataset_id}/confirm-classification", json={"confirmed_by": confirmer})
    return summary("U126")


if __name__ == "__main__":
    sys.exit(main())
