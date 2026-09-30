"""U60: audio arrives as audio, and is sealed as a version the pipeline can read.

The claim is not that a file was stored. It is that the platform read the file
and can therefore say what it is: an uploaded version sealed by this path
carries the encounter_raw contract, and its duration and sample rate came from
the bytes rather than from anything the caller asserted.

Derived at upload rather than by a later job because that is the one moment the
platform holds the data and has an unambiguous right to look at it. Afterwards a
reader needs a credential, and POST /credentials is scoped to a sealed dataset
version, which does not exist while somebody is still deciding whether to seal.

Per assertion, never in aggregate.

    docker compose exec -T munitas-api python /verify/v60_audio_prepare.py
"""

from __future__ import annotations

import io
import json
import sys
import uuid

from common import (CANARY, ENGINEER, api, bearer_for, check, db, heading,
                    require_api, summary, tiny_wav)

TENANT = CANARY


def department(name: str) -> tuple[str, str]:
    with db() as conn:
        row = conn.execute(
            "select id, custodian from department where tenant_id = %s and name = %s",
            (TENANT, name),
        ).fetchone()
    if not row:
        raise RuntimeError(
            f"department {name!r} is missing from tenant {TENANT!r}. Apply "
            "infra/postgres/seed-canary.sql."
        )
    return str(row["id"]), row["custodian"]


def register(department_id: str) -> str:
    body = {
        "tenant_id": TENANT,
        "name": f"audio-{uuid.uuid4().hex[:8]}",
        "department_id": department_id,
        "registered_by": ENGINEER,
        "provenance": "internal_regulated",
        "declared_class": "RAW",
        "source_kind": "upload",
    }
    return api("POST", "/datasets/register", json=body).json()["id"]


def put(dataset_id: str, name: str, payload: bytes):
    return api(
        "POST",
        f"/datasets/{dataset_id}/files",
        files={"file": (name, io.BytesIO(payload), "application/octet-stream")},
    )


def reasons_of(response) -> list[str]:
    """The refusal's own reasons, or nothing.

    Looked up here rather than searched for in the raw body, because a
    filename appears in a success response too. A check that greps the whole
    body for a name passes whether or not the request was refused, which is a
    check that cannot fail.
    """
    if response.status_code < 400:
        return []
    try:
        detail = response.json().get("detail")
    except Exception:  # noqa: BLE001 - a non-JSON error body carries no reasons
        return []
    if isinstance(detail, dict):
        return [str(r) for r in detail.get("reasons", [])]
    return [str(detail)] if detail else []


def answer_key(with_hazard: bool = True) -> bytes:
    body = {
        "spans": [{"entity": "PERSON", "start": 0, "end": 5, "text": "Aoife"}],
        "reference_transcript": "Aoife attended on Tuesday",
    }
    if with_hazard:
        body["hazard"] = False
    return json.dumps(body).encode("utf-8")


