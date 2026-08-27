"""MCP Registry — central hub for tool discovery, permission enforcement,
schema validation, invocation, and observability.

Subsystem boundaries
--------------------
- *Registry* owns the tool instance map and the call gateway.
- *Permissions* module owns the agent → tool ACL.
- *Schema* module owns the Tool ↔ MCP-spec conversion & argument validation.
- *AgentService* builds prompts and orchestrates the ReAct loop; it calls the
  registry to discover tools and execute them.

Usage
-----
    registry = MCPRegistry()
    registry.register_all()       # called once at startup

    # From AgentService:
    tools = registry.list_tools(agent_type="knowledge_agent")
    result = await registry.call_tool("document_search_tool", args, ...)
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.mcp.permissions import allowed_tools_for
from app.mcp.policy import policy_engine
from app.mcp.schema import tool_to_mcp_spec, trim_sensitive_args, validate_tool_args
from app.services.agent.agent_approval_service import agent_approval_service
from app.tools.base import BaseAgentTool
from app.tools.document_tool import DocumentConflictTool, DocumentRiskTool, DocumentSearchTool, DocumentSummaryTool
from app.tools.legal_tool import LegalConsultationTool, LegalContractReviewTool, LegalDraftTool
from app.tools.sql_tool import SQLTool
from app.tools.task_tool import TaskCreateTool, TaskQueryTool

# ── Tool catalogue ─────────────────────────────────────────────────────
# Central registry of every tool instance.  Adding a new tool here makes
# it available via MCP discovery to any agent whose permissions include it.
_TOOL_INSTANCES: dict[str, BaseAgentTool] = {
    "document_search_tool": DocumentSearchTool(),
    "document_summary_tool": DocumentSummaryTool(),
    "document_risk_tool": DocumentRiskTool(),
    "document_conflict_tool": DocumentConflictTool(),
    "task_create_tool": TaskCreateTool(),
    "task_query_tool": TaskQueryTool(),
    "sql_query_tool": SQLTool(),
    "legal_consultation_tool": LegalConsultationTool(),
    "legal_contract_review_tool": LegalContractReviewTool(),
    "legal_draft_tool": LegalDraftTool(),
}


# ── Observability hooks ────────────────────────────────────────────────
# External callbacks that fire before / after every tool invocation.
# AgentService (or a future metrics service) can attach to these.

_OnBeforeCall = Callable[[str, dict[str, Any], str], None]
"""Args: (tool_name, args, agent_type)"""

_OnAfterCall = Callable[[str, dict[str, Any], str, dict[str, Any], float], None]
"""Args: (tool_name, args, agent_type, result, duration_s)"""


# ── MCP error codes ────────────────────────────────────────────────────

MCP_ERR_TOOL_NOT_FOUND = {"code": "MCP_TOOL_NOT_FOUND", "http": 404}
MCP_ERR_PERMISSION_DENIED = {"code": "MCP_PERMISSION_DENIED", "http": 403}
MCP_ERR_VALIDATION = {"code": "MCP_VALIDATION_ERROR", "http": 400}
MCP_ERR_INTERNAL = {"code": "MCP_INTERNAL_ERROR", "http": 500}


class MCPRegistry:
    """Manages tool lifecycle, permission checks, and invocation dispatch."""

    def __init__(self) -> None:
        self._before_call: list[_OnBeforeCall] = []
        self._after_call: list[_OnAfterCall] = []

    # ── Lifecycle ───────────────────────────────────────────────────────

    def register_all(self) -> None:
        """Idempotent init — ensures all tool instances are ready.

        Currently the instance dict is created at module level, so this is
        a no-op.  When tools gain stateful lifecycle (e.g. lazy DB connects)
        this method becomes the place to initialise them.
        """
        _ = _TOOL_INSTANCES  # ensure dict is materialised

    def get_tool(self, name: str) -> BaseAgentTool | None:
        return _TOOL_INSTANCES.get(name)

    # ── Tool discovery (MCP `tools/list`) ───────────────────────────────

    def list_all_tools(self) -> list[dict[str, Any]]:
        """Return every known tool as an MCP tool spec."""
        return [tool_to_mcp_spec(t) for t in _TOOL_INSTANCES.values()]

    def list_tools_for(self, agent_type: str, db: Session | None = None) -> list[dict[str, Any]]:
        """Return only the tools that ``agent_type`` is allowed to call."""
        allowed = sorted(policy_engine.allowed_tools_for(agent_type, db))
        return [tool_to_mcp_spec(_TOOL_INSTANCES[name]) for name in allowed if name in _TOOL_INSTANCES]

    # ── Observability hooks ─────────────────────────────────────────────

    def on_before_call(self, cb: _OnBeforeCall) -> None:
        self._before_call.append(cb)

    def on_after_call(self, cb: _OnAfterCall) -> None:
        self._after_call.append(cb)

    # ── Tool invocation (MCP `tools/call`) ──────────────────────────────

    async def call_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        agent_type: str = "general_agent",
        user_id: int | None = None,
        db: Session | None = None,
        agent_run_id: int | None = None,
        skip_approval: bool = False,
    ) -> dict[str, Any]:
        """Discover → authorise → validate → invoke → return.

        Steps
        1. Lookup tool by name.
        2. Permission check (agent_type vs tool).
        3. JSON Schema validation of args.
        4. Inject auto-context fields (user_id, db).
        5. Call the tool.
        6. Normalise the result into a standard dict.
        7. Fire after-call hooks (for observability).
        """
        started = time.time()

        # ── 1. Lookup ────────────────────────────────────────────────
        tool = self.get_tool(tool_name)
        if tool is None:
            return self._error_response(
                MCP_ERR_TOOL_NOT_FOUND,
                f"Unknown tool: {tool_name}",
                data={"tool_name": tool_name},
            )

        # ── 2. Permission check ──────────────────────────────────────
        declared_contract = getattr(tool, "contract", None)
        policy = policy_engine.evaluate(
            agent_type=agent_type,
            tool_name=tool_name,
            db=db,
            contract=declared_contract if declared_contract is not None else None,
        )
        if not policy.allowed:
            allowed = sorted(allowed_tools_for(agent_type, db=db))
            return self._error_response(
                MCP_ERR_PERMISSION_DENIED,
                f"Agent '{agent_type}' is not allowed to call '{tool_name}'",
                data={
                    "agent_type": agent_type,
                    "requested_tool": tool_name,
                    "allowed_tools": allowed,
                    "policy_version": policy.policy_version,
                    "rule_id": policy.rule_id,
                    "data_scope": policy.data_scope,
                    "risk_level": policy.risk_level,
                },
            )

        # ── 3. Inject context ────────────────────────────────────────
        merged = dict(args)
        if user_id is not None and "user_id" in tool.auto_context_fields:
            merged.setdefault("user_id", user_id)
        if db is not None and "db" in tool.auto_context_fields:
            merged.setdefault("db", db)

        # ── 4. Schema validation (after injection — auto-context
        #      fields like user_id/db are required by the schema)  ──
        try:
            validate_tool_args(tool, merged)
        except Exception:
            return self._error_response(
                MCP_ERR_VALIDATION,
                "工具参数校验失败",
                data={"tool_name": tool_name},
            )

        if not skip_approval and db is not None and user_id is not None and policy.requires_approval:
            approval = agent_approval_service.create_request(
                db=db,
                user_id=user_id,
                tool_name=tool_name,
                input_params=trim_sensitive_args(merged),
                agent_type=agent_type,
                agent_run_id=agent_run_id,
                risk_level=policy.risk_level,
                policy_version=policy.policy_version,
                data_scope=policy.data_scope,
            )
            return {
                "success": False,
                "message": "工具调用需要人工审批",
                "data": {
                    "tool_name": tool_name,
                    "approval_required": True,
                    "approval_request_id": approval.id,
                    "approval_status": approval.status,
                    "risk_level": approval.risk_level,
                },
                "error": "工具调用需要人工审批",
                "mcp_error_code": "MCP_APPROVAL_REQUIRED",
                "mcp_http_status": 409,
            }

        # ── 4a. Fire before-call hooks ───────────────────────────────
        for cb in self._before_call:
            cb(tool_name, trim_sensitive_args(merged), agent_type)

        # ── 5. Invoke ────────────────────────────────────────────────
        try:
            raw = await tool.run(**merged)
            result = tool.normalize_result(raw)
        except Exception:
            duration_s = time.time() - started
            result = {
                "success": False,
                "message": "Tool execution failed",
                "data": {"tool_name": tool_name},
                "error": "Tool execution failed",
                "mcp_error_code": MCP_ERR_INTERNAL["code"],
            }
            for cb in self._after_call:
                cb(tool_name, trim_sensitive_args(merged), agent_type, result, duration_s)
            return result

        duration_s = time.time() - started
        result.setdefault("data", {})
        if isinstance(result["data"], dict):
            result["data"].setdefault("mcp_tool_name", tool_name)
            result["data"].setdefault("mcp_duration_s", round(duration_s, 3))

        # ── 6. Fire after-call hooks ─────────────────────────────────
        for cb in self._after_call:
            cb(tool_name, trim_sensitive_args(merged), agent_type, result, duration_s)

        return result

    @staticmethod
    def _error_response(
        err: dict[str, Any],
        message: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "success": False,
            "message": message,
            "data": data or {},
            "error": message,
            "mcp_error_code": err["code"],
            "mcp_http_status": err["http"],
        }

    def serialize_result(self, result: dict[str, Any]) -> str:
        return json.dumps(result, ensure_ascii=False, default=str)


# ── Singleton ──────────────────────────────────────────────────────────
mcp_registry = MCPRegistry()
mcp_registry.register_all()
