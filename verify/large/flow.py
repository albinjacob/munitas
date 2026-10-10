"""What the large-tier checks share: bringing one file in through the person's door, watching the worker's memory while it is written, and timing ordinary
calls while a large upload is going on.

Nothing here runs by itself. verify/run_large.py names the checks, says what they will do, and runs one only when it is told to.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT))

from common import ADMIN, API, CANARY, ENGINEER, S3_ENDPOINT, api, bearer_for, db  # noqa: E402,F401
from scripts.testdata import contracts, generate, tables, truth  # noqa: E402,F401

DISTRO = "Munitas"
WORKER = "munitas-table-worker-1"
WORKER_LIMIT_MIB = 1536
DATA = Path("C:/Work/testdata-large")           # outside the repository, and outside the ordinary tier's folder
POLL_SECONDS = 2
# MUNITAS_LARGE_DIVISOR makes every size that many times smaller, to see that a check works before it is given the hours it needs. It is for trying a check
# out, not for proving anything: at a small size the memory readings are too few to say, and nothing is recorded unless run_large.py runs the check.
DIVISOR = max(1, int(os.environ.get("MUNITAS_LARGE_DIVISOR", "1")))


def scaled(number: int) -> int:
    return max(2_000, number // DIVISOR) if DIVISOR > 1 else number


def out(text: str) -> None:
    print(text, flush=True)


# ---- the worker's memory ------------------------------------------------------------------------------------------------------------------


def _mib(text: str) -> float:
    number, unit = re.match(r"([\d.]+)\s*([KMGT]?i?B)", text.strip()).groups()
    return float(number) * {"B": 1 / 1048576, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "TiB": 1048576}[unit]


def worker_memory(container: str = WORKER) -> float | None:
    """The memory the worker container is using now, in MiB, or None when it cannot be read (the container is not running)."""
    done = subprocess.run(["wsl.exe", "-d", DISTRO, "--", "docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container], capture_output=True, text=True, timeout=60)
    if done.returncode != 0 or not done.stdout.strip():
        return None
    try:
        return _mib(done.stdout.split("/")[0])
    except (AttributeError, ValueError, KeyError):
        return None


class MemoryWatch:
    """Reads the worker's memory every few seconds in the background, and keeps (seconds since start, MiB)."""

    def __init__(self, container: str = WORKER):
        self.container, self.samples, self._stop, self.started = container, [], threading.Event(), time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            used = worker_memory(self.container)
            if used is not None:
                self.samples.append((time.monotonic() - self.started, used))
            self._stop.wait(1.0)

    def __enter__(self) -> "MemoryWatch":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=70)

    def peak(self, start: float, end: float) -> float | None:
        window = [m for t, m in self.samples if start <= t <= end]
        return max(window) if window else None


def flatness(watch: MemoryWatch, progress: list[tuple[float, int]], total: int) -> dict:
    """Does the memory stay level while the rows go by? `progress` is (seconds since start, rows written so far) as the status reported them. The peak while
    the first third of the rows went by is set against the peak while the last 40 percent did. A table that is held in memory grows with its rows, so the second
    is far above the first; a table that is written in batches stays level."""
    def at(fraction: float) -> float | None:
        for t, rows in progress:
            if rows >= fraction * total:
                return t
        return None

    a, b, c = at(0.10), at(0.35), at(0.60)
    early = watch.peak(a, b) if a is not None and b is not None else None
    late = watch.peak(c, float("inf")) if c is not None else None
    return {"early_mib": early, "late_mib": late, "peak_mib": max((m for _, m in watch.samples), default=None), "samples": len(watch.samples)}


def level(measure: dict) -> tuple[bool, str]:
    early, late, peak = measure["early_mib"], measure["late_mib"], measure["peak_mib"]
    if early is None or late is None or measure["samples"] < 6:
        return False, f"too few readings to say ({measure['samples']} readings, early {early}, late {late})"
    ok = late <= early * 1.25 + 200 and peak is not None and peak < WORKER_LIMIT_MIB * 0.85
    return ok, f"peak while the first third went by {early:.0f} MiB, peak in the last 40 percent {late:.0f} MiB, highest ever {peak:.0f} MiB of {WORKER_LIMIT_MIB}"


