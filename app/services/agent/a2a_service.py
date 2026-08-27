"""Internal A2A control plane for discoverable agent roles and delegated runs.

This is deliberately a local control plane, not an unbounded remote-agent
transport. Every child run inherits the parent identity, tenant, trace and
authorization snapshot; MCP remains the sole tool authorization gateway.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import deque
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.mcp.permissions import CANONICAL_AGENT_TYPES, allowed_tools_for, canonical_agent_type
from app.mcp.policy import policy_engine
from app.models.agent import A2ADelegation, AgentAuditEvent, AgentRun
from app.models.user import User
from app.services.agent.agent_audit import (
    EVENT_A2A_DELEGATION_ACCEPTED,
    EVENT_A2A_DELEGATION_AWAITING_APPROVAL,
    EVENT_A2A_DELEGATION_COMPLETED,
    EVENT_A2A_DELEGATION_CREATED,
    EVENT_A2A_DELEGATION_DENIED,
    EVENT_A2A_DELEGATION_DISPATCHED,
    EVENT_A2A_DELEGATION_FAILED,
    agent_audit_service,
)
from app.services.agent.agent_registry import (
    AGENT_REGISTRY_VERSION,
    TASK_PROTOCOL_VERSION,
    get_agent_registration,
    get_supervisor_registration,
)

A2A_PROTOCOL_VERSION = "0.3.0"
_ACTIVE_PARENT_STATUSES = frozenset({"running", "awaiting_approval"})
_TERMINAL_DELEGATION_STATUSES = frozenset({"completed", "failed"})


class A2ADelegationError(ValueError):
    """A stable, API-safe delegation validation failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _payload_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return _payload_hash(encoded)


