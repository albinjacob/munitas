"""U147: a script step tells Temporal it is alive while it runs, so a lost result or a dead worker is noticed in a minute and a half, not thirty.

A step runs for minutes inside one blocking call. Without a heartbeat Temporal hears nothing until the activity returns, so a result lost on its way
back (the sandbox worker has logged "Network error while completing activity" three times in a day) left the run stuck for the whole start-to-close
time, and Temporal would then have run the step again. A step that writes files must not be run twice.

This checks the pieces that make that so, without a Temporal server, using the SDK's own test environment:
  * the helper heartbeats from a thread of its own for as long as the step runs, and stops before the activity returns;
  * a script step is given a heartbeat timeout of 90 seconds, a step that writes is never retried, a step that only reads is retried up to three times.

Whether Temporal then fails the step after the timeout is Temporal's own behaviour, tried by hand once (RUNBOOK) and not here.

    .venv\\Scripts\\python.exe verify\\v144_step_heartbeat.py
"""

from __future__ import annotations

import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from temporalio.testing import ActivityEnvironment  # noqa: E402

from common import check, heading, summary  # noqa: E402
from worker import dag_workflow  # noqa: E402
from worker.dag_activities import HEARTBEAT_SECONDS, heartbeating  # noqa: E402


def main() -> int:
    heading("The helper heartbeats while the step runs, and stops when it is over")
    beats: list[float] = []
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *details: beats.append(time.monotonic())

    def step() -> str:
        with heartbeating(every=0.05):
            time.sleep(0.5)
        return "done"

    began = time.monotonic()
    result = env.run(step)
    check("the step's own result is untouched", result == "done")
    check("it heartbeat many times while the step ran", len(beats) >= 5, str(len(beats)))
    check("and only while it ran", all(began <= b <= time.monotonic() for b in beats))
    count = len(beats)
    time.sleep(0.4)
    check("after the step returned it says nothing more, which is how a lost result is noticed", len(beats) == count, f"{count} then {len(beats)}")

    def failing() -> None:
        with heartbeating(every=0.05):
            time.sleep(0.2)
            raise RuntimeError("the step failed")

    beats.clear()
    try:
        env.run(failing)
        raised = False
    except RuntimeError:
        raised = True
    count = len(beats)
    time.sleep(0.3)
    check("a step that raises still stops the beating, and the error goes on", raised and len(beats) == count, f"{raised} {count} {len(beats)}")
    check("the beat is every 15 seconds, far inside the 90 the workflow allows", HEARTBEAT_SECONDS == 15 and dag_workflow.STEP_HEARTBEAT == timedelta(seconds=90))

    heading("What the workflow does when a step goes quiet")
    check("a step that writes files is never run again", dag_workflow.STEP_WRITES.maximum_attempts == 1)
    check("a step that only reads is run again, up to three times in all", dag_workflow.STEP_READS_ONLY.maximum_attempts == 3)
    source = Path(dag_workflow.__file__).read_text(encoding="utf-8")
    check("the script step is given the heartbeat timeout and chooses its retry by whether it writes",
          "heartbeat_timeout=STEP_HEARTBEAT" in source and 'retry_policy=STEP_WRITES if step.get("writes_files") is True else STEP_READS_ONLY' in source)
    return summary("v144")


if __name__ == "__main__":
    sys.exit(main())
