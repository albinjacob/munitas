"""U113: every route knows who is calling, and the ones that act for a person act as that person.

Forty-two routes could be called by anybody who could reach the API. Several of them minted the signed credentials the rest of
the platform treats as proof of "a real task", one decrypted any record, one destroyed any record's key, and the registration
routes believed a name in the request body about who was registering. This checks the whole door and not a list of doors:

  * the invariant: every route in the live route table carries an authentication check, or is on a short list of routes that are
    open on purpose, each with its reason. A new route that is not either fails here;
  * every route on the old open list refuses an anonymous caller, before it looks at what was asked;
  * the routes only the platform's workers use refuse a signed-in person, a platform administrator included;
  * the routes a person uses act as the signed-in person, in their own organisation: another organisation's person finds nothing,
    and a name in the body that is not the person's is refused.

    docker compose exec -T munitas-api python /verify/v113_every_route_has_a_caller.py
"""

from __future__ import annotations

import sys
import uuid

sys.path.insert(0, "/app")

from common import WORKER_HEADERS, api, bearer_for, check, db, heading, require_api, summary  # noqa: E402
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, finish_org, make_org  # noqa: E402

# Open on purpose, and why. Anything not here must carry an authentication check.
OPEN_ON_PURPOSE = {
    ("GET", "/health"): "liveness for monitors and the start-up scripts; it reports the state of the stack and no organisation's data",
    ("GET", "/legal-exports/signing-key"): "the PUBLIC half of the key that signs a legal-export manifest; a recipient needs it to verify one",
    ("GET", "/legal-exports/download/{token}"): "a capability URL: the token in the path is the credential, it works a few times and expires",
    ("POST", "/iceberg/v1/oauth/tokens"): "answers every caller with the same refusal and reads and writes nothing",
    ("GET", "/policy/roles"): "reference material: what each role is and may do, readable before signing in (the console's roles page, U16)",
}
AUTH_DEPENDENCIES = {"current_session", "current_session_while_closing", "organisation_scope", "person_or_worker", "worker_only",
                     "catalog_principal", "_claim", "_worker"}


def dependencies_of(dependant) -> set[str]:
    names = set()
    for d in dependant.dependencies:
        names.add(getattr(d.call, "__name__", str(d.call)))
        names |= dependencies_of(d)
    return names


