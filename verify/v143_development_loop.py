"""U145: the development loop, run for real: a pipeline version in development runs on a sample drawn for the engineer, and on nothing else.

U144 proves the platform's half. This proves what it is for. An engineer uploads a pipeline version whose script step reads files. The version is in
development, so a run on the real files is refused. The engineer asks for a sample, an approver decides, the platform draws it, and the same version
is then run for real in the sandbox worker's container over the sample:

  * the step is told of exactly the sample's files, and of no others, and what it reads is the original sound byte for byte, though the sample holds
    no audio of its own: it names the stored files the original already names;
  * a version that writes files also works in development, and what it writes is sealed "under review" like any step's output;
  * when the sample is ended, a new run on it is refused at the start;
  * a patient erased after the sample was drawn is left out of the next run on it.

It runs on the host, because the script runs in the sandbox worker's Docker.

    $env:MUNITAS_PG_DSN = "postgresql://munitas:munitas@localhost:5432/platform"
    $env:MUNITAS_VERIFY_KRATOS = "http://localhost:4433"
    $env:MUNITAS_S3_ENDPOINT = "http://localhost:8333"
    .venv\\Scripts\\python.exe verify\\v143_development_loop.py

It needs `python -m worker.main` and `python -m worker.sandbox_worker` running, and the step gateway container up. About four minutes.
"""

from __future__ import annotations

import hashlib
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from common import ADMIN, API, CANARY, api, bearer_for, bucket_for, check, db, fixture_tenant, heading, require_api, s3_client, summary  # noqa: E402
from v128_person_keys_erasure import CUSTODIAN, ENGINEER, register_dataset, reset_keys  # noqa: E402
from v137_locked_files import make_wav, upload_whole  # noqa: E402
from v141_custom_step_given_locked_files import SCRIPT, WRITER_SCRIPT, WRITER_YAML, YAML, locked_size, run_and_wait, silenced, zipped  # noqa: E402


def upload_in_development(dept: str, yaml_text: str, name: str, script: str, headers: dict) -> tuple[str, str]:
    """A pipeline with one version, left in development."""
    made = api("POST", "/pipelines/register", json={"tenant_id": CANARY, "name": name, "department_id": dept, "registered_by": ENGINEER}, headers=headers)
    pipeline_id = made.json()["id"]
    up = httpx.post(f"{API}/pipelines/{pipeline_id}/versions/upload", data={"registered_by": ENGINEER}, headers=headers, timeout=30.0,
                    files={"config_file": ("pipeline.yaml", yaml_text.encode(), "application/x-yaml"), "scripts": ("scripts.zip", zipped("steps/silence.py" if "silence" in yaml_text else "steps/inspect.py", script), "application/zip")})
    assert up.status_code == 201 and up.json()["stage"] == "development", up.text[:200]
    return pipeline_id, up.json()["id"]


