r"""L4: a worker killed in the middle of writing a table of five million rows: the table is still written once, and nothing is left behind.

LARGE TIER. Never part of the ordinary suite. Run it with verify\run_large.py, which says what it will do, how long it takes and how much disk it needs
before it does anything.

The container is killed outright (no warning, no clean stop) when about a third of the rows are written. The platform notices the worker has gone quiet,
the work is handed out again, and a worker, which is started again if it is not already, writes the table from the start. Checked:

  * the table ends as written and sealed, once: one version, with every row and no row twice (the truth file says so);
  * no data file of the first attempt is left in storage beside the table's own (every data file in the folder is one the table lists).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import flow  # noqa: E402
from common import ADMIN, CANARY, ENGINEER, api, bearer_for, bucket_for, check, db, heading, require_api, s3_client, summary  # noqa: E402

ID = "L4"
TITLE = "a worker killed in the middle of a table of five million rows: the table is written once and nothing is left behind"
TABLE = "fx_ticks"
ROWS = flow.scaled(5_000_000)


def need() -> dict:
    return {"disk_bytes": flow.generate.estimate(flow.tables.SPECS[TABLE], ROWS, ["csv"]), "minutes": "15 to 45 (not measured yet)",
            "does": [f"makes {TABLE} with {ROWS:,} rows as one CSV file under {flow.DATA}",
                     "sends it, and when a third of the rows are written kills the table worker's container",
                     "starts the worker again if it is not already running, and waits for the job to end",
                     "checks the table against the truth file, and that storage holds no file the table does not list; removes the made file"]}


def main() -> int:
    require_api()
    headers = bearer_for(ENGINEER)
    department = api("GET", "/organisation", headers=headers).json()["departments"][0]["id"]
    folder = flow.make_files(TABLE, ROWS, ["csv"])
    try:
        path = folder / "csv" / f"{TABLE}.csv"
        dataset = flow.register(headers, department, TABLE)
        opened = flow.open_upload(headers, dataset, TABLE, path)
        check("the upload is opened", opened.status_code == 201, f"{opened.status_code} {opened.text[:200]}")
        if opened.status_code != 201:
            return summary(ID)
        job, part = opened.json()["job_id"], opened.json()["part_bytes"]
        flow.send_parts(headers, job, path, part)
        flow.finish(headers, job)

        heading("Killing the worker when a third of the rows are written")
        killed_at = None
        started = time.monotonic()
        while time.monotonic() - started < 3600:
            status = api("GET", f"/table-uploads/{job}", headers=headers).json()
            written = int((status.get("progress") or {}).get("rows") or 0)
            if written >= ROWS // 3:
                subprocess.run(["wsl.exe", "-d", flow.DISTRO, "--", "docker", "kill", flow.WORKER], capture_output=True, timeout=60)
                killed_at = written
                flow.out(f"    killed the worker at {written:,} rows")
                break
            if status.get("status") in ("sealed", "refused", "expired"):
                break
            time.sleep(1)
        check("the worker was killed part way through, not before or after", killed_at is not None and killed_at < ROWS, f"killed at {killed_at}")
        subprocess.run(["wsl.exe", "-d", flow.DISTRO, "--", "docker", "start", flow.WORKER], capture_output=True, timeout=60)
        status, _progress = flow.watch_until_done(headers, job, 3 * 3600, time.monotonic(), note_every=60)
        check("the table ends as written and sealed", status.get("status") == "sealed" and (status.get("progress") or {}).get("rows") == ROWS,
              f"{status.get('status')}: {status.get('reason')} {status.get('progress')}")
        if status.get("status") != "sealed":
            return summary(ID)
        with db() as conn:
            versions = conn.execute("select count(*) as n from dataset_version where dataset_id = %s", (dataset,)).fetchone()["n"]
        check("there is one version of the dataset", versions == 1, f"{versions} versions")
        problems = flow.agrees_with_truth(status["version_id"], TABLE, folder)
        check("the stored table agrees with the truth file: every row once", not problems, "; ".join(problems[:2]))

        heading("Nothing of the first attempt is left in storage")
        stored = flow.stored_table(status["version_id"])
        listed = {task.file.file_path for task in stored.scan().plan_files()}
        prefix = status["storage_prefix"].rstrip("/") + "/"
        keys = [o["Key"] for o in s3_client(*ADMIN).list_objects_v2(Bucket=bucket_for(CANARY), Prefix=prefix).get("Contents", [])]
        strays = [k for k in keys if k.endswith(".parquet") and not any(path_.endswith(k) for path_ in listed)]
        check("every data file in the version's folder is one the table lists", not strays, f"{len(strays)} stray of {len(keys)} files: {strays[:2]}")
    finally:
        flow.remove(folder)
    return summary(ID)


if __name__ == "__main__":
    sys.exit(main())
