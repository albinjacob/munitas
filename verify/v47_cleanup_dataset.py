"""U47: clearing one unsealed dataset's leftover files, and refusing a sealed one.

Runs on the host, because it drives `scripts/admin/cleanup-dataset.py`, which lives beside
the Compose file rather than inside the API image, the same reason
`v29_reclamation.py` runs on the host for `scripts/admin/reclaim-storage.py`.

    .venv\\Scripts\\python.exe verify\\v47_cleanup_dataset.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verify"))

from common import (ENGINEER, api, bearer_for, check, db,  # noqa: E402
                    fixture_contract, fixture_tenant, fixture_version,
                    heading, require_api, summary)

SCRIPT = ROOT / "scripts" / "admin" / "cleanup-dataset.py"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
DEPARTMENT = "Verification"


def cleanup(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PYTHON), str(SCRIPT), *args],
        # An empty stdin, so a confirmation prompt the script asks is answered by end of input at once instead of waiting for a
        # person (the same hang U52 had when it inherited an open stdin).
        capture_output=True, text=True, cwd=str(ROOT), timeout=300, stdin=subprocess.DEVNULL,
    )


def department(tenant: str, name: str = DEPARTMENT) -> tuple[str, str]:
    with db() as conn:
        row = conn.execute(
            "select id, custodian from department where tenant_id = %s and name = %s",
            (tenant, name),
        ).fetchone()
    if not row:
        raise RuntimeError(
            f"department {name!r} is missing from tenant {tenant!r}. Apply "
            "infra/postgres/seed-canary.sql."
        )
    return str(row["id"]), row["custodian"]


def register(tenant: str, owning: str, name: str) -> str:
    r = api("POST", "/datasets/register", json={
        "tenant_id": tenant, "name": name, "department_id": owning,
        "registered_by": "canary-engineer", "provenance": "external_public",
        "declared_class": "RAW",
    })
    r.raise_for_status()
    return r.json()["id"]


def upload(dataset_id: str, filename: str, payload: bytes) -> None:
    r = api("POST", f"/datasets/{dataset_id}/files",
            files={"file": (filename, payload)})
    r.raise_for_status()


def main() -> int:
    require_api()

    tenant = fixture_tenant()
    owning, _custodian = department(tenant)

    # --------------------------------------------------------------- U47 --
    heading("U47: dry run reports without changing anything")

    name = f"cleanup-{uuid.uuid4().hex[:8]}"
    dataset_id = register(tenant, owning, name)
    upload(dataset_id, "part-0.csv", b"a,b,c\n1,2,3\n")

    with db() as conn:
        conn.execute(
            """insert into huggingface_fetch_job
                 (id, dataset_id, repo_id, revision, path, fetched_by, status,
                  files_total, files_done, bytes_total, bytes_done, workflow_id)
               values (%s, %s, 'scikit-learn/iris', 'main', '', 'canary-engineer',
                       'succeeded', 1, 1, 12, 12, %s)""",
            (str(uuid.uuid4()), dataset_id, f"wf-{uuid.uuid4().hex[:8]}"),
        )

    # Registering itself writes a placeholder `dataset_source` row ("uploaded
    # by hand"), so a dataset with one real upload holds two: the placeholder
    # and the file. Both are pre-seal residue and both are this script's to
    # clear.
    dry = cleanup("--tenant", tenant, "--name", name, "--dry-run")
    check("the dry run succeeds", dry.returncode == 0, dry.stdout + dry.stderr)
    check("and reports both source rows and the job",
          "2 dataset_source" in dry.stdout and "1 huggingface_fetch_job" in dry.stdout,
          dry.stdout)

    with db() as conn:
        sources = conn.execute(
            "select count(*) as n from dataset_source where dataset_id = %s",
            (dataset_id,),
        ).fetchone()["n"]
        jobs = conn.execute(
            "select count(*) as n from huggingface_fetch_job where dataset_id = %s",
            (dataset_id,),
        ).fetchone()["n"]
    check("nothing was removed by the dry run",
          sources == 2 and jobs == 1, f"{sources} sources, {jobs} jobs")

    heading("U47: --force clears the dataset and leaves it registered")

    forced = cleanup("--tenant", tenant, "--name", name, "--force")
    check("the forced run succeeds", forced.returncode == 0, forced.stdout + forced.stderr)

    with db() as conn:
        sources = conn.execute(
            "select count(*) as n from dataset_source where dataset_id = %s",
            (dataset_id,),
        ).fetchone()["n"]
        jobs = conn.execute(
            "select count(*) as n from huggingface_fetch_job where dataset_id = %s",
            (dataset_id,),
        ).fetchone()["n"]
    check("the dataset_source row is gone", sources == 0, f"{sources} rows")
    check("the huggingface_fetch_job row is gone", jobs == 0, f"{jobs} rows")

    # A read needs a signed-in person; the canary engineer is in the canary tenant this dataset belongs to.
    still = api("GET", f"/datasets/{dataset_id}", headers=bearer_for(ENGINEER))
    check("the dataset is still registered", still.status_code == 200,
          f"HTTP {still.status_code}")

    heading("U47: it can be brought data into again afterward")

    reupload = api("POST", f"/datasets/{dataset_id}/files",
                    files={"file": ("part-0.csv", b"a,b\n1,2\n")})
    check("a fresh upload succeeds", reupload.status_code == 201,
          f"HTTP {reupload.status_code}")

    with db() as conn:
        sources = conn.execute(
            "select count(*) as n from dataset_source where dataset_id = %s",
            (dataset_id,),
        ).fetchone()["n"]
    check("and leaves a new source row", sources == 1, f"{sources} rows")

    # --------------------------------------------------------------- U47 --
    heading("U47: a sealed dataset is refused, not cleared")

    contract = fixture_contract(tenant)
    sealed = fixture_version(tenant, contract, "RAW")

    refused = cleanup("--tenant", tenant, "--name", sealed["dataset_name"])
    check("cleaning up a sealed dataset is refused", refused.returncode != 0,
          f"exit {refused.returncode}")
    check("and points at scripts/admin/reclaim-storage.py instead",
          "scripts/admin/reclaim-storage.py" in (refused.stdout + refused.stderr),
          (refused.stdout + refused.stderr).strip()[:160])

    with db() as conn:
        still_sealed = conn.execute(
            "select sealed from dataset_version where id = %s", (sealed["id"],)
        ).fetchone()
    check("the sealed version is untouched", bool(still_sealed) and still_sealed["sealed"] is True)

    # --------------------------------------------------------------- U47 --
    heading("U47: no path in the script deletes a protected table")

    source_text = SCRIPT.read_text(encoding="utf-8")
    protected = re.findall(
        r"delete\s+from\s+(dataset_version|class_transition|lease_request|"
        r"access_lease|access_decision)\b",
        source_text, re.IGNORECASE,
    )
    check("no delete against dataset_version, class_transition, lease_request, "
          "access_lease or access_decision anywhere in the script",
          not protected, ", ".join(protected) or "none found")

    return summary("U47")


if __name__ == "__main__":
    sys.exit(main())