# ---- ordinary calls while something large goes on -----------------------------------------------------------------------------------------------


class Latency:
    """Times one ordinary, cheap call about once a second in the background."""

    def __init__(self, headers: dict, path: str = "/organisation"):
        self.headers, self.path, self.seconds, self._stop = headers, path, [], threading.Event()
        self.paused = False          # set while the platform is being restarted on purpose, when a call is expected to fail
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.paused:
                self._stop.wait(1.0)
                continue
            started = time.monotonic()
            try:
                api("GET", self.path, headers=self.headers)
                self.seconds.append(time.monotonic() - started)
            except Exception:  # noqa: BLE001
                self.seconds.append(60.0)
            self._stop.wait(1.0)

    def __enter__(self) -> "Latency":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=70)

    def summary(self) -> dict:
        if not self.seconds:
            return {"calls": 0, "median": None, "p95": None, "worst": None}
        ordered = sorted(self.seconds)
        return {"calls": len(ordered), "median": statistics.median(ordered), "p95": ordered[int(0.95 * (len(ordered) - 1))], "worst": ordered[-1]}


# ---- generating, sending, waiting -----------------------------------------------------------------------------------------------------------


def make_files(table: str, rows: int, variants: list[str], faulty: bool = False) -> Path:
    """The table's files, made on demand in DATA, after saying how much disk they take and stopping if there is not enough."""
    spec = tables.SPECS[table]
    needed = generate.estimate(spec, rows, variants)
    out(f"  making {table} with {rows:,} rows in {', '.join(variants)}: about {needed / 1e9:.1f} GB, {shutil.disk_usage(DATA.anchor).free / 1e9:.0f} GB free")
    generate.check_disk(DATA, needed)
    started = time.monotonic()
    generate.make(table, rows, variants, DATA, faulty=faulty)
    out(f"  made in {time.monotonic() - started:.0f}s")
    return DATA / table / str(spec.context(rows, generate.SEED).rows)


def remove(folder: Path) -> None:
    """The made files are removed once the check is done: they are cheap to make again, and they are large."""
    shutil.rmtree(folder.parent, ignore_errors=True)


def register(headers: dict, department: str, name: str) -> str:
    r = api("POST", "/datasets/register", headers=headers, json={"tenant_id": CANARY, "name": f"large-{name}-{uuid.uuid4().hex[:8]}", "department_id": department,
                                                                 "registered_by": ENGINEER, "provenance": "internal_regulated"})
    assert r.status_code == 201, f"register: HTTP {r.status_code} {r.text[:160]}"
    return r.json()["id"]


def open_upload(headers: dict, dataset: str, table: str, path: Path, options: dict | None = None, contract: str | None = None):
    spec = tables.SPECS[table]
    return api("POST", f"/datasets/{dataset}/table-uploads", headers=headers, json={
        "file_name": path.name, "size_bytes": path.stat().st_size, "contract": contract or contracts.to_yaml(spec), "options": options or {}})


