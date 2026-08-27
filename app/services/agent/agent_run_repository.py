"""RunStateRepository：Agent Run / 步骤 / 工具调用 / 工作流快照的唯一持久化入口。

- workflow node 不得散落直接写数据库；所有读写经本仓库。
- ``workflow_state`` 兼容既有 snapshot（version 3 结构），并额外嵌入具类型
  ``AgentRunState.snapshot()`` 于 ``model`` 键，支持按 run_id 恢复执行。
- 幂等恢复：已成功步骤不重复执行（由上层依据 logs / snapshot 判定）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.agent import AgentRun, ToolCallLog
from app.services.agent.agent_json import json_dumps as _json_dumps
from app.services.agent.agent_json import json_loads_dict as _json_loads_dict
from app.services.agent.agent_run_state import AgentRunState


class RunStateRepository:
    def create_run(
        self,
        db: Session,
        *,
        goal: str,
        user_id: int,
        session_id: int | None = None,
        trace_id: str | None = None,
        organization_id: int | None = None,
        agent_type: str | None = None,
        parent_run_id: int | None = None,
        delegation_id: str | None = None,
    ) -> AgentRun:
        agent_run = AgentRun(
            user_id=user_id,
            session_id=session_id,
            goal=goal,
            status="running",
            total_steps=0,
            trace_id=trace_id,
            organization_id=organization_id,
            agent_type=agent_type,
            parent_run_id=parent_run_id,
            delegation_id=delegation_id,
        )
        db.add(agent_run)
        db.commit()
        db.refresh(agent_run)
        return agent_run

    def get_run(self, db: Session, run_id: int, user_id: int | None = None) -> AgentRun | None:
        query = db.query(AgentRun).filter(AgentRun.id == run_id)
        if user_id is not None:
            query = query.filter(AgentRun.user_id == user_id)
        return query.first()

    def get_run_logs(
        self, db: Session, run_id: int, user_id: int | None = None
    ) -> list[ToolCallLog]:
        query = db.query(ToolCallLog).join(AgentRun).filter(ToolCallLog.agent_run_id == run_id)
        if user_id is not None:
            query = query.filter(AgentRun.user_id == user_id)
        return query.order_by(ToolCallLog.step.asc(), ToolCallLog.created_at.asc()).all()

    def save_run(self, db: Session, agent_run: AgentRun, **fields: Any) -> AgentRun:
        for key, value in fields.items():
            setattr(agent_run, key, value)
        db.add(agent_run)
        db.commit()
        db.refresh(agent_run)
        # P1：Agent 完成率 SLO 采集（仅终态转移；org/status 为有限枚举维度）。
        try:
            from app.core.metrics import metrics

            status = fields.get("status")
            if status in ("completed", "error", "cancelled"):
                metrics.increment(
                    "agent_runs",
                    labels={"status": status, "org": str(agent_run.organization_id) if agent_run.organization_id else "none"},
                )
        except Exception:  # noqa: BLE001 - 指标失败不影响业务
            pass
        return agent_run

    def save_workflow_state(
        self,
        db: Session,
        agent_run: AgentRun,
        *,
        snapshot: dict[str, Any],
        state: AgentRunState,
        node: str,
        **fields: Any,
    ) -> AgentRun:
        """持久化工作流快照：兼容结构 + 具类型 state，并落 node / 时间戳 / 附加字段。"""
        state.node = node
        payload = dict(snapshot)
        payload["model"] = state.snapshot()
        payload["version"] = 3
        payload["node"] = node
        fields.update(
            {
                "workflow_state": _json_dumps(payload),
                "workflow_state_updated_at": utc_now(),
            }
        )
        return self.save_run(db, agent_run, **fields)

    def load_workflow_snapshot(self, agent_run: AgentRun) -> dict[str, Any]:
        return _json_loads_dict(agent_run.workflow_state)

    def load_state_model(self, agent_run: AgentRun) -> AgentRunState | None:
        snapshot = self.load_workflow_snapshot(agent_run)
        return AgentRunState.from_snapshot(snapshot.get("model"))

    def append_log(
        self,
        db: Session,
        *,
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
        safe_input = {key: value for key, value in input_params.items() if key != "db"}
        log = ToolCallLog(
            agent_run_id=agent_run_id,
            step=step,
            action_type=decision.get("action_type"),
            thought=decision.get("thought"),
            tool_name=tool_name,
            input_params=_json_dumps(safe_input),
            raw_decision=raw_decision,
            observation=observation,
            output_result=output_result,
            status=status,
            error=error,
            duration_ms=duration_ms,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    def update_log(self, db: Session, log: ToolCallLog, **fields: Any) -> ToolCallLog:
        for key, value in fields.items():
            setattr(log, key, value)
        db.add(log)
        db.commit()
        db.refresh(log)
        return log


run_state_repository = RunStateRepository()
