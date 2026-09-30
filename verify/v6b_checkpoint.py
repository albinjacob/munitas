"""V6b: a retried activity resumes instead of starting over.

Temporal restarts an activity from its first line on retry. That is resumption
at the workflow level and repetition at the activity level, and the difference
is invisible in the output: a run that should have transcribed 60 records
transcribed 112 and looked only slow.

So the expensive activities checkpoint per record. This tests the checkpoint
directly rather than through a transcription, because a test that takes six
minutes of GPU time to run is a test nobody runs.

    .venv\\Scripts\\python.exe verify\\v6b_checkpoint.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from worker.activities import _Checkpoint  # noqa: E402

_results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    _results.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def main() -> int:
    key = f"test-{uuid.uuid4().hex[:10]}"
    print("\nV6b: per-record checkpointing")
    print("-" * 29)

    cp = _Checkpoint("transcribe", key)
    check("a fresh checkpoint is empty", cp.load() == {})

    # Simulate an activity that got through 3 of 5 records and then died.
    for i in range(3):
        cp.append({"record_id": f"rec-{i}", "transcript": f"text {i}", "words": []})

    resumed = _Checkpoint("transcribe", key).load()
    check("a new instance sees the completed records", len(resumed) == 3,
          f"{len(resumed)} records")
    check("the resumed records keep their content",
          resumed.get("rec-1", {}).get("transcript") == "text 1",
          str(resumed.get("rec-1", {}).get("transcript")))

    # The retry does the remaining two and nothing else.
    all_records = [f"rec-{i}" for i in range(5)]
    redone = [r for r in all_records if r not in resumed]
    check("only the unfinished records would be redone",
          redone == ["rec-3", "rec-4"], str(redone))
    check("no completed record would be repeated",
          not any(r in resumed for r in redone))

    # A different idempotency key must not see this work. Two runs sharing a
    # checkpoint would be worse than no checkpoint, because one would silently
    # inherit the other's results.
    other = _Checkpoint("transcribe", f"other-{uuid.uuid4().hex[:8]}")
    check("a different run does not see this checkpoint", other.load() == {})

    # A different activity with the same key must also be separate.
    same_key_other_activity = _Checkpoint("detect", key)
    check("a different activity with the same key is separate",
          same_key_other_activity.load() == {})

    # A crash mid-append leaves a torn line. It must cost one record, not the
    # file, or the first crash discards everything before it.
    with cp.path.open("a", encoding="utf-8") as handle:
        handle.write('{"record_id": "rec-3", "transcr')
    torn = _Checkpoint("transcribe", key).load()
    check("a torn final line is skipped, not fatal", len(torn) == 3,
          f"{len(torn)} records survived a truncated write")

    cp.clear()
    check("clearing removes the checkpoint", _Checkpoint("transcribe", key).load() == {})
    check("the file is gone from disk", not cp.path.exists(), str(cp.path))

    other.clear()
    same_key_other_activity.clear()

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"\nV6b: {passed} passed, {failed} failed")
    print("\n  This tests the mechanism. That transcription actually uses it is")
    print("  visible in the worker log, which reports how many records were")
    print("  resumed from checkpoint on every run.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
