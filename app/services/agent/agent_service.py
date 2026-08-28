import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.model_policy import new_trace_id
from app.core.time import utc_now
from app.mcp.executor import tool_executor
from app.mcp.permission_guard import permission_guard
from app.mcp.permissions import (
    canonical_agent_type,
)
from app.mcp.registry import mcp_registry
from app.models.agent import AgentApprovalRequest, AgentRun, ToolCallLog
from app.models.user import User
from app.services.agent.agent_approval_service import agent_approval_service
from app.services.agent.agent_audit import (
    EVENT_ERROR,
    EVENT_PLAN_CREATED,
    agent_audit_service,
)
from app.services.agent.agent_harness_service import get_harness_profile
from app.services.agent.agent_json import json_dumps as _json_dumps
from app.services.agent.agent_json import json_loads_dict as _json_loads_dict
from app.services.agent.agent_mixins import EvidenceVerificationMixin
from app.services.agent.agent_observability import observe_agent_run, record_state_transition
from app.services.agent.agent_planner import Planner
from app.services.agent.agent_planner import planner as _planner_default
from app.services.agent.agent_prompts import (
    PARALLEL_READ_ONLY_TOOLS,
    PARALLEL_READ_ONLY_WORKERS,
    SUB_AGENTS,
)
from app.services.agent.agent_prompts import (
    build_demo_plan_preview as _build_demo_plan_preview,
)
from app.services.agent.agent_prompts import (
    build_preview_prompt as _build_preview_prompt,
)
from app.services.agent.agent_prompts import (
    build_worker_system_prompt as _build_worker_system_prompt,
)
from app.services.agent.agent_prompts import (
    goal_execution_hints as _goal_execution_hints,
)
from app.services.agent.agent_prompts import (
    normalize_decision as _normalize_decision,
)
from app.services.agent.agent_prompts import (
    sanitize_agent_error_message as _sanitize_agent_error_message,
)
from app.services.agent.agent_registry import AGENT_REGISTRY, AGENT_REGISTRY_VERSION
from app.services.agent.agent_run_repository import RunStateRepository, run_state_repository
from app.services.agent.agent_run_state import (
    STATUS_RUNNING,
    AgentPlan,
    AgentRunState,
)
from app.services.agent.agent_runtime import AgentGraphState, AgentRuntime
from app.services.agent.agent_skill_registry import resolve_agent_skill
from app.services.agent.agent_workflow_nodes import AgentWorkflowNodesMixin
from app.services.agent.run_queries import RunQueriesMixin
from app.services.agent.supervisor_planning import SupervisorPlanningMixin
from app.services.llm.llm_observability_service import llm_observability_service
from app.services.llm.llm_service import llm_service
from app.services.llm.prompt_service import prompt_service
from app.services.memory.conversation_memory_service import conversation_memory_service
from app.workflows.langgraph_compat import (
    GRAPH_END,
    GRAPH_START,
    INTERRUPT_AVAILABLE,
    RUNTIME_CONTEXT_KEY,
    Command,
    StateGraph,
    build_checkpointer,
    workflow_engine_name,
)


