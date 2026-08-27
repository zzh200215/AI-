# MCP Policy-as-Code

MCP authorization is governed by a versioned policy document instead of a
role/tool matrix spread across the executor and approval service.

The built-in policy lives in `app/mcp/policy.py` as reviewable data. Each rule
declares:

- `allowed_agents`: canonical Agent identities allowed to invoke the tool;
- `data_scope`: the domain the tool can access (`documents`, `tasks`, `legal`,
  or `organization`);
- `requires_approval`: whether a human approval is mandatory;
- `risk_level`: `low`, `medium`, `high`, or `critical`.

Validated documents can be saved as drafts and atomically activated through the
admin API. The active policy is loaded for each DB-backed authorization check,
so a long-running Agent cannot retain a stale tool ACL. A caller can provide a
`data_scopes` list or `risk_threshold` in the policy context; missing scope or
an exceeded threshold fails closed.

Every permission decision is persisted with `policy_version`, `rule_id`,
`data_scope`, `risk_level`, and Agent identity. The replay endpoint compares
the historical decision with the currently active policy and reports drift.

## API

- `GET /api/analytics/mcp-policies`
- `POST /api/analytics/mcp-policies` to save a validated draft
- `POST /api/analytics/mcp-policies/{version}/activate`
- `GET /api/analytics/mcp-policies/replay/{run_id}`

Run `python -m alembic upgrade head` before enabling database-backed policy
activation. The initial built-in policy remains the fallback until a database
version is activated.
