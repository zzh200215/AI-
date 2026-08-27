"""Compatibility facade for the versioned MCP policy engine."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.mcp.policy import canonical_agent_type as _policy_canonical_agent_type
from app.mcp.policy import policy_engine

CANONICAL_AGENT_TYPES = (
    "knowledge_agent",
    "legal_compliance_agent",
    "workflow_agent",
)
LEGACY_AGENT_ALIASES = {
    "document_agent": "knowledge_agent",
    "task_agent": "workflow_agent",
}
_BYPASS_TOOLS = frozenset({"finish", "retry"})


def canonical_agent_type(agent_type: str) -> str:
    return LEGACY_AGENT_ALIASES.get(agent_type, _policy_canonical_agent_type(agent_type))


def agent_allows_tool(agent_type: str, tool_name: str) -> bool:
    if tool_name in _BYPASS_TOOLS:
        return True
    return policy_engine.evaluate(agent_type=agent_type, tool_name=tool_name).allowed


def allowed_tools_for(agent_type: str, db: Any | None = None) -> set[str]:
    """Return tools from the active policy when a database session is supplied."""
    return policy_engine.allowed_tools_for(agent_type, db=db)


def resolve_agent_for_tool(tool_name: str, fallback_agent: str) -> str:
    canonical_fallback = canonical_agent_type(fallback_agent)
    if tool_name in _BYPASS_TOOLS or tool_name in allowed_tools_for(canonical_fallback):
        return canonical_fallback
    for agent_type in CANONICAL_AGENT_TYPES:
        if tool_name in allowed_tools_for(agent_type):
            return agent_type
    return canonical_fallback


def all_agent_types() -> Sequence[str]:
    return [*CANONICAL_AGENT_TYPES, "general_agent", "document_agent", "task_agent"]


# Kept as a read-only compatibility view for callers that imported the old
# matrix. New authorization code must use policy_engine.evaluate().
AGENT_TOOL_ALLOW: dict[str, set[str]] = {agent_type: allowed_tools_for(agent_type) for agent_type in all_agent_types()}
