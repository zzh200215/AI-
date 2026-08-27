from typing import TYPE_CHECKING, Any

from app.mcp.permissions import (
    agent_allows_tool,
    canonical_agent_type,
)
from app.models.agent import AgentRun
from app.services.agent.agent_planner import Planner
from app.services.agent.agent_registry import TASK_PROTOCOL_VERSION

if TYPE_CHECKING:
    from app.services.agent.agent_runtime import AgentRuntime


class SupervisorPlanningMixin:
    @staticmethod
    def _is_cancel_requested(runtime: "AgentRuntime") -> bool:
        run_id = runtime.agent_run.id
        status = runtime.db.query(AgentRun.status).filter(AgentRun.id == run_id).scalar()
        return status == "cancelling"

    def _select_worker_agent(self, goal: str) -> str:
        return self._planner.plan_worker(goal)

    def _build_supervisor_worker_plan(self, goal: str) -> list[str]:
        """Return the ordered Worker handoff plan for cross-domain goals."""
        from app.services.agent.agent_planner import build_worker_plan

        return build_worker_plan(goal)

    @staticmethod
    def _can_parallelize_workers(workers: list[str]) -> bool:
        return Planner._can_parallelize(workers)

    def _parallel_worker_plan(self, goal: str, workers: list[str]) -> dict[str, Any] | None:
        """Build bounded, explicit fan-out steps without asking the model to infer IDs."""
        return self._planner._parallel_plan(goal, workers)

    def _fallback_supervisor_plan(self, goal: str, *, reason: str | None = None) -> dict[str, Any]:
        return self._planner._fallback_dict(goal, reason=reason)

    def _validate_supervisor_plan(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        return self._planner._validate_dict(payload)

    async def _plan_with_supervisor(self, goal: str, user_id: int) -> dict[str, Any]:
        """兼容层：委托 Planner 生成 supervisor_plan dict（形状与历史一致）。"""
        return await self._planner.plan_dict(goal, user_id)

    def _worker_allows_tool(self, worker_name: str, tool_name: str) -> bool:
        """Delegate to the MCP permissions module."""
        return agent_allows_tool(worker_name, tool_name)

    @staticmethod
    def _build_task_contract(
        *,
        agent_run_id: int,
        goal: str,
        receiver: str,
        supervisor_plan: dict[str, Any],
        max_steps: int,
        sender: str = "supervisor_agent",
        parent_task_id: str | None = None,
        sequence: int = 0,
    ) -> dict[str, Any]:
        """Create the structured task envelope exchanged between roles.

        The envelope carries only execution constraints and references. Access
        control remains server-enforced and is never delegated to the model.
        """
        root_task_id = f"agent_run_{agent_run_id}"
        task_id = root_task_id if sequence == 0 else f"{root_task_id}.step_{sequence}"
        expected_artifacts = supervisor_plan.get("expected_artifacts")
        return {
            "protocol_version": TASK_PROTOCOL_VERSION,
            "task_id": task_id,
            "parent_task_id": parent_task_id,
            "sender": sender,
            "receiver": canonical_agent_type(receiver),
            "task_type": str(supervisor_plan.get("intent") or "legal_request"),
            "input": {"goal": goal},
            "constraints": {"language": "zh-CN", "max_steps": max_steps},
            "expected_output": {
                "artifacts": expected_artifacts if isinstance(expected_artifacts, list) else [],
                "format": "structured_result",
            },
            "skill": supervisor_plan.get("selected_skill") if isinstance(supervisor_plan.get("selected_skill"), dict) else None,
            "authorization": "server_enforced_rbac_acl_approval",
            "status": "assigned",
        }
