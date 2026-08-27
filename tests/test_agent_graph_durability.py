"""状态-运行时分离的回归护栏。

图 state 一旦混入 Session / ORM 实例 / 回调，checkpointer 就无法落盘，
断点续跑与回放也就无从谈起。这里用一次真实 Run 反查 checkpoint：
通道数 > 0、全部可被 LangGraph 序列化、且没有活对象泄漏进 state。
"""

import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import User
from app.services.agent.agent_runtime import AgentGraphState, AgentRuntime
from app.services.agent.agent_service import AgentService
from app.tools.base import BaseAgentTool, tool_success


class FakeTool(BaseAgentTool):
    def __init__(self, name, description, auto_context_fields=(), handler=None, parameters=None):
        self.name = name
        self.description = description
        self.auto_context_fields = auto_context_fields
        self.parameters = parameters or {"type": "object", "properties": {}, "required": []}
        self._handler = handler or (lambda **kwargs: tool_success("ok", kwargs))

    async def run(self, **kwargs):
        return self._handler(**kwargs)


class AgentGraphDurabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.user = User(username="ckpt", email="ckpt@example.com", hashed_password="secret")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.service = AgentService()

    def tearDown(self):
        self.db.close()

    def test_runtime_objects_are_not_state_channels(self):
        """活对象只能挂在 AgentRuntime 上，不得出现在 state schema 里。"""
        channels = set(AgentGraphState.__annotations__)
        for live_object in ("db", "agent_run", "event_callback", "final_run", "_model"):
            self.assertNotIn(live_object, channels)
        runtime_fields = set(AgentRuntime.__dataclass_fields__)
        self.assertEqual(
            runtime_fields,
            {"db", "agent_run", "user_id", "event_callback", "model", "final_run"},
        )

    async def _run_once(self):
        calls = [
            '{"thought":"查询任务","action_type":"tool_call","tool_name":"task_query_tool","action_input":{}}',
            '{"thought":"完成","action_type":"finish","answer":"已查询到 0 个未完成任务。"}',
        ]

        async def fake_chat(messages, stream=False, temperature=0.7):
            return calls.pop(0)

        fake_tools = {
            "task_query_tool": FakeTool(
                "task_query_tool",
                "查询任务",
                auto_context_fields=("user_id", "db"),
                parameters={
                    "type": "object",
                    "properties": {"user_id": {"type": "integer"}},
                    "required": ["user_id"],
                },
                handler=lambda **kwargs: tool_success("查询完成", {"tasks": []}),
            ),
        }
        with (
            patch("app.services.agent.agent_service.llm_service.generate", new=AsyncMock(return_value="{}")),
            patch("app.services.agent.agent_service.llm_service.chat", side_effect=fake_chat),
            patch.dict("app.mcp.registry._TOOL_INSTANCES", fake_tools, clear=True),
        ):
            return await self.service.run("查询我未完成的任务", self.user.id, self.db, max_steps=4)

    async def test_run_checkpoints_serializable_state_per_thread(self):
        run = await self._run_once()
        self.assertEqual(run.status, "completed")

        workflow = self.service._workflow
        if not hasattr(workflow, "get_state"):
            self.skipTest("fallback workflow engine has no checkpointer")

        config = self.service._graph_config(run)
        self.assertEqual(config["configurable"]["thread_id"], f"agent-run-{run.id}")

        snapshot = workflow.get_state(config)
        self.assertGreater(len(snapshot.values), 0)
        self.assertGreater(len(list(workflow.get_state_history(config))), 1)

        # 声明外的通道会被静默丢弃，落盘内容必须全在 schema 内。
        self.assertLessEqual(set(snapshot.values), set(AgentGraphState.__annotations__))

        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        serde = JsonPlusSerializer()
        for key, value in snapshot.values.items():
            with self.subTest(channel=key):
                serde.dumps_typed(value)
                self.assertFalse(
                    type(value).__module__.startswith(("sqlalchemy", "app.models")),
                    f"live object leaked into state channel {key}: {type(value).__name__}",
                )


if __name__ == "__main__":
    unittest.main()
