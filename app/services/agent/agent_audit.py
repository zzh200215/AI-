"""结构化审计事件：持久化 run/step/trace 维度的计划决策、权限决策、工具执行、
审批、状态变更、重试/超时/取消/补偿与错误分类。

安全约定：审计只记录脱敏摘要，不写入密钥、令牌、完整个人信息、原始文档正文或
敏感 SQL 参数（规范化模板与参数哈希由 SQLTool 层负责）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.mcp.schema import trim_sensitive_args
from app.models.agent import AgentAuditEvent

# 事件类型常量
EVENT_RUN_STATE_CHANGED = "run_state_changed"
EVENT_PLAN_CREATED = "plan_created"
EVENT_PERMISSION_DECISION = "permission_decision"
EVENT_TOOL_EXECUTED = "tool_executed"
EVENT_APPROVAL_CREATED = "approval_created"
EVENT_APPROVAL_DECIDED = "approval_decided"
EVENT_STEP_COMPLETED = "step_completed"
EVENT_RETRY = "retry"
EVENT_TIMEOUT = "timeout"
EVENT_CANCEL = "cancel"
EVENT_COMPENSATION = "compensation"
EVENT_ERROR = "error"
EVENT_RETRIEVAL_RESULT = "retrieval_result"
EVENT_A2A_DELEGATION_CREATED = "a2a_delegation_created"
EVENT_A2A_DELEGATION_ACCEPTED = "a2a_delegation_accepted"
EVENT_A2A_DELEGATION_DISPATCHED = "a2a_delegation_dispatched"
EVENT_A2A_DELEGATION_AWAITING_APPROVAL = "a2a_delegation_awaiting_approval"
EVENT_A2A_DELEGATION_COMPLETED = "a2a_delegation_completed"
EVENT_A2A_DELEGATION_FAILED = "a2a_delegation_failed"
EVENT_A2A_DELEGATION_DENIED = "a2a_delegation_denied"


class AgentAuditService:
    def record(
        self,
        *,
        db: Session,
        event_type: str,
        run_id: int | None = None,
        step: int | None = None,
        trace_id: str | None = None,
        user_id: int | None = None,
        organization_id: int | None = None,
        tool_name: str | None = None,
        tool_version: str | None = None,
        decision: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
        error_category: str | None = None,
        status: str | None = None,
        duration_ms: int | None = None,
    ) -> AgentAuditEvent:
        """写入一条审计事件。decision/summary 先脱敏再 JSON 序列化。"""
        event = AgentAuditEvent(
            run_id=run_id,
            step=step,
            trace_id=trace_id,
            event_type=event_type,
            user_id=user_id,
            organization_id=organization_id,
            tool_name=tool_name,
            tool_version=tool_version,
            decision_json=json.dumps(trim_sensitive_args(decision), ensure_ascii=False, default=str) if decision else None,
            summary_json=json.dumps(trim_sensitive_args(summary), ensure_ascii=False, default=str) if summary else None,
            error_category=error_category,
            status=status,
            duration_ms=duration_ms,
            created_at=utc_now(),
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event

    def list_for_run(self, db: Session, run_id: int, limit: int = 500) -> list[AgentAuditEvent]:
        return (
            db.query(AgentAuditEvent)
            .filter(AgentAuditEvent.run_id == run_id)
            .order_by(AgentAuditEvent.id.asc())
            .limit(max(1, min(limit, 2000)))
            .all()
        )


agent_audit_service = AgentAuditService()
