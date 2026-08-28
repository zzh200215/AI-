"""进度事件走图的 ``custom`` 流通道，而不是节点直接 await 业务回调。

原先事件传输是自建的：WebSocket 回调作为活对象随 runtime 传进每个节点，节点里
``await event_callback(...)``。改成节点往图的流通道里写、服务层从 ``astream`` 里取出再
转发之后，这里守住三件事：

- 节点事件确实经过流通道，且服务层把它们按序转给订阅者（对外契约不变）；
- 服务层自己发的事件（run 开始 / 失败 / 恢复）没有图上下文，不走流通道；
- ``ainvoke`` 驱动时 LangGraph 给的是 no-op writer，节点事件会被静默丢弃——所以图必须
  一律经 ``_stream_workflow`` 驱动。这条不变量以前没有任何测试覆盖：事件全丢也不会有
  用例变红。
"""

import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import User
from app.services.agent.agent_service import AgentService
from app.tools.base import BaseAgentTool, tool_success
from app.workflows import langgraph_compat

GOAL = "查询我未完成的任务"


class _TaskQueryTool(BaseAgentTool):
    def __init__(self):
        self.name = "task_query_tool"
        self.description = "查询任务"
        self.auto_context_fields = ("user_id", "db")
        self.parameters = {
            "type": "object",
            "properties": {"user_id": {"type": "integer"}},
            "required": ["user_id"],
        }

    async def run(self, **kwargs):
        return tool_success("查询完成", {"tasks": []})


def _recording_writer(recorded: list[dict]):
    """包一层真实 writer：记录写进流通道的事件，但不改变传输行为。"""
    real = langgraph_compat.graph_stream_writer

    def factory():
        writer = real()
        if writer is None:
            return None

        def _write(payload):
            recorded.append(payload)
            writer(payload)

        return _write

    return factory


class AgentStreamEventsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.user = User(username="stream", email="stream@example.com", hashed_password="secret")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.service = AgentService()

    def tearDown(self):
        self.db.close()

    async def _run_once(self, events: list[dict]):
        """一次 tool_call -> finish 的最短 Run：节点侧会发 step_completed 与 run_completed。"""
        calls = [
            '{"thought":"查询任务","action_type":"tool_call","tool_name":"task_query_tool","action_input":{}}',
            '{"thought":"完成","action_type":"finish","answer":"已查询到 0 个未完成任务。"}',
        ]

        async def fake_chat(messages, stream=False, temperature=0.7):
            return calls.pop(0)

        async def event_callback(payload):
            events.append(payload)

        with (
            patch("app.services.agent.agent_service.llm_service.generate", new=AsyncMock(return_value="{}")),
            patch("app.services.agent.agent_service.llm_service.chat", side_effect=fake_chat),
            patch.dict("app.mcp.registry._TOOL_INSTANCES", {"task_query_tool": _TaskQueryTool()}, clear=True),
        ):
            return await self.service.run(
                GOAL,
                self.user.id,
                self.db,
                max_steps=4,
                event_callback=event_callback,
            )

    async def test_node_events_reach_the_subscriber_through_the_stream_channel(self):
        events: list[dict] = []
        streamed: list[dict] = []
        with patch("app.services.agent.agent_service.graph_stream_writer", _recording_writer(streamed)):
            run = await self._run_once(events)

        self.assertEqual(run.status, "completed")
        types = [event["type"] for event in events]
        self.assertEqual(types[0], "run_started")
        self.assertIn("step_completed", types)
        self.assertEqual(types[-1], "run_completed")

        # 节点事件全部经过流通道，且服务层按流的顺序原样转发；
        # run_started 由服务层在图启动前发出，没有图上下文，不该出现在流里。
        self.assertNotIn("run_started", [payload["type"] for payload in streamed])
        self.assertEqual(
            [payload["type"] for payload in streamed],
            [event_type for event_type in types if event_type != "run_started"],
            "订阅者收到的节点事件与流通道里的不一致",
        )

    async def test_events_are_dropped_when_the_graph_runs_without_the_stream(self):
        """``ainvoke`` 下 writer 是 no-op：这正是所有图调用必须走 _stream_workflow 的原因。"""
        if langgraph_compat.workflow_engine_name() != "langgraph":
            self.skipTest("fallback engine has no stream channel")

        async def ainvoke_instead(service, graph_input, *, agent_run, runtime):
            await service._workflow.ainvoke(graph_input, service._graph_config(agent_run), context=runtime)

        events: list[dict] = []
        with patch.object(AgentService, "_stream_workflow", ainvoke_instead):
            run = await self._run_once(events)

        self.assertEqual(run.status, "completed")
        self.assertEqual(
            [event["type"] for event in events],
            ["run_started"],
            "no-op writer 下节点事件竟然还能到达订阅者，说明传输没走流通道",
        )

    async def test_events_fall_back_to_the_callback_without_a_stream_channel(self):
        """回退引擎没有流通道：writer 为 None 时节点必须直接回调订阅者。"""
        events: list[dict] = []
        with patch("app.services.agent.agent_service.graph_stream_writer", lambda: None):
            run = await self._run_once(events)

        self.assertEqual(run.status, "completed")
        types = [event["type"] for event in events]
        self.assertEqual(types[0], "run_started")
        self.assertIn("step_completed", types)
        self.assertEqual(types[-1], "run_completed")


if __name__ == "__main__":
    unittest.main()
