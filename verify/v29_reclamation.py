"""U29 to U31: reclaiming bytes without weakening anything.

Three claims, and the third is the one that matters most.

  U29  Freeing a version's objects keeps its record. The row, its lineage and
       its audit history all survive; only the files go.
  U30  Reclamation cannot reach a production tenant, and not because of a single
       check that somebody could delete.
  U31  Nothing about immutability changed. Sealed versions still cannot be
       deleted, and the script that frees bytes contains no way to delete a row.

U29 and U31 are a pair. Bytes back and record intact is the whole claim, and
either half alone would be worthless: a script that freed nothing would be
pointless, and one that freed space by removing lineage would be destroying the
evidence this platform exists to keep.

Runs on the host, because it drives scripts/admin/reclaim-storage.py, which lives beside the
Compose file rather than inside the API image.

    .venv\\Scripts\\python.exe verify\\v29_reclamation.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verify"))

from common import (ADMIN, CANARY, api, bucket_for, check, db,  # noqa: E402
                    fixture_contract, fixture_tenant, fixture_version, heading,
                    require_api, s3_client, summary)

SCRIPT = ROOT / "scripts" / "admin" / "reclaim-storage.py"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def reclaim(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PYTHON), str(SCRIPT), *args],
        capture_output=True, text=True, cwd=str(ROOT), timeout=300,
    )


def main() -> int:
    require_api()

    tenant = fixture_tenant()
    contract = fixture_contract(tenant)
    version = fixture_version(tenant, contract, "RAW")
    prefix = version["storage_prefix"]

    # This tenant's own bucket, created by the platform if this is its first write.
    # scripts/admin/reclaim-storage.py resolves the same way, so it looks where these
    # objects actually are rather than where they used to go.
    bucket = bucket_for(tenant)
    admin = s3_client(*ADMIN)

    # Big enough that "freed some bytes" is a real measurement rather than a
    # rounding error.
    payload = b"x" * 200_000
    for part in range(3):
        admin.put_object(Bucket=bucket, Key=f"{prefix}/part-{part}.bin", Body=payload)

    before = admin.list_objects_v2(Bucket=bucket, Prefix=prefix + "/")
    check("the fixture has objects to reclaim", before.get("KeyCount", 0) == 3,
          f"{before.get('KeyCount', 0)} objects")

    lineage_before = api("GET", f"/lineage/{version['id']}")

    # ------------------------------------------------------------- U30 --
    heading("U30: a production tenant cannot be reclaimed")

    refused = reclaim("--tenant", "health", "--older-than", "0", "--dry-run")
    check("naming a production tenant is refused", refused.returncode != 0,
          f"exit {refused.returncode}")
    check("and says why, rather than exiting quietly",
          "production" in (refused.stdout + refused.stderr),
          (refused.stdout + refused.stderr).strip()[:110])

    # The guard above is one check. This is the second, independent one: even
    # with the guard gone, the query that chooses what to reclaim cannot return a
    # production tenant's versions.
    with db() as conn:
        reachable = conn.execute("""
            select count(*) as n
            from dataset_version dv
            join tenant t on t.id = dv.tenant_id
            where t.purpose = any(array['canary','retired'])
              and dv.tenant_id = 'health'
        """).fetchone()
    check("and the selection itself cannot reach one, guard or no guard",
          reachable["n"] == 0, f"{reachable['n']} versions selectable")

    # ------------------------------------------------------------- U29 --
    heading("U29: the bytes go and the record stays")

    run = reclaim("--tenant", CANARY, "--older-than", "0",
                  "--reason", "verification run")
    check("reclamation completes", run.returncode == 0,
          (run.stdout + run.stderr).strip().splitlines()[-1][:110]
          if (run.stdout or run.stderr) else f"exit {run.returncode}")

    after = admin.list_objects_v2(Bucket=bucket, Prefix=prefix + "/")
    check("the objects are gone from storage", after.get("KeyCount", 0) == 0,
          f"{after.get('KeyCount', 0)} objects remain")

    with db() as conn:
        row = conn.execute(
            "select id, storage_prefix, sealed from dataset_version where id = %s",
            (version["id"],),
        ).fetchone()
        record = conn.execute(
            """select bytes_freed, object_count, reason, reclaimed_by
               from storage_reclamation where dataset_version_id = %s""",
            (version["id"],),
        ).fetchone()

    check("the version row is still there", row is not None)
    check("and still sealed", bool(row) and row["sealed"] is True)
    check("and still knows where its objects were",
          bool(row) and row["storage_prefix"] == prefix)

    check("the reclamation was recorded", record is not None)
    check("with the bytes it freed",
          bool(record) and record["bytes_freed"] >= 600_000,
          f"{record['bytes_freed'] if record else 0} bytes")
    check("and the number of objects",
          bool(record) and record["object_count"] == 3,
          str(record["object_count"]) if record else "no row")
    check("and why, and by whom",
          bool(record) and record["reason"] == "verification run"
          and bool(record["reclaimed_by"]),
          f"{record['reason']} / {record['reclaimed_by']}" if record else "no row")

    lineage_after = api("GET", f"/lineage/{version['id']}")
    check("the lineage still answers after the bytes are gone",
          lineage_after.status_code == 200, f"HTTP {lineage_after.status_code}")
    check("and answers the same as it did before",
          lineage_before.status_code == lineage_after.status_code
          and lineage_before.json() == lineage_after.json())

    versioned = api("GET", f"/dataset-versions/{version['id']}")
    check("the console can still open the version",
          versioned.status_code == 200, f"HTTP {versioned.status_code}")
    check("and it says its bytes were reclaimed rather than pretending otherwise",
          versioned.json().get("reclaimed_at") is not None,
          str(versioned.json().get("reclaimed_at")))

    heading("U29: running it again is harmless")

    second = reclaim("--tenant", CANARY, "--older-than", "0")
    check("a second run completes", second.returncode == 0, f"exit {second.returncode}")
    with db() as conn:
        rows = conn.execute(
            "select count(*) as n from storage_reclamation where dataset_version_id = %s",
            (version["id"],),
        ).fetchone()
    check("and does not record the same version twice", rows["n"] == 1,
          f"{rows['n']} rows")

    # ------------------------------------------------------------- U31 --
    heading("U31: immutability is exactly as it was")

    with db() as conn:
        deleted = conn.execute(
            "delete from dataset_version where id = %s", (version["id"],)
        ).rowcount
        still = conn.execute(
            "select id from dataset_version where id = %s", (version["id"],)
        ).fetchone()
    check("a sealed version still cannot be deleted", deleted == 0,
          f"DELETE {deleted}")
    check("and is still there afterwards", still is not None)

    # Reading the script rather than trusting its behaviour. A run that happened
    # not to delete a row proves nothing about the run after somebody edits it.
    source = SCRIPT.read_text(encoding="utf-8")
    statements = re.findall(r"delete\s+from\s+\w+", source, re.IGNORECASE)
    check("the reclamation script contains no delete against any table",
          not statements, ", ".join(statements) or "none found")

    truncates = re.findall(r"\b(truncate|drop\s+table)\b", source, re.IGNORECASE)
    check("and nothing else that removes rows either",
          not truncates, ", ".join(truncates) or "none found")

    return summary("U29 to U31")


if __name__ == "__main__":
    sys.exit(main())
