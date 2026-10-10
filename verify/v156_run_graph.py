"""U163: a custom pipeline's run says which step waits for which, so a screen can draw the run as the graph it is, and says nothing else of the definition.

The run of a registered pipeline version carries its steps as names, kinds and what each waits for, and nothing more: not the scripts, the inputs or the
settings the version was uploaded with. A run that is not tied to a version with steps, and a built-in kind of run, carry none. Another organisation's
run is not found. Everything is made up in the verification organisation and removed afterwards, whether or not the check passes.

    docker compose exec -T munitas-api python /verify/v156_run_graph.py
"""

from __future__ import annotations

import json
import sys
import uuid

sys.path.insert(0, "/app")

from common import CANARY, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary

DEPARTMENT = "d0000000-0000-4000-8000-0000000000c1"
ENGINEER = "canary-engineer"
OUTSIDER = "neighbour-custodian"  # somebody from another test organisation

STEPS = [
    {"name": "prepare", "kind": "script", "script": "steps/secret_prepare.py", "writes_files": True, "memory_mb": 4096},
    {"name": "left", "kind": "block", "block": "transcribe", "depends_on": ["prepare"], "inputs": {"token": "do-not-show"}},
    {"name": "right", "kind": "script", "script": "steps/right.py", "depends_on": ["prepare"]},
    {"name": "decide", "kind": "gate", "to_class": "OPEN_FOR_ANNOTATION", "depends_on": ["left", "right"]},
]


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    tag = uuid.uuid4().hex[:8]
    pipeline, version = str(uuid.uuid4()), str(uuid.uuid4())
    runs = {name: str(uuid.uuid4()) for name in ("custom", "no_version", "empty", "builtin")}
    empty_version = str(uuid.uuid4())
    pipelines = [pipeline]
    try:
        with db() as conn:
            conn.execute("insert into pipeline (id, tenant_id, name, department_id, registered_by) values (%s, %s, %s, %s, %s)", (pipeline, CANARY, f"u163-{tag}", DEPARTMENT, ENGINEER))
            conn.execute("insert into pipeline_version (id, pipeline_id, version, dag_config, config_hash, code_object_key, uploaded_by) values (%s, %s, 1, %s::jsonb, %s, %s, %s)",
                         (version, pipeline, json.dumps({"name": f"u163-{tag}", "steps": STEPS}), "sha256:u163", f"u163/{tag}/scripts.zip", ENGINEER))
            conn.execute("insert into pipeline_version (id, pipeline_id, version, dag_config, config_hash, code_object_key, uploaded_by) values (%s, %s, 2, %s::jsonb, %s, %s, %s)",
                         (empty_version, pipeline, json.dumps({"name": f"u163-{tag}", "steps": []}), "sha256:u163b", f"u163/{tag}/scripts2.zip", ENGINEER))
            for name, kind, version_id in (("custom", "dag", version), ("no_version", "dag", None), ("empty", "dag", empty_version), ("builtin", "deidentify", None)):
                conn.execute("insert into pipeline_run (id, tenant_id, dataset, workflow_id, triggered_by, pipeline_kind, pipeline_version_id, status) values (%s, %s, %s, %s, %s, %s, %s, 'running')",
                             (runs[name], CANARY, f"u163-{tag}", f"u163-{uuid.uuid4()}", ENGINEER, kind, version_id))
            for step, state in (("prepare", "succeeded"), ("left", "running")):
                conn.execute("insert into pipeline_step_run (id, pipeline_run_id, step_name, status, started_at) values (%s, %s, %s, %s, now())", (str(uuid.uuid4()), runs["custom"], step, state))

        def read(name: str, who: str = ENGINEER):
            return api("GET", f"/pipeline-runs/{runs[name]}", headers=bearer_for(who))

        heading("A custom pipeline's run carries the shape of its steps")
        r = read("custom")
        check("the run is read", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
        body = r.json() if r.status_code == 200 else {}
        dag = body.get("dag") or []
        check("every step is there, in the order the version lists them", [s.get("name") for s in dag] == [s["name"] for s in STEPS], str([s.get("name") for s in dag]))
        check("each step says its kind and which steps it waits for",
              [(s.get("name"), s.get("kind"), s.get("depends_on")) for s in dag] == [(s["name"], s["kind"], s.get("depends_on", [])) for s in STEPS], str(dag)[:200])
        check("only the name, the kind and what it waits for: nothing else of the definition", all(set(s) == {"name", "kind", "depends_on"} for s in dag), str([sorted(s) for s in dag]))
        text = r.text
        check("no script, input or setting of the version is in the answer", all(w not in text for w in ("secret_prepare", "do-not-show", "4096", "scripts.zip")))
        check("the records of the steps that have started are still there", sorted(s["action"] for s in body.get("steps", [])) == ["left", "prepare"], str(body.get("steps"))[:160])

        heading("A run with no steps to draw says so, and is not refused")
        for name, why in (("no_version", "not tied to a pipeline version"), ("empty", "its version has no steps")):
            r = read(name)
            check(f"a custom run {why}: read, with no graph", r.status_code == 200 and r.json().get("dag") is None, f"{r.status_code} {r.text[:100]}")
        r = read("builtin")
        check("a built-in kind of run carries no graph", r.status_code == 200 and r.json().get("dag") is None, f"{r.status_code} {r.text[:100]}")

        heading("Another organisation's run is not found")
        check("a person of another organisation is told there is no such run", read("custom", OUTSIDER).status_code == 404)
        check("a request with no session is refused", api("GET", f"/pipeline-runs/{runs['custom']}").status_code == 401)
    finally:
        with db() as conn:
            for run in runs.values():
                conn.execute("delete from pipeline_step_run where pipeline_run_id = %s", (run,))
                conn.execute("delete from pipeline_run where id = %s", (run,))
            for p in pipelines:
                conn.execute("delete from pipeline_version where pipeline_id = %s", (p,))
                conn.execute("delete from pipeline where id = %s", (p,))
        with db() as conn:
            left = conn.execute("select (select count(*) from pipeline where id = any(%s)) + (select count(*) from pipeline_run where id = any(%s)) as n",
                                (pipelines, list(runs.values()))).fetchone()["n"]
        check("what this check made is gone", left == 0, f"{left} row(s) left")
    return summary("U163")


if __name__ == "__main__":
    sys.exit(main())