def sample_dataset_of(version_id: str) -> str:
    with db() as conn:
        return str(conn.execute("select dataset_id from dataset_version where id = %s", (version_id,)).fetchone()["dataset_id"])


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    engineer = bearer_for(ENGINEER)
    custodian = bearer_for(CUSTODIAN)
    tag = uuid.uuid4().hex[:8]
    with db() as conn:
        dept = str(conn.execute("select id from department where tenant_id = %s and name = 'Verification'", (CANARY,)).fetchone()["id"])

    heading("Six files from five patients, locked")
    patients = [f"L-{tag}-{i}-chart" for i in range(5)]
    plan = [("s-0001", 0), ("s-0002", 0), ("s-0003", 1), ("s-0004", 2), ("s-0005", 3), ("s-0006", 4)]
    wavs = {record: make_wav(150_000 + n * 70_000) for n, (record, _) in enumerate(plan)}
    dataset = register_dataset(ENGINEER)
    for record, p in plan:
        upload_whole(dataset, f"{record}.wav", wavs[record], patients[p])
    sealed = api("POST", f"/datasets/{dataset}/seal-files", headers=engineer)
    check("they seal", sealed.status_code == 201, sealed.text[:160])
    version = sealed.json()["id"]
    with db() as conn:
        owners = {r["record_id"]: r["person_code"] for r in conn.execute("select record_id, person_code from file_person where dataset_version_id = %s", (version,)).fetchall()}

    heading("A version in development cannot run on the real files")
    _, dev_pv = upload_in_development(dept, YAML, f"hash-dev-{tag}", SCRIPT, engineer)
    refused = api("POST", f"/datasets/{dataset}/versions/{version}/deidentify", json={"pipeline_kind": "dag", "pipeline_version_id": dev_pv}, headers=engineer)
    check("the run is refused at the start, and says what it may run on", refused.status_code == 400 and "development data" in refused.text and "Ask for a sample" in refused.text,
          f"{refused.status_code} {refused.text[:240]}")

    heading("A sample is asked for, approved, and drawn")
    asked = api("POST", f"/dataset-versions/{version}/development-samples", json={"kind": "develop", "purpose": "tune the step", "days": 5, "records": 4}, headers=engineer)
    check("the engineer asks for four files", asked.status_code == 201, f"{asked.status_code} {asked.text[:160]}")
    decided = api("POST", f"/development-samples/{asked.json()['id']}/approve", json={"records": 3, "days": 3}, headers=custodian)
    check("the department's approver approves three, for three days", decided.status_code == 200 and decided.json()["granted_count"] == 3, f"{decided.status_code} {decided.text[:200]}")
    sample_version = decided.json()["sample_version_id"]
    with db() as conn:
        chosen = sorted(conn.execute("select chosen_records from development_sample where id = %s", (asked.json()["id"],)).fetchone()["chosen_records"])
    check("the three are of three different patients", len({owners[r] for r in chosen}) == 3, str(chosen))
    check("and not all six files are in it", len(chosen) == 3 and set(chosen) < set(owners))

    heading("The version in development runs on the sample, for real, in its closed container")
    run, out = run_and_wait(sample_dataset_of(sample_version), sample_version, dev_pv, engineer)
    seen = {x["record_id"]: x for x in out.get("seen", [])}
    check("the step was told of the sample's three files and no others", sorted(seen) == chosen, f"{sorted(seen)} {chosen}")
    for record in chosen:
        want = hashlib.sha256(wavs[record]).hexdigest()
        got = seen.get(record, {})
        check(f"{record}: read as a file, as bytes and saved to its own scratch space, it is the original sound, byte for byte, though the sample holds no audio of its own",
              got.get("streamed") == want and got.get("whole") == want and got.get("saved") == want and got.get("bytes") == len(wavs[record]), str(got)[:200])
    with db() as conn:
        opened = [r["purpose"] for r in conn.execute("select purpose from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s", (CANARY, f"step_run {run.get('id')}")).fetchall()]
    check("every opening is in the key log, naming the step and the pipeline: three files, three ways each", len(opened) == 9 and all(f"custom step inspect of pipeline hash-dev-{tag} version 1" in p for p in opened), f"{len(opened)}")

    heading("A version that writes files also works in development, and what it writes waits for a review")
    _, writer_pv = upload_in_development(dept, WRITER_YAML, f"silence-dev-{tag}", WRITER_SCRIPT, engineer)
    wrun, wout = run_and_wait(sample_dataset_of(sample_version), sample_version, writer_pv, engineer, step="silence")
    check("it wrote one file for each file of the sample", sorted(wout.get("done", [])) == chosen and wout.get("files_written") == 3, str(wout)[:200])
    with db() as conn:
        made = conn.execute("select v.visibility_class, v.object_manifest from dataset_version v where v.id = %s", (wout.get("output_version_id"),)).fetchone()
    check("sealed under review, so nobody may use it until a person has looked", made and made["visibility_class"] == "UNDER_REVIEW", str(made and made["visibility_class"]))
    client = s3_client(*ADMIN)
    files = {m["key"].rsplit("/", 1)[1]: m["key"] for m in made["object_manifest"] if m["key"].endswith(".silenced.wav")}
    blobs = {r: client.get_object(Bucket=bucket_for(CANARY), Key=files[f"{r}.silenced.wav"])["Body"].read() for r in chosen} if sorted(files) == [f"{r}.silenced.wav" for r in chosen] else {}
    check("and what it wrote is locked under each patient's key: the size of the silenced sound plus the lock, and none of the sound in it",
          sorted(blobs) == chosen and all(len(blobs[r]) == locked_size(len(silenced(wavs[r]))) and silenced(wavs[r])[2000:2064] not in blobs[r] and not blobs[r].startswith(b"RIFF") for r in chosen))

    heading("The engineer opens what the step wrote, and the sample's own files, through the platform")

    def opened(version_id: str, record: str) -> bytes:
        got = api("POST", f"/dataset-versions/{version_id}/records/{record}/open", headers=engineer)
        assert got.status_code == 200, (got.status_code, got.text[:200])
        return httpx.get(f"{API}/record-stream/{got.json()['handle']}", timeout=60.0).content

    listing_out = api("GET", f"/dataset-versions/{wout.get('output_version_id')}/records", headers=engineer).json()
    check("the platform says the engineer may open the files of the version their run made, because their run made it on their sample",
          listing_out["basis"] == "own_development_output" and listing_out["may_open"] is True and sorted(r["record_id"] for r in listing_out["records"]) == [f"{r}.silenced.wav" for r in chosen], str(listing_out)[:240])
    check("each file the step wrote opens, unlocked inside the platform, to the original sound with its first half second silenced, byte for byte",
          all(opened(wout.get("output_version_id"), f"{r}.silenced.wav") == silenced(wavs[r]) for r in chosen))
    check("and the sound really did change, and only at the start", all(opened(wout.get("output_version_id"), f"{r}.silenced.wav") != wavs[r]
                                                                     and opened(wout.get("output_version_id"), f"{r}.silenced.wav")[44 + 16000:] == wavs[r][44 + 16000:] for r in chosen[:1]))
    check("the sample's own files open to the original sound, though the sample holds no audio of its own", all(opened(sample_version, r) == wavs[r] for r in chosen))
    custodian_view = api("GET", f"/dataset-versions/{wout.get('output_version_id')}/records", headers=custodian).json()
    check("the approver, who did not run it, may not open what it made", custodian_view["may_open"] is False and custodian_view["basis"] == "none")

    heading("When the sample is ended, a new run on it is refused at the start")
    ended = api("POST", f"/development-samples/{asked.json()['id']}/end", headers=custodian)
    check("the approver ends the sample", ended.status_code == 200 and ended.json()["status"] == "ended", ended.text[:160])
    again = api("POST", f"/datasets/{sample_dataset_of(sample_version)}/versions/{sample_version}/deidentify", json={"pipeline_kind": "dag", "pipeline_version_id": dev_pv}, headers=engineer)
    check("a run on it is refused, and says what it may run on", again.status_code == 400 and "development data" in again.text, f"{again.status_code} {again.text[:200]}")

    heading("A patient erased after the sample was drawn is left out of the next run on it")
    asked2 = api("POST", f"/dataset-versions/{version}/development-samples", json={"kind": "develop", "purpose": "tune the step", "days": 5, "records": 4}, headers=engineer)
    decided2 = api("POST", f"/development-samples/{asked2.json()['id']}/approve", json={"records": 3, "days": 3}, headers=custodian)
    second_version = decided2.json()["sample_version_id"]
    with db() as conn:
        chosen2 = sorted(conn.execute("select chosen_records from development_sample where id = %s", (asked2.json()["id"],)).fetchone()["chosen_records"])
    check("asking again for the same thing is given the same three files", chosen2 == chosen, f"{chosen2} {chosen}")
    victim = chosen2[0]
    with db() as conn:
        conn.execute("update subject_key set wrapped_key = null, destroyed_at = now() where tenant_id = %s and person_code = %s", (CANARY, owners[victim]))
    run2, out2 = run_and_wait(sample_dataset_of(second_version), second_version, dev_pv, engineer)
    check("the step was told that file was erased, and saw only the other two", out2.get("skipped") == [victim] and sorted(x["record_id"] for x in out2.get("seen", [])) == chosen2[1:], str(out2)[:240])
    with db() as conn:
        second = conn.execute("select person_code from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s", (CANARY, f"step_run {run2.get('id')}")).fetchall()
    check("the erased patient's key was never asked for in that run", len(second) == 6 and all(r["person_code"] != owners[victim] for r in second), str(len(second)))

    reset_keys()
    return summary("v143")


if __name__ == "__main__":
    sys.exit(main())
