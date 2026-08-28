"""并行只读分支的图原生 map-reduce 护栏。

并发调度以前写在节点内部（``asyncio.gather`` + 手写 semaphore），图上看不见：
既不是图任务，也就没有独立的 checkpoint 记录，并发上限还绕开了图的配置。
改成 ``Send`` 展开分支 + 通道 reducer 汇总后，这里守住三件事：

- 两个分支**真的重叠执行**（顺序执行会让时间区间不相交，断言直接失败）；
- 每个分支是一个真实的图任务（同一 superstep 里两个独立 task）；
- 审计日志按**计划顺序**而非完成顺序生成——并发下完成顺序不定，编号跟着它走
  会让同一次执行的审计记录不可复现。

最后一个用例单独盯回退引擎：它没有调度器，``Send`` 退化为顺序执行，但通道 reducer
的合并语义必须与真 langgraph 一致，否则两套引擎的结果会不一样。
"""

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import User
from app.services.agent.agent_planner import planner
from app.services.agent.agent_service import AgentService
from app.tools.base import BaseAgentTool, tool_success

GOAL = "分析文档 7 的风险"


class RecordingDocumentRiskTool(BaseAgentTool):
    """document_risk_tool 的测试替身：记录每次调用的时间区间与所用 Session。"""

    def __init__(self, delays):
        self.name = "document_risk_tool"
        self.description = "文档风险分析"
        self.auto_context_fields = ("user_id", "db")
        self.parameters = {
            "type": "object",
            "properties": {"document_id": {"type": "integer"}, "user_id": {"type": "integer"}},
            "required": ["document_id", "user_id"],
        }
        self._delays = list(delays)
        self._spans: dict[int, tuple[float, float]] = {}
        self.sessions: list[int] = []
        self._calls = 0

    @property
    def spans(self) -> list[tuple[float, float]]:
        """按**调用顺序**（即计划顺序）返回时间区间，而不是按完成顺序。"""
        return [self._spans[index] for index in sorted(self._spans)]

    async def run(self, **kwargs):
        index = self._calls
        self._calls += 1
        self.sessions.append(id(kwargs.get("db")))
        started = time.perf_counter()
        await asyncio.sleep(self._delays[index] if index < len(self._delays) else 0.0)
        self._spans[index] = (started, time.perf_counter())
        return tool_success(
            "风险分析完成",
            {
                "document_id": kwargs.get("document_id"),
                "risks": [{"title": f"风险{index + 1}", "severity": "medium", "evidence": "合同第 3 条原文"}],
            },
        )


class AgentParallelFanoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.user = User(username="fanout", email="fanout@example.com", hashed_password="secret")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.service = AgentService()

    def tearDown(self):
        self.db.close()

    def _parallel_plan(self):
        """用真实 planner 生成计划，只把 workers 固定成可并行的那一对。"""
        plan = planner._fallback_dict(GOAL)
        plan["workers"] = ["knowledge_agent", "legal_compliance_agent"]
        plan["parallel_plan"] = planner._parallel_plan(GOAL, plan["workers"])
        plan["execution_mode"] = "parallel_read_only"
        self.assertEqual(list(plan["parallel_plan"]), ["knowledge_agent", "legal_compliance_agent"])
        return plan

    async def _run_fanout(self, delays=(0.20, 0.05)):
        """计划里的第一个分支跑得最慢——完成顺序因此与计划顺序相反。"""
        tool = RecordingDocumentRiskTool(delays)
        plan = self._parallel_plan()
        with (
            patch("app.services.agent.agent_service.llm_service.generate", new=AsyncMock(return_value="{}")),
            patch.object(AgentService, "_plan_with_supervisor", new=AsyncMock(return_value=plan)),
            patch.dict("app.mcp.registry._TOOL_INSTANCES", {"document_risk_tool": tool}, clear=True),
        ):
            run = await self.service.run(GOAL, self.user.id, self.db, max_steps=5)
        return run, tool

    async def test_branches_execute_concurrently_as_graph_tasks(self):
        run, tool = await self._run_fanout()
        self.assertEqual(run.status, "completed")
        self.assertEqual(len(tool.spans), 2, "两个只读分支都应被执行")

        (first_start, first_end), (second_start, second_end) = tool.spans
        overlap = min(first_end, second_end) - max(first_start, second_start)
        self.assertGreater(overlap, 0, f"两个分支没有重叠执行，说明仍是顺序跑：{tool.spans}")

        # 分支并发使用同一个 Session 是不安全的，每个分支必须拿到自己的。
        self.assertEqual(len(set(tool.sessions)), 2, "并行分支共用了同一个 Session")
        self.assertNotIn(id(self.db), tool.sessions)

    async def test_branch_concurrency_is_bounded_by_graph_max_concurrency(self):
        """并发上限由图配置说话：``max_concurrency=1`` 时图把分支串起来跑。

        反过来也证明上限确实生效——它取代了原先节点内手写的 semaphore，不是装饰。
        """
        base = AgentService._graph_config
        with patch.object(
            AgentService,
            "_graph_config",
            staticmethod(lambda agent_run: {**base(agent_run), "max_concurrency": 1}),
        ):
            _, tool = await self._run_fanout()
        (first_start, first_end), (second_start, second_end) = tool.spans
        self.assertLessEqual(
            min(first_end, second_end) - max(first_start, second_start),
            0,
            f"max_concurrency=1 仍然并发，说明并发上限没走图配置：{tool.spans}",
        )

    async def test_branches_are_dispatched_as_graph_nodes(self):
        """分支必须是图任务：同一 superstep 里排出两个独立的 parallel_branch 任务。"""
        run, _ = await self._run_fanout()
        workflow = self.service._workflow
        if not hasattr(workflow, "get_state_history"):
            self.skipTest("fallback workflow engine has no checkpointer")

        config = self.service._graph_config(run)
        history = list(workflow.get_state_history(config))
        path = [tuple(task.name for task in (snapshot.tasks or ())) for snapshot in reversed(history)]
        self.assertEqual(
            [step for step in path if step and step != ("__start__",)],
            [
                ("decide",),
                ("parallel_fanout",),
                ("parallel_branch", "parallel_branch"),
                ("parallel_collect",),
                ("verify_evidence",),
                ("parallel_aggregate",),
            ],
            f"图的执行路径不是 fanout -> N 个分支任务 -> collect：{path}",
        )

        # 两个分支是两个独立任务（task id 不同），而不是一个节点内部 gather 出来的协程。
        fanout = next(snapshot for snapshot in history if len(snapshot.tasks or ()) == 2)
        self.assertEqual(len({task.id for task in fanout.tasks}), 2)
        self.assertEqual(fanout.next, ("parallel_branch", "parallel_branch"))

        # 两个分支并发写同一通道，靠 reducer 合并——缺 reducer 会直接 InvalidUpdateError。
        values = workflow.get_state(config).values
        self.assertEqual(sorted(values["parallel_results"]), ["knowledge_agent", "legal_compliance_agent"])

    async def test_audit_order_follows_the_plan_not_completion_order(self):
        run, tool = await self._run_fanout()
        # 计划里的第一个分支最慢：它最后完成，完成顺序与计划顺序相反。
        self.assertGreater(tool.spans[0][1], tool.spans[1][1])

        logs = self.service.get_run_logs(run.id, self.db, user_id=self.user.id)
        branch_logs = [log for log in logs if log.tool_name == "document_risk_tool"]
        self.assertEqual(
            [self._worker_of(log) for log in branch_logs],
            ["knowledge_agent", "legal_compliance_agent"],
            "分支日志跟着完成顺序走，同一次执行的审计记录将不可复现",
        )
        self.assertEqual([log.step for log in branch_logs], [1, 2])
        self.assertEqual(
            [log.tool_name for log in logs],
            [
                "document_risk_tool",
                "document_risk_tool",
                "supervisor_parallel_fanout",
                "evidence_verifier",
                "supervisor_aggregate",
            ],
        )
        self.assertTrue(all(log.status == "success" for log in logs))

    async def test_aggregate_reports_both_workers(self):
        run, _ = await self._run_fanout()
        aggregation = self.service.get_run(run.id, self.db, user_id=self.user.id).result or ""
        self.assertIn("knowledge_agent", aggregation)
        self.assertIn("legal_compliance_agent", aggregation)
        self.assertIn("并行完成 2 个只读 Worker", run.final_answer)

    @staticmethod
    def _worker_of(log):
        import json

        return json.loads(log.input_params or "{}").get("_worker_agent")


class FallbackEngineSendTests(unittest.IsolatedAsyncioTestCase):
    """回退引擎的 ``Send`` 展开与 reducer 合并：顺序执行，但结果必须与真引擎一致。"""

    async def test_send_branches_merge_through_channel_reducer(self):
        from typing import Annotated, Any, TypedDict

        from app.services.agent.agent_runtime import merge_parallel_branches
        from app.workflows import langgraph_compat
        from app.workflows.langgraph_compat import Send, _FallbackStateGraph

        class _State(TypedDict):
            parallel_results: Annotated[dict[str, Any], merge_parallel_branches]

        order: list[str] = []

        async def fanout(state):
            return state

        async def branch(state):
            order.append(state["worker"])
            return {"parallel_results": {state["worker"]: state["worker"].upper()}}

        async def collect(state):
            return state

        graph = _FallbackStateGraph(_State)
        graph.add_node("fanout", fanout)
        graph.add_node("branch", branch)
        graph.add_node("collect", collect)
        graph.add_edge(langgraph_compat.START, "fanout")
        graph.add_conditional_edges(
            "fanout",
            lambda _state: [Send("branch", {"worker": name}) for name in ("a", "b")],
            ["branch", "collect"],
        )
        graph.add_edge("branch", "collect")

        result = await graph.compile().ainvoke({"parallel_results": {}})
        self.assertEqual(order, ["a", "b"], "回退引擎按计划顺序逐个执行分支")
        self.assertEqual(result["parallel_results"], {"a": "A", "b": "B"}, "分支增量未经 reducer 合并")

    async def test_empty_send_list_falls_through_to_mapped_branch(self):
        """空计划时 router 返回节点名而不是 Send 列表——回退引擎同样要认。"""
        from app.workflows import langgraph_compat
        from app.workflows.langgraph_compat import _FallbackStateGraph

        async def fanout(state):
            return state

        async def collect(state):
            return {**state, "collected": True}

        graph = _FallbackStateGraph(dict)
        graph.add_node("fanout", fanout)
        graph.add_node("branch", fanout)
        graph.add_node("collect", collect)
        graph.add_edge(langgraph_compat.START, "fanout")
        graph.add_conditional_edges("fanout", lambda _state: "collect", ["branch", "collect"])

        self.assertTrue((await graph.compile().ainvoke({}))["collected"])


if __name__ == "__main__":
    unittest.main()
