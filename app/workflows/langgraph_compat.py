from __future__ import annotations

import asyncio
import os
import sqlite3
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

START = "__start__"
END = "__end__"

# 运行时上下文在 state 中的传递键：仅回退引擎与图外调用使用。
# 真 langgraph 走 `ainvoke(..., context=...)` + `langgraph.runtime.get_runtime()`，
# 上下文不进入 checkpoint，因此可以承载 Session / ORM 实例 / 回调等活对象。
RUNTIME_CONTEXT_KEY = "__runtime_context__"

# 持久化 checkpoint 的本地 SQLite 默认路径（data/ 已在 .gitignore 中）
_DEFAULT_CHECKPOINT_DB_PATH = "data/langgraph_checkpoints.sqlite"

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
ConditionFn = Callable[[dict[str, Any]], Any]


class _FallbackSend:
    """回退引擎的 ``Send`` 等价物：只承载 (node, arg)，由回退图顺序执行。

    真 langgraph 的 ``Send`` 会把每个分支变成同一 superstep 内的并发任务；这里没有
    调度器，语义退化为「按计划顺序逐个执行」——结果一致，只是没有并发。
    """

    __slots__ = ("node", "arg")

    def __init__(self, node: str, arg: Any) -> None:
        self.node = node
        self.arg = arg

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Send(node={self.node!r}, arg={self.arg!r})"


def _state_reducers(state_type: Any) -> dict[str, Callable[[Any, Any], Any]]:
    """从 state schema 的 ``Annotated[..., reducer]`` 元数据里提取通道 reducer。

    并发分支写同一通道时必须靠 reducer 合并；回退引擎虽是顺序执行，但分支节点返回的
    是增量更新，同样需要 reducer 才能把多个分支的结果并到一起。
    """
    try:
        from typing import get_type_hints

        hints = get_type_hints(state_type, include_extras=True)
    except Exception:  # noqa: BLE001 - schema 不可解析时退化为「无 reducer」
        return {}
    reducers: dict[str, Callable[[Any, Any], Any]] = {}
    for key, hint in hints.items():
        for meta in getattr(hint, "__metadata__", ()) or ():
            if callable(meta):
                reducers[key] = meta
                break
    return reducers


class _FallbackCompiledGraph:
    def __init__(
        self,
        *,
        nodes: dict[str, NodeFn],
        edges: dict[str, list[str]],
        conditional_edges: dict[str, tuple[ConditionFn, dict[str, str]]],
        entry_point: str,
        reducers: dict[str, Callable[[Any, Any], Any]] | None = None,
    ) -> None:
        self._nodes = nodes
        self._edges = edges
        self._conditional_edges = conditional_edges
        self._entry_point = entry_point
        self._reducers = reducers or {}

    def _merge(self, state: dict[str, Any], update: Any) -> dict[str, Any]:
        """把节点返回的增量更新并入 state，声明了 reducer 的通道走 reducer。"""
        if not isinstance(update, dict):
            return state
        merged = dict(state)
        for key, value in update.items():
            reducer = self._reducers.get(key)
            merged[key] = reducer(merged.get(key), value) if reducer else value
        return merged

    async def _run_sends(
        self,
        destinations: list[Any],
        state: dict[str, Any],
        context: Any | None,
    ) -> tuple[dict[str, Any], str]:
        """顺序执行 ``Send`` 分支，再按 reducer 合并各分支的增量更新。"""
        target = END
        for destination in destinations:
            if not isinstance(destination, Send | _FallbackSend):
                raise RuntimeError("fallback engine routes either a single branch name or a list of Send")
            branch_state = dict(destination.arg or {})
            if context is not None:
                branch_state[RUNTIME_CONTEXT_KEY] = context
            state = self._merge(state, await self._nodes[destination.node](branch_state))
            next_nodes = self._edges.get(destination.node) or []
            target = next_nodes[0] if next_nodes else END
        return state, target

    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
        *,
        context: Any | None = None,
    ) -> dict[str, Any]:
        _ = config  # 回退引擎无状态；checkpoint config 在此为 no-op，仅为与真 langgraph 对齐签名
        current = self._entry_point
        current_state = state
        if context is not None:
            # 回退引擎没有 Runtime 通道，把上下文放进 state 让节点用同一 API 取用。
            current_state = {**current_state, RUNTIME_CONTEXT_KEY: context}
        while current != END:
            handler = self._nodes[current]
            current_state = await handler(current_state)
            if current in self._conditional_edges:
                router, mapping = self._conditional_edges[current]
                branch = router(current_state)
                if isinstance(branch, list | tuple):
                    current_state, current = await self._run_sends(list(branch), current_state, context)
                    continue
                current = mapping[branch]
                continue
            next_nodes = self._edges.get(current) or []
            current = next_nodes[0] if next_nodes else END
        return current_state

    async def astream(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
        *,
        context: Any | None = None,
        stream_mode: Any = None,
    ) -> AsyncIterator[Any]:
        """执行整张图，但不产出任何流块——回退引擎没有 ``custom`` 流通道。

        节点里的 ``graph_stream_writer()`` 在回退引擎下返回 None，进度事件因此仍直接回调
        订阅者：结果与真 langgraph 的「事件出图再由服务层转发」一致，只是少了图这一跳。
        """
        _ = stream_mode
        await self.ainvoke(state, config, context=context)
        for chunk in ():
            yield chunk


