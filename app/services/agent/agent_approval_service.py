"""Agent 审批协调器（ApprovalCoordinator）。

在既有审批服务之上加固生命周期：
- 状态：pending / approved / rejected / executed / expired / revoked。
- 绑定：run_id + step_id + 工具 + 参数摘要（param_digest）+ 操作者 + 时间 + 过期时间。
- 过期：到期未决审批不可执行（resume 前校验）。
- 撤销：显式 revoke 使审批失效。
- 参数变化必须重新审批：resume 执行写工具前比对 digest，不匹配则要求新审批。

为兼容既有调用方，保留 ``AgentApprovalService`` 类名与 ``agent_approval_service`` 单例。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.mcp.policy import DEFAULT_POLICY_DOCUMENT
from app.models.agent import AgentApprovalRequest

# 审批状态
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXECUTED = "executed"
STATUS_EXPIRED = "expired"
STATUS_REVOKED = "revoked"

_DECIDABLE = frozenset({STATUS_PENDING})
_NOT_EXECUTABLE = frozenset({STATUS_REJECTED, STATUS_EXPIRED, STATUS_REVOKED})


class ApprovalStateError(ValueError):
    """审批状态不合法（不可决策/已过期/参数变化）。"""


def param_digest(params: dict) -> str:
    """规范化参数摘要（sort_keys 保证稳定）。"""
    canonical = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AgentApprovalService:
    HIGH_RISK_TOOLS = {
        str(rule["tool"]): str(rule["risk_level"])
        for rule in DEFAULT_POLICY_DOCUMENT["rules"]
        if rule.get("requires_approval")
    }

    def requires_approval(self, tool_name: str) -> bool:
        from app.mcp.policy import policy_engine

        return policy_engine.evaluate(agent_type="general_agent", tool_name=tool_name).requires_approval

    def create_request(
        self,
        *,
        db: Session,
        user_id: int,
        tool_name: str,
        input_params: dict,
        agent_type: str,
        agent_run_id: int | None = None,
        step_id: int | None = None,
        risk_level: str | None = None,
        policy_version: str | None = None,
        data_scope: str | None = None,
    ) -> AgentApprovalRequest:
        expires_at = utc_now() + timedelta(seconds=get_settings().AGENT_APPROVAL_EXPIRE_SECONDS)
        request = AgentApprovalRequest(
            agent_run_id=agent_run_id,
            user_id=user_id,
            tool_name=tool_name,
            agent_type=agent_type,
            input_params=json.dumps(input_params, ensure_ascii=False, sort_keys=True),
            risk_level=risk_level or self.HIGH_RISK_TOOLS.get(tool_name, "high"),
            status=STATUS_PENDING,
            approval_token=secrets.token_hex(16),
            step_id=step_id,
            param_digest=param_digest(input_params),
            expires_at=expires_at,
            policy_version=policy_version,
            data_scope=data_scope,
        )
        db.add(request)
        db.commit()
        db.refresh(request)
        return request

    def list_requests(self, *, db: Session, user_id: int, status: str | None = None) -> list[AgentApprovalRequest]:
        query = db.query(AgentApprovalRequest).filter(AgentApprovalRequest.user_id == user_id)
        if status:
            query = query.filter(AgentApprovalRequest.status == status)
        return query.order_by(AgentApprovalRequest.created_at.desc(), AgentApprovalRequest.id.desc()).all()

    def get_request(self, *, db: Session, approval_id: int, user_id: int) -> AgentApprovalRequest | None:
        return (
            db.query(AgentApprovalRequest)
            .filter(AgentApprovalRequest.id == approval_id, AgentApprovalRequest.user_id == user_id)
            .first()
        )

    def _refresh_status(self, db: Session, request: AgentApprovalRequest) -> None:
        """把已过期的 pending 审批标记为 expired（惰性，查询/决策前调用）。"""
        if (
            request.status == STATUS_PENDING
            and request.expires_at is not None
            and request.expires_at < utc_now()
        ):
            request.status = STATUS_EXPIRED
            request.decided_at = request.decided_at or utc_now()
            db.add(request)
            db.commit()
            db.refresh(request)

    def decide_request(
        self,
        *,
        db: Session,
        approval_id: int,
        user_id: int,
        approved: bool,
        decision_note: str | None = None,
    ) -> AgentApprovalRequest:
        request = self.get_request(db=db, approval_id=approval_id, user_id=user_id)
        if not request:
            raise ValueError("Approval request not found")
        self._refresh_status(db, request)
        if request.status != STATUS_PENDING:
            raise ValueError("Approval request already decided")
        request.status = STATUS_APPROVED if approved else STATUS_REJECTED
        request.decision_note = decision_note
        request.decided_at = utc_now()
        request.decided_by = user_id
        db.add(request)
        db.commit()
        db.refresh(request)
        return request

    def revoke_request(
        self,
        *,
        db: Session,
        approval_id: int,
        user_id: int,
        reason: str = "revoked",
    ) -> AgentApprovalRequest:
        """撤销待执行审批：已 executed 不可撤销。"""
        request = self.get_request(db=db, approval_id=approval_id, user_id=user_id)
        if not request:
            raise ValueError("Approval request not found")
        if request.status == STATUS_EXECUTED:
            raise ApprovalStateError("Approval request already executed")
        request.status = STATUS_REVOKED
        request.revoked_at = utc_now()
        request.revoke_reason = reason
        request.decided_at = request.decided_at or utc_now()
        request.decided_by = user_id
        db.add(request)
        db.commit()
        db.refresh(request)
        return request

    def mark_executed(
        self,
        *,
        db: Session,
        approval_id: int,
        user_id: int,
        decision_note: str | None = None,
    ) -> AgentApprovalRequest:
        request = self.get_request(db=db, approval_id=approval_id, user_id=user_id)
        if not request:
            raise ValueError("Approval request not found")
        request.status = STATUS_EXECUTED
        if decision_note:
            request.decision_note = decision_note
        request.decided_at = request.decided_at or utc_now()
        db.add(request)
        db.commit()
        db.refresh(request)
        return request

    def try_claim_execution(self, *, db: Session, approval_id: int, user_id: int) -> bool:
        """原子地将审批从 approved 置为 executed（一次性 CAS）。

        返回 True 表示本调用者取得唯一执行权；False 表示该审批已被并发恢复流程抢先执行。
        用于防止并发 resume_after_approval 导致需要审批的写工具重复执行。
        """
        from sqlalchemy import text as sa_text

        result = db.execute(
            sa_text(
                "UPDATE agent_approval_requests SET status = :executed "
                "WHERE id = :id AND user_id = :uid AND status = :approved"
            ),
            {
                "executed": STATUS_EXECUTED,
                "id": approval_id,
                "uid": user_id,
                "approved": STATUS_APPROVED,
            },
        )
        db.commit()
        return result.rowcount == 1

    def verify_unchanged(self, approval: AgentApprovalRequest, current_params: dict) -> bool:
        """审批绑定参数与当前待执行参数是否一致；不一致必须重新审批。"""
        if not approval.param_digest:
            return True
        return approval.param_digest == param_digest(current_params)

    def require_executable(
        self,
        *,
        db: Session,
        approval_id: int,
        user_id: int,
        current_params: dict | None = None,
    ) -> AgentApprovalRequest:
        """校验审批可执行：已批准、未过期/未撤销、参数未变化。不满足抛 ApprovalStateError。"""
        request = self.get_request(db=db, approval_id=approval_id, user_id=user_id)
        if not request:
            raise ValueError("Approval request not found")
        self._refresh_status(db, request)
        if request.status != STATUS_APPROVED:
            raise ApprovalStateError(f"Approval request is not approved (status={request.status})")
        if current_params is not None and not self.verify_unchanged(request, current_params):
            raise ApprovalStateError("Approval parameters changed; re-approval required")
        return request

    def expire_stale(self, db: Session, *, batch: int = 200) -> int:
        """批量把已过期的 pending 审批标记为 expired，返回处理数。"""
        now = utc_now()
        stale = (
            db.query(AgentApprovalRequest)
            .filter(
                AgentApprovalRequest.status == STATUS_PENDING,
                AgentApprovalRequest.expires_at.isnot(None),
                AgentApprovalRequest.expires_at < now,
            )
            .limit(batch)
            .all()
        )
        for request in stale:
            request.status = STATUS_EXPIRED
            request.decided_at = request.decided_at or now
        if stale:
            db.commit()
        return len(stale)


agent_approval_service = AgentApprovalService()
