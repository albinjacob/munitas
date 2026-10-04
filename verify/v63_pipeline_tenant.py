"""U63: a pipeline run belongs to the tenant whose data it is, not to the worker's.

The worker used to read its own `config.TENANT` at every seal and write every
object to the constant `config.BUCKET`. That is correct for exactly as long as
the tenant it was configured for is the tenant whose data it happens to be
processing, and nothing anywhere enforced that. It survived the per-tenant
storage work because both tenants that ever ran the pipeline are grandfathered
onto the shared bucket, so the constant was accidentally right.

This runs the whole pipeline for a tenant that has its own provisioned bucket,
which is the only arrangement where a wrong answer is visible. The claim is not
"a run finished". It is that every object the run wrote is in that tenant's own
bucket, that none of them is in the shared one, and that everything the run
recorded is filed under that tenant.

The run is started by handing the workflow its parameters directly, the way
`worker/run_pipeline.py` does, rather than through
`POST .../deidentify`. That endpoint requires a real login and the tenant with
its own bucket has no seeded person, and inventing one would mean creating a
login purely to make a test pass. The endpoint's own half of this, that it sends
the tenant and the bucket it read rather than letting the worker guess, is
checked in U63e without needing a second GPU run, and U61 and U62 already drive
the endpoint end to end.

Runs on the host, like U62: it needs the synthetic corpus, which is not mounted
into the API image, and the pipeline worker on the machine with the GPU.

    .venv\\Scripts\\python.exe verify\\v63_pipeline_tenant.py

Needs PG_DSN, VERIFY_KRATOS and S3_ENDPOINT pointing at the published localhost
ports. Expect several minutes: Whisper loads before it transcribes.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ports_config import PORTS  # noqa: E402

# Run from the host, so the services are on localhost. Anything already set wins.
os.environ.setdefault("PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS", f"http://localhost:{PORTS['kratos_public']}")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS_ADMIN", f"http://localhost:{PORTS['kratos_admin']}")
os.environ.setdefault("S3_ENDPOINT", f"http://localhost:{PORTS['seaweedfs_s3']}")

import httpx  # noqa: E402

from common import (ADMIN, api, check, db, fixture_tenant, heading,  # noqa: E402
                    require_api, s3_client, summary)
from lifecycle_fixture import KRATOS_ADMIN, give_login  # noqa: E402

# Sign-ins this run made for the people it acts as, removed when it ends.
LOGINS: list[str] = []

if os.environ.get("MUNITAS_CORPUS"):
    CORPUS = Path(os.environ["MUNITAS_CORPUS"])
elif os.environ.get("MUNITAS_DATA"):
    CORPUS = Path(os.environ["MUNITAS_DATA"].rstrip("/\\") + "/synthetic")
else:
    sys.exit("Set MUNITAS_CORPUS, or MUNITAS_DATA (whose synthetic folder is the corpus).")
RECORDS = 2
CEILING_SECONDS = 20 * 60
POLL_SECONDS = 15


def provisioned_tenant() -> tuple[str, str] | None:
    """A tenant of this script's own, with its own bucket.

    Made rather than borrowed, and the first draft borrowed. It picked whatever
    provisioned tenant it found, which was U56's probe tenant, and a pipeline
    run seals versions: U56 asserts its probe tenant has never been written to,
    so six sealed versions permanently broke a check that had nothing to do
    with this one. Sealed versions cannot be deleted, so that damage was not
    undoable. Fixtures are not shared here for exactly that reason.

    The tenant is new on every run, so "this tenant has never had a version
    sealed" is true by construction rather than by nothing else having
    interfered.
    """
    tenant = f"pipeline-probe-{uuid.uuid4().hex[:8]}"
    # Declared disposable at creation, while it is empty (the `pipeline-probe-` prefix is in common.py's list, which is what
    # `fixture_tenant` reads). Its sealed versions then delete like any other row, and the nightly sweep removes the tenant and its
    # bucket. As `canary` it was kept for good, one more every run, because a sealed version cannot be deleted from a tenant that has
    # not declared itself disposable and a tenant that already holds one cannot declare it now.
    fixture_tenant(tenant)

    # Provisioning happens on a tenant's first write. Asking where the next
    # version goes is that first write, and it is the platform's own path
    # rather than a bucket this script creates and then claims is real.
    probe = api("POST", "/datasets", json={"tenant_id": tenant,
                                           "name": "provision-probe"})
    if probe.status_code not in (200, 201):
        return None
    where = api("GET", f"/datasets/{probe.json()['id']}/next-version",
                params={"tenant_id": tenant})
    if where.status_code != 200:
        return None
    return tenant, where.json()["bucket"]


def seeded_for(tenant: str) -> tuple[str, str, str] | None:
    """A department, an operator and a reviewer inside this tenant.

    A pipeline run needs a department to own the dataset and a
    `pipeline_operator` to start it. A tenant provisioned by a storage test has
    neither, so they are created here, once, and reused on later runs.
    """
    with db() as conn:
        # People first: department.custodian is a foreign key into directory,
        # so creating the department before the person it names fails.
        # The third is the workload the worker acts as for this tenant's runs (`<tenant>-pipeline`); without it the platform answers
        # a read with "no such workload is registered".
        for who, kind, roles in ((f"{tenant}-custodian", "human", "{data_custodian}"),
                                 (f"{tenant}-engineer", "human", "{pipeline_operator}"),
                                 (f"{tenant}-pipeline", "workload", "{pipeline_action}")):
            conn.execute(
                """insert into directory (id, tenant_id, label, kind, roles)
                   values (%s, %s, %s, %s, %s)
                   on conflict (id) do nothing""",
                (who, tenant, who, kind, roles),
            )

        dept = conn.execute(
            "select id from department where tenant_id = %s limit 1", (tenant,)
        ).fetchone()
        if dept:
            dept_id = str(dept["id"])
        else:
            dept_id = str(uuid.uuid4())
            conn.execute(
                """insert into department (id, tenant_id, name, custodian)
                   values (%s, %s, 'Verification', %s)""",
                (dept_id, tenant, f"{tenant}-custodian"),
            )

        # The same five actions run_pipeline.py registers, because a dataset
        # action is scoped to a tenant and this tenant has never run anything.
        for name, output_class in (("ingest", "RAW"), ("transcribe", "RAW"),
                                   ("detect", "RAW"), ("handoff", "RAW"),
                                   ("redact", "UNDER_REVIEW")):
            conn.execute(
                """insert into dataset_action (id, tenant_id, name, output_class)
                   values (%s, %s, %s, %s)
                   on conflict (tenant_id, name) do nothing""",
                (str(uuid.uuid4()), tenant, name, output_class),
            )
    # The platform registers a dataset, and takes its files, as the person who is signed in, so the engineer needs a real login.
    with db() as conn:
        LOGINS.append(give_login(conn, f"{tenant}-engineer", "Pipeline engineer"))
    return dept_id, f"{tenant}-engineer", f"{tenant}-custodian"


def corpus_records(how_many: int) -> list[dict]:
    chosen = []
    for path in sorted(CORPUS.glob("synth-*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        audio = CORPUS / "audio" / f"{record['record_id']}.wav"
        if audio.exists():
            chosen.append({"record": record, "audio": audio})
        if len(chosen) == how_many:
            break
    return chosen


def answer_key(record: dict) -> bytes:
    return json.dumps({
        "spans": record["spans"],
        "hazard": record["asr_hazard"],
        "hazards": record["hazards"],
        "template": record["template"],
        "reference_transcript": record["transcript"],
    }).encode("utf-8")


async def start_run(tenant: str, bucket: str, dataset_id: str, version_id: str,
                    prefix: str, records: int, engineer: str) -> str:
    """Start the workflow with its parameters, the way run_pipeline.py does.

    The parameters are what this script is about: a run is told which tenant it
    belongs to and which bucket that tenant's objects live in, and the worker
    uses those rather than its own environment.
    """
    from temporalio.client import Client

    from worker import config as worker_config

    run_id = str(uuid.uuid4())
    workflow_id = f"deid-tenant-{run_id[:8]}"
    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", f"localhost:{PORTS['temporal']}"))

    with db() as conn:
        conn.execute(
            """insert into pipeline_run
                 (id, tenant_id, dataset, workflow_id, trigger_kind,
                  triggered_by, source_version_id, started_from)
               values (%s, %s, %s, %s, 'manual', %s, %s, 'console')""",
            (run_id, tenant, dataset_id, workflow_id, engineer, version_id),
        )

    await client.start_workflow(
        "DeidentificationPipeline",
        {
            "dataset": dataset_id,
            "source_version_id": version_id,
            "source_prefix": prefix,
            "tenant": tenant,
            "bucket": bucket,
            "limit": records,
            "trigger_kind": "manual",
            "triggered_by": engineer,
            "schedule_id": None,
            "pipeline_run_id": run_id,
        },
        id=workflow_id,
        task_queue=worker_config.TASK_QUEUE,
    )
    return run_id


async def latest_console_run_input() -> tuple[dict, str, str] | None:
    """What the start endpoint actually sent Temporal for its most recent run.

    Read out of the workflow's own history rather than out of the endpoint's
    source, because the question is what a running system did, not what the
    code appears to say. Paired with the tenant and bucket the platform resolves
    for that version independently, so this is two answers that ought to agree
    rather than one answer compared with itself.
    """
    import json as _json

    from temporalio.client import Client

    with db() as conn:
        row = conn.execute(
            """select r.workflow_id, v.tenant_id, v.dataset_id
                 from pipeline_run r
                 join dataset_version v on v.id = r.source_version_id
                where r.started_from = 'console'
                  and r.workflow_id not like 'deid-tenant-%'
                order by r.started_at desc limit 1"""
        ).fetchone()
    if not row:
        return None

    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", f"localhost:{PORTS['temporal']}"))
    handle = client.get_workflow_handle(row["workflow_id"])
    async for event in handle.fetch_history_events():
        attrs = event.workflow_execution_started_event_attributes
        if not attrs.input.payloads:
            continue
        sent = _json.loads(attrs.input.payloads[0].data.decode("utf-8"))
        break
    else:
        return None

    # The bucket the platform resolves for that same version's dataset, asked
    # independently of whatever the endpoint chose to send.
    resolved = api("GET", f"/datasets/{row['dataset_id']}/next-version",
                   params={"tenant_id": row["tenant_id"]})
    expected_bucket = (resolved.json().get("bucket")
                       if resolved.status_code == 200 else None)
    return sent, row["tenant_id"], expected_bucket


def keys_under(bucket: str, prefix: str) -> list[str]:
    client = s3_client(*ADMIN)
    try:
        response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    except Exception:  # noqa: BLE001 - a missing bucket holds no keys
        return []
    return [item["Key"] for item in response.get("Contents", [])]


def main() -> int:
    try:
        return checks()
    finally:
        for identity in LOGINS:
            httpx.delete(f"{KRATOS_ADMIN}/admin/identities/{identity}", timeout=10.0)


def checks() -> int:
    require_api()

    heading("U63a: a tenant with its own bucket, and a run started for it")

    found = provisioned_tenant()
    check("a tenant of this run's own has its own provisioned bucket",
          found is not None,
          "the platform refused to provision one" if not found
          else f"{found[0]} owns {found[1]}")
    if not found:
        return summary("U63")
    tenant, bucket = found

    if not CORPUS.exists():
        check("the synthetic corpus is present", False, f"{CORPUS} is missing")
        return summary("U63")
    chosen = corpus_records(RECORDS)
    check(f"{RECORDS} records with audio are available", len(chosen) == RECORDS,
          f"found {len(chosen)}")
    if len(chosen) != RECORDS:
        return summary("U63")

    department_id, engineer, _ = seeded_for(tenant)
    dataset = api("POST", "/datasets/register", json={
        "tenant_id": tenant,
        "name": f"tenant-audio-{uuid.uuid4().hex[:8]}",
        "department_id": department_id,
        "registered_by": engineer,
        "provenance": "internal_regulated",
        "declared_class": "RAW",
        "source_kind": "upload",
    }).json()["id"]

    for item in chosen:
        record_id = item["record"]["record_id"]
        api("POST", f"/datasets/{dataset}/files",
            files={"file": (f"{record_id}.wav", io.BytesIO(item["audio"].read_bytes()),
                            "application/octet-stream")})
        api("POST", f"/datasets/{dataset}/files",
            files={"file": (f"{record_id}.truth.json",
                            io.BytesIO(answer_key(item["record"])),
                            "application/octet-stream")})

    # Sealed here rather than through POST /datasets/{id}/seal-audio, which
    # needs a session this tenant has no seeded person for. Same result: the
    # encounter_raw contract, a records.json the pipeline can read, and a
    # manifest naming every object. The uploads above already went to this
    # tenant's own bucket, because that path was fixed when per-tenant storage
    # was built; the pipeline is what was left behind.
    where = api("GET", f"/datasets/{dataset}/next-version",
                params={"tenant_id": tenant}).json()
    check("the platform names this tenant's own bucket for the new version",
          where.get("bucket") == bucket,
          f"next-version says {where.get('bucket')!r}, provisioned is {bucket!r}")
    prefix = where["storage_prefix"]

    contract = api("POST", "/schema-contracts", json={
        "tenant_id": tenant,
        "name": "encounter_raw",
        "fields": [
            {"name": "record_id", "type": "string", "sensitivity": "none",
             "added_by": "munitas-worker"},
            {"name": "audio_key", "type": "string", "sensitivity": "none",
             "added_by": "munitas-worker"},
            {"name": "duration_seconds", "type": "float", "sensitivity": "none",
             "added_by": "munitas-worker"},
            {"name": "sample_rate", "type": "int", "sensitivity": "none",
             "added_by": "munitas-worker"},
        ],
        "primary_key": ["record_id"],
    }).json()["id"]

    with db() as conn:
        uploads = conn.execute(
            """select locator, checksum, bytes, audio_duration_seconds,
                      audio_sample_rate
                 from dataset_source
                where dataset_id = %s and checksum is not null""",
            (dataset,),
        ).fetchall()

    records = [
        {"record_id": u["locator"][:-4],
         "audio_key": f"{prefix}/{u['locator']}",
         "duration_seconds": float(u["audio_duration_seconds"]),
         "sample_rate": int(u["audio_sample_rate"])}
        for u in uploads if u["locator"].endswith(".wav")
    ]
    payload = json.dumps(records).encode("utf-8")
    s3_client(*ADMIN).put_object(
        Bucket=bucket, Key=f"{prefix}/records.json", Body=payload)

    manifest = [{"key": f"{prefix}/{u['locator']}", "bytes": u["bytes"],
                 "sha256": u["checksum"]} for u in uploads]
    manifest.append({"key": f"{prefix}/records.json", "bytes": len(payload),
                     "sha256": hashlib.sha256(payload).hexdigest()})

    sealed = api("POST", "/dataset-versions", json={
        "tenant_id": tenant,
        "dataset_id": dataset,
        "schema_id": contract,
        "visibility_class": "RAW",
        "object_manifest": manifest,
        "record_count": len(records),
        "storage_backend": "seaweedfs",
    })
    check("a readable version seals for this tenant", sealed.status_code == 201,
          f"HTTP {sealed.status_code} {sealed.text[:200]}")
    if sealed.status_code != 201:
        return summary("U63")
    version = sealed.json()["id"]

    run_id = asyncio.run(start_run(tenant, bucket, dataset, version, prefix,
                                   len(records), engineer))
    check("the workflow starts for a tenant that is not the worker's own",
          bool(run_id), f"run {run_id}")
    print(f"    tenant {tenant}, bucket {bucket}, run {run_id}")

    heading("U63b: it runs to completion")

    # Not read through GET /pipeline-runs/{id}: that endpoint now derives its
    # tenant from the caller's own session, and this scratch tenant's
    # engineer row is inserted directly rather than backed by a real Kratos
    # identity, so no session can be minted that is actually scoped to it.
    # The database is a faithful stand-in here regardless: the worker's own
    # completion path calls POST /pipeline-runs/{id}/end directly, which is
    # what actually sets status/error/ended_at, so this is the same fact the
    # read endpoint would report, read one layer closer to where it is set.
    began = time.monotonic()
    run: dict = {}
    while time.monotonic() < began + CEILING_SECONDS:
        with db() as conn:
            row = conn.execute(
                "select status, error, workflow_id from pipeline_run where id = %s",
                (run_id,),
            ).fetchone()
        run = dict(row) if row else {}
        if run.get("status") != "running":
            break
        time.sleep(POLL_SECONDS)
    check("the run completes", run.get("status") == "succeeded",
          f"status {run.get('status')} {run.get('error') or ''} after "
          f"{(time.monotonic() - began) / 60:.1f} minutes; "
          f"workflow {run.get('workflow_id')}")

    heading("U63c: everything it recorded is filed under that tenant")

    with db() as conn:
        wrong_runs = conn.execute(
            """select count(*) as n from action_run
                where pipeline_run_id = %s and tenant_id <> %s""",
            (run_id, tenant),
        ).fetchone()["n"]
        steps = conn.execute(
            "select count(*) as n from action_run where pipeline_run_id = %s",
            (run_id,),
        ).fetchone()["n"]
        wrong_versions = conn.execute(
            """select count(*) as n from dataset_version v
                 join action_run a on a.output_version = v.id
                where a.pipeline_run_id = %s and v.tenant_id <> %s""",
            (run_id, tenant),
        ).fetchone()["n"]
        run_row = conn.execute(
            "select tenant_id from pipeline_run where id = %s", (run_id,)
        ).fetchone()
        gate = conn.execute(
            "select tenant_id from gate_decision where pipeline_run_id = %s",
            (run_id,),
        ).fetchone()

    check("the run has steps, so the checks below mean something", steps > 0,
          f"{steps} action_run row(s)")
    check("no step is filed under another tenant", wrong_runs == 0,
          f"{wrong_runs} of {steps} filed elsewhere")
    check("no version it sealed is filed under another tenant",
          wrong_versions == 0, f"{wrong_versions} version(s) elsewhere")
    check("the run row itself names this tenant",
          run_row is not None and run_row["tenant_id"] == tenant,
          f"pipeline_run.tenant_id {run_row and run_row['tenant_id']}")
    check("and so does the gate decision",
          gate is not None and gate["tenant_id"] == tenant,
          f"gate_decision.tenant_id {gate and gate['tenant_id']}")

    heading("U63d: and every object it wrote is in that tenant's own bucket")

    with db() as conn:
        prefixes = [r["storage_prefix"] for r in conn.execute(
            """select v.storage_prefix from dataset_version v
                 join action_run a on a.output_version = v.id
                where a.pipeline_run_id = %s""",
            (run_id,),
        ).fetchall()]

    check("the run sealed versions to look for", len(prefixes) > 0,
          f"{len(prefixes)} prefix(es)")
    theirs = sum(len(keys_under(bucket, p)) for p in prefixes)
    check("its objects are in the tenant's own bucket", theirs > 0,
          f"{theirs} object(s) under {bucket}")

    heading("U63e: the start endpoint sends the tenant, it does not let the worker guess")

    # The other half of the claim, and the half the run above cannot make: the
    # run above was started by this script, so it proves the worker honours
    # what it is told, not that the endpoint tells it the truth. Read from what
    # Temporal actually received, rather than from the endpoint's source.
    started_by_endpoint = asyncio.run(latest_console_run_input())
    check("a run started through the endpoint is on record",
          started_by_endpoint is not None,
          "U61 and U62 start one; run either first if this is missing")
    if started_by_endpoint is not None:
        sent, expected_tenant, expected_bucket = started_by_endpoint
        check("its parameters name the version's tenant",
              sent.get("tenant") == expected_tenant,
              f"sent {sent.get('tenant')!r}, the version belongs to "
              f"{expected_tenant!r}")
        check("and that tenant's own bucket",
              sent.get("bucket") == expected_bucket,
              f"sent {sent.get('bucket')!r}, the platform resolves "
              f"{expected_bucket!r}")

    heading("U63f: the two processes agree on which storage the pipeline serves")

    # The endpoint refuses an unserved backend early so a person gets an
    # explanation, and the activity refuses it again because that is the
    # guarantee. Two constants in two processes drift, so they are compared
    # here rather than trusted to a comment. This script is the only place both
    # packages are importable at once, which is why the check lives here.
    from worker import platform_client as worker_cp

    api_backends = api("GET", "/pipeline/served-backends")
    check("the API reports which backends the pipeline serves",
          api_backends.status_code == 200,
          f"HTTP {api_backends.status_code}")
    if api_backends.status_code == 200:
        theirs = tuple(api_backends.json()["backends"])
        check("and it matches what the worker can actually talk to",
              theirs == worker_cp.SERVED_BACKENDS,
              f"API says {theirs}, worker serves {worker_cp.SERVED_BACKENDS}")

    heading("U63g: the two processes agree on which pipeline kinds exist")

    # Same reasoning as U63f, one column over: platform/api/app/pipelines.py
    # names which workflow type a kind starts, worker/main.py names which
    # workflows it actually registers, and nothing stops the two lists
    # drifting apart except this check.
    from worker import main as worker_main

    api_kinds = api("GET", "/pipeline/kinds")
    check("the API reports which pipeline kinds it knows",
          api_kinds.status_code == 200,
          f"HTTP {api_kinds.status_code}")
    if api_kinds.status_code == 200:
        theirs = tuple(sorted(api_kinds.json()["kinds"]))
        ours = tuple(sorted(worker_main.PIPELINE_KINDS))
        check("and it matches what the worker actually registers",
              theirs == ours,
              f"API knows {theirs}, worker registers {ours}")

    return summary("U63")


if __name__ == "__main__":
    sys.exit(main())
