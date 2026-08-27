# Agent Observability and Online Evals

## Trace contract

When OpenTelemetry is enabled, Agent execution emits bounded metadata-only spans:

| Span | Required attributes |
| --- | --- |
| `agent.run` | run ID, trace ID, user/org ID, step budget |
| `agent.tool_call` | run ID, step, tool name, agent type, read-only flag, outcome |
| `agent.retrieval` | run ID, step, retrieval tool, result count, success flag |
| `agent.state_transition` | run ID, trace ID, source and target status |

Request text, tool inputs, retrieved document text, citations, and model output are never added to span attributes. Durable, queryable metadata remains in `agent_audit_events` under the same `trace_id`.

## Online failure to regression case

1. A terminal Agent error or failed tool call creates a redacted `pending_review` row in `agent_eval_candidates`.
2. An administrator reviews the trace/audit evidence and either rejects it or supplies a deliberately authored `evaluation_input` and `expected_outcome`.
3. `POST /api/analytics/agent-evals/export` returns approved cases in the versioned Agent-eval dataset shape. Exported cases remain available in later exports.
4. CI runs cases with `eval.agent_online_eval.run_cases` and fails the build when `regression_gate` exceeds its allowed failures.

The automatic capture contains only a salted goal hash and redacted evidence. It intentionally does not copy production legal requests into the evaluation corpus.

## Admin API

- `GET /api/analytics/agent-evals/candidates?status=pending_review`
- `POST /api/analytics/agent-evals/candidates/{id}/approve`
- `POST /api/analytics/agent-evals/candidates/{id}/reject`
- `POST /api/analytics/agent-evals/export`

Approval requires `evaluation_input.goal` and an `expected_outcome.terminal_status` of `completed`, `error`, or `cancelled`. Regression cases should target isolated test infrastructure and read-only flows unless a dedicated sandbox is explicitly configured.
