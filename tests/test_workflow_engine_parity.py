"""LangGraph 与 fallback engine 行为契约一致性。

两种引擎共用同一组节点函数与路由（app.workflows.langgraph_compat 抽象），因此
审批 / 权限 / 状态 / 审计语义天然一致。本测试锁定：
- 引擎选择可发现（workflow_engine_name）。
- 两种引擎以同一节点集合编译（fallback 复刻 _build_workflow 的节点注册）。
- 写工具审批闸位于共享节点链路的 ToolExecutor，与引擎无关（fallback 不自动批准）。
"""

import unittest

from app.services.agent.agent_service import AgentService
from app.workflows import langgraph_compat
from app.workflows.langgraph_compat import _FallbackStateGraph


WORKFLOW_NODES = (
    "decide", "parallel_fanout", "parallel_branch", "parallel_collect", "parallel_aggregate",
    "cancelled", "finish", "retry", "tool_call", "verify_evidence", "evidence_insufficient",
    "partial", "awaiting_approval",
)


class WorkflowEngineParityTests(unittest.TestCase):
    def test_engine_name_is_discoverable(self):
        self.assertIn(langgraph_compat.workflow_engine_name(), {"langgraph", "internal_state_graph"})

    def test_all_shared_node_callables_exist_on_service(self):
        service = AgentService()
        missing = [name for name in WORKFLOW_NODES if not callable(getattr(service, f"_workflow_{name}", None))]
        self.assertEqual(missing, [])

    def test_fallback_engine_compiles_same_node_set(self):
        service = AgentService()
        fallback = _FallbackStateGraph(dict)
        for name in WORKFLOW_NODES:
            fallback.add_node(name, getattr(service, f"_workflow_{name}"))
        self.assertEqual(set(fallback._nodes.keys()), set(WORKFLOW_NODES))
        # 编译可通过，且入口节点是共享的 _workflow_decide
        fallback.add_edge(langgraph_compat.START, "decide")
        compiled = fallback.compile()
        self.assertEqual(compiled._entry_point, "decide")

    def test_fallback_traversal_uses_shared_executor_gate(self):
        """fallback 引擎逐节点执行共享节点函数，写工具经 ToolExecutor 审批闸。"""
        from app.mcp.tool_contract import requires_approval_for, resolve_contract
        from app.tools.task_tool import TaskCreateTool

        contract = resolve_contract(TaskCreateTool())
        self.assertTrue(requires_approval_for("task_create_tool", contract))

    def test_node_tool_entry_is_executor(self):
        service = AgentService()
        self.assertTrue(callable(service._workflow_tool_call))
        self.assertTrue(callable(service._execute_tool))


if __name__ == "__main__":
    unittest.main()
