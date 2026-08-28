"""#94/Agent workflow 节点层（langgraph 节点 + 路由决策）

从 agent_service.py 拆出（E-4），MRO 经 AgentService 访问执行/生命周期辅助方法。

状态与运行时分离：节点只读写 ``AgentGraphState`` 的可序列化通道，Session / ORM /
事件回调等活对象一律经 ``resolve_runtime(state)`` 从 LangGraph runtime context 取用。
"""
import json
import time
from typing import Any

from app.core.time import utc_now
from app.mcp.permissions import canonical_agent_type
from app.services.agent.agent_approval_service import agent_approval_service
from app.services.agent.agent_json import json_dumps as _json_dumps
from app.services.agent.agent_prompts import (
    EVIDENCE_GATED_WRITE_TOOLS,
    EVIDENCE_SOURCE_TOOLS,
    POLICY_GUARDRAIL_ROLE,
    normalize_decision as _normalize_decision,
    sanitize_agent_error_message as _sanitize_agent_error_message,
)
from app.services.agent.agent_run_state import AgentRunState
from app.services.agent.agent_runtime import resolve_runtime
from app.workflows.langgraph_compat import INTERRUPT_AVAILABLE, interrupt

# 连续非法决策（retry）上限：达到后强制收敛为 finish，避免挤占受限的步骤预算。
MAX_CONSECUTIVE_RETRIES = 3


