"""ToolExecutor：Agent 工具统一执行链路（单一 choke point）。

职责（全部集中在此，任何工具/节点不得绕过）：
1. 权限校验（PermissionGuard：ACL + 长流程权限快照）。
2. 取消检查：工具执行前若已请求取消则拒绝执行。
3. 审批闸：写工具/敏感读工具需审批，审批绑定 run/step/参数摘要。
4. 幂等：写工具按 (run, step, tool, input_hash) 去重，重放返回缓存快照。
5. 超时：工具级超时（契约优先，回退 AGENT_TOOL_TIMEOUT_SECONDS）。
6. 重试：仅暂时性错误 + 契约声明可重试才重试；不可重试写工具绝不盲目重试。
7. 结果标准化 / 错误映射 / 审计事件。

底层复用 ``MCPRegistry``（discover / schema 校验 / context 注入 / invoke / 规范化 / hooks），
并传 ``skip_approval=True`` 避免 registry 重复建审批（审批由本执行器统一管理）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.mcp.permission_guard import permission_guard
from app.mcp.registry import mcp_registry
from app.mcp.tool_contract import requires_approval_for, resolve_contract
from app.services.agent.agent_approval_service import agent_approval_service
from app.services.agent.agent_audit import (
    EVENT_APPROVAL_CREATED,
    EVENT_PERMISSION_DECISION,
    EVENT_RETRIEVAL_RESULT,
    EVENT_TIMEOUT,
    EVENT_TOOL_EXECUTED,
    agent_audit_service,
)
from app.services.agent.agent_observability import (
    observe_tool_call,
    record_retrieval_result,
    record_tool_outcome,
)
from app.services.agent.agent_run_state import (
    ERROR_CATEGORY_CANCELLED,
    RETRYABLE_CATEGORIES,
    classify_error,
)
from app.services.jobs.idempotency_service import IdempotencyConflictError, idempotency_service

logger = logging.getLogger("app.mcp.executor")

CancelCheck = Callable[[], bool]


class ToolExecutionError(RuntimeError):
    """工具执行被安全策略拒绝/中断（携带结构化决策）。"""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__(result.get("error") or "Tool execution blocked")


def _serialize_input(action_input: dict[str, Any]) -> str:
    safe = {k: v for k, v in action_input.items() if k != "db"}
    return json.dumps(safe, ensure_ascii=False, default=str)


def _approval_required_result(
    *,
    tool_name: str,
    approval_id: int,
    risk_level: str,
    approval_status: str,
) -> dict[str, Any]:
    return {
        "success": False,
        "message": "工具调用需要人工审批",
        "data": {
            "tool_name": tool_name,
            "approval_required": True,
            "approval_request_id": approval_id,
            "approval_status": approval_status,
            "risk_level": risk_level,
        },
        "error": "工具调用需要人工审批",
        "mcp_error_code": "MCP_APPROVAL_REQUIRED",
        "mcp_http_status": 409,
    }


def _timeout_result(tool_name: str, timeout_seconds: int) -> dict[str, Any]:
    return {
        "success": False,
        "message": "工具执行超时，已停止等待该步骤。",
        "data": {"tool_name": tool_name, "timeout_seconds": timeout_seconds},
        "error": "agent_tool_timeout",
        "mcp_error_code": "AGENT_TOOL_TIMEOUT",
    }


class AgentToolExecutor:
    """统一工具执行器单例。外部必须经 ``execute`` 执行工具。"""

    def __init__(self, *, registry=mcp_registry, guard=permission_guard) -> None:
        self._registry = registry
        self._guard = guard

    async def execute(
        self,
        tool_name: str,
        action_input: dict[str, Any],
        *,
        agent_type: str,
        user_id: int,
        db: Session,
        agent_run_id: int | None = None,
        skip_approval: bool = False,
        step_id: int | None = None,
        trace_id: str | None = None,
        organization_id: int | None = None,
        cancel_check: CancelCheck | None = None,
        approve_context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """执行一次工具调用，返回 (标准化结果, 序列化输入)。不会抛业务异常。

        安全中断（权限/取消/审批/参数变化）以结构化结果返回；致命执行异常记录审计后
        映射为标准化失败结果。
        """
        settings = get_settings()
        started = time.time()
        tool = self._registry.get_tool(tool_name)
        contract = resolve_contract(tool)
        timeout_seconds = contract.timeout_seconds or settings.AGENT_TOOL_TIMEOUT_SECONDS
        serialized_input = _serialize_input(action_input)

        def duration_ms() -> int:
            return int((time.time() - started) * 1000)

        # ── 1. 权限 ───────────────────────────────────────────────
        decision = self._guard.check_tool_execution(
            agent_type=agent_type,
            tool_name=tool_name,
            db=db,
            agent_run_id=agent_run_id,
            user_id=user_id,
            organization_id=organization_id,
            policy_context=approve_context,
        )
        if not decision.allowed:
            self._audit(
                db=db,
                run_id=agent_run_id,
                step=step_id,
                trace_id=trace_id,
                user_id=user_id,
                organization_id=organization_id,
                tool_name=tool_name,
                tool_version=contract.version,
                event_type=EVENT_PERMISSION_DECISION,
                decision=decision.to_dict(),
                status="denied",
                duration_ms=duration_ms(),
            )
            return self._guard.denied_result(decision), serialized_input

        # Persist allowed decisions as well as denials.  Replay needs the
        # complete decision stream to detect policy-version or authorization
        # drift, not only calls that were blocked.
        self._audit(
            db=db,
            run_id=agent_run_id,
            step=step_id,
            trace_id=trace_id,
            user_id=user_id,
            organization_id=organization_id,
            tool_name=tool_name,
            tool_version=contract.version,
            event_type=EVENT_PERMISSION_DECISION,
            decision=decision.to_dict(),
            status="allowed",
            duration_ms=duration_ms(),
        )

        # ── 2. 取消检查 ───────────────────────────────────────────
        if cancel_check is not None:
            try:
                cancelled = bool(cancel_check())
            except Exception:  # noqa: BLE001 - 取消检查异常按未取消处理
                cancelled = False
            if cancelled:
                result = {
                    "success": False,
                    "message": "执行已取消，未继续调用该工具。",
                    "data": {"tool_name": tool_name, "cancelled": True},
                    "error": "cancelled_by_user",
                    "mcp_error_code": "AGENT_CANCELLED",
                }
                self._audit(
                    db=db,
                    run_id=agent_run_id,
                    step=step_id,
                    trace_id=trace_id,
                    user_id=user_id,
                    organization_id=organization_id,
                    tool_name=tool_name,
                    tool_version=contract.version,
                    event_type=EVENT_PERMISSION_DECISION,
                    status="cancelled",
                    error_category=ERROR_CATEGORY_CANCELLED,
                    duration_ms=duration_ms(),
                )
                return result, serialized_input

        # ── 3. 审批闸 ─────────────────────────────────────────────
        approval_required = (
            not skip_approval
            and user_id is not None
            and (decision.requires_approval or requires_approval_for(tool_name, contract))
        )
        if approval_required and db is None:
            # fail-closed：需要人工审批的写工具在缺少数据库会话（无法创建审批记录）
            # 时一律拒绝执行，绝不静默跳过审批闸。
            result = {
                "success": False,
                "message": "该工具需要人工审批，但当前执行上下文缺少数据库会话，已拒绝执行。",
                "data": {"tool_name": tool_name, "approval_required": True},
                "error": "approval_context_missing",
                "mcp_error_code": "APPROVAL_CONTEXT_REQUIRED",
            }
            self._audit(
                db=db,
                run_id=agent_run_id,
                step=step_id,
                trace_id=trace_id,
                user_id=user_id,
                organization_id=organization_id,
                tool_name=tool_name,
                tool_version=contract.version,
                event_type=EVENT_PERMISSION_DECISION,
                status="denied",
                summary={"approval_context_missing": True},
                duration_ms=duration_ms(),
            )
            return result, serialized_input
        if approval_required:
            approval = agent_approval_service.create_request(
                db=db,
                user_id=user_id,
                tool_name=tool_name,
                input_params=json.loads(serialized_input) if serialized_input else {},
                agent_type=agent_type,
                agent_run_id=agent_run_id,
                step_id=step_id,
                risk_level=decision.risk_level,
                policy_version=decision.policy_version,
                data_scope=decision.data_scope,
            )
            self._audit(
                db=db,
                run_id=agent_run_id,
                step=step_id,
                trace_id=trace_id,
                user_id=user_id,
                organization_id=organization_id,
                tool_name=tool_name,
                tool_version=contract.version,
                event_type=EVENT_APPROVAL_CREATED,
                summary={"approval_request_id": approval.id, "risk_level": approval.risk_level},
                status="pending",
                duration_ms=duration_ms(),
            )
            return _approval_required_result(
                tool_name=tool_name,
                approval_id=approval.id,
                risk_level=approval.risk_level,
                approval_status=approval.status,
            ), serialized_input

        # ── 4. 幂等（仅写工具且支持幂等键）────────────────────────
        idempotency_key: str | None = None
        if (
            settings.AGENT_TOOL_IDEMPOTENCY_ENABLED
            and contract.idempotency_keyed
            and not contract.read_only
            and agent_run_id is not None
            and step_id is not None
        ):
            import hashlib

            input_hash = hashlib.sha256(serialized_input.encode("utf-8")).hexdigest()
            idempotency_key = f"{agent_run_id}:{step_id}:{tool_name}:{input_hash}"
            try:
                idem = idempotency_service.begin(db, scope="agent_tool", key=idempotency_key, request_hash=input_hash)
            except IdempotencyConflictError:
                idem = {"replay": False, "response_snapshot": None}
            if idem.get("replay") and idem.get("response_snapshot"):
                try:
                    cached = json.loads(idem["response_snapshot"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    cached = None
                if isinstance(cached, dict):
                    cached.setdefault("data", {})
                    cached["data"]["idempotent_replay"] = True
                    self._audit(
                        db=db,
                        run_id=agent_run_id,
                        step=step_id,
                        trace_id=trace_id,
                        user_id=user_id,
                        organization_id=organization_id,
                        tool_name=tool_name,
                        tool_version=contract.version,
                        event_type=EVENT_TOOL_EXECUTED,
                        status="replayed",
                        summary={"idempotency_key": idempotency_key},
                        duration_ms=duration_ms(),
                    )
                    return cached, serialized_input

        # ── 5. 执行（超时 + 重试）─────────────────────────────────
        with observe_tool_call(
            run_id=agent_run_id,
            trace_id=trace_id,
            step=step_id,
            tool_name=tool_name,
            agent_type=agent_type,
            read_only=contract.read_only,
        ) as tool_span:
            result = await self._invoke_with_policy(
                tool_name=tool_name,
                action_input=action_input,
                contract_retryable=contract.retryable,
                contract_max_retries=contract.max_retries,
                backoff_base=contract.backoff_base_seconds,
                timeout_seconds=timeout_seconds,
                agent_type=agent_type,
                user_id=user_id,
                db=db,
                agent_run_id=agent_run_id,
                cancel_check=cancel_check,
            )
            outcome_category = classify_error(result.get("error"), result.get("mcp_error_code"))
            record_tool_outcome(
                tool_span,
                success=bool(result.get("success")),
                status="success" if result.get("success") else "error",
                duration_ms=duration_ms(),
                error_category=outcome_category,
            )

        # ── 6. 幂等登记完成/失败 ──────────────────────────────────
        if idempotency_key is not None:
            try:
                if result.get("success"):
                    idempotency_service.complete(db, scope="agent_tool", key=idempotency_key, response_snapshot=result)
                else:
                    idempotency_service.fail(db, scope="agent_tool", key=idempotency_key)
            except Exception:  # noqa: BLE001 - 幂等登记失败不影响返回
                db.rollback()

        # ── 7. 审计 ───────────────────────────────────────────────
        error_category = classify_error(result.get("error"), result.get("mcp_error_code"))
        if result.get("mcp_error_code") == "AGENT_TOOL_TIMEOUT":
            self._audit(
                db=db,
                run_id=agent_run_id,
                step=step_id,
                trace_id=trace_id,
                user_id=user_id,
                organization_id=organization_id,
                tool_name=tool_name,
                tool_version=contract.version,
                event_type=EVENT_TIMEOUT,
                summary={"timeout_seconds": timeout_seconds},
                error_category=error_category,
                status="timeout",
                duration_ms=duration_ms(),
            )
        else:
            self._audit(
                db=db,
                run_id=agent_run_id,
                step=step_id,
                trace_id=trace_id,
                user_id=user_id,
                organization_id=organization_id,
                tool_name=tool_name,
                tool_version=contract.version,
                event_type=EVENT_TOOL_EXECUTED,
                summary={"success": bool(result.get("success")), "error_category": error_category},
                error_category=error_category,
                status="success" if result.get("success") else "error",
                duration_ms=duration_ms(),
            )
        if contract.read_only and tool_name in {"document_search_tool", "legal_search_tool", "knowledge_search_tool"}:
            record_retrieval_result(
                run_id=agent_run_id,
                trace_id=trace_id,
                step=step_id,
                tool_name=tool_name,
                result=result,
            )
            self._audit(
                db=db,
                run_id=agent_run_id,
                step=step_id,
                trace_id=trace_id,
                user_id=user_id,
                organization_id=organization_id,
                tool_name=tool_name,
                tool_version=contract.version,
                event_type=EVENT_RETRIEVAL_RESULT,
                summary={"success": bool(result.get("success"))},
                status="success" if result.get("success") else "error",
                duration_ms=duration_ms(),
            )
        if not result.get("success") and agent_run_id is not None:
            try:
                from app.services.agent.online_eval_service import online_eval_service

                online_eval_service.capture_failure(
                    db,
                    run_id=agent_run_id,
                    trace_id=trace_id,
                    user_id=user_id,
                    organization_id=organization_id,
                    failure_type=f"tool_{tool_name}_{error_category}",
                    evidence={
                        "tool_name": tool_name,
                        "status": "error",
                        "error_category": error_category,
                        "mcp_error_code": result.get("mcp_error_code"),
                        "duration_ms": duration_ms(),
                    },
                )
            except Exception:
                db.rollback()
        return result, serialized_input

    async def _invoke_with_policy(
        self,
        *,
        tool_name: str,
        action_input: dict[str, Any],
        contract_retryable: bool,
        contract_max_retries: int,
        backoff_base: float,
        timeout_seconds: int,
        agent_type: str,
        user_id: int,
        db: Session,
        agent_run_id: int | None,
        cancel_check: CancelCheck | None,
    ) -> dict[str, Any]:
        attempt = 0
        max_attempts = contract_max_retries + 1
        while True:
            attempt += 1
            result = await self._invoke_once(
                tool_name=tool_name,
                action_input=action_input,
                timeout_seconds=timeout_seconds,
                agent_type=agent_type,
                user_id=user_id,
                db=db,
                agent_run_id=agent_run_id,
            )
            error_category = classify_error(result.get("error"), result.get("mcp_error_code"))
            retryable = (
                contract_retryable
                and error_category in RETRYABLE_CATEGORIES
                and attempt < max_attempts
                and not result.get("success")
            )
            if not retryable:
                return result
            if cancel_check is not None and cancel_check():
                result = {
                    "success": False,
                    "message": "执行已取消，未继续重试。",
                    "data": {"tool_name": tool_name, "cancelled": True, "attempts": attempt},
                    "error": "cancelled_by_user",
                    "mcp_error_code": "AGENT_CANCELLED",
                }
                return result
            await asyncio.sleep(backoff_base * attempt)

    async def _invoke_once(
        self,
        *,
        tool_name: str,
        action_input: dict[str, Any],
        timeout_seconds: int,
        agent_type: str,
        user_id: int,
        db: Session,
        agent_run_id: int | None,
    ) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                self._registry.call_tool(
                    tool_name,
                    action_input,
                    agent_type=agent_type,
                    user_id=user_id,
                    db=db,
                    agent_run_id=agent_run_id,
                    skip_approval=True,
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return _timeout_result(tool_name, timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - 执行器兜底，不透出内部细节
            logger.warning("tool invocation unexpected error tool=%s: %s", tool_name, type(exc).__name__)
            return {
                "success": False,
                "message": "Tool execution failed",
                "data": {"tool_name": tool_name},
                "error": "Tool execution failed",
                "mcp_error_code": "MCP_INTERNAL_ERROR",
            }

    def _audit(
        self,
        *,
        db: Session,
        run_id: int | None,
        step: int | None,
        trace_id: str | None,
        user_id: int | None,
        organization_id: int | None,
        tool_name: str | None,
        tool_version: str | None,
        event_type: str,
        status: str | None = None,
        decision: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
        error_category: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        try:
            agent_audit_service.record(
                db=db,
                event_type=event_type,
                run_id=run_id,
                step=step,
                trace_id=trace_id,
                user_id=user_id,
                organization_id=organization_id,
                tool_name=tool_name,
                tool_version=tool_version,
                decision=decision,
                summary=summary,
                error_category=error_category,
                status=status,
                duration_ms=duration_ms,
            )
        except Exception:  # noqa: BLE001 - 审计失败不回滚业务结果
            if db is not None:
                db.rollback()


tool_executor = AgentToolExecutor()
