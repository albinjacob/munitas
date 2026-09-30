"""Traces that carry the visibility class of the data touched.

A trace of an agent that read UNDER_REVIEW data is
itself a record about UNDER_REVIEW data. Observability is where classification
usually leaks, because traces are shipped to a system chosen for convenience and
nobody thought of a span as a copy of the data.

Two rules follow, and both are enforced here rather than documented:

  * every span records the class of the data it touched, so a span is
    classifiable rather than anonymous
  * no span attribute carries the data itself, only identifiers, counts and
    classes

The collector stamps `munitas.visibility_class: UNDECLARED` on anything that
arrives without a class, so a span that forgets shows up as a problem rather
than passing as unclassified.
"""

from __future__ import annotations

from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from ports_config import PORTS

_configured = False


def configure(endpoint: str = f"http://localhost:{PORTS['jaeger_otlp_grpc']}") -> None:
    global _configured
    if _configured:
        return
    provider = TracerProvider(resource=Resource.create({
        "service.name": "munitas-agent",
        "munitas.component": "agent",
    }))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)
    _configured = True


def tracer():
    configure()
    return trace.get_tracer("munitas.agent")


@contextmanager
def tool_span(name: str, *, principal: str, tenant: str,
              dataset_version: str | None = None,
              visibility_class: str | None = None,
              agent_run_id: str | None = None,
              agent_version_id: str | None = None):
    """A span for one tool call.

    The class is required in spirit even though the signature allows None: a
    None becomes "UNDECLARED" rather than being omitted, so the gap is visible
    in Jaeger instead of looking like a span that touched nothing.

    `agent_run_id`/`agent_version_id` make a trace self-describing in
    Jaeger without a join back to Postgres to find out which run, and
    which sealed version of the agent's code, produced it.
    """
    with tracer().start_as_current_span(name) as span:
        span.set_attribute("munitas.principal", principal)
        span.set_attribute("munitas.tenant", tenant)
        span.set_attribute("munitas.visibility_class",
                           visibility_class or "UNDECLARED")
        if dataset_version:
            span.set_attribute("munitas.dataset_version", dataset_version)
        if agent_run_id:
            span.set_attribute("munitas.agent_run_id", agent_run_id)
        if agent_version_id:
            span.set_attribute("munitas.agent_version_id", agent_version_id)
        yield span


def record_decision(span, allowed: bool, reasons: list[str]) -> None:
    """Attach a policy decision to a span.

    Reasons are attached for denials as well as grants. A trace showing that an
    agent was refused, and why, is the artefact an investigation needs, and it
    is exactly the one that gets dropped when only successful calls are traced.
    """
    span.set_attribute("munitas.policy.allowed", allowed)
    span.set_attribute("munitas.policy.reasons", reasons)
    if not allowed:
        span.set_attribute("munitas.policy.denied", True)