class _FallbackStateGraph:
    def __init__(self, state_type: type[dict[str, Any]] | None = None, context_schema: type | None = None) -> None:
        _ = context_schema
        self._nodes: dict[str, NodeFn] = {}
        self._edges: dict[str, list[str]] = defaultdict(list)
        self._conditional_edges: dict[str, tuple[ConditionFn, dict[str, str]]] = {}
        self._entry_point: str | None = None
        # 通道 reducer 来自 state schema，与真 langgraph 同源，并发/多分支写入语义才一致。
        self._reducers = _state_reducers(state_type) if state_type is not None else {}

    def add_node(self, name: str, handler: NodeFn) -> None:
        self._nodes[name] = handler

    def add_edge(self, source: str, target: str) -> None:
        if source == START:
            self._entry_point = target
            return
        self._edges[source].append(target)

    def add_conditional_edges(
        self,
        source: str,
        router: ConditionFn,
        mapping: dict[str, str] | list[str] | None = None,
    ) -> None:
        if isinstance(mapping, list):
            mapping = {name: name for name in mapping}
        self._conditional_edges[source] = (router, mapping or {})

    def compile(self, *, checkpointer: Any | None = None, **_: Any) -> _FallbackCompiledGraph:
        _ = checkpointer  # 回退引擎不做持久化；接受该参数仅为与真 langgraph 的 compile 签名兼容
        if not self._entry_point:
            raise ValueError("Workflow entry point is not configured")
        return _FallbackCompiledGraph(
            nodes=self._nodes,
            edges=self._edges,
            conditional_edges=self._conditional_edges,
            entry_point=self._entry_point,
            reducers=self._reducers,
        )


StateGraph = LangGraphStateGraph if LANGGRAPH_AVAILABLE else _FallbackStateGraph
GRAPH_START = LANGGRAPH_START if LANGGRAPH_AVAILABLE else START
GRAPH_END = LANGGRAPH_END if LANGGRAPH_AVAILABLE else END


def workflow_engine_name() -> str:
    return "langgraph" if LANGGRAPH_AVAILABLE else "internal_state_graph"


def graph_stream_writer() -> Callable[[Any], None] | None:
    """当前节点的 ``custom`` 流写入口；不在图内执行时返回 None。

    ``get_stream_writer()`` 在 runnable 上下文之外直接抛 RuntimeError，这个返回值正好用来
    区分「节点内」与「节点外」：节点内的进度事件写进图的流通道，由服务层从流里取出转发；
    服务层自己发的事件（run 开始 / 失败 / 恢复）没有图上下文，仍直接回调订阅者。

    只有当图以含 ``"custom"`` 的 ``stream_mode`` 驱动时 writer 才是真的，否则 LangGraph
    给的是 no-op、事件被静默丢弃。因此图必须一律经 ``AgentService._stream_workflow``
    驱动，这条不变量由 tests/test_agent_stream_events.py 守住。
    """
    if not LANGGRAPH_AVAILABLE:
        return None
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:  # noqa: BLE001 - 图外调用：无 runnable 上下文
        return None


if LANGGRAPH_AVAILABLE:
    try:
        from langgraph.types import Send

        SEND_AVAILABLE = True
    except Exception:
        Send = _FallbackSend  # type: ignore[assignment,misc]
        SEND_AVAILABLE = False