def main() -> int:
    require_api()
    owning, _ = department("Verification")

    heading("U60a: the platform reads the audio it is given")

    dataset = register(owning)
    # Built here, so the expected numbers come from what was written rather
    # than from what the platform later says about it. Two measurements that
    # ought to agree is the only honest check available.
    expected_rate = 16000
    expected_seconds = 0.25
    response = put(dataset, "synth-0000.wav", tiny_wav(expected_seconds, expected_rate))
    check("a real wav uploads", response.status_code == 201,
          f"HTTP {response.status_code}")
    facts = response.json()
    check("the sample rate is derived from the bytes",
          facts.get("audio_sample_rate") == expected_rate,
          f"reported {facts.get('audio_sample_rate')}, wrote {expected_rate}")
    check("the duration is derived from the bytes",
          abs((facts.get("audio_duration_seconds") or 0) - expected_seconds) < 0.01,
          f"reported {facts.get('audio_duration_seconds')}, wrote {expected_seconds}")

    with db() as conn:
        row = conn.execute(
            """select audio_duration_seconds, audio_sample_rate from dataset_source
               where dataset_id = %s and locator = %s""",
            (dataset, "synth-0000.wav"),
        ).fetchone()
    check("the derived facts are stored, not only returned",
          row is not None and row["audio_sample_rate"] == expected_rate,
          f"row says {row and row['audio_sample_rate']}")

    heading("U60b: a file that contradicts its own name is refused")

    refused = put(dataset, "broken.wav", b"pretend audio bytes")
    check("a wav that is not audio is refused", refused.status_code == 400,
          f"HTTP {refused.status_code}")
    check("the refusal names the file",
          any("broken.wav" in r for r in reasons_of(refused)),
          f"reasons {reasons_of(refused)}")

    with db() as conn:
        left = conn.execute(
            "select count(*) as n from dataset_source where dataset_id = %s and locator = %s",
            (dataset, "broken.wav"),
        ).fetchone()
    check("a refused upload leaves no ledger row", left["n"] == 0,
          f"{left['n']} row(s)")

    heading("U60c: an answer key must carry what scoring reads")

    ok = put(dataset, "synth-0000.truth.json", answer_key())
    check("a complete answer key uploads", ok.status_code == 201,
          f"HTTP {ok.status_code}")
    check("the response says the hazard flag is present",
          ok.json().get("truth_has_hazard") is True,
          f"reported {ok.json().get('truth_has_hazard')}")

    partial = json.dumps({"spans": []}).encode("utf-8")
    bad = put(dataset, "synth-0001.truth.json", partial)
    check("an answer key missing reference_transcript is refused",
          bad.status_code == 400, f"HTTP {bad.status_code}")
    check("the refusal names the missing key",
          any("reference_transcript" in r for r in reasons_of(bad)),
          f"reasons {reasons_of(bad)}")

    hazardless = put(dataset, "synth-0002.truth.json", answer_key(with_hazard=False))
    check("an answer key with no hazard flag still uploads",
          hazardless.status_code == 201, f"HTTP {hazardless.status_code}")
    check("and the response says the hazard flag is absent",
          hazardless.json().get("truth_has_hazard") is False,
          f"reported {hazardless.json().get('truth_has_hazard')}")

    heading("U60d: an upload can be withdrawn without being erased")

    withdrawable = register(owning)
    put(withdrawable, "keep-0000.wav", tiny_wav())
    put(withdrawable, "drop-0000.wav", tiny_wav())
    with db() as conn:
        doomed = conn.execute(
            "select id from dataset_source where dataset_id = %s and locator = %s",
            (withdrawable, "drop-0000.wav"),
        ).fetchone()

    # A real Kratos session, because the endpoint takes the acting person from
    # the session rather than from the body. Recording who withdrew a file is
    # the point of keeping the row, so a claimed name would defeat it.
    engineer = bearer_for(ENGINEER)
    unauthenticated = api(
        "POST", f"/datasets/{withdrawable}/uploads/{doomed['id']}/withdraw")
    check("withdrawing without a session is refused",
          unauthenticated.status_code == 401,
          f"HTTP {unauthenticated.status_code}")

    gone = api("POST", f"/datasets/{withdrawable}/uploads/{doomed['id']}/withdraw",
               headers=engineer)
    check("withdrawing with a session is accepted", gone.status_code == 200,
          f"HTTP {gone.status_code}")

    with db() as conn:
        row = conn.execute(
            "select withdrawn_at, withdrawn_by from dataset_source where id = %s",
            (doomed["id"],),
        ).fetchone()
    check("the row survives the withdrawal", row is not None,
          "the ledger still shows the file was uploaded")
    check("the withdrawal records who did it, from the session",
          row is not None and row["withdrawn_by"] == ENGINEER,
          f"withdrawn_by {row and row['withdrawn_by']}, expected {ENGINEER}")

    sealed = api("POST", f"/datasets/{withdrawable}/seal")
    check("the generic seal succeeds", sealed.status_code == 201,
          f"HTTP {sealed.status_code}")
    # Read from the database, not from the response. versions.seal returns only
    # id, version, storage_prefix, content_hash and sealed, so asking the
    # response for a manifest yields an empty list and every membership test
    # below would pass without looking at anything.
    keys: list[str] = []
    if sealed.status_code == 201:
        with db() as conn:
            manifest = conn.execute(
                "select object_manifest from dataset_version where id = %s",
                (sealed.json()["id"],),
            ).fetchone()["object_manifest"]
        keys = [entry["key"] for entry in manifest]
    check("the manifest is not empty, so the two checks below mean something",
          len(keys) > 0, f"{len(keys)} object(s)")
    check("the generic seal leaves a withdrawn file out",
          not any(k.endswith("drop-0000.wav") for k in keys), f"manifest {keys}")
    check("and still includes the kept one",
          any(k.endswith("keep-0000.wav") for k in keys), f"manifest {keys}")

    heading("U60e: sealing as audio, or refusing and sealing nothing")

    good = register(owning)
    put(good, "synth-0000.wav", tiny_wav(0.25, 16000))
    put(good, "synth-0000.truth.json", answer_key())
    put(good, "synth-0001.wav", tiny_wav(0.5, 16000))

    orphan = register(owning)
    put(orphan, "synth-0000.wav", tiny_wav())
    put(orphan, "synth-0009.truth.json", answer_key())
    put(orphan, "notes.txt", b"a file that is neither kind")

    refusal = api("POST", f"/datasets/{orphan}/seal-audio", headers=engineer)
    check("an incoherent set is refused", refusal.status_code == 400,
          f"HTTP {refusal.status_code}")
    said = reasons_of(refusal)
    check("the refusal names the unpaired answer key",
          any("synth-0009.truth.json" in r for r in said), f"reasons {said}")
    check("the refusal also names the unrecognised file, in the same response",
          any("notes.txt" in r for r in said), f"reasons {said}")
    with db() as conn:
        made = conn.execute(
            "select count(*) as n from dataset_version where dataset_id = %s",
            (orphan,),
        ).fetchone()
    check("a refusal seals nothing", made["n"] == 0, f"{made['n']} version(s)")

    sealed = api("POST", f"/datasets/{good}/seal-audio", headers=engineer)
    check("a coherent set seals", sealed.status_code == 201,
          f"HTTP {sealed.status_code} {sealed.text[:160]}")

    # Every assertion below reads the row, not the response. versions.seal
    # returns only id, version, storage_prefix, content_hash and sealed, so
    # asking the response for a class or a manifest would compare against None
    # and an empty list, and a check that cannot fail is worse than no check.
    row = None
    if sealed.status_code == 201:
        with db() as conn:
            row = conn.execute(
                """select dv.visibility_class, dv.storage_prefix, dv.record_count,
                          dv.object_manifest, sc.name as contract
                     from dataset_version dv
                     join schema_contract sc on sc.id = dv.schema_id
                   where dv.id = %s""",
                (sealed.json()["id"],),
            ).fetchone()
    check("the sealed version is RAW",
          row is not None and row["visibility_class"] == "RAW",
          f"class {row and row['visibility_class']}")
    check("it carries encounter_raw, not uploaded_files",
          row is not None and row["contract"] == "encounter_raw",
          f"contract {row and row['contract']}")

    heading("U60f: the version holds what the pipeline will look for")

    prefix = row["storage_prefix"] if row else ""
    keys = [entry["key"] for entry in row["object_manifest"]] if row else []
    check("the manifest is not empty, so the checks below mean something",
          len(keys) > 0, f"{len(keys)} object(s)")
    check("records.json sits at the version prefix",
          f"{prefix}/records.json" in keys, f"{len(keys)} objects")
    check("the answer key sits exactly where verify will read it",
          f"{prefix}/synth-0000.truth.json" in keys,
          "verify reads {truth_prefix}/{record_id}.truth.json")
    check("each uploaded file appears once",
          len(keys) > 0 and len(keys) == len(set(keys)),
          f"{len(keys)} keys, {len(set(keys))} distinct")
    check("the record count is the number of audio files, not of objects",
          row is not None and row["record_count"] == 2,
          f"record_count {row and row['record_count']}")

    heading("U60g: the contract the API registers matches the one the worker uses")

    # The contract this version actually points at, not "the one named
    # encounter_raw in this tenant". Canary accumulates artefacts on purpose,
    # so a check that assumes a pristine count would report a stale row from
    # some earlier run as a defect in this one.
    with db() as conn:
        registered = conn.execute(
            """select sc.fields, sc.primary_key from dataset_version dv
                 join schema_contract sc on sc.id = dv.schema_id
               where dv.id = %s""",
            (sealed.json()["id"],),
        ).fetchall() if sealed.status_code == 201 else []
    check("the sealed version points at a contract", len(registered) == 1,
          f"{len(registered)} row(s)")
    shape = {(f["name"], f["type"]) for f in registered[0]["fields"]} if registered else set()
    # worker/contracts.py RAW_AUDIO, restated here on purpose: the point of the
    # check is that two independently written definitions agree.
    expected = {("record_id", "string"), ("audio_key", "string"),
                ("duration_seconds", "float"), ("sample_rate", "int")}
    check("its fields match worker/contracts.py RAW_AUDIO",
          shape == expected, f"registered {sorted(shape)}")

    # The check that actually matters. The two above compare the API's
    # registration against a list restated in this file, which is close to
    # comparing the API with itself. This one replays exactly what the worker
    # sends, worker/contracts.py's Contract.as_payload with FieldSpec.as_dict,
    # and asserts the platform hands back the row that already exists. If the
    # two ever computed the digest differently, encounter_raw would quietly
    # exist twice under one name and the pipeline and the console would each
    # hold a different id for it.
    as_the_worker_sends = {
        "tenant_id": TENANT,
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
    }
    def encounter_raw_rows() -> int:
        with db() as conn:
            return conn.execute(
                """select count(*) as n from schema_contract
                   where tenant_id = %s and name = 'encounter_raw'""",
                (TENANT,),
            ).fetchone()["n"]

    before = encounter_raw_rows()
    replayed = api("POST", "/schema-contracts", json=as_the_worker_sends)
    check("the worker's own registration finds the existing row",
          replayed.status_code in (200, 201)
          and replayed.json().get("created") is False,
          f"created {replayed.json().get('created')} "
          f"(HTTP {replayed.status_code})")
    check("so it adds no second encounter_raw under the same name",
          encounter_raw_rows() == before,
          f"{before} row(s) before, {encounter_raw_rows()} after")

    return summary("U60")


if __name__ == "__main__":
    sys.exit(main())
