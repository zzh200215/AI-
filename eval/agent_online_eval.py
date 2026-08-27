"""Deterministic regression gate helpers for approved Agent online-eval cases."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


def evaluate_outcome(actual: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    expected_status = expected.get("terminal_status")
    if actual.get("status") != expected_status:
        reasons.append(f"status expected={expected_status} actual={actual.get('status')}")
    required_tools = set(expected.get("required_tool_names") or [])
    actual_tools = set(actual.get("tool_names") or [])
    missing_tools = sorted(required_tools - actual_tools)
    if missing_tools:
        reasons.append("missing tools: " + ", ".join(missing_tools))
    forbidden_categories = set(expected.get("forbidden_failure_categories") or [])
    actual_categories = set(actual.get("failure_categories") or [])
    forbidden_seen = sorted(forbidden_categories & actual_categories)
    if forbidden_seen:
        reasons.append("forbidden failures: " + ", ".join(forbidden_seen))
    return {"passed": not reasons, "reasons": reasons}


def regression_gate(results: list[dict[str, Any]], *, max_failures: int = 0) -> dict[str, Any]:
    failures = [item for item in results if not item.get("passed")]
    return {
        "passed": len(failures) <= max_failures,
        "total_cases": len(results),
        "failure_count": len(failures),
        "max_failures": max_failures,
        "failures": failures,
    }


async def run_cases(
    cases: list[dict[str, Any]],
    run_case: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    *,
    max_failures: int = 0,
) -> dict[str, Any]:
    """Execute approved cases through an injected runner and enforce the gate.

    Production/CI provides the runner so this module stays deterministic and
    does not own database credentials or model-provider setup.
    """
    results = []
    for case in cases:
        actual = await run_case(case["input"])
        verdict = evaluate_outcome(actual, case["expected"])
        results.append({"id": case.get("id"), **verdict})
    return regression_gate(results, max_failures=max_failures)
