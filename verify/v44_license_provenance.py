"""U44: a HuggingFace dataset's licence decides what "provenance" means.

`provenance` used to be a human's free choice even for HuggingFace-sourced
data, and `may_export` answered a single binary question regardless of what
the licence actually said. A CC-BY-NC dataset registered as `external_public`
by mistake passed the same gate as CC0 data.

This script proves the fix: `fetch_huggingface()` now looks the real licence
up (a small, static table, not a judgement call) and overwrites `provenance`
with what the licence supports, not what was asked for at registration. The
human's original choice is kept, not discarded, so the correction is visible.
Export then answers two separate questions, because a real licence can be
*fine as fetched, not fine once this platform has modified it*, which is a
no-derivatives licence's actual shape and nothing before this could express.

The fetch itself runs as a background job (U46 covers the machinery); this
script starts one and waits for it, since what matters here is the licence
derivation, not the async plumbing.

Depends on reaching huggingface.co, which most of this suite does not.
Skipped by name, not failed, if unreachable.

    docker compose exec -T munitas-api python /verify/v44_license_provenance.py
"""

from __future__ import annotations

import sys
import time
import uuid

import httpx

from common import (CANARY, ENGINEER, api, bearer_for, check, db,
                    fixture_contract, heading, require_api, skip, summary,
                    tiny_wav)

TENANT = CANARY

# Small, real, and stable enough to depend on for a test: a CC0-licensed
# toy dataset (4 files at the repo root, license confirmed live).
OPEN_REPO, OPEN_PATH, OPEN_FILES, OPEN_LICENSE = "scikit-learn/iris", "", 4, "cc0-1.0"

# The same non-commercial-licensed repo v43 already depends on.
NC_REPO, NC_PATH, NC_FILES, NC_LICENSE = (
    "xhluca/publichealth-qa", "data", 8, "cc-by-nc-sa-3.0",
)

# A HuggingFace test-fixture repo that declares no licence at all.
UNKNOWN_REPO, UNKNOWN_PATH, UNKNOWN_FILES = (
    "hf-internal-testing/fixtures_ade20k", "", 7,
)


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


def register(owning: str, provenance: str, **over) -> "object":
    body = {
        "tenant_id": TENANT,
        "name": f"license-{uuid.uuid4().hex[:8]}",
        "department_id": owning,
        "registered_by": ENGINEER,
        "provenance": provenance,
        "declared_class": over.pop("declared_class", "RAW"),
    }
    body.update(over)
    return api("POST", "/datasets/register", json=body)


