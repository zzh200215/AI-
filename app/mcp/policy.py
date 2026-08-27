"""Versioned policy-as-code for MCP authorization.

The default document is intentionally plain data so it can be reviewed in a
code diff.  Production may activate a validated copy from the policy-version
table without changing the application binary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

POLICY_SCHEMA_VERSION = 1
DEFAULT_POLICY_VERSION = "mcp_policy_v1"
RISK_ORDER = {"low": 10, "medium": 20, "high": 30, "critical": 40}
_ALIASES = {"document_agent": "knowledge_agent", "task_agent": "workflow_agent"}


DEFAULT_POLICY_DOCUMENT: dict[str, Any] = {
    "schema_version": POLICY_SCHEMA_VERSION,
    "version": DEFAULT_POLICY_VERSION,
    "rules": [
        {
            "id": "document-search",
            "tool": "document_search_tool",
            "allowed_agents": ["knowledge_agent", "legal_compliance_agent", "general_agent"],
            "data_scope": "documents",
            "risk_level": "low",
            "requires_approval": False,
        },
        {
            "id": "document-summary",
            "tool": "document_summary_tool",
            "allowed_agents": ["knowledge_agent", "legal_compliance_agent", "general_agent"],
            "data_scope": "documents",
            "risk_level": "low",
            "requires_approval": False,
        },
        {
            "id": "document-risk",
            "tool": "document_risk_tool",
            "allowed_agents": ["knowledge_agent", "legal_compliance_agent", "general_agent"],
            "data_scope": "documents",
            "risk_level": "medium",
            "requires_approval": False,
        },
        {
            "id": "document-conflict",
            "tool": "document_conflict_tool",
            "allowed_agents": ["knowledge_agent", "legal_compliance_agent", "general_agent"],
            "data_scope": "documents",
            "risk_level": "medium",
            "requires_approval": False,
        },
        {
            "id": "legal-consultation",
            "tool": "legal_consultation_tool",
            "allowed_agents": ["legal_compliance_agent", "general_agent"],
            "data_scope": "legal",
            "risk_level": "medium",
            "requires_approval": False,
        },
        {
            "id": "legal-contract-review",
            "tool": "legal_contract_review_tool",
            "allowed_agents": ["legal_compliance_agent", "general_agent"],
            "data_scope": "legal",
            "risk_level": "medium",
            "requires_approval": False,
        },
        {
            "id": "legal-draft",
            "tool": "legal_draft_tool",
            "allowed_agents": ["legal_compliance_agent", "general_agent"],
            "data_scope": "legal",
            "risk_level": "medium",
            "requires_approval": False,
        },
        {
            "id": "task-query",
            "tool": "task_query_tool",
            "allowed_agents": ["workflow_agent", "general_agent"],
            "data_scope": "tasks",
            "risk_level": "low",
            "requires_approval": False,
        },
        {
            "id": "task-create",
            "tool": "task_create_tool",
            "allowed_agents": ["workflow_agent", "general_agent"],
            "data_scope": "tasks",
            "risk_level": "high",
            "requires_approval": True,
        },
        {
            "id": "sql-query",
            "tool": "sql_query_tool",
            "allowed_agents": ["general_agent"],
            "data_scope": "organization",
            "risk_level": "critical",
            "requires_approval": True,
        },
    ],
    "bypass_tools": ["finish", "retry"],
}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool = False
    reason: str | None = None
    error_code: str | None = None
    policy_version: str = DEFAULT_POLICY_VERSION
    rule_id: str | None = None
    data_scope: str | None = None
    risk_level: str = "low"
    agent_type: str | None = None
    tool_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "requires_approval": self.requires_approval,
            "reason": self.reason,
            "error_code": self.error_code,
            "policy_version": self.policy_version,
            "rule_id": self.rule_id,
            "data_scope": self.data_scope,
            "risk_level": self.risk_level,
            "agent_type": self.agent_type,
            "tool_name": self.tool_name,
        }


def canonical_agent_type(agent_type: str) -> str:
    return _ALIASES.get(agent_type, agent_type)


def validate_policy_document(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise ValueError("Unsupported MCP policy schema_version")
    version = str(document.get("version") or "").strip()
    if not version or len(version) > 64:
        raise ValueError("MCP policy version is required")
    rules = document.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("MCP policy rules are required")
    seen_tools: set[str] = set()
    seen_rule_ids: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict) or not str(rule.get("tool") or "").strip():
            raise ValueError("Every MCP policy rule needs a tool")
        tool = str(rule["tool"])
        if tool in seen_tools:
            raise ValueError(f"Duplicate MCP policy tool: {tool}")
        seen_tools.add(tool)
        rule_id = str(rule.get("id") or tool).strip()
        if rule_id in seen_rule_ids:
            raise ValueError(f"Duplicate MCP policy rule id: {rule_id}")
        seen_rule_ids.add(rule_id)
        agents = rule.get("allowed_agents")
        if (
            not isinstance(agents, list)
            or not agents
            or not all(isinstance(item, str) and item.strip() for item in agents)
        ):
            raise ValueError(f"Policy rule {tool} needs allowed_agents")
        risk = str(rule.get("risk_level") or "").lower()
        if risk not in RISK_ORDER:
            raise ValueError(f"Invalid risk_level for {tool}")
        if not str(rule.get("data_scope") or "").strip():
            raise ValueError(f"Policy rule {tool} needs data_scope")
        if "requires_approval" in rule and not isinstance(rule["requires_approval"], bool):
            raise ValueError(f"Policy rule {tool} requires_approval must be boolean")
    bypass_tools = document.get("bypass_tools") or []
    if not isinstance(bypass_tools, list) or not all(isinstance(item, str) and item.strip() for item in bypass_tools):
        raise ValueError("bypass_tools must be a list of non-empty strings")
    return json.loads(json.dumps(document, ensure_ascii=False))


def policy_checksum(document: dict[str, Any]) -> str:
    canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MCPPolicyEngine:
    def __init__(self, default_document: dict[str, Any] | None = None) -> None:
        self.default_document = validate_policy_document(default_document or DEFAULT_POLICY_DOCUMENT)

    def document_for(self, db: Session | None = None) -> dict[str, Any]:
        if db is not None and hasattr(db, "query"):
            try:
                from app.models.mcp_policy import MCPPolicyVersion

                row = (
                    db.query(MCPPolicyVersion)
                    .filter(MCPPolicyVersion.status == "active")
                    .order_by(MCPPolicyVersion.activated_at.desc(), MCPPolicyVersion.id.desc())
                    .first()
                )
                if row:
                    document = json.loads(row.policy_json)
                    return validate_policy_document(document)
            except Exception:
                if hasattr(db, "rollback"):
                    db.rollback()
        return self.default_document

    def rules(self, db: Session | None = None) -> dict[str, dict[str, Any]]:
        return {str(rule["tool"]): rule for rule in self.document_for(db).get("rules", [])}

    def allowed_tools_for(self, agent_type: str, db: Session | None = None) -> set[str]:
        canonical = canonical_agent_type(agent_type)
        return {
            tool
            for tool, rule in self.rules(db).items()
            if canonical in {canonical_agent_type(str(item)) for item in rule.get("allowed_agents", [])}
        }

    def evaluate(
        self,
        *,
        agent_type: str,
        tool_name: str,
        db: Session | None = None,
        contract: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        document = self.document_for(db)
        version = str(document.get("version") or DEFAULT_POLICY_VERSION)
        canonical = canonical_agent_type(agent_type)
        if tool_name in set(document.get("bypass_tools") or []):
            return PolicyDecision(True, policy_version=version, agent_type=canonical, tool_name=tool_name)
        rule = self.rules(db).get(tool_name)
        if rule is None:
            return PolicyDecision(
                False,
                reason="Tool has no policy rule",
                error_code="MCP_POLICY_DENIED",
                policy_version=version,
                agent_type=canonical,
                tool_name=tool_name,
            )
        rule_id = str(rule.get("id") or tool_name)
        if canonical not in {canonical_agent_type(str(item)) for item in rule.get("allowed_agents", [])}:
            return PolicyDecision(
                False,
                reason="Agent identity is not allowed by MCP policy",
                error_code="MCP_PERMISSION_DENIED",
                policy_version=version,
                rule_id=rule_id,
                data_scope=rule.get("data_scope"),
                risk_level=rule.get("risk_level", "low"),
                agent_type=canonical,
                tool_name=tool_name,
            )
        context = context or {}
        available_scopes = context.get("data_scopes")
        required_scope = str(rule.get("data_scope") or "")
        if available_scopes is not None and required_scope not in set(available_scopes):
            return PolicyDecision(
                False,
                reason="Data scope is not authorized",
                error_code="MCP_DATA_SCOPE_DENIED",
                policy_version=version,
                rule_id=rule_id,
                data_scope=required_scope,
                risk_level=rule.get("risk_level", "low"),
                agent_type=canonical,
                tool_name=tool_name,
            )
        threshold = context.get("risk_threshold")
        risk = str(rule.get("risk_level") or "low").lower()
        if threshold is not None:
            threshold_value = RISK_ORDER.get(str(threshold).lower(), int(threshold) if str(threshold).isdigit() else 0)
            if RISK_ORDER[risk] > threshold_value:
                return PolicyDecision(
                    False,
                    reason="Tool risk exceeds policy threshold",
                    error_code="MCP_RISK_THRESHOLD_EXCEEDED",
                    policy_version=version,
                    rule_id=rule_id,
                    data_scope=required_scope,
                    risk_level=risk,
                    agent_type=canonical,
                    tool_name=tool_name,
                )
        requires_approval = bool(rule.get("requires_approval"))
        if contract is not None:
            requires_approval = (
                requires_approval
                or bool(getattr(contract, "requires_approval", False))
                or not bool(getattr(contract, "read_only", True))
            )
        return PolicyDecision(
            True,
            requires_approval=requires_approval,
            policy_version=version,
            rule_id=rule_id,
            data_scope=required_scope,
            risk_level=risk,
            agent_type=canonical,
            tool_name=tool_name,
        )


policy_engine = MCPPolicyEngine()