class A2ACollaborationService:
    def __init__(self, *, max_delegation_depth: int | None = None) -> None:
        self.max_delegation_depth = max_delegation_depth or get_settings().A2A_MAX_DELEGATION_DEPTH

    @staticmethod
    def _registration(agent_type: str) -> dict[str, Any] | None:
        if agent_type == "supervisor_agent":
            return get_supervisor_registration()
        return get_agent_registration(agent_type)

    def _data_domains(self, *, agent_type: str, db: Session | None) -> list[str]:
        domains: set[str] = set()
        for tool_name in allowed_tools_for(agent_type, db=db):
            decision = policy_engine.evaluate(agent_type=agent_type, tool_name=tool_name, db=db)
            if decision.allowed and decision.data_scope:
                domains.add(str(decision.data_scope))
        return sorted(domains)

    def build_agent_card(self, *, agent_type: str, db: Session | None = None) -> dict[str, Any] | None:
        canonical = canonical_agent_type(agent_type)
        registration = self._registration(canonical)
        if not registration:
            return None
        allowed_tools = [] if canonical == "supervisor_agent" else sorted(allowed_tools_for(canonical, db=db))
        capabilities = [str(item) for item in registration.get("capabilities") or []]
        return {
            # A2A Agent Card standard fields. The local-only transport contract
            # is explicitly placed in the extension rather than claiming a remote
            # JSON-RPC endpoint that this service does not expose.
            "protocolVersion": A2A_PROTOCOL_VERSION,
            "name": registration["label"],
            "description": registration["description"],
            "version": AGENT_REGISTRY_VERSION,
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "stateTransitionHistory": True,
            },
            "skills": [
                {
                    "id": capability,
                    "name": capability.replace("_", " "),
                    "description": registration["description"],
                    "tags": [canonical],
                }
                for capability in capabilities
            ],
            "securitySchemes": {
                "userBearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                    "description": "End-user identity and authorization snapshot are required.",
                }
            },
            "security": [{"userBearerAuth": []}],
            "defaultInputModes": ["text/plain", "application/json"],
            "defaultOutputModes": ["text/plain", "application/json"],
            "x-aibg": {
                "agent_type": canonical,
                "task_protocol_version": TASK_PROTOCOL_VERSION,
                "execution_mode": registration["execution_mode"],
                "allowed_tools": allowed_tools,
                "data_domains": self._data_domains(agent_type=canonical, db=db) if allowed_tools else [],
                "identity_required": True,
                "authorization": "inherit_parent_authorization_snapshot",
                "transport": "internal_control_plane",
                "audit": "trace_replay",
                "integration_surfaces": self._integration_surfaces(canonical),
            },
        }

    @staticmethod
    def _integration_surfaces(agent_type: str) -> list[dict[str, str]]:
        """Advertise verified internal hand-off points without inventing remote endpoints."""
        surfaces = {
            "knowledge_agent": [
                {"id": "legal_knowledge_base", "access": "read_only"},
            ],
            "legal_compliance_agent": [
                {"id": "contract_workbench", "access": "read_only"},
                {"id": "legal_knowledge_base", "access": "read_only"},
            ],
            "workflow_agent": [
                {"id": "internal_workflow", "access": "approval_controlled"},
                {"id": "client_portal", "access": "workflow_mediated"},
            ],
            "supervisor_agent": [
                {"id": "contract_workbench", "access": "orchestration_only"},
                {"id": "legal_knowledge_base", "access": "orchestration_only"},
                {"id": "client_portal", "access": "orchestration_only"},
            ],
        }
        return surfaces.get(agent_type, [])

    def list_agent_cards(self, *, db: Session | None = None) -> list[dict[str, Any]]:
        agent_types = ["supervisor_agent", *CANONICAL_AGENT_TYPES]
        return [card for agent_type in agent_types if (card := self.build_agent_card(agent_type=agent_type, db=db))]

    def _record_denied(
        self,
        *,
        db: Session,
        parent_run: AgentRun,
        to_agent_type: str,
        task_type: str,
        input_hash: str,
        code: str,
    ) -> None:
        agent_audit_service.record(
            db=db,
            event_type=EVENT_A2A_DELEGATION_DENIED,
            run_id=parent_run.id,
            trace_id=parent_run.trace_id,
            user_id=parent_run.user_id,
            organization_id=parent_run.organization_id,
            decision={
                "from_agent_type": canonical_agent_type(parent_run.agent_type or "supervisor_agent"),
                "to_agent_type": to_agent_type,
                "task_type": task_type,
                "input_hash": input_hash,
            },
            summary={"reason_code": code},
            error_category="permission_denied" if code.startswith("A2A_ACCESS") else "validation",
            status="denied",
        )

    def _assert_parent_can_delegate(self, *, db: Session, parent_run: AgentRun, user: User) -> None:
        if parent_run.user_id != user.id:
            raise A2ADelegationError("A2A_PARENT_NOT_FOUND", "父运行记录不存在")
        if parent_run.organization_id is not None and parent_run.organization_id != user.organization_id:
            raise A2ADelegationError("A2A_ORGANIZATION_MISMATCH", "父运行所属组织已变化")
        if parent_run.status not in _ACTIVE_PARENT_STATUSES:
            raise A2ADelegationError("A2A_PARENT_NOT_ACTIVE", "仅运行中的 Agent Run 可以委派任务")
        if parent_run.run_deadline_at is not None and parent_run.run_deadline_at <= utc_now():
            raise A2ADelegationError("A2A_PARENT_EXPIRED", "父运行已超过执行期限")
        if not parent_run.authorization_snapshot_id:
            raise A2ADelegationError("A2A_AUTHZ_SNAPSHOT_REQUIRED", "父运行缺少有效授权快照")
        try:
            from app.services.org.authorization_service import authorization_service

            authorization_service.assert_snapshot(db, parent_run.authorization_snapshot_id, user_id=user.id)
        except Exception as exc:  # Authorization service returns transport-safe HTTP exceptions.
            raise A2ADelegationError("A2A_AUTHZ_SNAPSHOT_INVALID", "委派授权已失效") from exc

    @staticmethod
    def _depth_for_parent(db: Session, parent_run: AgentRun) -> int:
        depth = 0
        seen = {parent_run.id}
        current = parent_run
        while current.parent_run_id is not None:
            if current.parent_run_id in seen:
                raise A2ADelegationError("A2A_LINEAGE_INVALID", "委派谱系存在循环")
            seen.add(current.parent_run_id)
            parent = db.query(AgentRun).filter(AgentRun.id == current.parent_run_id).first()
            if not parent:
                raise A2ADelegationError("A2A_LINEAGE_INVALID", "委派谱系不完整")
            depth += 1
            current = parent
        return depth

    @staticmethod
    def _task_summary(*, task_type: str, input_hash: str, task: str) -> dict[str, Any]:
        return {
            "task_type": task_type,
            "input_hash": input_hash,
            "input_length": len(task),
        }

    def create_delegation(
        self,
        *,
        db: Session,
        parent_run: AgentRun,
        user: User,
        to_agent_type: str,
        task: str,
        task_type: str = "analysis",
        idempotency_key: str | None = None,
    ) -> A2ADelegation:
        normalized_task = task.strip()
        normalized_type = task_type.strip().lower() or "analysis"
        canonical_target = canonical_agent_type(to_agent_type.strip())
        input_hash = _payload_hash(normalized_task)
        if not normalized_task:
            self._record_denied(
                db=db,
                parent_run=parent_run,
                to_agent_type=canonical_target,
                task_type=normalized_type,
                input_hash=input_hash,
                code="A2A_TASK_INVALID",
            )
            raise A2ADelegationError("A2A_TASK_INVALID", "委派任务不能为空")
        if canonical_target not in CANONICAL_AGENT_TYPES or not self._registration(canonical_target):
            self._record_denied(
                db=db,
                parent_run=parent_run,
                to_agent_type=canonical_target,
                task_type=normalized_type,
                input_hash=input_hash,
                code="A2A_TARGET_NOT_FOUND",
            )
            raise A2ADelegationError("A2A_TARGET_NOT_FOUND", "目标 Agent 不可委派")
        try:
            self._assert_parent_can_delegate(db=db, parent_run=parent_run, user=user)
            if self._depth_for_parent(db, parent_run) >= self.max_delegation_depth:
                raise A2ADelegationError("A2A_MAX_DEPTH_EXCEEDED", "超过允许的委派深度")
        except A2ADelegationError as exc:
            self._record_denied(
                db=db,
                parent_run=parent_run,
                to_agent_type=canonical_target,
                task_type=normalized_type,
                input_hash=input_hash,
                code=exc.code,
            )
            raise

        normalized_key = (idempotency_key or "").strip() or None
        if normalized_key:
            existing = (
                db.query(A2ADelegation)
                .filter(
                    A2ADelegation.parent_run_id == parent_run.id,
                    A2ADelegation.idempotency_key == normalized_key,
                )
                .first()
            )
            if existing:
                same_request = (
                    existing.to_agent_type == canonical_target
                    and existing.task_type == normalized_type
                    and existing.input_hash == input_hash
                )
                if not same_request:
                    self._record_denied(
                        db=db,
                        parent_run=parent_run,
                        to_agent_type=canonical_target,
                        task_type=normalized_type,
                        input_hash=input_hash,
                        code="A2A_IDEMPOTENCY_CONFLICT",
                    )
                    raise A2ADelegationError("A2A_IDEMPOTENCY_CONFLICT", "幂等键已用于不同委派请求")
                return existing

        delegation_id = str(uuid.uuid4())
        child_run = AgentRun(
            user_id=parent_run.user_id,
            session_id=parent_run.session_id,
            goal=normalized_task,
            status="running",
            total_steps=0,
            trace_id=parent_run.trace_id,
            organization_id=parent_run.organization_id,
            authorization_snapshot_id=parent_run.authorization_snapshot_id,
            agent_type=canonical_target,
            parent_run_id=parent_run.id,
            delegation_id=delegation_id,
            run_deadline_at=parent_run.run_deadline_at,
            last_observation="A2A delegation accepted by internal control plane.",
        )
        db.add(child_run)
        db.flush()
        now = utc_now()
        delegation = A2ADelegation(
            delegation_id=delegation_id,
            parent_run_id=parent_run.id,
            child_run_id=child_run.id,
            user_id=parent_run.user_id,
            organization_id=parent_run.organization_id,
            trace_id=parent_run.trace_id,
            authorization_snapshot_id=parent_run.authorization_snapshot_id,
            from_agent_type=canonical_agent_type(parent_run.agent_type or "supervisor_agent"),
            to_agent_type=canonical_target,
            task_type=normalized_type,
            status="accepted",
            idempotency_key=normalized_key,
            input_hash=input_hash,
            task_summary_json=json.dumps(
                self._task_summary(task_type=normalized_type, input_hash=input_hash, task=normalized_task),
                ensure_ascii=False,
                sort_keys=True,
            ),
            accepted_at=now,
        )
        db.add(delegation)
        db.commit()
        db.refresh(delegation)

        audit_decision = {
            "delegation_id": delegation.delegation_id,
            "parent_run_id": parent_run.id,
            "child_run_id": child_run.id,
            "from_agent_type": delegation.from_agent_type,
            "to_agent_type": delegation.to_agent_type,
            "task_type": delegation.task_type,
        }
        audit_summary = self._task_summary(task_type=normalized_type, input_hash=input_hash, task=normalized_task)
        agent_audit_service.record(
            db=db,
            event_type=EVENT_A2A_DELEGATION_CREATED,
            run_id=parent_run.id,
            trace_id=parent_run.trace_id,
            user_id=parent_run.user_id,
            organization_id=parent_run.organization_id,
            decision=audit_decision,
            summary=audit_summary,
            status="accepted",
        )
        agent_audit_service.record(
            db=db,
            event_type=EVENT_A2A_DELEGATION_ACCEPTED,
            run_id=child_run.id,
            trace_id=child_run.trace_id,
            user_id=child_run.user_id,
            organization_id=child_run.organization_id,
            decision=audit_decision,
            summary={"input_hash": input_hash},
            status="accepted",
        )
        return delegation

    def complete_delegation(
        self,
        *,
        db: Session,
        delegation_id: str,
        succeeded: bool,
        result_summary: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> A2ADelegation:
        """Internal worker callback for terminal status; not exposed as a user API."""
        delegation = db.query(A2ADelegation).filter(A2ADelegation.delegation_id == delegation_id).first()
        if not delegation:
            raise A2ADelegationError("A2A_DELEGATION_NOT_FOUND", "委派记录不存在")
        target_status = "completed" if succeeded else "failed"
        if delegation.status in _TERMINAL_DELEGATION_STATUSES:
            if delegation.status != target_status:
                raise A2ADelegationError("A2A_TERMINAL_CONFLICT", "委派已以不同状态结束")
            return delegation
        child_run = db.query(AgentRun).filter(AgentRun.id == delegation.child_run_id).first()
        if not child_run:
            raise A2ADelegationError("A2A_CHILD_RUN_NOT_FOUND", "子运行记录不存在")
        raw_summary = result_summary or {}
        safe_summary = {
            "result_hash": _json_hash(raw_summary),
            "result_keys": sorted(str(key) for key in raw_summary)[:30],
        }
        now = utc_now()
        delegation.status = target_status
        delegation.error_code = None if succeeded else (error_code or "a2a_worker_failed")
        delegation.result_summary_json = json.dumps(safe_summary, ensure_ascii=False, sort_keys=True)
        delegation.completed_at = now
        # Direct internal callbacks may complete an otherwise running child.
        # Workflow-originated partial/cancelled states remain authoritative.
        if child_run.status == "running":
            child_run.status = "completed" if succeeded else "error"
            child_run.completed_at = now
            child_run.failure_reason = None if succeeded else delegation.error_code
        db.add_all([delegation, child_run])
        db.commit()
        db.refresh(delegation)
        agent_audit_service.record(
            db=db,
            event_type=EVENT_A2A_DELEGATION_COMPLETED if succeeded else EVENT_A2A_DELEGATION_FAILED,
            run_id=child_run.id,
            trace_id=child_run.trace_id,
            user_id=child_run.user_id,
            organization_id=child_run.organization_id,
            decision={
                "delegation_id": delegation.delegation_id,
                "parent_run_id": delegation.parent_run_id,
                "child_run_id": child_run.id,
                "to_agent_type": delegation.to_agent_type,
            },
            summary=safe_summary,
            error_category=delegation.error_code if not succeeded else None,
            status=target_status,
        )
        return delegation

    def sync_from_child_run(self, *, db: Session, child_run: AgentRun) -> A2ADelegation | None:
        """Project the child Run lifecycle into its delegation ledger.

        The child remains the execution source of truth. The A2A record only
        tracks the delivery lifecycle and keeps a hashed terminal summary.
        """
        if not child_run.delegation_id:
            return None
        delegation = db.query(A2ADelegation).filter(A2ADelegation.delegation_id == child_run.delegation_id).first()
        if not delegation:
            return None
        if delegation.child_run_id != child_run.id:
            raise A2ADelegationError("A2A_CHILD_RUN_MISMATCH", "委派与子运行记录不匹配")
        if child_run.status == "awaiting_approval" and delegation.status == "accepted":
            delegation.status = "awaiting_approval"
            db.add(delegation)
            db.commit()
            db.refresh(delegation)
            agent_audit_service.record(
                db=db,
                event_type=EVENT_A2A_DELEGATION_AWAITING_APPROVAL,
                run_id=child_run.id,
                trace_id=child_run.trace_id,
                user_id=child_run.user_id,
                organization_id=child_run.organization_id,
                decision={"delegation_id": delegation.delegation_id, "to_agent_type": delegation.to_agent_type},
                summary={"child_run_status": child_run.status},
                status="awaiting_approval",
            )
            return delegation
        if child_run.status in {"completed", "partial"}:
            return self.complete_delegation(
                db=db,
                delegation_id=delegation.delegation_id,
                succeeded=True,
                result_summary={
                    "child_run_status": child_run.status,
                    "total_steps": int(child_run.total_steps or 0),
                    "has_result": bool(child_run.result or child_run.final_answer),
                },
            )
        if child_run.status in {"error", "cancelled"}:
            return self.complete_delegation(
                db=db,
                delegation_id=delegation.delegation_id,
                succeeded=False,
                result_summary={
                    "child_run_status": child_run.status,
                    "total_steps": int(child_run.total_steps or 0),
                },
                error_code="a2a_child_run_cancelled" if child_run.status == "cancelled" else "a2a_child_run_error",
            )
        return delegation

    async def dispatch_delegation(self, *, db: Session, delegation: A2ADelegation, max_steps: int = 5) -> A2ADelegation:
        """Execute an accepted child Run through the existing protected workflow."""
        if delegation.status in _TERMINAL_DELEGATION_STATUSES | {"awaiting_approval"}:
            return delegation
        child_run = db.query(AgentRun).filter(AgentRun.id == delegation.child_run_id).first()
        if not child_run:
            raise A2ADelegationError("A2A_CHILD_RUN_NOT_FOUND", "子运行记录不存在")
        if child_run.status != "running":
            return self.sync_from_child_run(db=db, child_run=child_run) or delegation

        agent_audit_service.record(
            db=db,
            event_type=EVENT_A2A_DELEGATION_DISPATCHED,
            run_id=child_run.id,
            trace_id=child_run.trace_id,
            user_id=child_run.user_id,
            organization_id=child_run.organization_id,
            decision={"delegation_id": delegation.delegation_id, "to_agent_type": delegation.to_agent_type},
            summary={"max_steps": max_steps},
            status="running",
        )
        try:
            # Local import keeps the orchestration service free from an A2A
            # dependency except when it is actually dispatching a child Run.
            from app.services.agent.agent_service import agent_service

            child_run = await agent_service.run(
                goal=child_run.goal,
                user_id=child_run.user_id,
                db=db,
                session_id=child_run.session_id,
                max_steps=max_steps,
                existing_run=child_run,
                forced_worker_agent=delegation.to_agent_type,
            )
        except Exception:
            child_run.status = "error"
            child_run.failure_reason = "a2a_worker_dispatch_failed"
            child_run.completed_at = utc_now()
            db.add(child_run)
            db.commit()
        return self.sync_from_child_run(db=db, child_run=child_run) or delegation

    @staticmethod
    def list_delegations(db: Session, *, parent_run_id: int) -> list[A2ADelegation]:
        return (
            db.query(A2ADelegation)
            .filter(A2ADelegation.parent_run_id == parent_run_id)
            .order_by(A2ADelegation.id.asc())
            .all()
        )

    @staticmethod
    def _lineage_run_ids(db: Session, *, run_id: int, limit: int = 256) -> list[int]:
        """Return the requested Run's full ancestor/descendant tree, bounded."""
        current = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        if not current:
            return []
        ids = {current.id}
        while current.parent_run_id is not None and current.parent_run_id not in ids:
            ids.add(current.parent_run_id)
            current = db.query(AgentRun).filter(AgentRun.id == current.parent_run_id).first()
            if not current:
                break
        pending = deque(ids)
        while pending and len(ids) < limit:
            parent_id = pending.popleft()
            children = db.query(AgentRun.id).filter(AgentRun.parent_run_id == parent_id).all()
            for (child_id,) in children:
                if child_id not in ids:
                    ids.add(child_id)
                    pending.append(child_id)
                    if len(ids) >= limit:
                        break
        return sorted(ids)

    def audit_replay(self, *, db: Session, run_id: int) -> tuple[list[A2ADelegation], list[AgentAuditEvent]]:
        run_ids = self._lineage_run_ids(db, run_id=run_id)
        if not run_ids:
            return [], []
        delegations = (
            db.query(A2ADelegation)
            .filter(A2ADelegation.parent_run_id.in_(run_ids))
            .order_by(A2ADelegation.id.asc())
            .all()
        )
        events = (
            db.query(AgentAuditEvent)
            .filter(AgentAuditEvent.run_id.in_(run_ids))
            .order_by(AgentAuditEvent.id.asc())
            .all()
        )
        return delegations, events


a2a_collaboration_service = A2ACollaborationService()