class AgentService(EvidenceVerificationMixin, AgentWorkflowNodesMixin, SupervisorPlanningMixin, RunQueriesMixin):
    def __init__(self) -> None:
        self.settings = get_settings()
        # 职责拆分：Planner 只规划，PermissionGuard/ToolExecutor 管权限与执行，
        # RunStateRepository 管持久化，EvidenceVerifier 管证据校验。
        self._planner: Planner = _planner_default
        self._executor = tool_executor
        self._guard = permission_guard
        self._repo: RunStateRepository = run_state_repository
        self._workflow = self._build_workflow()

    def _build_worker_messages(
        self,
        goal: str,
        worker_name: str,
        user_id: int,
        handoff_context: dict[str, Any] | None = None,
        memory_context: str = "",
        task_contract: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        worker_name = canonical_agent_type(worker_name)
        worker = SUB_AGENTS.get(worker_name) or SUB_AGENTS["knowledge_agent"]
        handoff_text = ""
        if handoff_context:
            handoff_text = (
                "\n\n上游 Worker 已完成，请仅使用以下结构化交接内容继续本职责：\n" f"{_json_dumps(handoff_context)}"
            )
        memory_text = f"\n\n用户会话记忆（仅作辅助上下文）：\n{memory_context}" if memory_context else ""
        task_text = (
            "\n\n结构化任务协议（权限由服务端强制执行，不得自行修改）：\n" f"{_json_dumps(task_contract)}"
            if task_contract
            else ""
        )
        return [
            {"role": "system", "content": _build_worker_system_prompt(worker_name, user_id)},
            {
                "role": "user",
                "content": (
                    "主 Agent 已完成任务分派。\n"
                    f"worker_agent: {worker_name}\n"
                    f"worker_scope: {worker['description']}\n"
                    f"goal: {goal}\n\n"
                    f"执行提示：\n{_goal_execution_hints(goal)}\n\n"
                    "请作为该从 Agent 逐步执行。需要工具时输出 tool_call；完成职责后输出 finish。"
                    f"{task_text}{handoff_text}{memory_text}"
                ),
            },
        ]

    def _build_handoff_context(self, logs: list[ToolCallLog], from_worker: str, answer: str) -> dict[str, Any]:
        completed_steps: list[dict[str, Any]] = []
        for log in logs[-6:]:
            if log.status != "success" or log.tool_name in {"finish", "evidence_verifier", "supervisor_handoff"}:
                continue
            observation = _json_loads_dict(log.observation)
            data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
            completed_steps.append({"tool_name": log.tool_name, "data": data})
        return {
            "from_worker": from_worker,
            "completion_summary": answer,
            "completed_steps": completed_steps[-3:],
        }

    def _collect_run_artifacts(self, logs: list[ToolCallLog]) -> dict[str, list[dict[str, Any]]]:
        artifacts = {
            "documents": [],
            "tasks": [],
        }
        seen_keys: set[tuple[str, Any]] = set()

        def add_artifact(kind: str, key: Any, payload: dict[str, Any]) -> None:
            dedupe_key = (kind, key)
            if key is None or dedupe_key in seen_keys:
                return
            seen_keys.add(dedupe_key)
            artifacts[kind].append(payload)

        for log in logs:
            input_params = _json_loads_dict(log.input_params)
            observation = _json_loads_dict(log.observation)
            data = observation.get("data") if isinstance(observation.get("data"), dict) else {}

            document_id = data.get("document_id") or input_params.get("document_id")
            if (
                log.tool_name in {"document_summary_tool", "document_risk_tool", "document_search_tool"}
                and document_id is not None
            ):
                add_artifact(
                    "documents",
                    document_id,
                    {
                        "document_id": document_id,
                        "tool_name": log.tool_name,
                        "summary": data.get("summary"),
                        "risk_count": len(data.get("risks") or []) if isinstance(data.get("risks"), list) else 0,
                        "chunk_count": len(data.get("chunks") or []) if isinstance(data.get("chunks"), list) else 0,
                    },
                )
            if log.tool_name == "document_conflict_tool":
                for conflict_document_id in data.get("document_ids") or input_params.get("document_ids") or []:
                    add_artifact(
                        "documents",
                        conflict_document_id,
                        {
                            "document_id": conflict_document_id,
                            "tool_name": log.tool_name,
                            "conflict_count": len(data.get("conflicts") or []),
                        },
                    )

            tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
            task_payload = data.get("task") if isinstance(data.get("task"), dict) else None
            if task_payload:
                tasks = [task_payload]
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                task_id = task.get("id")
                add_artifact(
                    "tasks",
                    task_id,
                    {
                        "task_id": task_id,
                        "title": task.get("title"),
                        "status": task.get("status"),
                        "priority": task.get("priority"),
                        "assignee": task.get("assignee"),
                        "tool_name": log.tool_name,
                    },
                )

        return artifacts

    def _record_run_summary(
        self, *, run: AgentRun, status: str, duration_ms: int, error_message: str | None = None
    ) -> None:
        parsed_result = _json_loads_dict(run.result)
        llm_observability_service.log_event(
            module_name="agent",
            action="agent_run",
            model_name="agent_orchestrator",
            status=status,
            duration_ms=duration_ms,
            user_id=run.user_id,
            error_message=_sanitize_agent_error_message(error_message),
            request_excerpt={"run_id": run.id, "goal": run.goal},
            response_excerpt={
                "total_steps": run.total_steps,
                "final_status": run.status,
                "failure_reason": _sanitize_agent_error_message(run.failure_reason),
                "master_agent": parsed_result.get("master_agent"),
                "worker_agent": parsed_result.get("worker_agent"),
            },
        )

    async def _chat(self, messages: list[dict[str, str]], user_id: int) -> str:
        metadata = prompt_service.get_template_metadata("agent_system_prompt", user_id=user_id)
        try:
            return await llm_service.chat(
                messages,
                temperature=0.2,
                action="agent_plan",
                user_id=user_id,
                prompt_template=metadata.get("prompt_template"),
                prompt_version=metadata.get("prompt_version"),
            )
        except TypeError as exc:
            error_text = str(exc)
            if "unexpected keyword argument" not in error_text and "positional argument" not in error_text:
                raise
            return await llm_service.chat(messages, temperature=0.2)

    async def preview_plan(self, goal: str, user_id: int, max_steps: int = 5) -> dict[str, Any]:
        selected_skill = resolve_agent_skill(goal)
        demo_preview = _build_demo_plan_preview(goal, max_steps)
        if demo_preview:
            return {**demo_preview, "selected_skill": selected_skill}

        metadata = prompt_service.get_template_metadata("agent_plan_preview", user_id=user_id)
        prompt = _build_preview_prompt(goal, user_id=user_id)
        try:
            raw = await llm_service.generate(
                prompt,
                temperature=0.2,
                action="agent_plan_preview",
                user_id=user_id,
                prompt_template=metadata.get("prompt_template"),
                prompt_version=metadata.get("prompt_version"),
            )
        except TypeError as exc:
            error_text = str(exc)
            if "unexpected keyword argument" not in error_text and "positional argument" not in error_text:
                raise
            raw = await llm_service.generate(prompt, temperature=0.2)

        payload = llm_service.parse_json_object(raw)
        steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
        normalized_steps = []
        for index, step in enumerate(steps[:max_steps], start=1):
            if not isinstance(step, dict):
                continue
            tool_name = str(step.get("tool_name") or "").strip()
            if tool_name and tool_name not in {t["name"] for t in mcp_registry.list_all_tools()}:
                continue
            normalized_steps.append(
                {
                    "step": int(step.get("step") or index),
                    "tool_name": tool_name or "finish",
                    "purpose": str(step.get("purpose") or "").strip() or "待执行说明",
                    "action_input_preview": step.get("action_input_preview")
                    if isinstance(step.get("action_input_preview"), dict)
                    else {},
                }
            )

        risks = payload.get("risks") if isinstance(payload.get("risks"), list) else []
        normalized_risks = [str(item).strip() for item in risks if str(item).strip()]

        summary = str(payload.get("summary") or "").strip() or "将按目标分解步骤并调用相关工具执行。"
        can_execute = bool(payload.get("can_execute", True))
        estimated_steps = int(payload.get("estimated_steps") or len(normalized_steps) or 1)
        estimated_steps = max(1, min(estimated_steps, max_steps))

        if not normalized_steps:
            normalized_steps = [
                {
                    "step": 1,
                    "tool_name": "finish",
                    "purpose": "当前目标信息不足，建议补充更具体的对象编号或范围后再执行。",
                    "action_input_preview": {},
                }
            ]
            can_execute = False
            if not normalized_risks:
                normalized_risks.append("未能生成可靠的工具计划，请补充目标细节。")
            estimated_steps = 1

        return {
            "summary": summary,
            "estimated_steps": estimated_steps,
            "steps": normalized_steps,
            "risks": normalized_risks,
            "can_execute": can_execute,
            "selected_skill": selected_skill,
        }

    async def _emit_event(
        self,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
        payload: dict[str, Any],
    ) -> None:
        if not event_callback:
            return
        await event_callback(payload)

    def _create_run(
        self,
        goal: str,
        user_id: int,
        session_id: int | None,
        db: Session,
        *,
        trace_id: str | None = None,
        organization_id: int | None = None,
        agent_type: str | None = None,
        parent_run_id: int | None = None,
        delegation_id: str | None = None,
    ) -> AgentRun:
        agent_run = self._repo.create_run(
            db,
            goal=goal,
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
            organization_id=organization_id,
            agent_type=agent_type,
            parent_run_id=parent_run_id,
            delegation_id=delegation_id,
        )
        run_deadline = utc_now() + timedelta(seconds=self.settings.AGENT_RUN_DEADLINE_SECONDS)
        agent_run.run_deadline_at = run_deadline
        db.add(agent_run)
        db.commit()
        db.refresh(agent_run)
        return agent_run

    def _build_awaiting_approval_payload(
        self,
        *,
        agent_run_id: int,
        db: Session,
        user_id: int,
        master_agent: str,
        worker_agent: str,
        approval_request_id: int,
        tool_name: str,
        max_steps: int,
        worker_plan: list[str] | None = None,
        handoffs: list[dict[str, Any]] | None = None,
        supervisor_plan_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        supervisor_plan = dict(supervisor_plan_details or {})
        supervisor_plan["workers"] = worker_plan or [worker_agent]
        supervisor_plan["handoffs"] = handoffs or []
        return {
            "final_answer": "执行已暂停，等待人工审批。",
            "master_agent": master_agent,
            "worker_agent": worker_agent,
            "agent_mode": "langgraph_workflow",
            "architecture_version": AGENT_REGISTRY_VERSION,
            "workflow_engine": workflow_engine_name(),
            "mcp_enabled": True,
            "policy_guardrails": ["rbac", "tool_acl", "approval", "evidence_verification"],
            "awaiting_approval": True,
            "approval_request_id": approval_request_id,
            "pending_tool_name": tool_name,
            "max_steps": max_steps,
            "supervisor_plan": supervisor_plan,
            "artifacts": self._collect_run_artifacts(self.get_run_logs(agent_run_id, db, user_id=user_id)),
        }

    def _save_run(self, db: Session, agent_run: AgentRun, **fields) -> AgentRun:
        previous_status = agent_run.status
        saved = self._repo.save_run(db, agent_run, **fields)
        target_status = fields.get("status")
        record_state_transition(
            run_id=saved.id,
            trace_id=saved.trace_id,
            from_status=previous_status,
            to_status=target_status,
        )
        if target_status == "error":
            try:
                from app.services.agent.online_eval_service import online_eval_service

                online_eval_service.capture_failure(
                    db,
                    run_id=saved.id,
                    trace_id=saved.trace_id,
                    user_id=saved.user_id,
                    organization_id=saved.organization_id,
                    failure_type="run_error",
                    goal=saved.goal,
                    evidence={
                        "status": target_status,
                        "failure_reason": saved.failure_reason,
                        "total_steps": saved.total_steps,
                    },
                )
            except Exception:
                db.rollback()
        return saved

    @staticmethod
    def _sync_a2a_delegation(db: Session, agent_run: AgentRun) -> None:
        """Reflect a delegated child Run's lifecycle into the A2A ledger."""
        if not agent_run.delegation_id:
            return
        try:
            from app.services.agent.a2a_service import a2a_collaboration_service

            a2a_collaboration_service.sync_from_child_run(db=db, child_run=agent_run)
        except Exception:  # noqa: BLE001 - audit projection must not alter Run outcome
            db.rollback()

    @staticmethod
    def _load_workflow_snapshot(agent_run: AgentRun) -> dict[str, Any]:
        return run_state_repository.load_workflow_snapshot(agent_run)

    def _build_workflow_snapshot(self, state: dict[str, Any], *, node: str) -> dict[str, Any]:
        supervisor_plan = dict(state.get("supervisor_plan") or {})
        supervisor_plan["workers"] = list(state.get("worker_plan") or [state.get("worker_agent")])
        supervisor_plan["handoffs"] = list(state.get("handoffs") or [])
        return {
            "version": 3,
            "architecture_version": AGENT_REGISTRY_VERSION,
            "agent_registry_version": AGENT_REGISTRY_VERSION,
            "node": node,
            "task_contract": state.get("task_contract") or {},
            "worker_plan": list(state.get("worker_plan") or [state.get("worker_agent")]),
            "worker_agent": state.get("worker_agent"),
            "worker_index": int(state.get("worker_index") or 0),
            "handoffs": list(state.get("handoffs") or []),
            "parallel_plan": state.get("parallel_plan"),
            "parallel_results": state.get("parallel_results") or {},
            "step": int(state.get("step") or 0),
            "retry_count": int(state.get("retry_count") or 0),
            "evidence_scope_seen": bool(state.get("evidence_scope_seen")),
            "last_observation": state.get("last_observation") or "",
            "current_tool_name": state.get("current_tool_name"),
            "supervisor_plan": supervisor_plan,
            "updated_at": utc_now().isoformat(),
        }

    def _save_workflow_snapshot(self, runtime: AgentRuntime, state: dict[str, Any], *, node: str, **fields) -> AgentRun:
        model = runtime.model
        if not isinstance(model, AgentRunState):
            model = AgentRunState(
                run_id=runtime.agent_run.id,
                user_id=runtime.user_id,
                status=str(runtime.agent_run.status or STATUS_RUNNING),
                node=node,
                step=int(state.get("step") or 0),
                retry_count=int(state.get("retry_count") or 0),
            )
        snapshot = self._build_workflow_snapshot(state, node=node)
        return self._repo.save_workflow_state(
            runtime.db, runtime.agent_run, snapshot=snapshot, state=model, node=node, **fields
        )

    def _append_observation(self, messages: list[dict[str, str]], raw: str, observation: str) -> None:
        messages.append({"role": "assistant", "content": raw})
        messages.append(
            {
                "role": "user",
                "content": ("Observation:\n" f"{observation}\n\n" "请基于最新 observation 决定下一步，只输出 JSON。"),
            }
        )

    def _create_log(
        self,
        db: Session,
        agent_run_id: int,
        step: int,
        decision: dict[str, Any],
        raw_decision: str,
        tool_name: str,
        input_params: dict[str, Any],
        observation: str,
        output_result: str,
        status: str,
        error: str | None,
        duration_ms: int,
    ) -> ToolCallLog:
        return self._repo.append_log(
            db,
            agent_run_id=agent_run_id,
            step=step,
            decision=decision,
            raw_decision=raw_decision,
            tool_name=tool_name,
            input_params=input_params,
            observation=observation,
            output_result=output_result,
            status=status,
            error=_sanitize_agent_error_message(error),
            duration_ms=duration_ms,
        )

    def _update_log(self, db: Session, log: ToolCallLog, **fields) -> ToolCallLog:
        return self._repo.update_log(db, log, **fields)

    async def _execute_tool(
        self,
        tool_name: str,
        action_input: dict[str, Any],
        user_id: int,
        db: Session,
        agent_type: str = "general_agent",
        agent_run_id: int | None = None,
        skip_approval: bool = False,
        *,
        step_id: int | None = None,
        trace_id: str | None = None,
        organization_id: int | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> tuple[dict, str | None]:
        """统一执行入口：委托 ToolExecutor（权限/取消/审批/幂等/超时/重试/审计）。

        Returns (result_dict, serialized_input_for_logging).
        """
        result, serialized_input = await self._executor.execute(
            tool_name,
            action_input,
            agent_type=agent_type,
            user_id=user_id,
            db=db,
            agent_run_id=agent_run_id,
            skip_approval=skip_approval,
            step_id=step_id,
            trace_id=trace_id,
            organization_id=organization_id,
            cancel_check=cancel_check,
        )
        return result, serialized_input

    def _assert_run_snapshot(self, db: Session, *, agent_run_id: int, user_id: int) -> dict | None:
        """校验该 Agent run 的权限快照；有效返回 None，失效返回拒绝结果。"""
        from app.services.org.authorization_service import authorization_service

        run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).first()
        snapshot_id = run.authorization_snapshot_id if run else None
        if not snapshot_id:
            return None
        try:
            authorization_service.assert_snapshot(db, snapshot_id, user_id=user_id)
            return None
        except Exception as exc:
            code = getattr(getattr(exc, "detail", None), "get", lambda *_: "authz_changed")("code", "authz_changed")
            return {
                "success": False,
                "message": "执行已终止：权限已变化，请重新发起。",
                "data": {"error_code": code},
                "error": code,
                "mcp_error_code": "AUTHZ_CHANGED",
            }

    @staticmethod
    def _parallel_session_factory(db: Session):
        bind = db.get_bind()
        return sessionmaker(autocommit=False, autoflush=False, bind=bind)

    async def _execute_parallel_read_only_worker(
        self,
        *,
        worker_name: str,
        tool_name: str,
        action_input: dict[str, Any],
        user_id: int,
        db: Session,
        agent_run_id: int,
        step_id: int | None = None,
        trace_id: str | None = None,
        organization_id: int | None = None,
    ) -> dict[str, Any]:
        canonical_worker = canonical_agent_type(worker_name)
        if canonical_worker not in PARALLEL_READ_ONLY_WORKERS or tool_name not in PARALLEL_READ_ONLY_TOOLS:
            return {
                "worker_agent": worker_name,
                "tool_name": tool_name,
                "success": False,
                "error": "parallel_read_only_policy_denied",
                "data": {},
                "duration_ms": 0,
            }
        branch_db = self._parallel_session_factory(db)()
        started = time.time()
        try:
            result, serialized_input = await self._execute_tool(
                tool_name,
                action_input,
                user_id,
                branch_db,
                agent_type=canonical_worker,
                agent_run_id=agent_run_id,
                step_id=step_id,
                trace_id=trace_id,
                organization_id=organization_id,
            )
            return {
                "worker_agent": worker_name,
                "tool_name": tool_name,
                "action_input": json.loads(serialized_input or "{}"),
                "success": bool(result.get("success")),
                "error": result.get("error"),
                "data": result.get("data") if isinstance(result.get("data"), dict) else {},
                "duration_ms": int((time.time() - started) * 1000),
            }
        finally:
            branch_db.close()

    async def _run_parallel_read_only(self, runtime: AgentRuntime, state: dict[str, Any]) -> dict[str, Any]:
        branch_plan = state.get("parallel_plan") or {}
        semaphore = asyncio.Semaphore(self.settings.AGENT_PARALLEL_MAX_WORKERS)

        async def run_bounded(**kwargs):
            async with semaphore:
                return await self._execute_parallel_read_only_worker(**kwargs)

        jobs = []
        for index, (worker_name, step) in enumerate(branch_plan.items()):
            if not isinstance(step, dict):
                continue
            jobs.append(
                run_bounded(
                    worker_name=worker_name,
                    tool_name=str(step.get("tool_name") or ""),
                    action_input=step.get("action_input") if isinstance(step.get("action_input"), dict) else {},
                    user_id=state["user_id"],
                    db=runtime.db,
                    agent_run_id=runtime.agent_run.id,
                    step_id=int(state.get("step") or 0) + index + 1,
                    trace_id=runtime.agent_run.trace_id,
                    organization_id=runtime.agent_run.organization_id,
                )
            )
        results = await asyncio.gather(*jobs) if jobs else []
        return {item["worker_agent"]: item for item in results}

    def _build_run_result_payload(
        self,
        *,
        final_answer: str,
        agent_run_id: int,
        db: Session,
        user_id: int,
        master_agent: str,
        worker_agent: str,
        worker_plan: list[str] | None = None,
        handoffs: list[dict[str, Any]] | None = None,
        supervisor_plan_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logs = self.get_run_logs(agent_run_id, db, user_id=user_id)
        supervisor_plan = dict(supervisor_plan_details or {})
        supervisor_plan["workers"] = worker_plan or [worker_agent]
        supervisor_plan["handoffs"] = handoffs or []
        return {
            "final_answer": final_answer,
            "master_agent": master_agent,
            "worker_agent": worker_agent,
            "agent_mode": "langgraph_workflow",
            "architecture_version": AGENT_REGISTRY_VERSION,
            "workflow_engine": workflow_engine_name(),
            "mcp_enabled": True,
            "policy_guardrails": ["rbac", "tool_acl", "approval", "evidence_verification"],
            "artifacts": self._collect_run_artifacts(logs),
            "evidence_verification": self._latest_evidence_verification(logs),
            "supervisor_plan": supervisor_plan,
        }

    def _finalize_completed_run(
        self,
        *,
        db: Session,
        agent_run: AgentRun,
        final_answer: str,
        last_observation: str,
        failure_reason: str | None,
        total_steps: int,
        master_agent: str,
        worker_agent: str,
        run_started: float,
        summary_status: str,
        error_message: str | None = None,
        worker_plan: list[str] | None = None,
        handoffs: list[dict[str, Any]] | None = None,
        supervisor_plan_details: dict[str, Any] | None = None,
    ) -> AgentRun:
        result_run = self._save_run(
            db,
            agent_run,
            status="completed",
            result=_json_dumps(
                self._build_run_result_payload(
                    final_answer=final_answer,
                    agent_run_id=agent_run.id,
                    db=db,
                    user_id=agent_run.user_id,
                    master_agent=master_agent,
                    worker_agent=worker_agent,
                    worker_plan=worker_plan,
                    handoffs=handoffs,
                    supervisor_plan_details=supervisor_plan_details,
                )
            ),
            final_answer=final_answer,
            last_observation=last_observation,
            failure_reason=failure_reason,
            total_steps=total_steps,
            completed_at=utc_now(),
        )
        self._record_run_summary(
            run=result_run,
            status=summary_status,
            duration_ms=int((time.time() - run_started) * 1000),
            error_message=error_message,
        )
        return result_run

    def _build_workflow(self):
        # 显式 state schema（可序列化通道）+ context schema（Session/ORM/回调等活对象），
        # 二者分离后 checkpointer 才能按 thread_id 落盘并回放图状态。
        graph = StateGraph(AgentGraphState, context_schema=AgentRuntime)
        graph.add_node("decide", self._workflow_decide)
        graph.add_node("parallel_fanout", self._workflow_parallel_fanout)
        graph.add_node("parallel_aggregate", self._workflow_parallel_aggregate)
        graph.add_node("cancelled", self._workflow_cancelled)
        graph.add_node("finish", self._workflow_finish)
        graph.add_node("retry", self._workflow_retry)
        graph.add_node("tool_call", self._workflow_tool_call)
        graph.add_node("verify_evidence", self._workflow_verify_evidence)
        graph.add_node("evidence_insufficient", self._workflow_evidence_insufficient)
        graph.add_node("partial", self._workflow_partial)
        graph.add_node("awaiting_approval", self._workflow_awaiting_approval)
        graph.add_edge(GRAPH_START, "decide")
        graph.add_conditional_edges(
            "decide",
            self._workflow_route_decision,
            {
                "finish": "finish",
                "retry": "retry",
                "tool_call": "tool_call",
                "verify_evidence": "verify_evidence",
                "parallel_fanout": "parallel_fanout",
                "cancelled": "cancelled",
            },
        )
        graph.add_conditional_edges(
            "verify_evidence",
            self._workflow_route_after_evidence_verification,
            {
                "tool_call": "tool_call",
                "finish": "finish",
                "evidence_insufficient": "evidence_insufficient",
                "parallel_aggregate": "parallel_aggregate",
            },
        )
        graph.add_conditional_edges(
            "parallel_fanout",
            self._workflow_route_after_parallel_fanout,
            {
                "verify_evidence": "verify_evidence",
                "parallel_aggregate": "parallel_aggregate",
            },
        )
        graph.add_conditional_edges(
            "finish",
            self._workflow_route_after_finish,
            {
                "handoff": "decide",
                "complete": GRAPH_END,
            },
        )
        graph.add_conditional_edges(
            "retry",
            self._workflow_route_continue,
            {
                "continue": "decide",
                "partial": "partial",
            },
        )
        graph.add_conditional_edges(
            "tool_call",
            self._workflow_route_continue,
            {
                "continue": "decide",
                "partial": "partial",
                "awaiting_approval": "awaiting_approval",
            },
        )
        graph.add_edge("partial", GRAPH_END)
        graph.add_edge("parallel_aggregate", GRAPH_END)
        graph.add_edge("cancelled", GRAPH_END)
        graph.add_edge("evidence_insufficient", GRAPH_END)
        # awaiting_approval 是 interrupt 断点：首次进入时挂起（图在此结束，等人工审批），
        # Command(resume=...) 后同一节点内执行写工具并清掉 awaiting_approval，再按预算回 decide。
        # "awaiting_approval" 分支留给无 interrupt 能力的回退引擎：停在此节点直接结束。
        graph.add_conditional_edges(
            "awaiting_approval",
            self._workflow_route_continue,
            {
                "continue": "decide",
                "partial": "partial",
                "awaiting_approval": GRAPH_END,
            },
        )
        return graph.compile(checkpointer=build_checkpointer())

    @staticmethod
    def _graph_config(agent_run: AgentRun) -> dict[str, Any]:
        """thread_id 按 Run 划分：checkpointer 以此为主键落盘，可回放/续跑同一次执行。

        必须带上 trace_id。checkpoint 现在落到 SQLite 并跨进程存活，而 ``AgentRun.id``
        只在单个业务库内唯一——重建库或换库后自增 id 会重复，只用 id 会让新 Run 直接
        恢复到上一个同 id Run 的图状态（表现为新 Run 一启动就停在旧的终态）。
        """
        thread_id = f"agent-run-{agent_run.id}"
        if agent_run.trace_id:
            thread_id = f"{thread_id}-{agent_run.trace_id}"
        return {"configurable": {"thread_id": thread_id}}

    async def run(
        self,
        goal: str,
        user_id: int,
        db: Session,
        session_id: int | None = None,
        max_steps: int = 5,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        *,
        existing_run: AgentRun | None = None,
        forced_worker_agent: str | None = None,
    ) -> AgentRun:
        # 长流程权限快照：Agent 执行期间权限范围保持稳定，硬撤销立即终止。
        from app.services.org.authorization_service import authorization_service

        user_row = db.query(User).filter(User.id == user_id).first()
        if not user_row:
            raise ValueError("Agent user not found")
        forced_worker = canonical_agent_type(forced_worker_agent or "") if forced_worker_agent else None
        if forced_worker and forced_worker not in AGENT_REGISTRY:
            raise ValueError("Forced worker is not a registered Worker")

        if existing_run:
            if existing_run.user_id != user_id:
                raise ValueError("Existing Agent run belongs to a different user")
            if existing_run.status != "running":
                raise ValueError("Existing Agent run is not dispatchable")
            if (
                forced_worker
                and existing_run.agent_type
                and canonical_agent_type(existing_run.agent_type) != forced_worker
            ):
                raise ValueError("Forced worker does not match delegated Agent run")
            if existing_run.authorization_snapshot_id:
                authorization_service.assert_snapshot(db, existing_run.authorization_snapshot_id, user_id=user_id)
            agent_run = existing_run
            trace_id = agent_run.trace_id or new_trace_id()
            organization_id = agent_run.organization_id
            if not agent_run.trace_id:
                agent_run.trace_id = trace_id
                db.add(agent_run)
                db.commit()
                db.refresh(agent_run)
        else:
            trace_id = new_trace_id()
            organization_id = user_row.organization_id
            agent_run = self._create_run(
                goal=goal,
                user_id=user_id,
                session_id=session_id,
                db=db,
                trace_id=trace_id,
                organization_id=organization_id,
                agent_type="supervisor_agent",
            )
            try:
                ctx = authorization_service.build_context(db, user_row)
                snapshot_id = authorization_service.capture_snapshot(db, user_row, ctx)
                agent_run.authorization_snapshot_id = snapshot_id
                db.add(agent_run)
                db.commit()
            except Exception:
                db.rollback()
        memory_context = conversation_memory_service.build_agent_context(db, user_id, session_id)
        run_started = time.time()
        master_agent = "supervisor_agent"
        selected_skill = resolve_agent_skill(goal)
        harness_profile = get_harness_profile()
        supervisor_plan = await self._plan_with_supervisor(goal, user_id)
        if forced_worker:
            supervisor_plan.update(
                {
                    "workers": [forced_worker],
                    "dependencies": [],
                    "execution_mode": "sequential",
                    "parallel_plan": None,
                    "plan_source": "a2a_delegation",
                    "fallback_reason": None,
                    "rationale": "A2A 控制面已将任务委派给具备最小权限的指定 Worker。",
                }
            )
        supervisor_plan["harness"] = harness_profile
        supervisor_plan["selected_skill"] = selected_skill
        worker_plan = supervisor_plan["workers"]
        worker_agent = worker_plan[0]
        task_contract = self._build_task_contract(
            agent_run_id=agent_run.id,
            goal=goal,
            receiver=worker_agent,
            supervisor_plan=supervisor_plan,
            max_steps=max_steps,
        )
        supervisor_plan = {
            **supervisor_plan,
            "agent_registry_version": AGENT_REGISTRY_VERSION,
            "harness": harness_profile,
            "selected_skill": selected_skill,
            "task_contract": task_contract,
            "active_task_contract": task_contract,
        }
        await self._emit_event(
            event_callback,
            {
                "type": "run_started",
                "run_id": agent_run.id,
                "goal": goal,
                "status": agent_run.status,
                "master_agent": master_agent,
                "worker_agent": worker_agent,
                "supervisor_plan": supervisor_plan,
                "task_contract": task_contract,
                "created_at": agent_run.created_at.isoformat() if agent_run.created_at else None,
            },
        )
        state = {
            "goal": goal,
            "user_id": user_id,
            "session_id": session_id,
            "memory_context": memory_context,
            "max_steps": max_steps,
            "run_started": run_started,
            "master_agent": master_agent,
            "worker_agent": worker_agent,
            "supervisor_plan": supervisor_plan,
            "task_contract": task_contract,
            "worker_plan": worker_plan,
            "worker_index": 0,
            "handoffs": [],
            "handoff_pending": False,
            "parallel_plan": supervisor_plan.get("parallel_plan"),
            "parallel_pending": bool(supervisor_plan.get("parallel_plan")),
            "parallel_results": {},
            "messages": self._build_worker_messages(
                goal,
                worker_agent,
                user_id,
                memory_context=memory_context,
                task_contract=task_contract,
            ),
            "last_observation": "",
            "step": 0,
            "evidence_scope_seen": False,
            "retry_count": 0,
        }
        # 具类型运行状态（取代无约束 dict 的持久化/恢复载体），并写计划审计。
        # db / agent_run / event_callback / 类型化状态属运行时上下文，不进入图 state，
        # 否则 checkpointer 无法序列化，也就谈不上断点续跑。
        runtime = AgentRuntime(
            db=db,
            agent_run=agent_run,
            user_id=user_id,
            event_callback=event_callback,
            model=AgentRunState(
                run_id=agent_run.id,
                user_id=user_id,
                status=STATUS_RUNNING,
                node="decide",
                step=0,
                trace_id=trace_id,
                organization_id=organization_id,
                plan=AgentPlan.from_dict(supervisor_plan),
                run_deadline_at=agent_run.run_deadline_at.isoformat() if agent_run.run_deadline_at else None,
            ),
        )
        try:
            agent_audit_service.record(
                db=db,
                event_type=EVENT_PLAN_CREATED,
                run_id=agent_run.id,
                trace_id=trace_id,
                user_id=user_id,
                organization_id=organization_id,
                decision={"plan_source": supervisor_plan.get("plan_source"), "workers": worker_plan},
                status="created",
            )
        except Exception:  # noqa: BLE001 - 审计失败不阻断执行
            db.rollback()
        self._save_workflow_snapshot(runtime, state, node="decide")

        try:
            with observe_agent_run(
                run_id=agent_run.id,
                trace_id=trace_id,
                user_id=user_id,
                organization_id=organization_id,
                max_steps=max_steps,
            ):
                await self._workflow.ainvoke(state, self._graph_config(agent_run), context=runtime)
            result_run = runtime.final_run or agent_run
            self._sync_a2a_delegation(db, result_run)
            return result_run
        except Exception as exc:
            safe_error = _sanitize_agent_error_message(str(exc))
            result_run = self._save_run(
                db,
                agent_run,
                status="error",
                error=safe_error,
                failure_reason=safe_error,
                last_observation=state.get("last_observation") or "",
                completed_at=utc_now(),
            )
            # 失败后补偿：反向补偿已完成的可补偿写步骤（不可补偿步骤记录审计）。
            try:
                from app.services.agent.agent_compensation import run_compensation

                run_compensation(db, result_run)
            except Exception:  # noqa: BLE001 - 补偿失败不阻断错误上报
                db.rollback()
            try:
                agent_audit_service.record(
                    db=db,
                    event_type=EVENT_ERROR,
                    run_id=result_run.id,
                    trace_id=result_run.trace_id,
                    user_id=result_run.user_id,
                    organization_id=result_run.organization_id,
                    status="error",
                    summary={"error_category": "unhandled_exception"},
                )
            except Exception:  # noqa: BLE001
                db.rollback()
            self._record_run_summary(
                run=result_run,
                status="error",
                duration_ms=int((time.time() - run_started) * 1000),
                error_message=safe_error,
            )
            await self._emit_event(
                event_callback,
                {
                    "type": "run_failed",
                    "run": self.serialize_run(result_run),
                    "master_agent": master_agent,
                    "worker_agent": worker_agent,
                },
            )
            if getattr(exc, "status_code", None) is not None:
                raise
            self._sync_a2a_delegation(db, result_run)
            return result_run

    def _rebuild_messages_from_logs(
        self,
        *,
        goal: str,
        worker_agent: str,
        user_id: int,
        db: Session,
        session_id: int | None,
        logs: list[ToolCallLog],
        task_contract: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        memory_context = conversation_memory_service.build_agent_context(db, user_id, session_id)
        messages = self._build_worker_messages(
            goal,
            worker_agent,
            user_id,
            memory_context=memory_context,
            task_contract=task_contract,
        )
        for log in logs:
            if log.status == "pending_approval":
                continue
            if not log.raw_decision or not log.observation:
                continue
            if log.tool_name == "finish":
                continue
            self._append_observation(messages, log.raw_decision, log.observation)
        return messages

    async def resume_after_approval(
        self,
        approval_id: int,
        user_id: int,
        db: Session,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> AgentRun:
        approval = agent_approval_service.get_request(db=db, approval_id=approval_id, user_id=user_id)
        if not approval:
            raise ValueError("Approval request not found")
        if approval.status not in {"approved", "executed"}:
            raise ValueError("Approval request is not approved")
        if approval.status == "executed":
            run = self.get_run(approval.agent_run_id or 0, db, user_id=user_id)
            if not run:
                raise ValueError("Agent run not found")
            self._sync_a2a_delegation(db, run)
            return run
        if not approval.agent_run_id:
            raise ValueError("Approval request is not bound to a run")

        agent_run = self.get_run(approval.agent_run_id, db, user_id=user_id)
        if not agent_run:
            raise ValueError("Agent run not found")

        # 审批等待超期守卫：run 截止时间已过则不再执行待审批写工具，
        # 与 decide 节点的 is_expired 收敛保持一致。
        run_deadline = agent_run.run_deadline_at
        if run_deadline is not None:
            if run_deadline.tzinfo is None:
                deadline_naive = run_deadline
            else:
                deadline_naive = run_deadline.astimezone(UTC).replace(tzinfo=None)
            if utc_now() > deadline_naive:
                self._save_run(
                    db,
                    agent_run,
                    status="partial",
                    final_answer="执行已超时，待审批操作未执行，请重新发起。",
                )
                self._sync_a2a_delegation(db, agent_run)
                raise ValueError("Agent run deadline exceeded; pending approval not executed")

        logs = self.get_run_logs(agent_run.id, db, user_id=user_id)
        pending_log = next((item for item in reversed(logs) if item.status == "pending_approval"), None)
        if not pending_log:
            raise ValueError("Pending approval step not found")
        # 审批绑定的参数以该步日志为准（与 require_executable 的摘要比对同源）。
        action_input = _json_loads_dict(pending_log.input_params)
        action_input.pop("_master_agent", None)
        action_input.pop("_worker_agent", None)

        graph_snapshot = self._pending_approval_interrupt(agent_run)
        if graph_snapshot is not None:
            return await self._resume_via_interrupt(
                db=db,
                user_id=user_id,
                agent_run=agent_run,
                approval=approval,
                pending_log=pending_log,
                action_input=action_input,
                snapshot_values=dict(graph_snapshot.values or {}),
                event_callback=event_callback,
            )
        return await self._resume_via_db_snapshot(
            db=db,
            user_id=user_id,
            agent_run=agent_run,
            approval=approval,
            pending_log=pending_log,
            action_input=action_input,
            logs=logs,
            event_callback=event_callback,
        )

    def _pending_approval_interrupt(self, agent_run: AgentRun) -> Any | None:
        """该 Run 的图线程上是否停在审批断点；是则返回 checkpoint 快照。

        返回 None 的情形：回退引擎（无 checkpoint 机制）、checkpoint 已被清理、
        或 Run 发起于本能力上线之前——这些都退回「从 DB 快照重建 state」的恢复路径。
        """
        if not INTERRUPT_AVAILABLE or not hasattr(self._workflow, "get_state"):
            return None
        try:
            snapshot = self._workflow.get_state(self._graph_config(agent_run))
        except Exception:  # noqa: BLE001 - checkpoint 不可读时退回 DB 快照恢复
            return None
        if "awaiting_approval" not in tuple(getattr(snapshot, "next", ()) or ()):
            return None
        return snapshot if getattr(snapshot, "interrupts", ()) else None

    def _claim_approval_for_execution(
        self,
        *,
        db: Session,
        user_id: int,
        agent_run: AgentRun,
        approval: AgentApprovalRequest,
        pending_log: ToolCallLog,
        action_input: dict[str, Any],
        execution_agent: str,
    ) -> None:
        """执行前的两道闸：参数漂移守卫 + approved → executed 的原子认领（CAS）。

        两者都在图外完成。守卫失败时图仍停在断点上，凭新建的审批单可以再次 resume。
        """
        from app.services.agent.agent_approval_service import ApprovalStateError

        try:
            agent_approval_service.require_executable(
                db=db, approval_id=approval.id, user_id=user_id, current_params=action_input
            )
        except ApprovalStateError as exc:
            agent_approval_service.create_request(
                db=db,
                user_id=user_id,
                tool_name=pending_log.tool_name,
                input_params=action_input,
                agent_type=execution_agent,
                agent_run_id=agent_run.id,
                step_id=pending_log.step,
            )
            self._save_run(db, agent_run, status="awaiting_approval", final_answer="审批参数已变化，需重新审批。")
            raise ValueError(f"Approval parameters changed; re-approval required: {exc}") from exc
        if not agent_approval_service.try_claim_execution(db=db, approval_id=approval.id, user_id=user_id):
            self._save_run(db, agent_run, status="running", final_answer="审批已被并发恢复流程执行，本次请求跳过。")
            raise ValueError("Approval already claimed by a concurrent resume")

    def _approval_resume_payload(
        self,
        *,
        approval: AgentApprovalRequest,
        pending_log: ToolCallLog,
        action_input: dict[str, Any],
        execution_agent: str,
        agent_run: AgentRun,
    ) -> dict[str, Any]:
        """送回断点的审批结论：只放可序列化数据，它会作为 resume 值写进 checkpoint。"""
        return {
            "approval_id": approval.id,
            "agent_type": execution_agent,
            "tool_name": pending_log.tool_name,
            "action_input": action_input,
            "raw_decision": pending_log.raw_decision or "",
            "step": pending_log.step or int(agent_run.total_steps or 0),
            "decision_note": approval.decision_note or "审批通过后已恢复执行",
        }

    async def _resume_via_interrupt(
        self,
        *,
        db: Session,
        user_id: int,
        agent_run: AgentRun,
        approval: AgentApprovalRequest,
        pending_log: ToolCallLog,
        action_input: dict[str, Any],
        snapshot_values: dict[str, Any],
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> AgentRun:
        """图原生恢复：``Command(resume=...)`` 把审批结论送回 awaiting_approval 断点。

        messages / worker_plan / handoffs / task_contract / worker_index 等全部由
        checkpoint 还原，这里不再从 DB 重建——只需要重新提供不可序列化的运行时上下文。
        """
        supervisor_plan = snapshot_values.get("supervisor_plan")
        if not isinstance(supervisor_plan, dict):
            supervisor_plan = {}
        execution_agent = canonical_agent_type(
            str(
                approval.agent_type
                or snapshot_values.get("current_worker_agent")
                or snapshot_values.get("worker_agent")
                or "supervisor_agent"
            )
        )
        runtime = AgentRuntime(
            db=db,
            agent_run=agent_run,
            user_id=user_id,
            event_callback=event_callback,
            model=AgentRunState(
                run_id=agent_run.id,
                user_id=user_id,
                status=STATUS_RUNNING,
                node="decide",
                step=int(snapshot_values.get("step") or 0),
                trace_id=agent_run.trace_id,
                organization_id=agent_run.organization_id,
                plan=AgentPlan.from_dict(supervisor_plan),
                run_deadline_at=agent_run.run_deadline_at.isoformat() if agent_run.run_deadline_at else None,
                retry_count=int(snapshot_values.get("retry_count") or 0),
            ),
        )
        await self._emit_event(
            event_callback,
            {
                "type": "run_resumed",
                "run_id": agent_run.id,
                "approval_request_id": approval.id,
                "tool_name": pending_log.tool_name,
            },
        )
        self._save_run(db, agent_run, status="running", final_answer=None, completed_at=None)
        self._claim_approval_for_execution(
            db=db,
            user_id=user_id,
            agent_run=agent_run,
            approval=approval,
            pending_log=pending_log,
            action_input=action_input,
            execution_agent=execution_agent,
        )
        await self._workflow.ainvoke(
            Command(
                resume=self._approval_resume_payload(
                    approval=approval,
                    pending_log=pending_log,
                    action_input=action_input,
                    execution_agent=execution_agent,
                    agent_run=agent_run,
                )
            ),
            self._graph_config(agent_run),
            context=runtime,
        )
        result_run = runtime.final_run or agent_run
        self._sync_a2a_delegation(db, result_run)
        return result_run

    async def _resume_via_db_snapshot(
        self,
        *,
        db: Session,
        user_id: int,
        agent_run: AgentRun,
        approval: AgentApprovalRequest,
        pending_log: ToolCallLog,
        action_input: dict[str, Any],
        logs: list[ToolCallLog],
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> AgentRun:
        """无可用断点时的恢复：从 DB 的 workflow_state 快照重建整份 state。

        回退引擎没有 checkpoint，只能走这条路；真 langgraph 下则是 checkpoint 缺失
        （被清理 / Run 早于该能力上线）时的兜底。
        """
        run_payload = _json_loads_dict(agent_run.result)
        snapshot = self._load_workflow_snapshot(agent_run)
        master_agent = str(run_payload.get("master_agent") or "supervisor_agent")
        supervisor_plan = snapshot.get("supervisor_plan") if isinstance(snapshot.get("supervisor_plan"), dict) else {}
        if not supervisor_plan:
            supervisor_plan = (
                run_payload.get("supervisor_plan") if isinstance(run_payload.get("supervisor_plan"), dict) else {}
            )
        worker_plan = supervisor_plan.get("workers") if isinstance(supervisor_plan.get("workers"), list) else []
        if not worker_plan:
            worker_plan = snapshot.get("worker_plan") if isinstance(snapshot.get("worker_plan"), list) else []
        if not worker_plan:
            worker_plan = self._build_supervisor_worker_plan(agent_run.goal)
        worker_plan = [canonical_agent_type(str(worker)) for worker in worker_plan]
        supervisor_plan["workers"] = worker_plan
        handoffs = snapshot.get("handoffs") if isinstance(snapshot.get("handoffs"), list) else []
        if not handoffs:
            handoffs = supervisor_plan.get("handoffs") if isinstance(supervisor_plan.get("handoffs"), list) else []
        worker_agent = canonical_agent_type(
            str(snapshot.get("worker_agent") or run_payload.get("worker_agent") or worker_plan[-1])
        )
        task_contract = snapshot.get("task_contract") if isinstance(snapshot.get("task_contract"), dict) else {}
        if not task_contract:
            candidate = supervisor_plan.get("active_task_contract") or supervisor_plan.get("task_contract")
            task_contract = candidate if isinstance(candidate, dict) else {}
        if not task_contract:
            task_contract = self._build_task_contract(
                agent_run_id=agent_run.id,
                goal=agent_run.goal,
                receiver=worker_agent,
                supervisor_plan=supervisor_plan,
                max_steps=max(int(agent_run.total_steps or 0) + 1, 5),
            )
        worker_index = int(snapshot.get("worker_index")) if isinstance(snapshot.get("worker_index"), int) else -1
        if worker_index < 0 or worker_index >= len(worker_plan):
            worker_index = worker_plan.index(worker_agent) if worker_agent in worker_plan else len(worker_plan) - 1
        max_steps = int(run_payload.get("max_steps") or max(int(agent_run.total_steps or 0) + 1, 5))

        resume_step = pending_log.step or int(agent_run.total_steps or 0)
        resume_retry_count = int(snapshot.get("retry_count") or 0)
        # 运行时上下文先于工具执行建立：取消检查与图执行共用同一份活对象。
        runtime = AgentRuntime(
            db=db,
            agent_run=agent_run,
            user_id=user_id,
            event_callback=event_callback,
            model=AgentRunState(
                run_id=agent_run.id,
                user_id=user_id,
                status=STATUS_RUNNING,
                node="decide",
                step=resume_step,
                trace_id=agent_run.trace_id,
                organization_id=agent_run.organization_id,
                plan=AgentPlan.from_dict(supervisor_plan),
                run_deadline_at=agent_run.run_deadline_at.isoformat() if agent_run.run_deadline_at else None,
                retry_count=resume_retry_count,
            ),
        )

        await self._emit_event(
            event_callback,
            {
                "type": "run_resumed",
                "run_id": agent_run.id,
                "approval_request_id": approval.id,
                "tool_name": pending_log.tool_name,
            },
        )

        run_started = time.time()
        self._save_run(
            db,
            agent_run,
            status="running",
            final_answer=None,
            completed_at=None,
        )
        execution_agent = canonical_agent_type(approval.agent_type or worker_agent)
        self._claim_approval_for_execution(
            db=db,
            user_id=user_id,
            agent_run=agent_run,
            approval=approval,
            pending_log=pending_log,
            action_input=action_input,
            execution_agent=execution_agent,
        )

        messages = self._rebuild_messages_from_logs(
            goal=agent_run.goal,
            worker_agent=worker_agent,
            user_id=user_id,
            db=db,
            session_id=agent_run.session_id,
            logs=logs,
            task_contract=task_contract,
        )
        memory_context = conversation_memory_service.build_agent_context(db, user_id, agent_run.session_id)
        state: dict[str, Any] = {
            "goal": agent_run.goal,
            "user_id": user_id,
            "session_id": agent_run.session_id,
            "memory_context": memory_context,
            "max_steps": max_steps,
            "run_started": run_started,
            "master_agent": master_agent,
            "worker_agent": worker_agent,
            "supervisor_plan": supervisor_plan,
            "task_contract": task_contract,
            "messages": messages,
            "last_observation": "",
            "step": resume_step,
            "evidence_scope_seen": self._has_evidence_source_logs(logs),
            "worker_plan": worker_plan,
            "worker_index": worker_index,
            "handoffs": handoffs,
            "handoff_pending": False,
            "parallel_plan": snapshot.get("parallel_plan") or supervisor_plan.get("parallel_plan"),
            "parallel_pending": False,
            "parallel_results": snapshot.get("parallel_results") or {},
            "retry_count": resume_retry_count,
            # 待执行的这一步：与图原生断点恢复时 checkpoint 里的通道保持同名同义。
            "current_decision": _normalize_decision(pending_log.raw_decision or ""),
            "current_raw": pending_log.raw_decision or "",
            "current_tool_name": pending_log.tool_name,
            "current_safe_input": action_input,
            "current_worker_agent": execution_agent,
            "awaiting_approval": True,
            "pending_approval_request_id": approval.id,
        }
        # 与图原生断点共用同一段执行逻辑：已获批写工具只有一处执行入口。
        state = await self._workflow_execute_approved_tool(
            {**state, RUNTIME_CONTEXT_KEY: runtime},
            self._approval_resume_payload(
                approval=approval,
                pending_log=pending_log,
                action_input=action_input,
                execution_agent=execution_agent,
                agent_run=agent_run,
            ),
        )
        state.pop(RUNTIME_CONTEXT_KEY, None)
        if int(state.get("step") or 0) >= max_steps:
            await self._workflow_partial({**state, RUNTIME_CONTEXT_KEY: runtime})
            result_run = runtime.final_run or agent_run
            self._sync_a2a_delegation(db, result_run)
            return result_run
        await self._workflow.ainvoke(state, self._graph_config(agent_run), context=runtime)
        result_run = runtime.final_run or agent_run
        self._sync_a2a_delegation(db, result_run)
        return result_run


agent_service = AgentService()
