"""Start a pipeline run.

    python -m worker.run_pipeline --limit 6

The dataset actions are registered here rather than by the workflow, because an
action is a declared, named thing with a contract pair, and creating one as a
side effect of running it would make the registry a log of what happened rather
than a statement of what is allowed to happen.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

import httpx
import psycopg
from temporalio.client import Client
from temporalio.service import RPCError

from . import config
from .workflows import DeidentificationPipeline
from ports_config import PORTS

# name -> (source contract, target contract, output class)
ACTIONS = {
    "ingest": (None, "encounter_raw", "RAW"),
    "transcribe": ("encounter_raw", "encounter_transcribed", "RAW"),
    "detect": ("encounter_transcribed", "encounter_detected", "RAW"),
    # Label Studio tasks carry unredacted transcripts, so the handoff output is
    # RAW and gets a sealed version like everything else holding PHI.
    "handoff": ("encounter_detected", "encounter_detected", "RAW"),
    "redact": ("encounter_detected", "encounter_redacted", "UNDER_REVIEW"),
}

PG_DSN = f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform"


def ensure_actions(tenant: str | None = None) -> dict[str, str]:
    """Register the dataset actions, returning name to id.

    For this machine's configured tenant unless another is named. The seed
    scripts name theirs, so a freshly built worked example can run the
    de-identification pipeline from the console without this script having
    been run against it first.

    Written straight to PostgreSQL because the control plane has no action
    registration endpoint yet. That is a gap and is recorded as one rather than
    papered over: it means an action's contract pair is not validated at
    registration time the way a dataset version's is.
    """
    tenant = tenant or config.TENANT
    ids: dict[str, str] = {}
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        for name, (_, _, output_class) in ACTIONS.items():
            row = conn.execute(
                "select id from dataset_action where tenant_id = %s and name = %s",
                (tenant, name),
            ).fetchone()
            if row:
                ids[name] = str(row[0])
                continue
            action_id = str(uuid.uuid4())
            conn.execute(
                """insert into dataset_action
                     (id, tenant_id, name, output_class) values (%s, %s, %s, %s)""",
                (action_id, tenant, name, output_class),
            )
            ids[name] = action_id
    return ids


def ensure_tenant() -> None:
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        conn.execute(
            """insert into tenant (id, isolation_level, key_ref)
               values (%s, 'shared', %s) on conflict (id) do nothing""",
            (config.TENANT, f"key/{config.TENANT}"),
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=6,
                        help="records to process; the full corpus takes hours on this GPU")
    parser.add_argument("--dataset", default="encounters")
    parser.add_argument("--recall-threshold", type=float, default=0.95)
    parser.add_argument("--confidence-threshold", type=float, default=0.4)
    parser.add_argument("--to-class", default="OPEN_FOR_ANNOTATION")
    parser.add_argument("--workflow-id", default=None)
    parser.add_argument(
        "--triggered-by", default=None,
        help="directory id of the human running this; required unless --schedule-id is set",
    )
    parser.add_argument(
        "--schedule-id", default=None,
        help="set by schedule_pipeline.py for a recurring run; marks this run as scheduled",
    )
    args = parser.parse_args()
    corpus = config.require_corpus_dir()

    # Refused here, before a Temporal workflow even starts, rather than deep
    # inside the first activity. An unattributed manual run is exactly the
    # audit gap trigger provenance exists to close, so it should not be able
    # to start and fail later; it should not start at all.
    if args.schedule_id:
        trigger_kind, triggered_by = "scheduled", None
    else:
        if not args.triggered_by:
            print("refusing to start: pass --triggered-by (a registered human) "
                  "or --schedule-id (for a recurring run)")
            return
        trigger_kind, triggered_by = "manual", args.triggered_by

    try:
        httpx.get(f"{config.API}/health", timeout=5.0, verify=config.api_verify()).raise_for_status()
    except Exception as exc:
        print(f"control plane not reachable at {config.API}: {exc}")
        return

    ensure_tenant()
    actions = ensure_actions()
    print(f"dataset actions: {json.dumps(actions, indent=2)}")

    client = await Client.connect(config.TEMPORAL)
    workflow_id = args.workflow_id or f"deid-{uuid.uuid4().hex[:10]}"

    print(f"starting workflow {workflow_id}")
    await client.start_workflow(
        DeidentificationPipeline.run,
        {
            "corpus": str(corpus),
            "limit": args.limit,
            "dataset": args.dataset,
            "actions": actions,
            # Stated by the caller rather than read again inside each activity.
            # For a corpus run this machine's configured tenant is the right
            # answer, and saying it here is what makes it a choice this program
            # made rather than a default five activities each fell back to.
            "tenant": config.TENANT,
            "recall_threshold": args.recall_threshold,
            "confidence_threshold": args.confidence_threshold,
            "to_class": args.to_class,
            "trigger_kind": trigger_kind,
            "triggered_by": triggered_by,
            "schedule_id": args.schedule_id,
        },
        id=workflow_id,
        task_queue=config.TASK_QUEUE,
    )

    print(f"  history at http://localhost:{PORTS['temporal_ui']}/namespaces/default/workflows/{workflow_id}")

    result = await wait_for(client, workflow_id)
    print(json.dumps(result, indent=2, default=str))


async def wait_for(client: Client, workflow_id: str, poll_seconds: int = 20) -> dict:
    """Wait for a workflow by polling, not by holding one long call open.

    `handle.result()` is a single long-poll. If anything interrupts it, the
    client raises `RPCError: operation was canceled` and looks like a failed
    pipeline. It is not: the workflow keeps running on the worker, which is the
    whole point of durable execution. Twice this made a healthy run look broken.

    Polling `describe()` reconnects each time, so a dropped connection costs one
    poll interval instead of the run. The client becoming detachable is also
    closer to how this would really be used, where nobody sits holding a socket
    open for an hour.
    """
    handle = client.get_workflow_handle(workflow_id)
    while True:
        try:
            description = await handle.describe()
            status = description.status.name if description.status else "RUNNING"
            if status != "RUNNING":
                break
        except RPCError as exc:
            print(f"  poll failed ({exc.status.name}), retrying in {poll_seconds}s")
        await asyncio.sleep(poll_seconds)

    print(f"  workflow {status}")
    if status != "COMPLETED":
        return {"workflow_id": workflow_id, "status": status,
                "note": "see the Temporal UI for the failure"}
    return await handle.result()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except config.RefusedPath as exc:
        raise SystemExit(str(exc))
