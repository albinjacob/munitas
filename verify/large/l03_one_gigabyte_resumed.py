r"""L3: a file of about a gigabyte is sent in parts, broken off in the middle, carried on from where it stopped, and ordinary calls stay fast throughout.

LARGE TIER. Never part of the ordinary suite. Run it with verify\run_large.py, which says what it will do, how long it takes and how much disk it needs
before it does anything.

The break is a real one: after about a third of the parts the sending stops, the API is restarted, and a fresh start asks the platform which parts it
has before sending only the ones that are missing. Checked:

  * the platform remembered the parts through the restart, and the status named exactly the ones that were not sent;
  * no part was sent twice (a part already stored is not sent again);
  * the table is written, agrees with the truth file, and the file's hash in the version is the hash of the file that was sent;
  * an ordinary call, timed all through (including the restart, which is the worst of them), stayed fast.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import flow  # noqa: E402
from common import ENGINEER, api, bearer_for, check, db, heading, require_api, summary  # noqa: E402

ID = "L3"
TITLE = "a file of about a gigabyte is sent in parts, broken off and carried on, and ordinary calls stay fast"
TABLE = "vehicle_readings"
WANTED_BYTES = flow.scaled(1024 ** 3)


def rows_for_bytes() -> int:
    spec = flow.tables.SPECS[TABLE]
    per_row = flow.generate.estimate(spec, 1000, ["csv"]) / 1000
    return int(WANTED_BYTES / per_row)


def need() -> dict:
    rows = rows_for_bytes()
    return {"disk_bytes": flow.generate.estimate(flow.tables.SPECS[TABLE], rows, ["csv"]), "minutes": "30 to 90 (not measured yet)",
            "does": [f"makes {TABLE} with about {rows:,} rows as one CSV file of about 1 GB under {flow.DATA}",
                     "sends a third of its parts, restarts the API container, asks which parts the platform has, and sends only the rest",
                     "has the worker write it, compares the stored table with the truth file, and removes the made file at the end"]}


def restart_api() -> None:
    subprocess.run(["wsl.exe", "-d", flow.DISTRO, "--", "docker", "restart", "munitas-munitas-api-1"], capture_output=True, timeout=180)
    for _ in range(90):
        try:
            if api("GET", "/health").status_code == 200:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)


def main() -> int:
    require_api()
    headers = bearer_for(ENGINEER)
    department = api("GET", "/organisation", headers=headers).json()["departments"][0]["id"]
    rows = rows_for_bytes()
    folder = flow.make_files(TABLE, rows, ["csv"])
    try:
        path = folder / "csv" / f"{TABLE}.csv"
        size = path.stat().st_size
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 22), b""):
                digest.update(block)
        dataset = flow.register(headers, department, TABLE)
        opened = flow.open_upload(headers, dataset, TABLE, path)
        check("the upload is opened", opened.status_code == 201, f"{opened.status_code} {opened.text[:200]}")
        if opened.status_code != 201:
            return summary(ID)
        job, part, parts = opened.json()["job_id"], opened.json()["part_bytes"], opened.json()["parts"]
        flow.out(f"  {size / 1e9:.2f} GB in {parts} parts of {part // (1024 * 1024)} MB")

        heading("Sending a third, a break, and carrying on")
        first = list(range(1, parts // 3 + 1))
        with flow.MemoryWatch() as memory, flow.Latency(headers) as latency:
            flow.send_parts(headers, job, path, part, only=set(first))
            held = api("GET", f"/table-uploads/{job}", headers=headers).json()
            check("before the break the platform holds exactly the parts that were sent", held["received"] == first, f"{len(held['received'])} of {len(first)}")
            latency.paused = True
            restart_api()
            latency.paused = False
            after = api("GET", f"/table-uploads/{job}", headers=headers).json()
            wanted_missing = [n for n in range(1, parts + 1) if n not in set(first)]
            check("after the API restarted it still holds them, and the status names every part that is missing",
                  after["received"] == first and after["missing"] == wanted_missing and after["open"] is True,
                  f"received {len(after['received'])}, missing {len(after['missing'])}")
            flow.send_parts(headers, job, path, part, only=set(after["missing"]))
            done = api("GET", f"/table-uploads/{job}", headers=headers).json()
            check("every part is held once the missing ones are sent, and the file is complete", done["missing"] == [] and done["received"] == list(range(1, parts + 1)),
                  f"missing {done['missing'][:5]}")
            flow.finish(headers, job)
            status, _progress = flow.watch_until_done(headers, job, 4 * 3600, memory.started)
        written = status.get("status") == "sealed"
        check("the table is written, with every row", written and (status.get("progress") or {}).get("rows") == rows,
              f"{status.get('status')}: {status.get('reason')} {str(status.get('problems'))[:200]}")
        if not written:
            return summary(ID)
        problems = flow.agrees_with_truth(status["version_id"], TABLE, folder)
        check("the stored table agrees with the truth file", not problems, "; ".join(problems[:2]))
        with db() as conn:
            version = conn.execute("select object_manifest from dataset_version where id = %s", (status["version_id"],)).fetchone()
        entry = next((o for o in version["object_manifest"] if o["key"].endswith("/records.csv")), {})
        check("the file's hash in the version is the hash of the file that was sent", entry.get("sha256") == digest.hexdigest() and entry.get("bytes") == size, str(entry)[:140])
        seen = latency.summary()
        check("an ordinary call stayed fast all through", seen["calls"] > 60 and seen["p95"] < 1.5,
              f"{seen['calls']} calls, median {seen['median']:.2f}s, 95th {seen['p95']:.2f}s, worst {seen['worst']:.2f}s")
    finally:
        flow.remove(folder)
    return summary(ID)


if __name__ == "__main__":
    sys.exit(main())
