"""状态-运行时分离 + 图原生断点的回归护栏。

图 state 一旦混入 Session / ORM 实例 / 回调，checkpointer 就无法落盘，
断点续跑与回放也就无从谈起。这里用一次真实 Run 反查 checkpoint：
通道数 > 0、全部可被 LangGraph 序列化、且没有活对象泄漏进 state。

审批暂停也据此改成 LangGraph 原生 ``interrupt()`` / ``Command(resume=...)``：
恢复时 state 从 checkpoint 还原，而不是从 DB 快照考古重建。
"""

import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import User
from app.services.agent.agent_approval_service import agent_approval_service
from app.services.agent.agent_runtime import AgentGraphState, AgentRuntime
from app.services.agent.agent_service import AgentService
from app.tools.base import BaseAgentTool, tool_success
from app.workflows.langgraph_compat import SQLITE_SAVER_AVAILABLE, build_checkpointer


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

    def test_module_singleton_gets_the_persistent_saver_at_import_time(self):
        """模块级单例在导入期（无 event loop）构造，这是持久 saver 唯一的落地时机。"""
        if not SQLITE_SAVER_AVAILABLE:
            self.skipTest("langgraph-checkpoint-sqlite 未安装")
        from app.services.agent.agent_service import agent_service

        self.assertEqual(type(agent_service._workflow.checkpointer).__name__, "_ThreadedSqliteSaver")

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
        thread_id = config["configurable"]["thread_id"]
        self.assertTrue(thread_id.startswith(f"agent-run-{run.id}-"))
        # trace_id 参与 thread_id：换库后自增 id 重复也不会撞上旧 Run 的 checkpoint。
        self.assertIn(str(run.trace_id), thread_id)

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

    async def test_checkpoints_outlive_the_saver_that_wrote_them(self):
        """换一个全新 saver 读同一个 DB 文件——重启后能否续跑，取决于这一步。"""
        if not SQLITE_SAVER_AVAILABLE:
            self.skipTest("langgraph-checkpoint-sqlite 未安装，checkpoint 仅进程内有效")

        run = await self._run_once()
        thread_config = self.service._graph_config(run)

        written = self.service._workflow.get_state(thread_config)
        self.assertGreater(len(written.values), 0)

        # 新连接、新 saver 实例，只共享磁盘文件——等价于进程重启后重新打开 checkpoint。
        reopened = build_checkpointer()
        self.assertNotIsInstance(reopened, type(None))
        restored = reopened.get_tuple(thread_config)
        self.assertIsNotNone(restored, "checkpoint 没有落到磁盘，重启后无法恢复")
        self.assertEqual(restored.checkpoint["channel_values"]["goal"], written.values["goal"])
        self.assertEqual(
            restored.checkpoint["channel_values"]["worker_plan"],
            written.values["worker_plan"],
        )