else:
    Send = _FallbackSend  # type: ignore[assignment,misc]
    SEND_AVAILABLE = False


def _interrupt_unsupported(value: Any) -> Any:
    """回退引擎没有 checkpoint，中断点无处保存，也就无法 resume。"""
    _ = value
    raise RuntimeError("graph interrupt requires langgraph with a checkpointer")


class _CommandUnsupported:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)
        raise RuntimeError("Command(resume=...) requires langgraph with a checkpointer")


if LANGGRAPH_AVAILABLE:
    try:
        from langgraph.types import Command, interrupt

        INTERRUPT_AVAILABLE = True
    except Exception:
        Command = _CommandUnsupported  # type: ignore[assignment,misc]
        interrupt = _interrupt_unsupported  # type: ignore[assignment]
        INTERRUPT_AVAILABLE = False
else:
    Command = _CommandUnsupported  # type: ignore[assignment,misc]
    interrupt = _interrupt_unsupported  # type: ignore[assignment]
    INTERRUPT_AVAILABLE = False


def _checkpoint_db_path() -> str:
    """每次调用时读环境变量，测试可重定向到临时目录而不用关心导入顺序。"""
    return os.environ.get("LANGGRAPH_CHECKPOINT_DB", _DEFAULT_CHECKPOINT_DB_PATH)


if LANGGRAPH_AVAILABLE:
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver as _SqliteSaver

        class _ThreadedSqliteSaver(_SqliteSaver):  # type: ignore[misc,valid-type]
            """让同步 ``SqliteSaver`` 能给 async 图当 checkpointer 用。

            ``SqliteSaver`` 的 ``aput`` / ``aget_tuple`` 直接 raise
            ``NotImplementedError``，而 langgraph 的异步 pregel 循环只调 async 方法，
            所以它不能直接喂给 ``ainvoke``。官方 ``AsyncSqliteSaver`` 又在 ``__init__``
            里捕获当前 event loop 并终身绑定：导入期（无 loop）构造不出来，每个测试换一个
            新 loop 也会失效。

            这里沿用 langgraph 自己给 sqlite cache 用的做法——async 方法投到线程池执行
            同步实现。``SqliteSaver`` 每次取 cursor 都加 ``threading.Lock``，因此
            ``check_same_thread=False`` 的连接跨线程访问是安全的（其 docstring 亦如此说明）。
            """

            async def aget_tuple(self, config: Any) -> Any:
                return await asyncio.to_thread(self.get_tuple, config)

            async def alist(
                self,
                config: Any | None,
                *,
                filter: dict[str, Any] | None = None,
                before: Any | None = None,
                limit: int | None = None,
            ) -> Any:
                def _collect() -> list[Any]:
                    return list(self.list(config, filter=filter, before=before, limit=limit))

                for item in await asyncio.to_thread(_collect):
                    yield item

            async def aput(
                self,
                config: Any,
                checkpoint: Any,
                metadata: Any,
                new_versions: Any,
            ) -> Any:
                return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

            async def aput_writes(
                self,
                config: Any,
                writes: Any,
                task_id: str,
                task_path: str = "",
            ) -> None:
                await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

            async def adelete_thread(self, thread_id: str) -> None:
                await asyncio.to_thread(self.delete_thread, thread_id)

        SQLITE_SAVER_AVAILABLE = True
    except Exception:
        SQLITE_SAVER_AVAILABLE = False
else:
    SQLITE_SAVER_AVAILABLE = False


def build_checkpointer() -> Any | None:
    """Return a LangGraph checkpointer for durable, thread-scoped graph state.

    Prefers SQLite (survives process restarts) when ``langgraph-checkpoint-sqlite``
    is installed, wrapped so its synchronous implementation is usable from the async
    pregel loop; otherwise falls back to the in-core ``InMemorySaver`` (per-process).
    Returns ``None`` when the fallback engine is active, since it has no checkpoint
    machinery. The connection lives for the process, like the compiled graph holding it.
    """
    if not LANGGRAPH_AVAILABLE:
        return None
    if SQLITE_SAVER_AVAILABLE:
        try:
            db_path = _checkpoint_db_path()
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            return _ThreadedSqliteSaver(sqlite3.connect(db_path, check_same_thread=False))
        except Exception:
            pass
    try:
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    except Exception:
        return None