def main() -> int:
    require_api()
    from fastapi.routing import APIRoute

    from app.main import app

    heading("The invariant: every route is authenticated or open on purpose")
    unguarded = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        headers = {p.name for p in route.dependant.header_params}
        if dependencies_of(route.dependant) & AUTH_DEPENDENCIES or headers & {"x_worker_token", "x_task_credential"}:
            continue
        for method in route.methods - {"HEAD"}:
            unguarded.append((method, route.path))
    stray = sorted(set(unguarded) - set(OPEN_ON_PURPOSE))
    check(f"{len(unguarded)} routes carry no authentication check, and every one is on the list of routes open on purpose", not stray, str(stray[:4]))
    check("and nothing on that list has since been given one without the list being updated",
          set(OPEN_ON_PURPOSE) <= set(unguarded), str(sorted(set(OPEN_ON_PURPOSE) - set(unguarded))))

    heading("An anonymous caller is refused by every route that used to be open, before anything else is looked at")
    fake = str(uuid.uuid4())
    anonymous = [
        ("POST", "/schema-contracts", {}), ("POST", "/datasets", {}), ("POST", "/action-runs", {}), ("POST", "/pipeline-runs", {}),
        ("POST", f"/pipeline-runs/{fake}/end", {}), ("POST", "/write-credentials", {}), ("POST", f"/dataset-versions/{fake}/promote", {}),
        ("POST", "/records", {}), ("POST", f"/records/{fake}/open", {}), ("DELETE", f"/records/{fake}", {}),
        ("POST", "/credentials", {"principal": "someone", "principal_kind": "human", "roles": ["analyst"], "tenant_id": "x",
                                  "dataset_version_id": fake}), ("POST", "/datasets/register", {}), ("POST", f"/datasets/{fake}/files", {}),
        ("POST", f"/datasets/{fake}/fetch-huggingface", {}), ("POST", f"/datasets/{fake}/huggingface-fetch-jobs/{fake}/cancel", {}),
        ("POST", f"/datasets/{fake}/seal", {}), ("POST", f"/datasets/{fake}/confirm-classification", {}), ("POST", "/agents/register", {}),
        ("POST", f"/agents/{fake}/versions", {}), ("POST", f"/agents/{fake}/versions/upload", {}), ("POST", "/pipelines/register", {}),
        ("POST", f"/pipelines/{fake}/versions/upload", {}), ("GET", "/pipeline/kinds", None),
        ("GET", "/pipeline/served-backends", None), ("GET", f"/datasets/{fake}/next-version?tenant_id=x", None),
        ("GET", f"/agent-versions/{fake}/egress-status", None), ("POST", "/dataset-versions", {}),
    ]
    refused = {}
    for method, path, body in anonymous:
        r = api(method, path, headers={}, **({"json": body} if body is not None else {}))
        refused[(method, path)] = r.status_code
    wrong = {k: v for k, v in refused.items() if v not in (401, 403)}
    check(f"all {len(anonymous)} answer 401 or 403 with no credential at all, whatever the body", not wrong, str(list(wrong.items())[:4]))
    malformed = api("POST", "/credentials", headers={}, json={})
    check("a malformed body to the credentials route is a 422 and nothing more: the shape of the request is published, and it reads and writes nothing",
          malformed.status_code == 422)

    one, two = make_org(), make_org()
    try:
        # A department in each organisation, owned by its custodian.
        with db() as conn:
            dept = {}
            for org in (one, two):
                dept[org.id] = str(uuid.uuid4())
                conn.execute("insert into department (id, tenant_id, name, custodian) values (%s, %s, 'Records', %s)",
                             (dept[org.id], org.id, org.people["custodian"]))
        a_cust, a_member, b_member = one.bearer("custodian"), one.bearer("member"), two.bearer("member")
        admin = bearer_for(ADMIN_A)

        heading("The routes only the platform's workers use refuse a person, a platform administrator included")
        worker_only = [("POST", "/schema-contracts", {}), ("POST", "/datasets", {"tenant_id": one.id, "name": "x"}), ("POST", "/action-runs", {}),
                       ("POST", "/pipeline-runs", {}), ("POST", f"/pipeline-runs/{fake}/end", {}), ("POST", "/write-credentials", {}),
                       ("POST", f"/dataset-versions/{fake}/promote", {}), ("POST", "/records", {}), ("POST", f"/records/{fake}/open?ciphertext_b64=AA", None)]
        for who, headers in (("a member", a_member), ("a custodian", a_cust), ("a platform administrator", admin)):
            got = {(m, p): api(m, p, headers=headers, **({"json": b} if b is not None else {})).status_code for m, p, b in worker_only}
            bad = {k: v for k, v in got.items() if v != 403}
            check(f"{who} is refused by all {len(worker_only)}", not bad, str(list(bad.items())[:3]))
        # control: the worker is let in (what happens next is the route's own business: an empty body is a 422, which is past the door)
        past = api("POST", "/schema-contracts", headers=WORKER_HEADERS, json={})
        check("control: the worker token gets past the door of the same routes (an empty body is then the route's 422)", past.status_code == 422, str(past.status_code))

        heading("Registering as a person: their organisation, and only as themselves")
        body = {"tenant_id": one.id, "name": f"d-{uuid.uuid4().hex[:6]}", "department_id": dept[one.id], "registered_by": one.people["custodian"],
                "provenance": "internal_regulated", "declared_class": "RAW", "source_kind": "upload", "modality": []}
        r = api("POST", "/datasets/register", json=body, headers=a_cust)
        check("a custodian registers a dataset in their own organisation as themselves", r.status_code == 201, f"{r.status_code} {r.text[:100]}")
        dataset_id = r.json().get("id")
        check("another organisation's person cannot register into this organisation", api("POST", "/datasets/register", json={**body, "name": "y"}, headers=b_member).status_code == 403)
        check("a person cannot register as somebody else of the same organisation",
              api("POST", "/datasets/register", json={**body, "name": "z", "registered_by": one.people["member"]}, headers=a_cust).status_code == 403)
        with db() as conn:
            check("and neither made a dataset", conn.execute("select count(*) as n from dataset where tenant_id = %s", (one.id,)).fetchone()["n"] == 1)

        files = {"file": ("note.txt", b"hello", "text/plain")}
        check("another organisation's person finds no such dataset to upload to", api("POST", f"/datasets/{dataset_id}/files", headers=b_member, files=files).status_code == 404)
        check("nor to seal", api("POST", f"/datasets/{dataset_id}/seal", headers=b_member).status_code == 404)
        check("nor to fetch into", api("POST", f"/datasets/{dataset_id}/fetch-huggingface", headers=b_member,
                                       json={"repo_id": "a/b", "fetched_by": two.people["member"]}).status_code in (404, 422))
        check("the person who registered it uploads and seals", api("POST", f"/datasets/{dataset_id}/files", headers=a_cust, files=files).status_code == 201
              and api("POST", f"/datasets/{dataset_id}/seal", headers=a_cust).status_code == 201)

        claimed = api("POST", "/datasets/register", json={**body, "name": f"c-{uuid.uuid4().hex[:6]}", "registered_by": one.people["member"], "declared_class": "UNDER_REVIEW"},
                      headers=a_member).json()["id"]
        check("a person cannot confirm a claim in another organisation", api("POST", f"/datasets/{claimed}/confirm-classification", headers=b_member,
                                                                           json={"confirmed_by": two.people["member"]}).status_code == 404)
        check("nor confirm as the custodian by naming them", api("POST", f"/datasets/{claimed}/confirm-classification", headers=a_member,
                                                                json={"confirmed_by": one.people["custodian"]}).status_code == 403)
        check("the custodian, signed in as themselves, confirms it", api("POST", f"/datasets/{claimed}/confirm-classification", headers=a_cust,
                                                                       json={"confirmed_by": one.people["custodian"]}).status_code == 200)

        agent = api("POST", "/agents/register", headers=a_cust, json={"tenant_id": one.id, "name": "helper", "registered_by": one.people["custodian"], "purpose": "tests"})
        check("a person registers an agent in their own organisation as themselves", agent.status_code == 201, f"{agent.status_code} {agent.text[:100]}")
        agent_id = agent.json().get("id")
        version = {"code_hash": "h", "source_path": "a.py", "image_digest": "native", "model_id": "m", "tool_scope": ["t"]}
        check("another organisation's person finds no such agent", api("POST", f"/agents/{agent_id}/versions", headers=b_member,
                                                                   json={**version, "registered_by": two.people["member"]}).status_code == 404)
        check("a person cannot register a version as somebody else", api("POST", f"/agents/{agent_id}/versions", headers=a_member,
                                                                       json={**version, "registered_by": one.people["custodian"]}).status_code == 403)
        check("as themselves they can", api("POST", f"/agents/{agent_id}/versions", headers=a_cust, json={**version, "registered_by": one.people["custodian"]}).status_code == 201)
        check("another organisation cannot register an agent here", api("POST", "/agents/register", headers=b_member,
                                                                       json={"tenant_id": one.id, "name": "x", "registered_by": two.people["member"], "purpose": "p"}).status_code == 403)
        pipe = api("POST", "/pipelines/register", headers=a_cust, json={"tenant_id": one.id, "name": "flow", "department_id": dept[one.id], "registered_by": one.people["custodian"]})
        check("a pipeline is registered the same way", pipe.status_code == 201, f"{pipe.status_code}")
        check("and another organisation's person cannot register one in this organisation", api("POST", "/pipelines/register", headers=b_member, json={
            "tenant_id": one.id, "name": "x", "department_id": dept[one.id], "registered_by": two.people["member"]}).status_code == 403)

        heading("Reading what a producer needs: its own organisation's, and only that")
        check("a person is told where their organisation's next version goes",
              api("GET", f"/datasets/{dataset_id}/next-version", params={"tenant_id": one.id}, headers=a_cust).status_code == 200)
        check("another organisation's person is not, whatever organisation they name",
              api("GET", f"/datasets/{dataset_id}/next-version", params={"tenant_id": one.id}, headers=b_member).status_code == 404)
        check("the worker is told", api("GET", f"/datasets/{dataset_id}/next-version", params={"tenant_id": one.id}, headers=WORKER_HEADERS).status_code == 200)
        with db() as conn:
            version_id = conn.execute("select id from agent_version where agent_id = %s", (agent_id,)).fetchone()["id"]
        check("an agent version's approval state is told to its own organisation", api("GET", f"/agent-versions/{version_id}/egress-status", headers=a_member).status_code == 200)
        check("and is not found by another organisation", api("GET", f"/agent-versions/{version_id}/egress-status", headers=b_member).status_code == 404)
        check("the policy's roles are readable by anybody, signed in or not, because they are reference material",
              all(api("GET", "/policy/roles", headers=h).status_code == 200 for h in (a_member, WORKER_HEADERS, {})))

        heading("Asking for a credential: as yourself, as a task that holds its credential, or as the platform")
        ask = {"principal_kind": "human", "roles": ["analyst"], "tenant_id": one.id, "dataset_version_id": fake, "purpose": "u113"}
        r = api("POST", "/credentials", headers=a_member, json={**ask, "principal": one.people["custodian"]})
        check("a person cannot ask for a credential as somebody else", r.status_code == 403 and "as yourself" in r.text, f"{r.status_code} {r.text[:100]}")
        r = api("POST", "/credentials", headers=a_member, json={**ask, "principal": one.people["member"]})
        check("as themselves they are let in, and then the decision is the route's own (here the version does not exist)", r.status_code == 404, f"{r.status_code}")
        r = api("POST", "/credentials", headers=WORKER_HEADERS, json={**ask, "principal": one.people["member"]})
        check("the platform's worker is let in as well", r.status_code == 404, f"{r.status_code}")
        r = api("POST", "/credentials", headers={}, json={**ask, "principal": one.people["member"], "run_secret": "not.a.credential"})
        check("a made-up task credential is refused", r.status_code == 403, f"{r.status_code}")

        heading("Erasing a record: the platform, or a person of that organisation as themselves")
        sealed = api("POST", "/records", headers=WORKER_HEADERS, json={"tenant_id": one.id, "record_id": f"r-{uuid.uuid4().hex[:8]}", "plaintext": "secret"}).json()
        record = sealed["record_id"]
        gone = {"tenant_id": one.id, "reason": "u113", "requested_by": one.people["custodian"]}
        check("another organisation's person cannot erase it", api("DELETE", f"/records/{record}", headers=b_member, json={**gone, "requested_by": two.people["member"]}).status_code == 403)
        check("nor can a person erase it in the name of somebody else", api("DELETE", f"/records/{record}", headers=a_member, json=gone).status_code == 403)
        check("anonymously nothing happens", api("DELETE", f"/records/{record}", headers={}, json=gone).status_code in (401, 403))
        opened = api("POST", f"/records/{record}/open", headers=WORKER_HEADERS, params={"ciphertext_b64": sealed["ciphertext"]})
        check("so the record is still there to read, for the platform", opened.status_code == 200 and opened.json()["plaintext"] == "secret", f"{opened.status_code}")
        check("a person of the organisation, as themselves, erases it", api("DELETE", f"/records/{record}", headers=a_cust, json=gone).status_code == 200)
        check("and it can no longer be read", api("POST", f"/records/{record}/open", headers=WORKER_HEADERS, params={"ciphertext_b64": sealed["ciphertext"]}).status_code == 410)
    finally:
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        for org in (one, two):
            finish_org(org, priya, ravi)
            drop_org(org)
    return summary("U113")


if __name__ == "__main__":
    sys.exit(main())
