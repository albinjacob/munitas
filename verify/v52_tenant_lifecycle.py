"""U52: creating, retiring, and hard-deleting a tenant from the host.

Runs on the host, because it drives scripts/admin/create-tenant.py, scripts/admin/retire-tenant.py and
scripts/admin/nuke-tenant.py, which sit beside the Compose file rather than inside the
API image, the same reason v47_cleanup_dataset.py runs on the host for
scripts/admin/cleanup-dataset.py.

    .venv\\Scripts\\python.exe verify\\v52_tenant_lifecycle.py

It makes two tenants, `scratch-` and a random suffix each, and removes both however it ends: the first is created, retired and
then deleted by its own cleanup, the second is deleted by the check that proves the delete. A failure part-way through still
removes them, along with the sign-in made for the person it acts as. It removes only the tenants this run made, by the names
it generated, and never anything else.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verify"))
sys.path.insert(0, str(ROOT))

# Run from the host, so the services are on localhost. Anything already set (a different machine, a container) wins.
from ports_config import PORTS  # noqa: E402

os.environ.setdefault("PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS", f"http://localhost:{PORTS['kratos_public']}")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS_ADMIN", f"http://localhost:{PORTS['kratos_admin']}")

from common import (api, check, db, fixture_contract, fixture_tenant,  # noqa: E402
                    fixture_version, heading, require_api, summary)
from lifecycle_fixture import KRATOS_ADMIN, give_login  # noqa: E402

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
CREATE = ROOT / "scripts" / "admin" / "create-tenant.py"
RETIRE = ROOT / "scripts" / "admin" / "retire-tenant.py"
NUKE = ROOT / "scripts" / "admin" / "nuke-tenant.py"
TEMPLATE = ROOT / "scripts" / "admin" / "onboarding-template.json"


def run(script: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    # With nothing to type, the script gets an empty stdin, so a prompt it asks (a dry run without --force asks for the tenant's
    # name) is answered by end of input at once. Left to inherit the caller's stdin it waits for a person, and an open stdin
    # made this check hang until its timeout.
    stdin = {"input": input_text} if input_text is not None else {"stdin": subprocess.DEVNULL}
    return subprocess.run(
        [str(PYTHON), str(script), *args],
        capture_output=True, text=True, cwd=str(ROOT), timeout=300, **stdin,
    )


def write_config(config: dict) -> str:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(config, f)
    f.close()
    return f.name


def remove_what_this_run_made(made: list[str], logins: list[str]) -> list[str]:
    """Delete the tenants this run created, and the sign-ins it made, whatever state the run reached. Returns the tenants still there.

    Only names this run generated (`scratch-` and a random suffix) are touched. One that never got as far as being retired is
    still a production tenant, which the delete refuses outright, so it is retired first. Both steps are the project's own scripts
    with their own guards, and the delete asks for the tenant's name typed back like any other use of it."""
    left = []
    for tenant in made:
        if not tenant.startswith("scratch-"):
            left.append(tenant)
            continue
        with db() as conn:
            row = conn.execute("select purpose from tenant where id = %s", (tenant,)).fetchone()
        if not row:
            continue
        if row["purpose"] == "production":
            run(RETIRE, "--tenant", tenant, "--force")
        run(NUKE, "--tenant", tenant, "--force", input_text=f"{tenant}\n")
        with db() as conn:
            if conn.execute("select 1 from tenant where id = %s", (tenant,)).fetchone():
                left.append(tenant)
    for identity in logins:
        httpx.delete(f"{KRATOS_ADMIN}/admin/identities/{identity}", timeout=10.0)
    return left


def main() -> int:
    made: list[str] = []
    logins: list[str] = []
    try:
        checks(made, logins)
    finally:
        left = remove_what_this_run_made(made, logins)
    heading("U52: nothing is left behind")
    check("the tenants this run made are all gone", not left, str(left))
    return summary("U52")


