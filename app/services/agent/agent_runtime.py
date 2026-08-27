"""Agent 图的「状态 / 运行时」边界。

LangGraph 的 checkpointer 只能持久化可序列化的 state 通道，因此 Session、ORM 实例、
事件回调这类活对象不能放进 state——否则图既无法落盘也无法回放。本模块把二者拆开：

- :class:`AgentRuntime`：一次 Run 的活对象容器，经 ``ainvoke(..., context=...)``
  传入，不进入 checkpoint。
- :class:`AgentGraphState`：图的可序列化状态通道（显式 schema，替代无约束 ``dict``）。
- :func:`resolve_runtime`：节点内统一取用运行时，兼容真 langgraph 与回退引擎。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from sqlalchemy.orm import Session

from app.models.agent import AgentRun
from app.services.agent.agent_run_state import AgentRunState
from app.workflows.langgraph_compat import RUNTIME_CONTEXT_KEY

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class AgentRuntime:
    """一次 Agent Run 的运行时上下文（不可序列化，不入 checkpoint）。"""

    db: Session
    agent_run: AgentRun
    user_id: int
    event_callback: EventCallback | None = None
    model: AgentRunState | None = None
    # 图的终态 Run 由节点写回运行时（AgentRun 是 ORM 实例，不能作为 state 通道）。
    final_run: AgentRun | None = None


class AgentGraphState(TypedDict, total=False):
    """Agent 图的可序列化状态通道。

    只放纯数据：活对象在 :class:`AgentRuntime` 里。未声明的通道会被 LangGraph
    静默丢弃，新增字段必须同时登记在此。
    """

    # 任务与角色
    goal: str
    user_id: int
    session_id: int | None
    memory_context: str
    master_agent: str
    worker_agent: str
    worker_plan: list[str]
    worker_index: int
    handoffs: list[dict[str, Any]]
    handoff_pending: bool
    handoff_target_worker: str | None
    supervisor_plan: dict[str, Any]
    task_contract: dict[str, Any]
    # 并行只读分支
    parallel_plan: dict[str, Any] | None
    parallel_pending: bool
    parallel_results: dict[str, Any]
    parallel_branch_logs: list[dict[str, Any]]
    # 步进与预算
    messages: list[dict[str, str]]
    max_steps: int
    step: int
    step_started_at: float
    run_started: float
    retry_count: int
    timed_out: bool
    awaiting_approval: bool
    # 当前步决策
    current_decision: dict[str, Any] | None
    current_raw: str
    current_action_type: str
    current_tool_name: str | None
    current_safe_input: dict[str, Any]
    current_worker_agent: str
    last_observation: str
    # 证据核验
    evidence_scope_seen: bool
    evidence_verification: dict[str, Any] | None
    needs_evidence_verification: bool
    verification_target: str | None


def resolve_runtime(state: dict[str, Any]) -> AgentRuntime:
    """取出当前节点的运行时上下文。

    解析顺序：state 中显式注入（回退引擎 / 图外调用）→ LangGraph Runtime context
    → 旧式活对象 state 键（保留给直接调用节点的单测）。
    """
    injected = state.get(RUNTIME_CONTEXT_KEY)
    if isinstance(injected, AgentRuntime):
        return injected
    try:
        from langgraph.runtime import get_runtime

        context = get_runtime(AgentRuntime).context
    except Exception:
        context = None
    if isinstance(context, AgentRuntime):
        return context
    legacy = _from_legacy_state(state)
    if legacy is not None:
        return legacy
    raise RuntimeError("Agent runtime context is unavailable for this graph node")


def _from_legacy_state(state: dict[str, Any]) -> AgentRuntime | None:
    db = state.get("db")
    agent_run = state.get("agent_run")
    if db is None or agent_run is None:
        return None
    model = state.get("_model")
    return AgentRuntime(
        db=db,
        agent_run=agent_run,
        user_id=int(state.get("user_id") or getattr(agent_run, "user_id", 0) or 0),
        event_callback=state.get("event_callback"),
        model=model if isinstance(model, AgentRunState) else None,
        final_run=state.get("final_run"),
    )