def fetch_and_wait(dataset_id: str, repo: str, path: str, timeout: float = 90.0) -> dict | None:
    started = api(
        "POST", f"/datasets/{dataset_id}/fetch-huggingface",
        json={"fetched_by": ENGINEER, "repo_id": repo, "revision": "main", "path": path},
    )
    if started.status_code != 202:
        return {"status": "failed", "error": f"could not even start: HTTP {started.status_code}"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = api("GET", f"/datasets/{dataset_id}/huggingface-fetch-jobs",
                  headers=bearer_for(ENGINEER)).json()
        if jobs and jobs[0]["status"] != "running":
            return jobs[0]
        time.sleep(2.0)
    return None


def dataset_row(dataset_id: str) -> dict:
    with db() as conn:
        return conn.execute(
            """select provenance, provenance_registered_as, provenance_overridden_at,
                      license_tag, license_export_unmodified, license_export_modified
               from dataset where id = %s""",
            (dataset_id,),
        ).fetchone()


def listed(name: str) -> dict | None:
    """The row the console's Datasets list gets for one dataset."""
    r = api("GET", "/datasets", params={"tenant_id": TENANT, "q": name},
           headers=bearer_for(ENGINEER))
    r.raise_for_status()
    return next((d for d in r.json()["datasets"] if d["name"] == name), None)


def list_carries_licence(owning: str) -> None:
    """The Datasets list says where data came from and what its licence allows.

    Set directly in the database, the same state `hf_ingest_activities` writes
    when a fetched licence corrects a registration, so this runs whether or not
    HuggingFace or the worker is reachable. It also leaves a corrected dataset
    in the canary organisation for the console check (U80) to find.
    """
    heading("U44: the Datasets list carries the licence and any correction")
    corrected_name = f"license-corrected-{uuid.uuid4().hex[:8]}"
    plain_name = f"license-plain-{uuid.uuid4().hex[:8]}"
    corrected = register(owning, "external_public", name=corrected_name).json()
    register(owning, "internal_regulated", name=plain_name).raise_for_status()
    with db() as conn:
        conn.execute(
            """update dataset set license_tag = 'cc-by-nc-sa-3.0',
                      license_export_unmodified = false, license_export_modified = false,
                      provenance_registered_as = provenance, provenance = 'internal_regulated',
                      provenance_overridden_at = now()
                where id = %s""",
            (corrected["id"],),
        )
    stored = dataset_row(corrected["id"])
    got = listed(corrected_name) or {}
    for field in ("provenance", "provenance_registered_as", "license_tag",
                  "license_export_unmodified", "license_export_modified"):
        check(f"the list returns {field} as stored", got.get(field) == stored[field],
              f"list {got.get(field)!r}, stored {stored[field]!r}")
    check("the list returns when the correction happened",
          got.get("provenance_overridden_at") is not None, str(got.get("provenance_overridden_at")))

    got = listed(plain_name) or {}
    check("a dataset nobody corrected shows no correction",
          got.get("provenance_registered_as") is None and got.get("provenance_overridden_at") is None
          and got.get("license_tag") is None, str(got))


def main() -> int:
    require_api()
    list_carries_licence(department("Verification")[0])

    try:
        reachable = httpx.get(
            f"https://huggingface.co/api/datasets/{OPEN_REPO}", timeout=10.0
        ).status_code == 200
    except httpx.HTTPError:
        reachable = False
    if not reachable:
        skip("every check in this script", "huggingface.co is not reachable from here")
        return summary("U44")

    owning, _custodian = department("Verification")

    # -------------------------------------------------------------------
    heading("U44: an openly licensed repo is verified, not assumed")

    open_ds = register(owning, "internal_regulated", declared_class="PUBLISHED")  # deliberately wrong, to see it corrected
    open_id = open_ds.json()["id"]
    open_job = fetch_and_wait(open_id, OPEN_REPO, OPEN_PATH)

    if open_job is None:
        skip("the open-licence fetch job completes",
             "no result within the timeout; is `python -m worker.main` running?")
        return summary("U44")

    check("fetching the open repo succeeds", open_job.get("status") == "succeeded",
          str(open_job))
    check(f"all {OPEN_FILES} files came back",
          open_job.get("files_done") == OPEN_FILES, str(open_job.get("files_done")))

    row = dataset_row(open_id)
    check("the real licence was recorded", row["license_tag"] == OPEN_LICENSE, str(row["license_tag"]))
    check("it grants unmodified export", row["license_export_unmodified"] is True, str(row))
    check("it grants modified export too", row["license_export_modified"] is True, str(row))
    check("provenance was corrected to external_licensed",
          row["provenance"] == "external_licensed", str(row["provenance"]))
    check("the human's original (wrong) choice is kept, not discarded",
          row["provenance_registered_as"] == "internal_regulated", str(row["provenance_registered_as"]))
    check("and the correction is timestamped, not silent",
          row["provenance_overridden_at"] is not None)

    with db() as conn:
        licences = conn.execute(
            "select licence from dataset_source where dataset_id = %s and kind = 'huggingface'",
            (open_id,),
        ).fetchall()
    check("every fetched file's licence is on record",
          len(licences) == OPEN_FILES and all(r["licence"] == OPEN_LICENSE for r in licences),
          f"{len(licences)} rows: {[r['licence'] for r in licences]}")

    sealed = api("POST", f"/datasets/{open_id}/seal")
    check("the open dataset seals", sealed.status_code == 201, f"HTTP {sealed.status_code}")

    exported = api("GET", f"/datasets/{open_id}/export",
                   params={"purpose": "share a toy dataset"},
                   headers=bearer_for(ENGINEER))
    check("an openly licensed, unmodified export is allowed",
          exported.status_code == 200, f"HTTP {exported.status_code}: {exported.text[:200]}")
    if exported.status_code == 200:
        check("the export names the licence it relied on",
              exported.json().get("license_tag") == OPEN_LICENSE, str(exported.json()))
        check("and says the data was not modified",
              exported.json().get("modified") is False, str(exported.json()))

    # -------------------------------------------------------------------
    heading("U44: the same repo, once this platform has modified it")

    # No permanently-tagged no-derivatives repo is reliable to depend on for
    # a test suite (a maintainer could relicense or delete it). The licence
    # lookup itself is already proven above against a real fetch; this part
    # simulates a no-derivatives licence's actual shape (fine as fetched, not
    # fine once modified) by adjusting the one fact a real ND repo would
    # produce, then proving policy honours the distinction, using the same
    # direct-SQL approach v39 already uses to reach a state the API alone
    # cannot set up on demand.
    with db() as conn:
        conn.execute(
            "update dataset set license_export_modified = false where id = %s", (open_id,)
        )

    contract = fixture_contract(TENANT)
    action_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """insert into dataset_action
                 (id, tenant_id, name, source_schema_id, target_schema_id, output_class)
               values (%s, %s, %s, %s, %s, 'PUBLISHED')""",
            (action_id, TENANT, f"license-verify-{action_id[:8]}", contract, contract),
        )
    run = api("POST", "/action-runs", json={
        "tenant_id": TENANT, "action_id": action_id,
        "code_hash": "sha256:u44-codehash", "image_digest": "sha256:u44-imagedigest",
        "operator": "verify-suite", "idempotency_key": f"u44-{uuid.uuid4().hex}",
        "input_versions": [sealed.json()["id"]] if sealed.status_code == 201 else [],
        "trigger_kind": "manual", "triggered_by": ENGINEER,
    })
    check("a pipeline run against the open dataset succeeds",
          run.status_code == 201, f"HTTP {run.status_code}: {run.text[:200]}")

    modified_version = api("POST", "/dataset-versions", json={
        "tenant_id": TENANT, "dataset_id": open_id, "schema_id": contract,
        "visibility_class": "PUBLISHED", "produced_by_run": run.json().get("id"),
        "record_count": 1,
    })
    check("a modified version is recorded against the same dataset",
          modified_version.status_code == 201, f"HTTP {modified_version.status_code}")

    modified_export = api("GET", f"/datasets/{open_id}/export",
                          params={"purpose": "share the modified copy"},
                          headers=bearer_for(ENGINEER))
    check("exporting the modified version is refused",
          modified_export.status_code == 403, f"HTTP {modified_export.status_code}")
    if modified_export.status_code == 403:
        reasons = modified_export.json().get("detail", {}).get("reasons", [])
        check("the refusal names the licence and the modification",
              any("modified" in r and OPEN_LICENSE in r for r in reasons),
              "; ".join(reasons)[:150])

    # -------------------------------------------------------------------
    heading("U44: a non-commercial licence grants export neither way")

    nc_ds = register(owning, "external_public", declared_class="PUBLISHED")  # deliberately wrong
    nc_id = nc_ds.json()["id"]
    nc_job = fetch_and_wait(nc_id, NC_REPO, NC_PATH)

    if nc_job is None:
        skip("the non-commercial-licence fetch job completes",
             "no result within the timeout; is `python -m worker.main` running?")
    else:
        check("fetching the non-commercial repo succeeds",
              nc_job.get("status") == "succeeded", str(nc_job))
        check(f"all {NC_FILES} files came back",
              nc_job.get("files_done") == NC_FILES, str(nc_job.get("files_done")))

        row = dataset_row(nc_id)
        check("the licence was recorded", row["license_tag"] == NC_LICENSE, str(row["license_tag"]))
        check("neither export aspect is granted",
              row["license_export_unmodified"] is False and row["license_export_modified"] is False,
              str(row))
        check("provenance was corrected away from the human's wrong claim",
              row["provenance"] == "internal_regulated" and
              row["provenance_registered_as"] == "external_public",
              str(row))

        api("POST", f"/datasets/{nc_id}/seal")
        nc_export = api("GET", f"/datasets/{nc_id}/export",
                        params={"purpose": "share the benchmark"},
                        headers=bearer_for(ENGINEER))
        check("export of the non-commercial dataset is refused even unmodified",
              nc_export.status_code == 403, f"HTTP {nc_export.status_code}")

    # -------------------------------------------------------------------
    heading("U44: no licence found at all defaults to no export, not to open")

    unknown_ds = register(owning, "internal_regulated")
    unknown_id = unknown_ds.json()["id"]
    unknown_job = fetch_and_wait(unknown_id, UNKNOWN_REPO, UNKNOWN_PATH)

    if unknown_job is None:
        skip("the unlicensed-repo fetch job completes",
             "no result within the timeout; is `python -m worker.main` running?")
    else:
        check("fetching the unlicensed repo still succeeds",
              unknown_job.get("status") == "succeeded", str(unknown_job))
        check(f"all {UNKNOWN_FILES} files came back",
              unknown_job.get("files_done") == UNKNOWN_FILES, str(unknown_job.get("files_done")))

        row = dataset_row(unknown_id)
        check("no licence tag was found", row["license_tag"] is None, str(row["license_tag"]))
        check("both export aspects default to refused",
              row["license_export_unmodified"] is False and row["license_export_modified"] is False,
              str(row))
        check("provenance was not overridden, since it already matched the safe default",
              row["provenance"] == "internal_regulated" and row["provenance_registered_as"] is None,
              str(row))

        api("POST", f"/datasets/{unknown_id}/seal")
        unknown_export = api("GET", f"/datasets/{unknown_id}/export",
                             params={"purpose": "share the benchmark"},
                             headers=bearer_for(ENGINEER))
        check("export of a dataset with no known licence is refused",
              unknown_export.status_code == 403, f"HTTP {unknown_export.status_code}")

    # -------------------------------------------------------------------
    heading("U44: a manual upload is not touched by any of this")

    import io

    upload_ds = register(owning, "internal_regulated", source_kind="upload")
    upload_id = upload_ds.json()["id"]
    up = api("POST", f"/datasets/{upload_id}/files",
             files={"file": ("recording.wav", io.BytesIO(tiny_wav()), "audio/wav")})
    check("the upload itself succeeds", up.status_code == 201, f"HTTP {up.status_code}")

    row = dataset_row(upload_id)
    check("a manual upload gets no licence tag",
          row["license_tag"] is None, str(row["license_tag"]))
    check("and its provenance is exactly what was chosen, never overridden",
          row["provenance"] == "internal_regulated" and row["provenance_registered_as"] is None,
          str(row))

    return summary("U44")


if __name__ == "__main__":
    sys.exit(main())
