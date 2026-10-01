"""V6, the third leg: the real pipeline workflow retries a lost step correctly.

Two other scripts cover V6. v6_idempotency.py proves a repeated action keyed by
an idempotency key stays single, inside the suite. v6_durable_retry.py proves,
by hand, that a worker killed mid-run resumes and produces one output per step.
This one sits between them: it runs in seconds, needs no GPU, no models and no
running stack, and so can run on every change.

It runs the real DeidentificationPipeline against Temporal's time-skipping test
server, with every activity replaced by a fake of the same name. The fake for
`transcribe` blocks without ever heartbeating, which is exactly what a killed
worker looks like to the server, and the test clock is moved forward by hand
over the four-minute heartbeat wait instead of sitting through it. What it checks is therefore the
workflow's own retry configuration, not Temporal and not the activities:

  * a step that stops heartbeating is retried, and the retry succeeds
  * every other step still runs exactly once
  * both attempts of the retried step carry the same idempotency key, which is
    what lets the control plane return the original run instead of a second one
  * a step that never recovers (silent once, then failing on every retry) fails
    the run after a bounded number of attempts instead of retrying for ever, and
    the run record is still closed as failed

What it cannot show is that the real steps are safe to repeat (their checkpoint
files, their database writes); that is the by-hand script's job.

Needs the packages worker.main needs, and downloads Temporal's test server
binary the first time it runs, from temporal.download into the temp folder
(about 60 MB on Windows, cached afterwards). Set MUNITAS_TEST_SERVER_DIR to a
folder of your choosing to keep the binary there instead, which is how CI keeps
it between runs; the folder is created if it does not exist.

Exit codes: 0 every check passed, 1 a check failed, 3 the test server could not
be started (nothing was tested, and that is not a failure of the workflow).

    .venv\\Scripts\\python.exe verify\\v6c_retry_policy.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from temporalio import activity  # noqa: E402
from temporalio.exceptions import ApplicationError  # noqa: E402
from temporalio.client import WorkflowFailureError  # noqa: E402
from temporalio.service import RPCError  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from worker.workflows import DeidentificationPipeline  # noqa: E402

_results: list[tuple[str, bool, str]] = []

# Real-time ceiling for each scenario. The test server skips the heartbeat wait,
# so a run takes seconds; if one takes this long, time was not skipped and the
# test is reporting that rather than hanging for minutes.
REAL_TIME_LIMIT_SECONDS = 60


def check(label: str, ok: bool, detail: str = "") -> None:
    _results.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


class Calls:
    """What the fake activities saw, per activity name."""

    def __init__(self) -> None:
        self.attempts: dict[str, list[int]] = {}
        self.transcribe_keys: list[str] = []
        self.closed_with: list[dict] = []
        # True from the moment a step starts hanging until the next attempt of
        # it begins. The test clock only moves while this is true.
        self.hung = False
        # The test server's clock when each attempt of transcribe began, so the
        # time taken to notice a lost step can be measured, not assumed.
        self.clock = None
        self.started_at: list = []

    def saw(self, name: str) -> None:
        self.attempts.setdefault(name, []).append(activity.info().attempt)


def build_fakes(calls: Calls, fail_from_attempt: int | None):
    """Fake activities under the real names. `transcribe` goes silent on its first
    attempt, without a heartbeat. From `fail_from_attempt` on it raises an error
    instead of answering; with None, the second attempt answers.

    Only the first attempt hangs, in both scenarios. The test server can move its
    clock over the first silent attempt and does not over a second one while it is
    in flight, so a step that never answers is modelled as silent once and then
    failing on every retry."""

    @activity.defn(name="open_pipeline_run")
    async def open_pipeline_run(params: dict) -> dict:
        calls.saw("open_pipeline_run")
        return {"pipeline_run_id": "run-under-test", "task_credential": "credential"}

    @activity.defn(name="check_score_store")
    async def check_score_store() -> None:
        calls.saw("check_score_store")

    @activity.defn(name="ingest")
    async def ingest(params: dict) -> dict:
        calls.saw("ingest")
        return {"records_key": "ingest-records", "version_id": "v-ingest",
                "bucket": "bucket", "prefix": "prefix"}

    @activity.defn(name="transcribe")
    async def transcribe(params: dict) -> dict:
        calls.saw("transcribe")
        calls.transcribe_keys.append(params["idempotency_key"])
        calls.hung = False
        if calls.clock is not None:
            calls.started_at.append(await calls.clock())
        attempt = activity.info().attempt
        if attempt == 1:
            # A killed worker sends nothing further. Sleeping without a heartbeat
            # is the same thing as seen from the server, and cancellation ends it
            # when the server gives up on this attempt.
            calls.hung = True
            await asyncio.sleep(3600)
        if fail_from_attempt is not None and attempt >= fail_from_attempt:
            raise ApplicationError("transcription failed on this attempt")
        return {"records_key": "transcribed-records", "version_id": "v-transcribe"}

    @activity.defn(name="detect")
    async def detect(params: dict) -> dict:
        calls.saw("detect")
        return {"records_key": "detected-records", "version_id": "v-detect"}

    @activity.defn(name="handoff")
    async def handoff(params: dict) -> dict:
        calls.saw("handoff")
        return {"version_id": "v-handoff"}

    @activity.defn(name="redact")
    async def redact(params: dict) -> dict:
        calls.saw("redact")
        return {"records_key": "redacted-records", "version_id": "v-redact"}

    @activity.defn(name="verify")
    async def verify(params: dict) -> dict:
        calls.saw("verify")
        return {"leak_detail": {}}

    @activity.defn(name="record_gate_decision")
    async def record_gate_decision(params: dict) -> dict:
        calls.saw("record_gate_decision")
        return {"recommendation": "promote"}

    @activity.defn(name="close_pipeline_run")
    async def close_pipeline_run(params: dict) -> None:
        calls.saw("close_pipeline_run")
        calls.closed_with.append(params)

    return [open_pipeline_run, check_score_store, ingest, transcribe, detect, handoff,
            redact, verify, record_gate_decision, close_pipeline_run]


def pipeline_params() -> dict:
    return {
        "dataset": "encounters",
        "limit": 1,
        "corpus": "corpus",
        "tenant": "canary",
        "trigger_kind": "manual",
        "triggered_by": "canary-engineer",
        # Passed in, as a corpus run does, so resolve_actions is not needed.
        "actions": {name: str(uuid.uuid4())
                    for name in ("ingest", "transcribe", "detect", "handoff", "redact")},
    }


async def drive_clock(env: WorkflowEnvironment, calls: Calls, finished: asyncio.Event) -> None:
    """Move the test server's clock forward while a hung step is pending.

    The time-skipping server does not skip over an activity that is still in
    flight, so the heartbeat wait would otherwise run in real time. Advancing it
    by hand, in steps of 30 seconds and only while a step is hanging, keeps every
    other step safe: they finish in milliseconds of real time and carry timeouts
    of a minute or more, so a 30 second step can never land inside one.
    """
    while not finished.is_set():
        if calls.hung:
            await env.sleep(timedelta(seconds=5))
        else:
            await asyncio.sleep(0.01)


async def run_on(env: WorkflowEnvironment, calls: Calls, fail_from_attempt: int | None):
    queue = f"v6c-{uuid.uuid4().hex[:8]}"
    finished = asyncio.Event()
    calls.clock = env.get_current_time
    async with Worker(env.client, task_queue=queue,
                      workflows=[DeidentificationPipeline],
                      activities=build_fakes(calls, fail_from_attempt)):
        handle = await env.client.start_workflow(
            DeidentificationPipeline.run, pipeline_params(),
            id=f"v6c-{uuid.uuid4().hex[:8]}", task_queue=queue)
        driver = asyncio.create_task(drive_clock(env, calls, finished))
        try:
            return await asyncio.wait_for(handle.result(), REAL_TIME_LIMIT_SECONDS)
        except asyncio.TimeoutError:
            return "timed out in real time"
        except WorkflowFailureError as exc:
            return exc
        finally:
            # Cancelled, not awaited: once the run has ended the server has
            # nothing left to advance to, so a clock step already in flight can
            # only time out. That timeout is expected and of no interest.
            finished.set()
            driver.cancel()
            try:
                await driver
            except (asyncio.CancelledError, RPCError):
                pass


class TestServerUnavailable(Exception):
    """Temporal's test server could not be obtained or started. Kept apart from a
    failed check on purpose: a download that fails on a bad day says nothing about
    the workflow, and reporting it as a regression would send someone looking for
    a retry bug that is not there."""


def test_server_options() -> dict:
    folder = os.environ.get("MUNITAS_TEST_SERVER_DIR")
    if not folder:
        return {}
    # The SDK does not create it: a missing folder fails with "path not found".
    os.makedirs(folder, exist_ok=True)
    return {"download_dest_dir": folder}


async def run_scenario(calls: Calls, fail_from_attempt: int | None):
    """One scenario on its own test server. Two scenarios on one server is not safe:
    the first one's clock driver is cancelled with a step possibly still in flight,
    and the server's time-skipping then misbehaves for the next workflow, which
    stopped advancing from its very first step."""
    try:
        env = await WorkflowEnvironment.start_time_skipping(**test_server_options())
    except Exception as exc:
        # Starting is the only place a download or a launch can fail; anything
        # after this point is the workflow's behaviour and stays a check.
        raise TestServerUnavailable(str(exc)) from exc
    async with env:
        return await run_on(env, calls, fail_from_attempt)


async def main() -> int:
    print("\nV6c: the workflow retries a step that stops heartbeating")
    print("-" * 56)
    print("  each scenario starts Temporal's time-skipping test server (the first run downloads it)")
    # Scenario 1: the step is lost once and then recovers.
    print("\n  scenario 1: transcribe stops heartbeating on attempt 1, answers on attempt 2")
    calls = Calls()
    outcome = await run_scenario(calls, fail_from_attempt=None)
    finished = isinstance(outcome, dict)
    check("the run finished in real time, so the heartbeat wait was skipped, not sat through",
          outcome != "timed out in real time",
          f"no result within {REAL_TIME_LIMIT_SECONDS} s of real time; transcribe attempts so far: "
          f"{calls.attempts.get('transcribe')}" if outcome == "timed out in real time" else "")
    check("the workflow completed despite the lost step", finished,
          "" if finished else f"{type(outcome).__name__}: {str(outcome)[:140]}")
    check("the lost step was retried and the retry ran",
          calls.attempts.get("transcribe") == [1, 2],
          f"attempts seen: {calls.attempts.get('transcribe')}")
    # The heartbeat timeout is four minutes. Without one, a lost step would
    # still be retried, but only when its whole time budget ran out, which for
    # this run is ten minutes or more. Six minutes separates the two.
    waited = (calls.started_at[1] - calls.started_at[0]) if len(calls.started_at) >= 2 else None
    check("the lost step was noticed within six minutes, by its heartbeat, not its full time budget",
          waited is not None and timedelta(0) < waited <= timedelta(minutes=6),
          f"{waited} of server time between attempt 1 and attempt 2" if waited is not None else "no second attempt")
    for step in ("ingest", "detect", "handoff", "redact", "verify"):
        check(f"{step} ran exactly once", calls.attempts.get(step) == [1],
              f"attempts seen: {calls.attempts.get(step)}")
    check("both attempts carried the same idempotency key",
          len(calls.transcribe_keys) == 2 and len(set(calls.transcribe_keys)) == 1,
          f"{len(calls.transcribe_keys)} attempts, {len(set(calls.transcribe_keys))} distinct key(s)")
    if finished:
        check("the result holds one output per step",
              all(k in outcome for k in ("ingest", "transcribe", "detect", "handoff",
                                         "redact", "verify", "gate")),
              f"keys: {sorted(outcome)}")
    check("the run record was closed as succeeded",
          [c.get("status") for c in calls.closed_with] == ["succeeded"],
          f"closed with: {[c.get('status') for c in calls.closed_with]}")

    # Scenario 2: the step is never recovered.
    print("\n  scenario 2: transcribe goes silent on attempt 1, then fails on every later attempt")
    calls = Calls()
    outcome = await run_scenario(calls, fail_from_attempt=2)
    check("the run finished in real time", outcome != "timed out in real time",
          f"no result within {REAL_TIME_LIMIT_SECONDS} s of real time; transcribe attempts so far: "
          f"{calls.attempts.get('transcribe')}" if outcome == "timed out in real time" else "")
    check("the workflow failed rather than retrying for ever",
          isinstance(outcome, WorkflowFailureError),
          type(outcome).__name__)
    seen = calls.attempts.get("transcribe", [])
    check("the number of attempts is bounded, and more than one",
          1 < len(seen) <= 5, f"attempts seen: {seen}")
    check("no later step ran after the failure",
          all(calls.attempts.get(s) is None for s in ("detect", "handoff", "redact", "verify")),
          f"ran: {sorted(k for k in calls.attempts if k not in ('open_pipeline_run', 'check_score_store', 'ingest', 'transcribe', 'close_pipeline_run'))}")
    check("the run record was still closed, as failed",
          [c.get("status") for c in calls.closed_with] == ["failed"],
          f"closed with: {[c.get('status') for c in calls.closed_with]}")

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"\nV6c retry policy: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    except TestServerUnavailable as exc:
        print("\nV6c could not run: Temporal's time-skipping test server could not be started.")
        print(f"  Cause: {exc}")
        print("  The workflow's retry settings were NOT tested. This is a problem getting or")
        print("  starting the test server (its download from temporal.download, or the folder")
        print("  it is saved to), not a failure of the workflow, so it exits 3, not 1.")
        code = 3
    sys.exit(code)