class AgentWorkflowNodesMixin:
    def _workflow_route_decision(self, state: dict[str, Any]) -> str:
        if state.get("timed_out"):
            return "partial"
        if state.get("needs_evidence_verification"):
            return "verify_evidence"
        return str((state.get("current_decision") or {}).get("action_type") or "retry")

    def _workflow_route_after_parallel_fanout(self, state: dict[str, Any]) -> str:
        return "verify_evidence" if state.get("needs_evidence_verification") else "parallel_aggregate"

    def _workflow_route_after_evidence_verification(self, state: dict[str, Any]) -> str:
        if not (state.get("evidence_verification") or {}).get("passed", True):
            return "evidence_insufficient"
        return str(state.get("verification_target") or "finish")

    def _workflow_route_after_finish(self, state: dict[str, Any]) -> str:
        return "handoff" if state.get("handoff_pending") else "complete"

    def _workflow_route_continue(self, state: dict[str, Any]) -> str:
        if state.get("awaiting_approval"):
            return "awaiting_approval"
        return "partial" if int(state.get("step") or 0) >= int(state.get("max_steps") or 0) else "continue"

    async def _workflow_decide(self, state: dict[str, Any]) -> dict[str, Any]:
        runtime = resolve_runtime(state)
        # run 级截止时间：在步骤边界检查，超时直接收敛为 partial（timeout）。
        model = runtime.model
        if model is not None and model.is_expired():
            state.update(
                {
                    "current_decision": {"action_type": "timeout", "thought": "[supervisor_agent] 执行已超时。"},
                    "current_raw": "run_deadline_exceeded",
                    "timed_out": True,
                }
            )
            return state
        if self._is_cancel_requested(runtime):
            state.update({"current_decision": {"action_type": "cancelled"}, "current_raw": "cancel_requested"})
            return state
        if state.get("parallel_pending"):
            state.update(
                {
                    "parallel_pending": False,
                    "current_decision": {"action_type": "parallel_fanout", "thought": "[supervisor_agent] 启动只读 Worker 并行分支。"},
                    "current_tool_name": "parallel_fanout",
                    "current_raw": "parallel_read_only",
                }
            )
            self._save_workflow_snapshot(runtime, state, node="parallel_fanout")
            return state
        step = int(state.get("step") or 0) + 1
        started = time.time()
        raw = await self._chat(state["messages"], state["user_id"])
        decision = _normalize_decision(raw)
        action_type = decision["action_type"]
        tool_name = decision["tool_name"] or action_type
        safe_input = decision["action_input"] if isinstance(decision["action_input"], dict) else {}
        step_worker_agent = canonical_agent_type(state["worker_agent"])
        if action_type == "tool_call" and not self._worker_allows_tool(step_worker_agent, tool_name):
            requested_tool = tool_name
            worker_plan = [canonical_agent_type(worker) for worker in (state.get("worker_plan") or [])]
            current_index = int(state.get("worker_index") or 0)
            next_authorized_worker = next(
                (
                    worker
                    for worker in worker_plan[current_index + 1 :]
                    if self._worker_allows_tool(worker, requested_tool)
                ),
                None,
            )
            if next_authorized_worker:
                # A downstream Worker owns the requested capability. Close the
                # current role and use the normal Supervisor handoff path so
                # the next role receives only structured upstream context.
                action_type = "finish"
                tool_name = "finish"
                safe_input = {}
                # 记录实际交接目标：计划顺序的下一个 Worker 可能并不持有该能力，
                # _workflow_finish 需按此目标（而非 index+1）交接。
                state["handoff_target_worker"] = next_authorized_worker
                decision.update(
                    {
                        "action_type": "finish",
                        "tool_name": "",
                        "answer": f"当前职责已完成，交由 {next_authorized_worker} 继续处理。",
                        "parse_error": None,
                        "thought": (
                            f"当前 {step_worker_agent} 无权调用 {requested_tool}；"
                            f"Supervisor 将按已批准计划交接给 {next_authorized_worker}。"
                        ),
                    }
                )
            else:
                action_type = "retry"
                tool_name = "retry"
                decision.update(
                    {
                        "action_type": "retry",
                        "tool_name": "",
                        "parse_error": f"role_boundary_violation:{step_worker_agent}:{requested_tool}",
                        "thought": (
                            f"当前 {step_worker_agent} 无权调用 {requested_tool}；"
                            "跨角色操作必须由 Supervisor 显式交接。"
                        ),
                    }
                )
        if action_type == "retry" and int(state.get("retry_count") or 0) >= MAX_CONSECUTIVE_RETRIES:
            # 连续非法决策达到上限：强制收敛为 finish，避免重试无限挤占步骤预算。
            action_type = "finish"
            tool_name = "finish"
            safe_input = {}
            decision.update(
                {
                    "action_type": "finish",
                    "tool_name": "",
                    "answer": (
                        f"Agent 连续 {MAX_CONSECUTIVE_RETRIES} 次输出非法决策，已停止继续执行，"
                        "请检查输入或降低任务复杂度后重新发起。"
                    ),
                    "parse_error": None,
                    "thought": f"连续非法决策达到上限（{MAX_CONSECUTIVE_RETRIES}），强制收敛。",
                }
            )
        has_evidence_context = bool(state.get("evidence_scope_seen"))
        requires_verification = (
            (action_type == "finish" and has_evidence_context)
            or (action_type == "tool_call" and tool_name in EVIDENCE_GATED_WRITE_TOOLS and has_evidence_context)
        )
        thought = decision.get("thought")
        decision["thought"] = (
            f"[{state['master_agent']} -> {step_worker_agent}] {thought}"
            if thought
            else f"[{state['master_agent']} -> {step_worker_agent}]"
        )
        await self._emit_event(
            runtime.event_callback,
            {
                "type": "step_started",
                "run_id": runtime.agent_run.id,
                "step": step,
                "action_type": action_type,
                "tool_name": tool_name,
                "thought": decision.get("thought"),
                "input_params": safe_input,
                "master_agent": state["master_agent"],
                "worker_agent": step_worker_agent,
            },
        )
        state.update(
            {
                "step": step,
                "step_started_at": started,
                "current_raw": raw,
                "current_decision": decision,
                "current_action_type": action_type,
                "current_tool_name": tool_name,
                "current_safe_input": safe_input,
                "current_worker_agent": step_worker_agent,
                "needs_evidence_verification": requires_verification,
                "verification_target": action_type,
            }
        )
        return state

    async def _workflow_cancelled(self, state: dict[str, Any]) -> dict[str, Any]:
        runtime = resolve_runtime(state)
        answer = "执行已取消，后续步骤未继续运行。"
        model = runtime.model
        if isinstance(model, AgentRunState):
            model.cancel_requested = True
        log = self._create_log(
            db=runtime.db, agent_run_id=runtime.agent_run.id, step=int(state.get("step") or 0),
            decision={"action_type": "cancelled", "thought": "[supervisor_agent] 检测到取消请求。"}, raw_decision=state.get("current_raw") or "cancel_requested",
            tool_name="run_cancelled", input_params={}, observation=_json_dumps({"success": False, "message": answer}),
            output_result=answer, status="cancelled", error="cancelled_by_user", duration_ms=0,
        )
        run = self._save_run(
            runtime.db, runtime.agent_run, status="cancelled", final_answer=answer, failure_reason="cancelled_by_user",
            total_steps=int(state.get("step") or 0), completed_at=utc_now(),
        )
        await self._emit_event(runtime.event_callback, {"type": "run_completed", "run": self.serialize_run(run), "master_agent": state["master_agent"], "worker_agent": state.get("worker_agent")})
        runtime.final_run = run
        return state

    async def _workflow_parallel_fanout(self, state: dict[str, Any]) -> dict[str, Any]:
        runtime = resolve_runtime(state)
        started = time.time()
        branches = await self._run_parallel_read_only(runtime, state)
        branch_logs = []
        for index, branch in enumerate(branches.values(), start=1):
            observation = _json_dumps(
                {
                    "success": branch["success"],
                    "message": "并行只读分支完成" if branch["success"] else "并行只读分支失败",
                    "data": branch["data"],
                    "error": branch.get("error"),
                }
            )
            log = self._create_log(
                db=runtime.db,
                agent_run_id=runtime.agent_run.id,
                step=int(state.get("step") or 0) + index,
                decision={"action_type": "parallel_tool_call", "thought": f"[supervisor_agent -> {branch['worker_agent']}] 并行只读分支"},
                raw_decision="parallel_read_only",
                tool_name=branch["tool_name"],
                input_params={**branch.get("action_input", {}), "_worker_agent": branch["worker_agent"], "_parallel_branch": True},
                observation=observation,
                output_result=observation,
                status="success" if branch["success"] else "error",
                error=branch.get("error"),
                duration_ms=branch["duration_ms"],
            )
            branch_logs.append(self.serialize_log(log))
            await self._emit_event(
                runtime.event_callback,
                {"type": "step_completed", "run_id": runtime.agent_run.id, "log": self.serialize_log(log), "master_agent": state["master_agent"], "worker_agent": branch["worker_agent"]},
            )
        fanout_observation = _json_dumps(
            {"success": all(item["success"] for item in branches.values()), "data": {"branches": branches, "execution_mode": "parallel_read_only"}}
        )
        fanout_log = self._create_log(
            db=runtime.db, agent_run_id=runtime.agent_run.id, step=int(state.get("step") or 0) + len(branches) + 1,
            decision={"action_type": "parallel_fanout", "thought": "[supervisor_agent] 并行只读 Worker 已启动。"}, raw_decision="parallel_read_only",
            tool_name="supervisor_parallel_fanout", input_params={"workers": list(branches)}, observation=fanout_observation,
            output_result=fanout_observation, status="success" if all(item["success"] for item in branches.values()) else "error",
            error=None, duration_ms=int((time.time() - started) * 1000),
        )
        state.update(
            {
                "parallel_results": branches,
                "parallel_branch_logs": branch_logs,
                "step": int(state.get("step") or 0) + len(branches) + 1,
                "last_observation": fanout_observation,
                "evidence_scope_seen": any(item["success"] and item["tool_name"] in EVIDENCE_SOURCE_TOOLS for item in branches.values()),
                "needs_evidence_verification": any(item["success"] and item["tool_name"] in EVIDENCE_SOURCE_TOOLS for item in branches.values()),
                "verification_target": "parallel_aggregate",
            }
        )
        self._save_workflow_snapshot(runtime, state, node="parallel_fanout")
        return state

    async def _workflow_parallel_aggregate(self, state: dict[str, Any]) -> dict[str, Any]:
        runtime = resolve_runtime(state)
        branches = state.get("parallel_results") or {}
        completed = [item for item in branches.values() if item.get("success")]
        failed = [item for item in branches.values() if not item.get("success")]
        findings: list[dict[str, Any]] = []
        for branch in completed:
            data = branch.get("data") or {}
            for claim_type in ("risks", "decisions", "action_items"):
                for item in data.get(claim_type) or []:
                    if not isinstance(item, dict):
                        continue
                    findings.append({
                        "worker_agent": branch["worker_agent"],
                        "type": claim_type,
                        "title": item.get("title") or item.get("task") or "待确认事项",
                        "evidence": item.get("evidence") or item.get("source_text"),
                        "severity": item.get("severity"),
                    })
        conclusion = f"已并行完成 {len(completed)} 个只读 Worker 的结果汇聚"
        answer_lines = [conclusion]
        if findings:
            answer_lines.append("关键发现：" + "；".join(str(item["title"]) for item in findings[:5]))
        answer_lines.append("建议动作：核对每项原文证据后，再通过确认流程创建风险任务。")
        answer = "\n".join(answer_lines)
        if failed:
            answer += f"\n其中 {len(failed)} 个分支失败，请查看执行日志。"
        else:
            answer += "\n结果均已保留来源与执行记录。"
        aggregation = {
            "execution_mode": "parallel_read_only",
            "conclusion": conclusion,
            "findings": findings[:12],
            "recommended_actions": ["核对原文证据后确认最终业务口径。", "需要推进时通过确认流程创建风险任务。"],
            "completed_workers": [item["worker_agent"] for item in completed],
            "failed_workers": [item["worker_agent"] for item in failed],
            "branches": branches,
        }
        state["supervisor_plan"] = {**state.get("supervisor_plan", {}), "aggregation": aggregation}
        observation = _json_dumps({"success": not failed, "data": aggregation})
        log = self._create_log(
            db=runtime.db, agent_run_id=runtime.agent_run.id, step=int(state.get("step") or 0) + 1,
            decision={"action_type": "aggregate", "thought": "[supervisor_agent] 汇聚并行只读 Worker 输出。"}, raw_decision="parallel_read_only",
            tool_name="supervisor_aggregate", input_params={"workers": list(branches)}, observation=observation,
            output_result=answer, status="success" if not failed else "partial", error=None, duration_ms=0,
        )
        await self._emit_event(runtime.event_callback, {"type": "step_completed", "run_id": runtime.agent_run.id, "log": self.serialize_log(log), "master_agent": state["master_agent"], "worker_agent": "supervisor_agent"})
        state["last_observation"] = observation
        self._save_workflow_snapshot(runtime, state, node="parallel_aggregate")
        result_run = self._finalize_completed_run(
            db=runtime.db, agent_run=runtime.agent_run, final_answer=answer, last_observation=observation,
            failure_reason=None if not failed else "parallel_branch_failed", total_steps=int(state.get("step") or 0) + 1,
            master_agent=state["master_agent"], worker_agent="supervisor_agent", run_started=state["run_started"],
            summary_status="success" if not failed else "partial", error_message=None if not failed else "parallel_branch_failed",
            worker_plan=state.get("worker_plan"), handoffs=state.get("handoffs"), supervisor_plan_details=state.get("supervisor_plan"),
        )
        await self._emit_event(runtime.event_callback, {"type": "run_completed", "run": self.serialize_run(result_run), "master_agent": state["master_agent"], "worker_agent": "supervisor_agent"})
        runtime.final_run = result_run
        return state

    async def _workflow_verify_evidence(self, state: dict[str, Any]) -> dict[str, Any]:
        runtime = resolve_runtime(state)
        logs = self.get_run_logs(runtime.agent_run.id, runtime.db, user_id=state["user_id"])
        verification = self._verify_evidence(logs)
        observation = _json_dumps(
            {
                "success": verification["passed"],
                "message": "证据核验通过" if verification["passed"] else "证据核验未通过",
                "data": verification,
            }
        )
        verifier_decision = {
            "action_type": "verify",
            "thought": f"[{state['master_agent']} -> {POLICY_GUARDRAIL_ROLE}] 核验结构化结论的原文依据。",
        }
        log = self._create_log(
            db=runtime.db,
            agent_run_id=runtime.agent_run.id,
            step=state["step"],
            decision=verifier_decision,
            raw_decision=state["current_raw"],
            tool_name="evidence_verifier",
            input_params={"verification_target": state.get("verification_target")},
            observation=observation,
            output_result=observation,
            status="success" if verification["passed"] else "error",
            error=None if verification["passed"] else "Evidence verification failed",
            duration_ms=0,
        )
        await self._emit_event(
            runtime.event_callback,
            {
                "type": "step_completed",
                "run_id": runtime.agent_run.id,
                "log": self.serialize_log(log),
                "master_agent": state["master_agent"],
                "worker_agent": POLICY_GUARDRAIL_ROLE,
            },
        )
        state["evidence_verification"] = verification
        self._save_workflow_snapshot(runtime, state, node="verify_evidence")
        return state

    async def _workflow_evidence_insufficient(self, state: dict[str, Any]) -> dict[str, Any]:
        runtime = resolve_runtime(state)
        verification = state.get("evidence_verification") or {}
        failed_claims = int(verification.get("failed_claims") or 0)
        answer = f"任务未继续执行：发现 {failed_claims} 条结论缺少原文证据，请补充资料或重新分析。"
        self._save_workflow_snapshot(runtime, state, node="evidence_insufficient")
        result_run = self._finalize_completed_run(
            db=runtime.db,
            agent_run=runtime.agent_run,
            final_answer=answer,
            last_observation=state["last_observation"],
            failure_reason="evidence_verification_failed",
            total_steps=state["step"],
            master_agent=state["master_agent"],
            worker_agent=state["worker_agent"],
            run_started=state["run_started"],
            summary_status="partial",
            error_message="evidence_verification_failed",
            worker_plan=state.get("worker_plan"),
            handoffs=state.get("handoffs"),
            supervisor_plan_details=state.get("supervisor_plan"),
        )
        await self._emit_event(
            runtime.event_callback,
            {
                "type": "run_completed",
                "run": self.serialize_run(result_run),
                "master_agent": state["master_agent"],
                "worker_agent": POLICY_GUARDRAIL_ROLE,
            },
        )
        runtime.final_run = result_run
        return state

    async def _workflow_finish(self, state: dict[str, Any]) -> dict[str, Any]:
        runtime = resolve_runtime(state)
        decision = state["current_decision"]
        answer = decision["answer"] or "任务已完成。"
        duration_ms = int((time.time() - state["step_started_at"]) * 1000)
        log = self._create_log(
            db=runtime.db,
            agent_run_id=runtime.agent_run.id,
            step=state["step"],
            decision=decision,
            raw_decision=state["current_raw"],
            tool_name="finish",
            input_params={},
            observation=state["last_observation"],
            output_result=answer,
            status="success",
            error=None,
            duration_ms=duration_ms,
        )
        await self._emit_event(
            runtime.event_callback,
            {
                "type": "step_completed",
                "run_id": runtime.agent_run.id,
                "log": self.serialize_log(log),
            },
        )
        next_worker_index = int(state.get("worker_index") or 0) + 1
        worker_plan = state.get("worker_plan") or [state["worker_agent"]]
        # 角色边界触发的交接以持有目标能力的 Worker 为准（decide 阶段写入），
        # 避免按计划顺序交接给无权 Worker 造成多余跳转。
        target_worker = state.get("handoff_target_worker")
        if target_worker:
            canonical_target = canonical_agent_type(target_worker)
            target_indexes = [
                i for i, worker in enumerate(worker_plan)
                if canonical_agent_type(worker) == canonical_target
            ]
            if target_indexes:
                next_worker_index = target_indexes[0]
            state["handoff_target_worker"] = None  # 一次性生效
        if next_worker_index < len(worker_plan):
            next_worker = worker_plan[next_worker_index]
            handoff_context = self._build_handoff_context(
                self.get_run_logs(runtime.agent_run.id, runtime.db, user_id=state["user_id"]),
                state["worker_agent"],
                answer,
            )
            next_task_contract = self._build_task_contract(
                agent_run_id=runtime.agent_run.id,
                goal=state["goal"],
                receiver=next_worker,
                supervisor_plan=state["supervisor_plan"],
                max_steps=state["max_steps"],
                sender=state["worker_agent"],
                parent_task_id=str((state.get("task_contract") or {}).get("task_id") or "") or None,
                sequence=next_worker_index,
            )
            handoff = {
                "from_worker": state["worker_agent"],
                "to_worker": next_worker,
                "completion_summary": answer,
                "step": state["step"],
                "task_contract": next_task_contract,
            }
            handoffs = list(state.get("handoffs") or [])
            handoffs.append(handoff)
            handoff_log = self._create_log(
                db=runtime.db,
                agent_run_id=runtime.agent_run.id,
                step=state["step"],
                decision={"action_type": "handoff", "thought": f"[{state['master_agent']}] Worker 交接"},
                raw_decision=state["current_raw"],
                tool_name="supervisor_handoff",
                input_params={"from_worker": state["worker_agent"], "to_worker": next_worker},
                observation=_json_dumps({"success": True, "data": handoff_context}),
                output_result="worker_handoff",
                status="success",
                error=None,
                duration_ms=0,
            )
            await self._emit_event(
                runtime.event_callback,
                {
                    "type": "worker_handoff",
                    "run_id": runtime.agent_run.id,
                    "log": self.serialize_log(handoff_log),
                    "from_worker": state["worker_agent"],
                    "to_worker": next_worker,
                },
            )
            state["supervisor_plan"] = {
                **state["supervisor_plan"],
                "active_task_contract": next_task_contract,
            }
            state.update(
                {
                    "worker_agent": next_worker,
                    "worker_index": next_worker_index,
                    "messages": self._build_worker_messages(
                        state["goal"],
                        next_worker,
                        state["user_id"],
                        handoff_context=handoff_context,
                        memory_context=state.get("memory_context") or "",
                        task_contract=next_task_contract,
                    ),
                    "task_contract": next_task_contract,
                    "handoffs": handoffs,
                    "handoff_pending": True,
                    "needs_evidence_verification": False,
                }
            )
            self._save_workflow_snapshot(runtime, state, node="decide")
            return state
        self._save_workflow_snapshot(runtime, state, node="completed")
        result_run = self._finalize_completed_run(
            db=runtime.db,
            agent_run=runtime.agent_run,
            final_answer=answer,
            last_observation=state["last_observation"],
            failure_reason=None,
            total_steps=state["step"],
            master_agent=state["master_agent"],
            worker_agent=state["worker_agent"],
            run_started=state["run_started"],
            summary_status="success",
            worker_plan=worker_plan,
            handoffs=state.get("handoffs"),
            supervisor_plan_details=state.get("supervisor_plan"),
        )
        await self._emit_event(
            runtime.event_callback,
            {
                "type": "run_completed",
                "run": self.serialize_run(result_run),
                "master_agent": state["master_agent"],
                "worker_agent": state["worker_agent"],
            },
        )
        runtime.final_run = result_run
        state["handoff_pending"] = False
        return state

    async def _workflow_retry(self, state: dict[str, Any]) -> dict[str, Any]:
        runtime = resolve_runtime(state)
        decision = state["current_decision"]
        error_message = decision.get("parse_error") or "Agent 决策要求重试"
        observation = _json_dumps(
            {
                "success": False,
                "message": "请修正输出后继续",
                "data": {
                    "master_agent": state["master_agent"],
                    "worker_agent": state["current_worker_agent"],
                },
                "error": error_message,
            }
        )
        duration_ms = int((time.time() - state["step_started_at"]) * 1000)
        log = self._create_log(
            db=runtime.db,
            agent_run_id=runtime.agent_run.id,
            step=state["step"],
            decision=decision,
            raw_decision=state["current_raw"],
            tool_name="retry",
            input_params={
                **state["current_safe_input"],
                "_master_agent": state["master_agent"],
                "_worker_agent": state["current_worker_agent"],
            },
            observation=observation,
            output_result=observation,
            status="error",
            error=error_message,
            duration_ms=duration_ms,
        )
        await self._emit_event(
            runtime.event_callback,
            {
                "type": "step_completed",
                "run_id": runtime.agent_run.id,
                "log": self.serialize_log(log),
                "master_agent": state["master_agent"],
                "worker_agent": state["current_worker_agent"],
            },
        )
        state["last_observation"] = observation
        state["retry_count"] = int(state.get("retry_count") or 0) + 1
        model = runtime.model
        if isinstance(model, AgentRunState):
            model.retry_count = state["retry_count"]
        self._save_workflow_snapshot(
            runtime,
            state,
            node="decide",
            last_observation=observation,
            failure_reason=_sanitize_agent_error_message(error_message),
            total_steps=state["step"],
        )
        self._append_observation(state["messages"], state["current_raw"], observation)
        return state

    async def _workflow_tool_call(self, state: dict[str, Any]) -> dict[str, Any]:
        runtime = resolve_runtime(state)
        result, serialized_input = await self._execute_tool(
            state["current_tool_name"],
            state["current_safe_input"],
            state["user_id"],
            runtime.db,
            agent_type=state["current_worker_agent"],
            agent_run_id=runtime.agent_run.id,
            step_id=int(state.get("step") or 0),
            trace_id=runtime.agent_run.trace_id,
            organization_id=runtime.agent_run.organization_id,
            cancel_check=lambda: self._is_cancel_requested(runtime),
        )
        result.setdefault("data", {})
        if isinstance(result["data"], dict):
            result["data"].setdefault("master_agent", state["master_agent"])
            result["data"].setdefault("worker_agent", state["current_worker_agent"])
        observation = _json_dumps(result)
        duration_ms = int((time.time() - state["step_started_at"]) * 1000)
        status = "success" if result.get("success") else "error"
        error = result.get("error")
        logged_input = json.loads(serialized_input) if serialized_input else state["current_safe_input"]
        logged_input["_master_agent"] = state["master_agent"]
        logged_input["_worker_agent"] = state["current_worker_agent"]
        approval_required = (
            result.get("mcp_error_code") == "MCP_APPROVAL_REQUIRED"
            or bool((result.get("data") or {}).get("approval_required"))
        )
        log = self._create_log(
            db=runtime.db,
            agent_run_id=runtime.agent_run.id,
            step=state["step"],
            decision=state["current_decision"],
            raw_decision=state["current_raw"],
            tool_name=state["current_tool_name"],
            input_params=logged_input,
            observation="" if approval_required else observation,
            output_result=observation,
            status="pending_approval" if approval_required else status,
            error=error,
            duration_ms=duration_ms,
        )
        await self._emit_event(
            runtime.event_callback,
            {
                "type": "step_completed",
                "run_id": runtime.agent_run.id,
                "log": self.serialize_log(log),
                "master_agent": state["master_agent"],
                "worker_agent": state["current_worker_agent"],
            },
        )
        if approval_required:
            approval_request_id = (result.get("data") or {}).get("approval_request_id")
            awaiting_run = self._save_workflow_snapshot(
                runtime,
                state,
                node="awaiting_approval",
                status="awaiting_approval",
                result=_json_dumps(
                    self._build_awaiting_approval_payload(
                        agent_run_id=runtime.agent_run.id,
                        db=runtime.db,
                        user_id=state["user_id"],
                        master_agent=state["master_agent"],
                        worker_agent=state["worker_agent"],
                        approval_request_id=int(approval_request_id or 0),
                        tool_name=state["current_tool_name"],
                        max_steps=state["max_steps"],
                        worker_plan=state.get("worker_plan"),
                        handoffs=state.get("handoffs"),
                        supervisor_plan_details=state.get("supervisor_plan"),
                    )
                ),
                final_answer="执行已暂停，等待人工审批。",
                failure_reason=None,
                total_steps=state["step"],
            )
            await self._emit_event(
                runtime.event_callback,
                {
                    "type": "run_waiting_approval",
                    "run": self.serialize_run(awaiting_run),
                    "approval_request_id": approval_request_id,
                    "tool_name": state["current_tool_name"],
                },
            )
            runtime.final_run = awaiting_run
            state["awaiting_approval"] = True
            state["pending_approval_request_id"] = int(approval_request_id) if approval_request_id else None
            return state
        if state["current_tool_name"] in EVIDENCE_SOURCE_TOOLS and result.get("success"):
            state["evidence_scope_seen"] = True
        state["last_observation"] = observation
        self._save_workflow_snapshot(
            runtime,
            state,
            node="decide",
            last_observation=observation,
            failure_reason=_sanitize_agent_error_message(error),
            total_steps=state["step"],
        )
        self._append_observation(state["messages"], state["current_raw"], observation)
        return state

    async def _workflow_awaiting_approval(self, state: dict[str, Any]) -> dict[str, Any]:
        """人在回路断点：图原生 ``interrupt()`` / ``Command(resume=...)``。

        ``interrupt()`` 之前不能有任何副作用——LangGraph 恢复时从节点开头**重跑**整段逻辑
        （见 ``langgraph.types.interrupt`` 文档），前置副作用会在每次 resume 时重复发生。

        回退引擎没有 checkpoint，中断点无处保存，退化为「停在本节点、图正常结束」，
        由 ``resume_after_approval`` 从 DB 快照重建状态后调用下面的执行段。
        """
        if not INTERRUPT_AVAILABLE:
            return state
        approved = interrupt(
            {
                "kind": "tool_approval",
                "approval_request_id": state.get("pending_approval_request_id"),
                "tool_name": state.get("current_tool_name"),
                "step": state.get("step"),
            }
        )
        # —— 以下仅在收到 Command(resume=...) 后执行 ——
        return await self._workflow_execute_approved_tool(state, approved if isinstance(approved, dict) else {})

    async def _workflow_execute_approved_tool(
        self,
        state: dict[str, Any],
        approved: dict[str, Any],
    ) -> dict[str, Any]:
        """执行已获批的写工具，并把该步日志从 pending_approval 收敛为终态。

        图原生恢复与回退引擎恢复共用这一段，写工具的执行路径只有一处。
        参数漂移守卫与 approved→executed 的 CAS 认领在图外（``resume_after_approval``）
        完成：守卫失败时中断点仍在，可凭新审批单再次 resume。
        """
        runtime = resolve_runtime(state)
        db = runtime.db
        user_id = int(state.get("user_id") or runtime.user_id)
        tool_name = str(approved.get("tool_name") or state.get("current_tool_name") or "")
        action_input = approved.get("action_input")
        if not isinstance(action_input, dict):
            action_input = dict(state.get("current_safe_input") or {})
        execution_agent = canonical_agent_type(
            str(approved.get("agent_type") or state.get("current_worker_agent") or state.get("worker_agent") or "")
        )
        approval_id = int(approved.get("approval_id") or state.get("pending_approval_request_id") or 0)
        step = int(approved.get("step") or state.get("step") or 0)

        result, serialized_input = await self._execute_tool(
            tool_name,
            action_input,
            user_id,
            db,
            agent_type=execution_agent,
            agent_run_id=runtime.agent_run.id,
            skip_approval=True,
            step_id=step,
            trace_id=runtime.agent_run.trace_id,
            organization_id=runtime.agent_run.organization_id,
            cancel_check=lambda: self._is_cancel_requested(runtime),
        )
        result.setdefault("data", {})
        if isinstance(result["data"], dict):
            result["data"].setdefault("master_agent", state.get("master_agent"))
            result["data"].setdefault("worker_agent", execution_agent)
        observation = _json_dumps(result)
        status = "success" if result.get("success") else "error"
        error = result.get("error")

        logs = self.get_run_logs(runtime.agent_run.id, db, user_id=user_id)
        pending_log = next((item for item in reversed(logs) if item.status == "pending_approval"), None)
        raw_decision = str(approved.get("raw_decision") or (pending_log.raw_decision if pending_log else "") or "")
        if pending_log is not None:
            self._update_log(db, pending_log, status="approved", error=None, output_result="approval_granted")
        logged_input = json.loads(serialized_input) if serialized_input else dict(action_input)
        logged_input["_master_agent"] = state.get("master_agent")
        logged_input["_worker_agent"] = execution_agent
        new_log = self._create_log(
            db=db,
            agent_run_id=runtime.agent_run.id,
            step=step,
            decision=state.get("current_decision") or _normalize_decision(raw_decision),
            raw_decision=raw_decision,
            tool_name=tool_name,
            input_params=logged_input,
            observation=observation,
            output_result=observation,
            status=status,
            error=error,
            # 审批等待时长不算步骤耗时。
            duration_ms=0,
        )
        await self._emit_event(
            runtime.event_callback,
            {
                "type": "step_completed",
                "run_id": runtime.agent_run.id,
                "log": self.serialize_log(new_log),
                "master_agent": state.get("master_agent"),
                "worker_agent": execution_agent,
            },
        )
        if approval_id:
            agent_approval_service.mark_executed(
                db=db,
                approval_id=approval_id,
                user_id=user_id,
                decision_note=str(approved.get("decision_note") or "审批通过后已恢复执行"),
            )

        state["awaiting_approval"] = False
        state["pending_approval_request_id"] = None
        state["last_observation"] = observation
        if tool_name in EVIDENCE_SOURCE_TOOLS and result.get("success"):
            state["evidence_scope_seen"] = True
        self._save_workflow_snapshot(
            runtime,
            state,
            node="decide",
            last_observation=observation,
            failure_reason=_sanitize_agent_error_message(error),
            total_steps=step,
        )
        self._append_observation(state["messages"], raw_decision, observation)
        return state

    async def _workflow_partial(self, state: dict[str, Any]) -> dict[str, Any]:
        runtime = resolve_runtime(state)
        if state.get("timed_out"):
            partial_answer = "执行已超时，任务未完成。"
            failure_reason = "run_timeout"
        else:
            partial_answer = "已达到最大执行步数，任务部分完成。"
            failure_reason = runtime.agent_run.failure_reason or "max_steps_reached"
        self._save_workflow_snapshot(runtime, state, node="partial")
        result_run = self._finalize_completed_run(
            db=runtime.db,
            agent_run=runtime.agent_run,
            final_answer=partial_answer,
            last_observation=state["last_observation"],
            failure_reason=failure_reason,
            total_steps=state["max_steps"],
            master_agent=state["master_agent"],
            worker_agent=state["worker_agent"],
            run_started=state["run_started"],
            summary_status="partial",
            error_message=failure_reason,
            worker_plan=state.get("worker_plan"),
            handoffs=state.get("handoffs"),
            supervisor_plan_details=state.get("supervisor_plan"),
        )
        await self._emit_event(
            runtime.event_callback,
            {
                "type": "run_completed",
                "run": self.serialize_run(result_run),
                "master_agent": state["master_agent"],
                "worker_agent": state["worker_agent"],
            },
        )
        runtime.final_run = result_run
        return state