class AgentApprovalInterruptTests(unittest.IsolatedAsyncioTestCase):
    """审批暂停走图原生断点：state 从 checkpoint 恢复，不再依赖 DB 快照重建。"""

    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.user = User(username="hitl", email="hitl@example.com", hashed_password="secret")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.service = AgentService()

    def tearDown(self):
        self.db.close()

    async def _pause_on_approval(self):
        """跑到需要审批的写工具处暂停，返回 (paused_run, approval)。"""
        calls = [
            '{"thought":"创建任务需要审批","action_type":"tool_call","tool_name":"task_create_tool",'
            '"action_input":{"title":"审批任务"}}',
            '{"thought":"任务创建完成","action_type":"finish","answer":"任务已在审批后创建完成。"}',
        ]

        async def fake_chat(messages, stream=False, temperature=0.7):
            return calls.pop(0)

        fake_tools = {
            "task_create_tool": FakeTool(
                "task_create_tool",
                "创建任务",
                auto_context_fields=("user_id", "db"),
                parameters={
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "user_id": {"type": "integer"}},
                    "required": ["title", "user_id"],
                },
                handler=lambda **kwargs: tool_success("任务已创建", {"task": {"id": 88, "title": kwargs["title"]}}),
            ),
        }
        for patcher in (
            patch("app.services.agent.agent_service.llm_service.chat", side_effect=fake_chat),
            patch.dict("app.mcp.registry._TOOL_INSTANCES", fake_tools, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        paused_run = await self.service.run("创建一个审批任务", self.user.id, self.db, max_steps=3)
        self.assertEqual(paused_run.status, "awaiting_approval")
        approval = agent_approval_service.list_requests(db=self.db, user_id=self.user.id, status="pending")[0]
        return paused_run, approval

    def _require_checkpointed_engine(self):
        if not hasattr(self.service._workflow, "get_state"):
            self.skipTest("fallback workflow engine has no checkpointer/interrupt")

    async def test_graph_pauses_at_a_native_interrupt(self):
        """暂停不是「走到终态节点」，而是图上真的挂着一个可恢复的断点。"""
        paused_run, approval = await self._pause_on_approval()
        self._require_checkpointed_engine()

        snapshot = self.service._workflow.get_state(self.service._graph_config(paused_run))
        self.assertEqual(tuple(snapshot.next), ("awaiting_approval",))
        self.assertTrue(snapshot.interrupts, "图没有挂起中断点，resume 只能退回 DB 快照重建")
        payload = snapshot.interrupts[0].value
        self.assertEqual(payload["kind"], "tool_approval")
        self.assertEqual(payload["approval_request_id"], approval.id)
        self.assertEqual(payload["tool_name"], "task_create_tool")
        # 断点处的通道足以独立恢复：无需回查 AgentRun.workflow_state。
        self.assertTrue(snapshot.values["awaiting_approval"])
        self.assertEqual(snapshot.values["pending_approval_request_id"], approval.id)
        self.assertEqual(snapshot.values["current_safe_input"]["title"], "审批任务")

    async def test_resume_restores_state_from_checkpoint_not_db_snapshot(self):
        """抹掉 DB 快照 + 封掉快照恢复路径，图仍应凭 checkpoint 跑完。"""
        paused_run, approval = await self._pause_on_approval()
        self._require_checkpointed_engine()

        agent_approval_service.decide_request(
            db=self.db, approval_id=approval.id, user_id=self.user.id, approved=True, decision_note="allow"
        )
        # workflow_state 清空、result 写坏：DB 快照恢复此时必然重建出错误的 state。
        paused_run.workflow_state = None
        paused_run.result = json.dumps({"worker_agent": "document_agent", "max_steps": 3})
        self.db.add(paused_run)
        self.db.commit()

        with patch.object(
            AgentService,
            "_resume_via_db_snapshot",
            side_effect=AssertionError("走了 DB 快照恢复，说明图原生断点没生效"),
        ):
            resumed_run = await self.service.resume_after_approval(approval.id, self.user.id, self.db)

        self.assertEqual(resumed_run.status, "completed")
        self.assertIn("审批后创建完成", resumed_run.final_answer)
        logs = self.service.get_run_logs(resumed_run.id, self.db, user_id=self.user.id)
        self.assertEqual([log.tool_name for log in logs], ["task_create_tool", "task_create_tool", "finish"])
        self.assertEqual(logs[0].status, "approved")
        self.assertEqual(logs[1].status, "success")
        self.assertEqual(
            agent_approval_service.get_request(db=self.db, approval_id=approval.id, user_id=self.user.id).status,
            "executed",
        )
        # 断点已消耗：线程回到终态，不会被二次 resume。
        after = self.service._workflow.get_state(self.service._graph_config(resumed_run))
        self.assertFalse(after.interrupts)
        self.assertFalse(after.values.get("awaiting_approval"))

    async def test_param_drift_keeps_the_interrupt_resumable(self):
        """参数漂移守卫拦下这一次执行后，断点必须还在——否则该 Run 再也无法恢复。"""
        paused_run, approval = await self._pause_on_approval()
        self._require_checkpointed_engine()

        agent_approval_service.decide_request(
            db=self.db, approval_id=approval.id, user_id=self.user.id, approved=True, decision_note="allow"
        )
        approval.param_digest = "0" * 32  # 与待执行参数不符 → 必须重新审批
        self.db.add(approval)
        self.db.commit()

        with self.assertRaises(ValueError):
            await self.service.resume_after_approval(approval.id, self.user.id, self.db)

        self.assertEqual(self.service.get_run(paused_run.id, self.db, user_id=self.user.id).status, "awaiting_approval")
        snapshot = self.service._workflow.get_state(self.service._graph_config(paused_run))
        self.assertTrue(snapshot.interrupts, "守卫拦下后断点丢失，Run 将无法再恢复")
        reissued = agent_approval_service.list_requests(db=self.db, user_id=self.user.id, status="pending")
        self.assertEqual([item.tool_name for item in reissued], ["task_create_tool"])

    async def test_resume_falls_back_to_db_snapshot_without_interrupt_support(self):
        """无 interrupt 能力（回退引擎 / checkpoint 已丢失）时，DB 快照恢复仍须走通。

        两条路径共用同一段写工具执行逻辑，这里守住的是回退分支本身。
        """
        with (
            patch("app.services.agent.agent_workflow_nodes.INTERRUPT_AVAILABLE", False),
            patch("app.services.agent.agent_service.INTERRUPT_AVAILABLE", False),
        ):
            paused_run, approval = await self._pause_on_approval()
            self.assertIsNone(self.service._pending_approval_interrupt(paused_run))
            agent_approval_service.decide_request(
                db=self.db, approval_id=approval.id, user_id=self.user.id, approved=True, decision_note="allow"
            )
            resumed_run = await self.service.resume_after_approval(approval.id, self.user.id, self.db)

        self.assertEqual(resumed_run.status, "completed")
        self.assertIn("审批后创建完成", resumed_run.final_answer)
        logs = self.service.get_run_logs(resumed_run.id, self.db, user_id=self.user.id)
        self.assertEqual([log.tool_name for log in logs], ["task_create_tool", "task_create_tool", "finish"])
        self.assertEqual(logs[0].status, "approved")
        self.assertEqual(logs[1].status, "success")
        self.assertEqual(
            agent_approval_service.get_request(db=self.db, approval_id=approval.id, user_id=self.user.id).status,
            "executed",
        )


if __name__ == "__main__":
    unittest.main()
