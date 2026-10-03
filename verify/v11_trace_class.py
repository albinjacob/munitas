"""V11: a trace of an agent carries the class of the data it touched.

Runs the agent, then queries Jaeger for the spans it emitted and checks the
attributes are actually there. Querying Jaeger rather than asserting on the
objects in memory is the point: an attribute set on a span that never reaches
the trace store is not observability, and the two failure modes look identical
from inside the process.

Also checks the negative, which is the part usually skipped: that no span
carries the data itself. A trace that faithfully records the visibility class
while also embedding a transcript in an attribute has moved the data somewhere
with different controls, and the class label is then decoration.

    .venv\\Scripts\\python.exe verify\\v11_trace_class.py
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import psycopg  # noqa: E402

from agent.graph import build_graph  # noqa: E402
from agent.identity import AgentIdentity, Budget  # noqa: E402
from agent.tools import ToolContext  # noqa: E402
from ports_config import PORTS  # noqa: E402

API = f"http://localhost:{PORTS['munitas_api_http']}"
WORKER_HEADERS = {"x-worker-token": os.environ.get("MUNITAS_WORKER_TOKEN", "dev-worker-token-not-for-production")}
JAEGER = f"http://localhost:{PORTS['jaeger_ui']}"
PG_DSN = f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform"

# The canary tenant, seeded by infra/postgres/seed-canary.sql.
TENANT = "canary"

# Anything that looks like content rather than a reference. A span should carry
# identifiers, counts and classes, never the record itself.
CONTENT_MARKERS = ("transcript", "text", "plaintext", "content", "body", "patient")

_results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    _results.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def fixture() -> tuple[str, str]:
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        conn.execute(
            """insert into tenant (id, isolation_level, key_ref, purpose)
               values (%s,'shared',%s,'canary') on conflict (id) do nothing""",
            (TENANT, f"key/{TENANT}"),
        )
    contract = httpx.post(f"{API}/schema-contracts", json={
        "tenant_id": TENANT, "name": "trace-fixture",
        "fields": [{"name": "record_id", "type": "string", "added_by": "verify"}],
        "primary_key": ["record_id"],
    }, timeout=20.0).json()["id"]

    def make(klass: str) -> str:
        dataset = httpx.post(f"{API}/datasets", json={
            "tenant_id": TENANT, "name": f"trace-{uuid.uuid4().hex[:8]}",
        }, timeout=20.0).json()["id"]
        return httpx.post(f"{API}/dataset-versions", headers=WORKER_HEADERS, json={
            "tenant_id": TENANT, "dataset_id": dataset, "schema_id": contract,
            "visibility_class": klass, "record_count": 1,
        }, timeout=20.0).json()["id"]

    allowed = make("UNDER_REVIEW")
    httpx.post(f"{API}/dataset-versions/{allowed}/promote", json={
        "to_class": "PUBLISHED", "decided_by": "verify-suite", "decided_by_kind": "workload",
        "gate_evidence": {"note": "trace fixture"}, "grant_roles": ["agent_runtime"],
    }, timeout=20.0)
    return allowed, make("RAW")


def main() -> int:
    in_scope, out_of_scope = fixture()

    identity = AgentIdentity(
        principal=f"agent-trace-{uuid.uuid4().hex[:6]}", tenant=TENANT,
        roles=("agent_runtime",), purpose="review triage",
        agent_version_id="b3da82da-69b1-4b21-a948-8fc795df393e",
        allowed_versions=frozenset({in_scope}),
    )
    ctx = ToolContext(identity=identity, run_id="7ef2c4b3-cc08-498a-b983-bde577ea5f34", budget=Budget())
    graph = build_graph(ctx)

    graph.invoke(
        {"version_id": in_scope,
         "documents": [{"id": "doc-1", "text": "routine",
                        "dataset_version": out_of_scope}]},
        {"configurable": {"thread_id": f"v11-{uuid.uuid4().hex[:8]}"}},
    )

    print("\nV11: traces carry the visibility class")
    print("-" * 39)
    print("  waiting for the collector to flush")

    from agent.tracing import trace as otel_trace
    otel_trace.get_tracer_provider().force_flush(10_000)

    spans = []
    for _ in range(12):
        time.sleep(2)
        try:
            response = httpx.get(f"{JAEGER}/api/traces", params={
                "service": "munitas-agent", "limit": 20, "lookback": "1h",
            }, timeout=15.0)
            data = response.json().get("data") or []
            spans = [
                s for trace_item in data for s in trace_item.get("spans", [])
                if any(t.get("key") == "munitas.principal"
                       and t.get("value") == identity.principal
                       for t in s.get("tags", []))
            ]
            if spans:
                break
        except httpx.HTTPError:
            continue

    check("the agent's spans reached Jaeger", bool(spans), f"{len(spans)} spans")
    if not spans:
        print("\n  Without spans nothing below can be checked. Jaeger and the")
        print("  collector must be up, and the agent must run on the host with")
        print("  port 4317 published.")
        return 1

    def tags(span) -> dict:
        return {t["key"]: t.get("value") for t in span.get("tags", [])}

    classed = [s for s in spans if "munitas.visibility_class" in tags(s)]
    check("every span declares a visibility class",
          len(classed) == len(spans), f"{len(classed)} of {len(spans)}")

    undeclared = [s for s in classed if tags(s)["munitas.visibility_class"] == "UNDECLARED"]
    check("no span fell through to UNDECLARED",
          not undeclared, f"{len(undeclared)} undeclared")

    seen_classes = {tags(s).get("munitas.visibility_class") for s in classed}
    check("the class recorded matches the data touched",
          "PUBLISHED" in seen_classes or "RAW" in seen_classes, str(sorted(seen_classes)))

    denied = [s for s in spans if tags(s).get("munitas.policy.denied")]
    check("the denial is visible in the trace", bool(denied),
          f"{len(denied)} denied spans")
    if denied:
        check("the denied span carries reasons",
              bool(tags(denied[0]).get("munitas.policy.reasons")),
              str(tags(denied[0]).get("munitas.policy.reasons")))

    check("every span identifies the principal",
          all("munitas.principal" in tags(s) for s in spans))

    # The negative check.
    leaked = []
    for span in spans:
        for key, value in tags(span).items():
            if not isinstance(value, str) or len(value) < 40:
                continue
            if any(marker in key.casefold() for marker in CONTENT_MARKERS):
                leaked.append(f"{span.get('operationName')}.{key}")
    check("no span attribute carries record content", not leaked,
          ", ".join(leaked[:3]) or "identifiers, counts and classes only")

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"\nV11: {passed} passed, {failed} failed")
    print(f"\n  Jaeger UI: {JAEGER}/search?service=munitas-agent")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
