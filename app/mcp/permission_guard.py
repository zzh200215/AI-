"""Single authorization boundary for MCP tool execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.mcp.policy import policy_engine
from app.models.agent import AgentRun

DECISION_KIND_ACL = "tool_acl"
DECISION_KIND_SNAPSHOT = "authz_snapshot"
DECISION_KIND_PLAN = "plan"
DECISION_KIND_POLICY = "policy"


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str | None = None
    error_code: str | None = None
    decision_kind: str = DECISION_KIND_ACL
    agent_type: str | None = None
    tool_name: str | None = None
    policy_version: str | None = None
    rule_id: str | None = None
    data_scope: str | None = None
    risk_level: str | None = None
    requires_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "error_code": self.error_code,
            "decision_kind": self.decision_kind,
            "agent_type": self.agent_type,
            "tool_name": self.tool_name,
            "policy_version": self.policy_version,
            "rule_id": self.rule_id,
            "data_scope": self.data_scope,
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
        }


class PermissionGuard:
    def check_tool_acl(self, agent_type: str, tool_name: str) -> PermissionDecision:
        policy = policy_engine.evaluate(agent_type=agent_type, tool_name=tool_name)
        return self._from_policy(policy)

    @staticmethod
    def _from_policy(policy) -> PermissionDecision:
        return PermissionDecision(
            allowed=policy.allowed,
            requires_approval=policy.requires_approval,
            reason=policy.reason,
            error_code=policy.error_code,
            decision_kind=DECISION_KIND_POLICY,
            agent_type=policy.agent_type,
            tool_name=policy.tool_name,
            policy_version=policy.policy_version,
            rule_id=policy.rule_id,
            data_scope=policy.data_scope,
            risk_level=policy.risk_level,
        )

    def check_run_snapshot(self, db: Session, *, agent_run_id: int, user_id: int) -> PermissionDecision:
        from app.services.org.authorization_service import authorization_service

        run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).first()
        snapshot_id = run.authorization_snapshot_id if run else None
        if not snapshot_id:
            return PermissionDecision(allowed=True, decision_kind=DECISION_KIND_SNAPSHOT)
        try:
            authorization_service.assert_snapshot(db, snapshot_id, user_id=user_id)
            return PermissionDecision(allowed=True, decision_kind=DECISION_KIND_SNAPSHOT)
        except Exception as exc:  # noqa: BLE001
            code = getattr(getattr(exc, "detail", None), "get", lambda *_: "authz_changed")("code", "authz_changed")
            return PermissionDecision(
                allowed=False,
                reason="Agent authorization snapshot is no longer valid",
                error_code=code,
                decision_kind=DECISION_KIND_SNAPSHOT,
            )

    def check_tool_execution(
        self,
        *,
        agent_type: str,
        tool_name: str,
        db: Session | None,
        agent_run_id: int | None,
        user_id: int | None,
        organization_id: int | None = None,
        policy_context: dict[str, Any] | None = None,
    ) -> PermissionDecision:
        static = self.check_tool_acl(agent_type, tool_name)
        if not static.allowed:
            return static
        policy = policy_engine.evaluate(
            agent_type=agent_type,
            tool_name=tool_name,
            db=db,
            context={**(policy_context or {}), "organization_id": organization_id},
        )
        if not policy.allowed:
            return self._from_policy(policy)
        decision = self._from_policy(policy)
        if db is not None and agent_run_id is not None and user_id is not None:
            snapshot = self.check_run_snapshot(db, agent_run_id=agent_run_id, user_id=user_id)
            if not snapshot.allowed:
                return snapshot
        return decision

    def check_plan(self, plan: dict[str, Any] | None) -> PermissionDecision:
        if not isinstance(plan, dict):
            return PermissionDecision(allowed=True, decision_kind=DECISION_KIND_PLAN)
        if plan.get("requires_approval") and plan.get("approval_context_missing"):
            return PermissionDecision(
                allowed=False,
                reason="Plan approval context is missing",
                error_code="PLAN_APPROVAL_REQUIRED",
                decision_kind=DECISION_KIND_PLAN,
            )
        return PermissionDecision(allowed=True, decision_kind=DECISION_KIND_PLAN)

    def denied_result(self, decision: PermissionDecision) -> dict[str, Any]:
        mcp_code = (
            "AUTHZ_CHANGED"
            if decision.decision_kind == DECISION_KIND_SNAPSHOT
            else decision.error_code or "MCP_PERMISSION_DENIED"
        )
        return {
            "success": False,
            "message": decision.reason or "Permission denied",
            "data": {
                "agent_type": decision.agent_type,
                "requested_tool": decision.tool_name,
                "decision_kind": decision.decision_kind,
                "error_code": decision.error_code,
                "policy_version": decision.policy_version,
                "rule_id": decision.rule_id,
                "data_scope": decision.data_scope,
                "risk_level": decision.risk_level,
            },
            "error": decision.reason or "Permission denied",
            "mcp_error_code": mcp_code,
            "mcp_http_status": 403,
        }


permission_guard = PermissionGuard()
