"""U119: the canary tidy works on the canary tenant and refuses everything else, at every point.

scripts/admin/tidy-canary.py hard-deletes datasets, steps around the immutability of sealed versions, and does it as test-harness
housekeeping. The only thing that makes that acceptable is that it can touch nothing but the canary tenant. This proves each guard
refuses, one at a time, and then proves the whole on a real fixture beside a real second organisation:

  * each guard function refuses a wrong tenant, a wrong purpose, a wrong bucket, a wrong backend, a dataset that is not canary's, a
    change in another tenant's row counts, and an object outside canary's prefix;
  * the tool takes no tenant argument at all;
  * handed a mix of one canary dataset and one dataset of another organisation, it refuses the whole and deletes neither;
  * handed only the canary dataset, it deletes that dataset, its versions and its objects, and the other organisation's dataset,
    versions and objects are exactly as they were.

    docker compose exec -T munitas-api python /verify/v119_canary_tidy_is_canary_only.py
"""

from __future__ import annotations

import subprocess
import sys
import uuid

sys.path.insert(0, "/app")
sys.path.insert(0, "/scripts-admin")

import _canary_purge as canary  # noqa: E402
from common import ADMIN, CANARY, api, bearer_for, bucket_for, check, db, fixture_tabular_contract, fixture_tabular_version, heading, require_api, s3_client, summary  # noqa: E402
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, finish_org, make_org  # noqa: E402


def refuses(call) -> str | None:
    try:
        call()
        return None
    except canary.NotCanary as exc:
        return str(exc)