def checks(made: list[str], logins: list[str]) -> None:
    require_api()

    # --------------------------------------------------------------- U52 --
    heading("U52: the shipped onboarding template is itself valid")

    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    check("scripts/admin/onboarding-template.json parses as JSON", bool(template))
    check("it declares at least one department, person and workload",
          bool(template.get("departments")) and bool(template.get("people"))
          and bool(template.get("workloads")))

    heading("U52: scripts/admin/create-tenant.py creates a tenant from an onboarding file")

    tenant = f"scratch-{uuid.uuid4().hex[:8]}"
    made.append(tenant)
    config = {
        "departments": [
            {"key": "cardiology", "name": "Cardiology", "custodian": "hartley"}
        ],
        "people": [
            {"key": "hartley", "label": "Hartley", "roles": ["data_custodian"]}
        ],
        "workloads": [
            {"key": "pipeline", "label": "Pipeline action", "roles": ["pipeline_action"]}
        ],
    }
    config_path = write_config(config)

    created = run(CREATE, "--tenant", tenant, "--config", config_path)
    check("scripts/admin/create-tenant.py succeeds", created.returncode == 0,
          created.stdout + created.stderr)

    with db() as conn:
        trow = conn.execute(
            "select purpose from tenant where id = %s", (tenant,)
        ).fetchone()
        hartley = conn.execute(
            "select id, kind, roles from directory where id = %s",
            (f"{tenant}-hartley",),
        ).fetchone()
        pipeline = conn.execute(
            "select id, kind, roles from directory where id = %s",
            (f"{tenant}-pipeline",),
        ).fetchone()
        dept = conn.execute(
            "select name, custodian from department where tenant_id = %s and name = %s",
            (tenant, "Cardiology"),
        ).fetchone()

    if hartley:
        # The platform acts as the signed-in person and refuses a name that is not theirs, so the person this check acts
        # as needs a real login. The onboarding script makes the directory entry; this makes the sign-in.
        with db() as conn:
            logins.append(give_login(conn, f"{tenant}-hartley", "Hartley"))

    check("the tenant row exists, defaulting to production",
          bool(trow) and trow["purpose"] == "production", str(trow))
    check("the person's id is tenant-prefixed from their key",
          bool(hartley) and hartley["kind"] == "human"
          and hartley["roles"] == ["data_custodian"], str(hartley))
    check("the workload's id is tenant-prefixed from its key",
          bool(pipeline) and pipeline["kind"] == "workload"
          and pipeline["roles"] == ["pipeline_action"], str(pipeline))
    check("the department's custodian resolves to the person's derived id",
          bool(dept) and dept["custodian"] == f"{tenant}-hartley", str(dept))

    heading("U52: rerunning is idempotent, row by row")

    rerun = run(CREATE, "--tenant", tenant, "--config", config_path)
    check("rerunning succeeds", rerun.returncode == 0, rerun.stdout + rerun.stderr)
    check("it reports the tenant already exists",
          "already exists" in rerun.stdout, rerun.stdout)

    config["people"].append(
        {"key": "sam", "label": "Sam", "roles": ["notebook_explore"]}
    )
    grown_path = write_config(config)
    grown = run(CREATE, "--tenant", tenant, "--config", grown_path)
    check("adding one person and rerunning succeeds", grown.returncode == 0,
          grown.stdout + grown.stderr)

    with db() as conn:
        sam = conn.execute(
            "select id from directory where id = %s", (f"{tenant}-sam",)
        ).fetchone()
        hartley_count = conn.execute(
            "select count(*) as n from directory where id = %s",
            (f"{tenant}-hartley",),
        ).fetchone()["n"]

    check("the new person was created", bool(sam), str(sam))
    check("the existing person was not duplicated", hartley_count == 1,
          str(hartley_count))

    heading("U52: an unknown role is refused before anything is written")

    bad_tenant = f"scratch-{uuid.uuid4().hex[:8]}"
    bad_config = {
        "departments": [],
        "people": [{"key": "nobody", "label": "Nobody", "roles": ["not_a_real_role"]}],
        "workloads": [],
    }
    bad_path = write_config(bad_config)
    bad = run(CREATE, "--tenant", bad_tenant, "--config", bad_path)
    check("scripts/admin/create-tenant.py refuses an unknown role", bad.returncode != 0,
          f"exit {bad.returncode}")
    check("and names the bad role", "not_a_real_role" in (bad.stdout + bad.stderr),
          (bad.stdout + bad.stderr)[:300])

    with db() as conn:
        nothing = conn.execute(
            "select id from tenant where id = %s", (bad_tenant,)
        ).fetchone()
    check("nothing was created for the refused tenant", nothing is None, str(nothing))

    heading("U52: scripts/admin/retire-tenant.py closes a tenant to writes, keeps it readable")

    dry = run(RETIRE, "--tenant", tenant)
    check("with no confirmation, it reports and asks rather than acting",
          dry.returncode != 0, f"exit {dry.returncode}")

    with db() as conn:
        still_open = conn.execute(
            "select purpose from tenant where id = %s", (tenant,)
        ).fetchone()
    check("the tenant is untouched", still_open["purpose"] == "production",
          str(still_open))

    retired = run(RETIRE, "--tenant", tenant, "--force")
    check("--force retires it", retired.returncode == 0,
          retired.stdout + retired.stderr)
    check("and points at scripts/admin/reclaim-storage.py as the next step",
          "scripts/admin/reclaim-storage.py" in retired.stdout, retired.stdout)

    with db() as conn:
        now_retired = conn.execute(
            "select purpose from tenant where id = %s", (tenant,)
        ).fetchone()
    check("the tenant is now retired", now_retired["purpose"] == "retired",
          str(now_retired))

    blocked = api("POST", "/agents/register", json={
        "tenant_id": tenant, "name": f"agent-{uuid.uuid4().hex[:8]}",
        "registered_by": f"{tenant}-hartley", "purpose": "should be refused",
    })
    # Refused before the route runs: a person of a retired organisation can do nothing in it (auth.closed_refusal), so this is a 403
    # that says why, not the 409 an earlier version of the platform answered with.
    check("a write to the retired tenant is refused through the API, and says the organisation is closing down",
          blocked.status_code == 403 and "closing down" in blocked.text,
          f"HTTP {blocked.status_code} {blocked.text[:200]}")

    already = run(RETIRE, "--tenant", tenant, "--force")
    check("retiring an already-retired tenant is refused",
          already.returncode != 0, f"exit {already.returncode}")

    heading("U52: scripts/admin/nuke-tenant.py refuses a production tenant outright")

    prod_refuse = run(NUKE, "--tenant", "health", "--force", input_text="health\n")
    check("scripts/admin/nuke-tenant.py refuses health (production) even with --force",
          prod_refuse.returncode != 0, f"exit {prod_refuse.returncode}")
    check("and says why", "production" in (prod_refuse.stdout + prod_refuse.stderr),
          (prod_refuse.stdout + prod_refuse.stderr)[:300])

    heading("U52: a dry run reports without changing anything")

    scratch = fixture_tenant(f"scratch-{uuid.uuid4().hex[:8]}")
    made.append(scratch)
    contract = fixture_contract(scratch)
    sealed = fixture_version(scratch, contract, "RAW")

    # Three tables hold this tenant's data with no tenant_id column of their
    # own, scoped only through a foreign key to dataset or dataset_version.
    # v52 previously never populated them, so the bug where scripts/admin/nuke-tenant.py's
    # table discovery missed all three (and got permanently stuck deleting
    # dataset and dataset_version) went uncaught until run against t1's real
    # data, which does carry rows in them.
    with db() as conn:
        conn.execute(
            "insert into directory (id, tenant_id, label, kind, roles) "
            "values (%s, %s, 'Verify scratch person', 'human', '{}') "
            "on conflict do nothing",
            (f"{scratch}-person", scratch),
        )
        conn.execute(
            "insert into dataset_source (id, dataset_id, kind, locator) "
            "values (%s, %s, 'upload', 'verify.csv')",
            (str(uuid.uuid4()), sealed["dataset_id"]),
        )
        conn.execute(
            "insert into class_transition (id, dataset_version_id, from_class, "
            "to_class, decided_by, decided_by_kind, gate_evidence) "
            "values (%s, %s, 'RAW', 'UNDER_REVIEW', %s, 'human', '{}'::jsonb)",
            (str(uuid.uuid4()), sealed["id"], f"{scratch}-person"),
        )
        conn.execute(
            "insert into huggingface_fetch_job (id, dataset_id, repo_id, "
            "revision, fetched_by, status, workflow_id) "
            "values (%s, %s, 'org/repo', 'main', %s, 'succeeded', %s)",
            (str(uuid.uuid4()), sealed["dataset_id"], f"{scratch}-person",
             str(uuid.uuid4())),
        )

    # Lineage that loops across three tables: a pipeline run starts from the
    # sealed version, an action run belongs to that pipeline run, and a second
    # version records that action run as what produced it. No row loops, but
    # the tables do, so a delete that clears one table at a time can never
    # finish. nuke-tenant.py did exactly that until a console-started run in
    # the hospital example made it refuse to delete the organisation at all.
    run_id, action_run_id, action_id = (str(uuid.uuid4()) for _ in range(3))
    with db() as conn:
        conn.execute(
            "insert into dataset_action (id, tenant_id, name) values (%s, %s, 'verify-loop')",
            (action_id, scratch))
        conn.execute(
            "insert into pipeline_run (id, tenant_id, dataset, workflow_id, triggered_by, "
            "source_version_id, started_from) values (%s, %s, %s, %s, %s, %s, 'console')",
            (run_id, scratch, sealed["dataset_name"], f"verify-loop-{run_id}",
             f"{scratch}-person", sealed["id"]))
        conn.execute(
            "insert into action_run (id, tenant_id, action_id, code_hash, image_digest, "
            "status, operator, idempotency_key, pipeline_run_id, triggered_by) "
            "values (%s, %s, %s, 'verify', 'verify', 'succeeded', 'verify', %s, %s, %s)",
            (action_run_id, scratch, action_id, f"verify-loop-{action_run_id}", run_id,
             f"{scratch}-person"))
    produced = api("POST", "/dataset-versions", json={
        "tenant_id": scratch, "dataset_id": sealed["dataset_id"], "schema_id": contract,
        "visibility_class": "RAW", "object_manifest": [{"key": "part-0.json", "bytes": 128}],
        "record_count": 1, "produced_by_run": action_run_id,
    })
    check("a version produced by a pipeline that started from another version",
          produced.status_code in (200, 201), f"HTTP {produced.status_code} {produced.text[:200]}")
    produced_id = produced.json().get("id") if produced.status_code in (200, 201) else None

    # A bucket of its own with a file in it, the state any tenant that has
    # uploaded anything is in. The version above only declares a manifest, so
    # nothing would otherwise have created one, and a check that its bucket
    # is gone would pass without testing anything.
    import importlib.util
    spec = importlib.util.spec_from_file_location("nuke_tenant", NUKE)
    nuke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nuke)
    client = nuke.s3()
    scratch_bucket = f"munitas-{scratch}"
    client.create_bucket(Bucket=scratch_bucket)
    client.put_object(Bucket=scratch_bucket, Key=f"{scratch}/probe.txt", Body=b"left behind?")
    with db() as conn:
        conn.execute(
            "insert into tenant_storage_provision (tenant_id, backend, bucket) "
            "values (%s, 'seaweedfs', %s)", (scratch, scratch_bucket))

    dry = run(NUKE, "--tenant", scratch)
    check("the dry run names the bucket and what is in it",
          f"bucket {scratch_bucket}: 1 file(s)" in dry.stdout, dry.stdout[-300:])
    check("with no --force it is a dry run", dry.returncode == 0,
          dry.stdout + dry.stderr)
    check("and reports the sealed version's table",
          "dataset_version" in dry.stdout, dry.stdout)
    check("and reports the indirectly-scoped tables too",
          all(t in dry.stdout for t in
              ("dataset_source", "class_transition", "huggingface_fetch_job")),
          dry.stdout)

    with db() as conn:
        still_sealed = conn.execute(
            "select sealed from dataset_version where id = %s", (sealed["id"],)
        ).fetchone()
    check("the dry run changed nothing",
          bool(still_sealed) and still_sealed["sealed"] is True, str(still_sealed))

    heading("U52: confirmed, it deletes every row and restores the rewrite rules")

    with db() as conn:
        scratch_buckets = [r["bucket"] for r in conn.execute(
            "select bucket from tenant_storage_provision "
            "where tenant_id = %s and backend = 'seaweedfs'", (scratch,)).fetchall()]

    nuked = run(NUKE, "--tenant", scratch, "--force", input_text=f"{scratch}\n")
    check("the confirmed run succeeds", nuked.returncode == 0,
          nuked.stdout + nuked.stderr)

    with db() as conn:
        gone_version = conn.execute(
            "select id from dataset_version where id = %s", (sealed["id"],)
        ).fetchone()
        gone_tenant = conn.execute(
            "select id from tenant where id = %s", (scratch,)
        ).fetchone()
    check("the sealed version row is gone", gone_version is None, str(gone_version))
    check("the tenant row is gone", gone_tenant is None, str(gone_tenant))

    with db() as conn:
        gone_source = conn.execute(
            "select id from dataset_source where dataset_id = %s",
            (sealed["dataset_id"],),
        ).fetchone()
        gone_transition = conn.execute(
            "select id from class_transition where dataset_version_id = %s",
            (sealed["id"],),
        ).fetchone()
        gone_job = conn.execute(
            "select id from huggingface_fetch_job where dataset_id = %s",
            (sealed["dataset_id"],),
        ).fetchone()
    check("dataset_source rows are gone despite carrying no tenant_id",
          gone_source is None, str(gone_source))
    check("class_transition rows are gone despite carrying no tenant_id",
          gone_transition is None, str(gone_transition))
    check("huggingface_fetch_job rows are gone despite carrying no tenant_id",
          gone_job is None, str(gone_job))

    with db() as conn:
        loop_left = conn.execute(
            "select (select count(*) from pipeline_run where id = %s) "
            "     + (select count(*) from action_run where id = %s) "
            "     + (select count(*) from dataset_version where id = %s) as n",
            (run_id, action_run_id, produced_id),
        ).fetchone()["n"]
    check("the lineage loop (pipeline run, action run, produced version) is gone too",
          produced_id is not None and loop_left == 0, f"{loop_left} rows left")

    # Its files go with it. Until they did, a deleted tenant's files stayed in
    # storage with nothing recording them, and a rebuilt tenant inherited every
    # previous copy under the same bucket name.
    check("the tenant had a bucket to remove", bool(scratch_buckets), str(scratch_buckets))
    survivors = [b for b in scratch_buckets if nuke.bucket_keys(client, b) is not None]
    check("and its bucket is gone, files and all", not survivors, ", ".join(survivors))
    check("and the delete said so",
          all(f"bucket {b}:" in nuked.stdout and "bucket removed" in nuked.stdout
              for b in scratch_buckets), nuked.stdout[-300:])

    heading("U52: the rewrite rules are still enforced everywhere else")

    with db() as conn:
        rule_enabled = conn.execute(
            "select relname from pg_class c "
            "join pg_rewrite r on r.ev_class = c.oid "
            "where c.relname = 'dataset_version' and r.rulename = 'dataset_version_no_delete' "
            "and r.ev_enabled != 'D'"
        ).fetchone()
    check("dataset_version_no_delete is enabled again after scripts/admin/nuke-tenant.py finishes",
          bool(rule_enabled), str(rule_enabled))

    other_tenant_version = fixture_version(
        fixture_tenant(), fixture_contract(fixture_tenant()), "RAW"
    )
    with db() as conn:
        conn.execute(
            "delete from dataset_version where id = %s",
            (other_tenant_version["id"],),
        )
        still_there = conn.execute(
            "select sealed from dataset_version where id = %s",
            (other_tenant_version["id"],),
        ).fetchone()
    check("a sealed version in an untouched tenant is still genuinely undeletable",
          bool(still_there) and still_there["sealed"] is True, str(still_there))



if __name__ == "__main__":
    sys.exit(main())