def send_parts(headers: dict, job: str, path: Path, part: int, only: range | None = None, on_part=None) -> None:
    """Send the parts of a file one after another, reading each from disk when it is wanted, so the check's own memory stays one part."""
    parts = -(-path.stat().st_size // part)
    with open(path, "rb") as handle:
        for n in range(1, parts + 1):
            handle.seek((n - 1) * part)
            body = handle.read(part)
            if only is not None and n not in only:
                continue
            for attempt in range(8):
                r = api("PUT", f"/table-uploads/{job}/parts/{n}", headers=headers, content=body)
                if r.status_code == 429:
                    time.sleep(min(30, int(r.headers.get("retry-after", 2))))
                    continue
                break
            assert r.status_code == 200, f"part {n}: HTTP {r.status_code} {r.text[:160]}"
            if on_part:
                on_part(n, parts)


def finish(headers: dict, job: str) -> None:
    r = api("POST", f"/table-uploads/{job}/finish", headers=headers)
    assert r.status_code == 202, f"finish: HTTP {r.status_code} {r.text[:160]}"


def watch_until_done(headers: dict, job: str, limit_seconds: int, started: float, note_every: int = 30) -> tuple[dict, list[tuple[float, int]]]:
    """Poll the upload's status until it ends, keeping (seconds since `started`, rows written) and saying where it is every so often."""
    progress, last_note, status = [], 0.0, {}
    while time.monotonic() - started < limit_seconds:
        status = api("GET", f"/table-uploads/{job}", headers=headers).json()
        rows = int((status.get("progress") or {}).get("rows") or 0)
        progress.append((time.monotonic() - started, rows))
        if time.monotonic() - last_note > note_every:
            last_note = time.monotonic()
            out(f"    {status.get('status')} {(status.get('progress') or {}).get('phase', '')}: {rows:,} rows after {time.monotonic() - started:.0f}s")
        if status.get("status") in ("sealed", "refused", "expired"):
            break
        time.sleep(POLL_SECONDS)
    return status, progress


def stored_table(version_id: str):
    from pyiceberg.table import StaticTable

    with db() as conn:
        ref = conn.execute("select metadata_location from iceberg_table_ref where dataset_version_id = %s", (version_id,)).fetchone()
    if not ref:
        return None
    props = {"s3.endpoint": S3_ENDPOINT, "s3.access-key-id": ADMIN[0], "s3.secret-access-key": ADMIN[1], "s3.region": "us-east-1", "s3.path-style-access": "true"}
    return StaticTable.from_metadata(ref["metadata_location"], properties=props)


def agrees_with_truth(version_id: str, table: str, folder: Path) -> list[str]:
    """The stored table against the generator's truth file, read in batches so a table of millions of rows is never held whole."""
    spec = tables.SPECS[table]
    expected = json.loads((folder / "truth.json").read_text(encoding="utf-8"))
    stored = stored_table(version_id)
    if stored is None:
        return ["there is no stored table"]
    summary = truth.Summary(spec)
    names = [c.name for c in spec.cols]
    for batch in stored.scan().to_arrow_batch_reader():
        import pyarrow as pa

        summary.add(pa.Table.from_batches([batch]).select(names).cast(spec.schema()))
    return truth.compare(expected, summary.result())


# ---- the usual run: one table, one file, watched from start to end --------------------------------------------------------------------------------


def bring_in(headers: dict, department: str, table: str, path: Path, rows: int, options: dict | None = None, limit_seconds: int = 4 * 3600) -> dict:
    """Send one file, finish it, and watch it written, with the worker's memory and an ordinary call timed all the way. Returns what happened."""
    dataset = register(headers, department, table)
    started = time.monotonic()
    opened = open_upload(headers, dataset, table, path, options)
    if opened.status_code != 201:
        return {"status": "not opened", "problems": [f"HTTP {opened.status_code} {opened.text[:300]}"], "dataset": dataset}
    job, part = opened.json()["job_id"], opened.json()["part_bytes"]
    with MemoryWatch() as memory, Latency(headers) as latency:
        sent_at = time.monotonic()
        send_parts(headers, job, path, part, on_part=lambda n, parts: out(f"    part {n} of {parts}") if n % 25 == 0 else None)
        sent = time.monotonic() - sent_at
        finish(headers, job)
        watch_started = time.monotonic()
        status, progress = watch_until_done(headers, job, limit_seconds, memory.started)
        written = time.monotonic() - watch_started
    status.update({"dataset": dataset, "job": job, "sent_seconds": sent, "written_seconds": written, "latency": latency.summary(), "memory": flatness(memory, progress, rows),
                   "total_seconds": time.monotonic() - started})
    return status
