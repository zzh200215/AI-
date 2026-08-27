"""OpenTelemetry semantics for the Agent execution lifecycle.

Only bounded, non-content metadata is sent to tracing.  The database audit
trail remains the durable source for regulated investigations.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from app.core.telemetry import observe_span


@contextmanager
def observe_agent_run(*, run_id: int | None, trace_id: str | None, user_id: int | None,
                      organization_id: int | None, max_steps: int | None = None):
    with observe_span("agent.run", {
        "agent.run.id": run_id or 0,
        "agent.trace_id": trace_id or "unknown",
        "agent.user_id": user_id or 0,
        "agent.organization_id": organization_id or 0,
        "agent.max_steps": max_steps or 0,
    }) as span:
        yield span


@contextmanager
def observe_tool_call(*, run_id: int | None, trace_id: str | None, step: int | None,
                      tool_name: str, agent_type: str, read_only: bool):
    with observe_span("agent.tool_call", {
        "agent.run.id": run_id or 0,
        "agent.trace_id": trace_id or "unknown",
        "agent.step": step or 0,
        "agent.tool.name": tool_name,
        "agent.type": agent_type,
        "agent.tool.read_only": read_only,
    }) as span:
        yield span


def record_tool_outcome(span: Any, *, success: bool, status: str, duration_ms: int,
                        error_category: str | None = None) -> None:
    if span is None:
        return
    try:
        span.set_attribute("agent.tool.success", success)
        span.set_attribute("agent.tool.status", status)
        span.set_attribute("agent.tool.duration_ms", duration_ms)
        if error_category:
            span.set_attribute("agent.error.category", error_category)
    except Exception:  # observability must not affect execution
        pass


def record_retrieval_result(*, run_id: int | None, trace_id: str | None, step: int | None,
                            tool_name: str, result: dict[str, Any]) -> None:
    """Emit retrieval result shape, never retrieved document text or identifiers."""
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    candidates = (
        data.get("results")
        or data.get("items")
        or data.get("documents")
        or data.get("chunks")
        or []
    )
    if not isinstance(candidates, list):
        candidates = []
    with observe_span("agent.retrieval", {
        "agent.run.id": run_id or 0,
        "agent.trace_id": trace_id or "unknown",
        "agent.step": step or 0,
        "agent.retrieval.tool": tool_name,
        "agent.retrieval.result_count": len(candidates),
        "agent.retrieval.success": bool(result.get("success")),
    }):
        pass


def record_state_transition(*, run_id: int | None, trace_id: str | None,
                            from_status: str | None, to_status: str | None) -> None:
    if not to_status or from_status == to_status:
        return
    with observe_span("agent.state_transition", {
        "agent.run.id": run_id or 0,
        "agent.trace_id": trace_id or "unknown",
        "agent.state.from": from_status or "created",
        "agent.state.to": to_status,
    }):
        pass