def main() -> int:
    require_api()

    heading("Each guard refuses what it is there to refuse")
    check("G2 refuses a tenant row that is not canary", refuses(lambda: canary.check_tenant_row({"id": "health", "purpose": "canary"})) is not None)
    check("G2 refuses the canary tenant if its purpose is not canary", refuses(lambda: canary.check_tenant_row({"id": "canary", "purpose": "production"})) is not None)
    check("G2 refuses a tenant that does not exist", refuses(lambda: canary.check_tenant_row(None)) is not None)
    check("G2 accepts the canary tenant", refuses(lambda: canary.check_tenant_row({"id": "canary", "purpose": "canary"})) is None)
    check("G3 refuses another tenant's storage binding",
          refuses(lambda: canary.check_binding({"tenant_id": "health", "backend": "seaweedfs", "bucket": "munitas-health"})) is not None)
    check("G3 refuses canary's binding if the bucket is not canary's",
          refuses(lambda: canary.check_binding({"tenant_id": "canary", "backend": "seaweedfs", "bucket": "munitas-health"})) is not None)
    check("G3 refuses a Cloudflare R2 binding", refuses(lambda: canary.check_binding({"tenant_id": "canary", "backend": "r2", "bucket": "munitas-canary"})) is not None)
    check("G3 accepts canary's own binding",
          refuses(lambda: canary.check_binding({"tenant_id": "canary", "backend": "seaweedfs", "bucket": "munitas-canary"})) is None)
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    check("G4 refuses a set that holds a dataset of another tenant",
          refuses(lambda: canary.check_datasets_are_canary([a, b], [{"id": a, "tenant_id": "canary"}, {"id": b, "tenant_id": "health"}])) is not None)
    check("G4 refuses a set where an id asked for was not found",
          refuses(lambda: canary.check_datasets_are_canary([a, b], [{"id": a, "tenant_id": "canary"}])) is not None)
    check("G7 refuses a change in another tenant's row counts",
          refuses(lambda: canary.check_snapshot({("health", "dataset"): 5, ("canary", "dataset"): 9}, {("health", "dataset"): 4, ("canary", "dataset"): 8})) is not None)
    check("G7 refuses another tenant appearing with rows it did not have",
          refuses(lambda: canary.check_snapshot({("canary", "dataset"): 9}, {("canary", "dataset"): 8, ("health", "dataset"): 1})) is not None)
    check("G7 accepts a change in canary's own counts",
          refuses(lambda: canary.check_snapshot({("health", "dataset"): 5, ("canary", "dataset"): 9}, {("health", "dataset"): 5, ("canary", "dataset"): 0})) is None)
    check("G8 refuses an object outside canary's prefix", refuses(lambda: canary.check_keys([f"health/{a}/v1/x"], [a], "munitas-canary")) is not None)
    check("G8 refuses an object under canary but not under a verified dataset", refuses(lambda: canary.check_keys([f"canary/{b}/v1/x"], [a], "munitas-canary")) is not None)
    check("G8 refuses another bucket", refuses(lambda: canary.check_keys([f"canary/{a}/v1/x"], [a], "munitas-health")) is not None)
    check("G8 accepts an object under a verified canary dataset", refuses(lambda: canary.check_keys([f"canary/{a}/v1/x"], [a], "munitas-canary")) is None)

    heading("Datasets that go together are deleted together, or not at all")
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    old_time, young_time, cutoff = now - timedelta(hours=5), now - timedelta(minutes=10), now - timedelta(hours=2)
    created = {"a": old_time, "b": old_time, "c": old_time, "d": old_time, "e": young_time, "f": old_time}
    groups = canary.group_old(created, [("a", "b"), ("b", "c"), ("d", "e")], cutoff)
    flat = sorted(sorted(g) for g in groups)
    check("old datasets linked to each other are one group", ["a", "b", "c"] in flat, str(flat))
    check("an old dataset linked to a young one is left alone, with the young one", not any(x in sum(groups, []) for x in ("d", "e")), str(flat))
    check("an old dataset with no links is a group of its own", ["f"] in flat)
    kept = canary.mark_kept({"a": old_time, "b": old_time, "c": old_time}, {"b"}, cutoff)
    kept_groups = canary.group_old(kept, [("a", "b")], cutoff)
    check("a dataset kept for the freed-files screen is never selected, and neither is anything that goes with it",
          sum(kept_groups, []) == ["c"], str(kept_groups))
    check("a link to something that is not a canary dataset pulls nothing in",
          sorted(sorted(g) for g in canary.group_old({"a": old_time}, [("a", "health-dataset-id")], cutoff)) == [["a"]])

    heading("The reprint relaxes the platform's guard only when it can prove the shrink is canary's deleted datasets")
    import _canary_reprint as reprint

    gone, there = str(uuid.uuid4()), str(uuid.uuid4())
    canary_folder = lambda d: f"Read:munitas-canary/canary/{d}/v1/*"  # noqa: E731
    check("a canary folder grant of a dataset that no longer exists is accepted",
          reprint.problems_with({"lease-abc": {canary_folder(gone)}}, {"lease-abc": set()}, existing=set()) == [])
    check("R1: a grant in another organisation's bucket is refused",
          any(p.startswith("R1") for p in reprint.problems_with({"lease-abc": {f"Read:munitas-health/health/{gone}/v1/*"}}, {"lease-abc": set()}, set())))
    check("R2: a bucket-wide grant is refused",
          any(p.startswith("R2") for p in reprint.problems_with({"lease-abc": {"Read:munitas-canary"}}, {"lease-abc": set()}, set())))
    check("R2: a folder grant that names no dataset is refused",
          any(p.startswith("R2") for p in reprint.problems_with({"lease-abc": {"Read:munitas-canary/canary/somewhere"}}, {"lease-abc": set()}, set())))
    check("R3: a grant of a dataset that still exists is refused",
          any(p.startswith("R3") for p in reprint.problems_with({"lease-abc": {canary_folder(there)}}, {"lease-abc": set()}, existing={there})))
    for name in ("pipeline_action~canary", "agent_runtime~canary", "munitas-admin", "ingest-canary"):
        check(f"R4: the identity {name} disappearing is refused",
              any(p.startswith("R4") for p in reprint.problems_with({name: set()}, {}, set())))
    check("R4: a lease, catalog, task or table job key disappearing is accepted",
          all(reprint.problems_with({n: set()}, {}, set()) == [] for n in ("lease-abc", "cat-abc", "run-abc", "tj-abc")))
    check("a task key that disappears with its row is accepted even if its grants name datasets that still exist",
          reprint.problems_with({"run-abc": {canary_folder(there)}}, {}, existing={there}) == [])
    check("but not if one of its grants is in another organisation's bucket",
          any(p.startswith("R1") for p in reprint.problems_with({"run-abc": {f"Read:munitas-health/health/{there}/v1/*"}}, {}, {there})))

    heading("The agent and people stages refuse what they must")
    protected = canary.seeded_ids()
    check("the seed file yields the eleven identities that must stay",
          len(protected) >= canary.MIN_SEEDED and {"canary-engineer", "canary-custodian", "canary-pipeline", "canary-agent"} <= protected, str(sorted(protected)))
    check("G10 refuses to go on without the protected set", refuses(lambda: canary.check_protected(set())) is not None)
    person = lambda i, tenant="canary", login=None: {"id": i, "tenant_id": tenant, "kratos_identity_id": login}  # noqa: E731
    check("G10 accepts an invented canary identity with no login", refuses(lambda: canary.check_people(["x"], [person("x")], protected)) is None)
    check("G10 refuses a seeded identity", refuses(lambda: canary.check_people(["canary-engineer"], [person("canary-engineer")], protected)) is not None)
    check("G10 refuses an identity that has a login", refuses(lambda: canary.check_people(["x"], [person("x", login="abc")], protected)) is not None)
    check("G10 refuses another tenant's identity", refuses(lambda: canary.check_people(["x"], [person("x", tenant="health")], protected)) is not None)
    check("G10 refuses an id that does not exist", refuses(lambda: canary.check_people(["x", "y"], [person("x")], protected)) is not None)
    check("G4 refuses an agent of another tenant", refuses(lambda: canary.check_agents_are_canary(["a"], [{"id": "a", "tenant_id": "health"}])) is not None)
    check("G8 refuses stored code outside the verified agents", refuses(lambda: canary.check_agent_keys(["canary/agents/zzz/v1/code.zip"], ["a"], "munitas-canary")) is not None)
    check("G8 refuses stored code of a dataset in an agent's place", refuses(lambda: canary.check_agent_keys([f"canary/{a}/v1/x"], [a], "munitas-canary")) is not None)
    check("G8 accepts a verified agent's stored code", refuses(lambda: canary.check_agent_keys(["canary/agents/a/v1/code.zip"], ["a"], "munitas-canary")) is None)

    heading("The tool takes no tenant")
    done = subprocess.run([sys.executable, "/scripts-admin/tidy-canary.py", "--tenant", "health"], capture_output=True, text=True)
    check("a --tenant argument is refused by the tool itself", done.returncode != 0 and "unrecognized arguments" in done.stderr, done.stderr[-120:])
    check("the tenant is a constant in the library", canary.TENANT == "canary" and canary.BUCKET == "munitas-canary")

    heading("On real data, beside a second organisation")
    other = make_org(engineer=True)  # registering an agent is a data engineer's act
    try:
        mine = fixture_tabular_version(CANARY, dataset_name=f"u119-mine-{uuid.uuid4().hex[:8]}")
        theirs = fixture_tabular_version(other.id, dataset_name="theirs", schema_id=fixture_tabular_contract(other.id))
        admin = s3_client(*ADMIN)
        canary_bucket, other_bucket = bucket_for(CANARY), bucket_for(other.id)
        check("the library's bucket is the canary bucket the platform resolves", canary_bucket == canary.BUCKET, canary_bucket)

        def exists(dataset_id: str) -> bool:
            with db() as conn:
                return bool(conn.execute("select 1 from dataset where id = %s", (dataset_id,)).fetchone())

        def versions_of(dataset_id: str) -> int:
            with db() as conn:
                return conn.execute("select count(*) as n from dataset_version where dataset_id = %s", (dataset_id,)).fetchone()["n"]

        def object_present(bucket: str, key: str) -> bool:
            try:
                admin.head_object(Bucket=bucket, Key=key)
                return True
            except Exception:  # noqa: BLE001
                return False

        with canary.connect() as conn:
            mixed = refuses(lambda: canary.make_plan(conn, [mine["dataset_id"], theirs["dataset_id"]]))
            conn.rollback()
        check("a set holding one canary dataset and one of another organisation is refused", mixed is not None, str(mixed)[:120])
        check("and neither dataset was touched", exists(mine["dataset_id"]) and exists(theirs["dataset_id"]))

        with canary.connect() as conn:
            only_theirs = refuses(lambda: canary.make_plan(conn, [theirs["dataset_id"]]))
            conn.rollback()
        check("a set holding only the other organisation's dataset is refused", only_theirs is not None)

        before_theirs = (versions_of(theirs["dataset_id"]), object_present(other_bucket, theirs["records_key"]))
        with canary.connect() as conn:
            plan = canary.make_plan(conn, [mine["dataset_id"]])
            deleted, removed = canary.purge(conn, plan, canary.s3())
        check("the canary dataset is deleted, with its version", not exists(mine["dataset_id"]) and versions_of(mine["dataset_id"]) == 0
              and deleted.get("dataset") == 1 and deleted.get("dataset_version") == 1, str(deleted)[:160])
        check("and its object is gone", not object_present(canary_bucket, mine["records_key"]) and mine["records_key"] in removed)
        check("every guard that applies to the delete reported passing",
              all(any(g.startswith(x) for g in plan.guards) for x in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8")), str(plan.guards)[:200])
        check("the other organisation's dataset, version and object are exactly as they were",
              exists(theirs["dataset_id"]) and (versions_of(theirs["dataset_id"]), object_present(other_bucket, theirs["records_key"])) == before_theirs)

        heading("An agent and the identity it made, beside a second organisation's")

        def register(tenant: str, who: str, name: str) -> dict:
            made = api("POST", "/agents/register", json={"tenant_id": tenant, "name": name, "registered_by": who, "purpose": "u119 fixture"})
            made.raise_for_status()
            with db() as conn:
                row = conn.execute("select id::text as id, principal_id from agent where id = %s", (made.json()["id"],)).fetchone()
                # Old enough to be selected: the tidy only takes what is older than its cutoff.
                conn.execute("update agent set created_at = now() - interval '3 hours' where id = %s", (row["id"],))
                conn.execute("update directory set created_at = now() - interval '3 hours' where id = %s", (row["principal_id"],))
            return row

        def agent_exists(agent_id: str) -> bool:
            with db() as conn:
                return bool(conn.execute("select 1 from agent where id = %s", (agent_id,)).fetchone())

        def person_exists(person_id: str) -> bool:
            with db() as conn:
                return bool(conn.execute("select 1 from directory where id = %s", (person_id,)).fetchone())

        my_agent = register(CANARY, "canary-engineer", f"u119-agent-{uuid.uuid4().hex[:6]}")
        their_agent = register(other.id, other.people["engineer"], f"u119-theirs-{uuid.uuid4().hex[:6]}")
        check("both agents, and the identity each made, exist", agent_exists(my_agent["id"]) and agent_exists(their_agent["id"])
              and person_exists(my_agent["principal_id"]) and person_exists(their_agent["principal_id"]))

        with canary.connect() as conn:
            mixed_agents = refuses(lambda: canary.purge_agents(conn, [my_agent["id"], their_agent["id"]]))
            conn.rollback()
        check("a set holding a canary agent and another organisation's is refused", mixed_agents is not None, str(mixed_agents)[:110])
        check("and neither agent was touched", agent_exists(my_agent["id"]) and agent_exists(their_agent["id"]))

        with canary.connect() as conn:
            selected_agents = canary.select_old_agents(conn, 2.0)
            conn.rollback()
        check("the old canary agent is selected, and the other organisation's never is", my_agent["id"] in selected_agents and their_agent["id"] not in selected_agents)

        with canary.connect() as conn:
            deleted_agents, _ = canary.purge_agents(conn, [my_agent["id"]], canary.s3())
        check("the canary agent is deleted", not agent_exists(my_agent["id"]) and deleted_agents.get("agent") == 1, str(deleted_agents)[:120])
        check("its identity is still there until the people stage takes it, and the other organisation's agent and identity are untouched",
              person_exists(my_agent["principal_id"]) and agent_exists(their_agent["id"]) and person_exists(their_agent["principal_id"]))

        with canary.connect() as conn:
            selected_people = canary.select_old_people(conn, 2.0)
            conn.rollback()
        check("the identity is now selected, a seeded one and the other organisation's are not",
              my_agent["principal_id"] in selected_people and "canary-engineer" not in selected_people and their_agent["principal_id"] not in selected_people)
        with canary.connect() as conn:
            refused_seed = refuses(lambda: canary.purge_people(conn, ["canary-engineer"]))
            conn.rollback()
            refused_other = refuses(lambda: canary.purge_people(conn, [their_agent["principal_id"]]))
            conn.rollback()
        check("deleting a seeded identity is refused", refused_seed is not None, str(refused_seed)[:100])
        check("deleting another organisation's identity is refused", refused_other is not None and person_exists(their_agent["principal_id"]), str(refused_other)[:100])
        with canary.connect() as conn:
            canary.purge_people(conn, [my_agent["principal_id"]])
        check("the canary identity is deleted", not person_exists(my_agent["principal_id"]))
        check("and the seeded people are all still there", all(person_exists(i) for i in protected))
    finally:
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        finish_org(other, priya, ravi)
        drop_org(other)
    return summary("U119")


if __name__ == "__main__":
    sys.exit(main())
