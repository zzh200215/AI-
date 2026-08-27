from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Awaitable, Callable

START = "__start__"
END = "__end__"

# 持久化 checkpoint 的本地 SQLite 路径（仅在安装 langgraph-checkpoint-sqlite 时启用）
_CHECKPOINT_DB_PATH = os.environ.get("LANGGRAPH_CHECKPOINT_DB", "data/langgraph_checkpoints.sqlite")

try:
    from langgraph.graph import END as LANGGRAPH_END
    from langgraph.graph import START as LANGGRAPH_START
    from langgraph.graph import StateGraph as LangGraphStateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:
    LANGGRAPH_AVAILABLE = False
    LANGGRAPH_START = START
    LANGGRAPH_END = END
    LangGraphStateGraph = None


NodeFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
ConditionFn = Callable[[dict[str, Any]], str]


class _FallbackCompiledGraph:
    def __init__(
        self,
        *,
        nodes: dict[str, NodeFn],
        edges: dict[str, list[str]],
        conditional_edges: dict[str, tuple[ConditionFn, dict[str, str]]],
        entry_point: str,
    ) -> None:
        self._nodes = nodes
        self._edges = edges
        self._conditional_edges = conditional_edges
        self._entry_point = entry_point

    async def ainvoke(self, state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        _ = config  # 回退引擎无状态；checkpoint config 在此为 no-op，仅为与真 langgraph 对齐签名
        current = self._entry_point
        current_state = state
        while current != END:
            handler = self._nodes[current]
            current_state = await handler(current_state)
            if current in self._conditional_edges:
                router, mapping = self._conditional_edges[current]
                branch = router(current_state)
                current = mapping[branch]
                continue
            next_nodes = self._edges.get(current) or []
            current = next_nodes[0] if next_nodes else END
        return current_state


class _FallbackStateGraph:
    def __init__(self, state_type: type[dict[str, Any]] | None = None) -> None:
        _ = state_type
        self._nodes: dict[str, NodeFn] = {}
        self._edges: dict[str, list[str]] = defaultdict(list)
        self._conditional_edges: dict[str, tuple[ConditionFn, dict[str, str]]] = {}
        self._entry_point: str | None = None

    def add_node(self, name: str, handler: NodeFn) -> None:
        self._nodes[name] = handler

    def add_edge(self, source: str, target: str) -> None:
        if source == START:
            self._entry_point = target
            return
        self._edges[source].append(target)

    def add_conditional_edges(self, source: str, router: ConditionFn, mapping: dict[str, str]) -> None:
        self._conditional_edges[source] = (router, mapping)

    def compile(self, *, checkpointer: Any | None = None, **_: Any) -> _FallbackCompiledGraph:
        _ = checkpointer  # 回退引擎不做持久化；接受该参数仅为与真 langgraph 的 compile 签名兼容
        if not self._entry_point:
            raise ValueError("Workflow entry point is not configured")
        return _FallbackCompiledGraph(
            nodes=self._nodes,
            edges=self._edges,
            conditional_edges=self._conditional_edges,
            entry_point=self._entry_point,
        )


StateGraph = LangGraphStateGraph if LANGGRAPH_AVAILABLE else _FallbackStateGraph
GRAPH_START = LANGGRAPH_START if LANGGRAPH_AVAILABLE else START
GRAPH_END = LANGGRAPH_END if LANGGRAPH_AVAILABLE else END


def workflow_engine_name() -> str:
    return "langgraph" if LANGGRAPH_AVAILABLE else "internal_state_graph"


def build_checkpointer() -> Any | None:
    """Return a LangGraph checkpointer for durable, thread-scoped graph state.

    Prefers a persistent SQLite saver when ``langgraph-checkpoint-sqlite`` is
    installed (survives process restarts); otherwise falls back to the in-core
    ``InMemorySaver`` (per-process). Returns ``None`` when the fallback engine is
    active, since it has no checkpoint machinery.
    """
    if not LANGGRAPH_AVAILABLE:
        return None
    try:  # 已安装 sqlite saver 时自动升级为跨重启持久化
        from langgraph.checkpoint.sqlite import SqliteSaver

        os.makedirs(os.path.dirname(_CHECKPOINT_DB_PATH) or ".", exist_ok=True)
        return SqliteSaver.from_conn_string(_CHECKPOINT_DB_PATH).__enter__()
    except Exception:
        pass
    try:
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    except Exception:
        return None
