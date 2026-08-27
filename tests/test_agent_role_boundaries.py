import unittest
from unittest.mock import AsyncMock, patch

from app.mcp.permissions import agent_allows_tool, canonical_agent_type
from app.services.agent.agent_prompts import POLICY_GUARDRAIL_ROLE, SUB_AGENTS
from app.services.agent.agent_registry import AGENT_REGISTRY_VERSION, TASK_PROTOCOL_VERSION, list_agent_registrations
from app.services.agent.agent_service import AgentService


class AgentRoleBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = AgentService()

    def test_expert_agents_are_runtime_planning_roles(self):
        self.assertEqual(
            set(SUB_AGENTS),
            {
                "knowledge_agent",
                "legal_compliance_agent",
                "workflow_agent",
            },
        )
        self.assertEqual(POLICY_GUARDRAIL_ROLE, "policy_guardrail")

    def test_agent_registry_exposes_canonical_contracts_and_live_acl(self):
        registrations = list_agent_registrations()
        self.assertEqual(AGENT_REGISTRY_VERSION, "enterprise_experts_v1")
        self.assertEqual(TASK_PROTOCOL_VERSION, "agent_task_v1")
        self.assertEqual(
            [item["agent_type"] for item in registrations],
            ["knowledge_agent", "legal_compliance_agent", "workflow_agent"],
        )
        legal = registrations[1]
        self.assertIn("legal_consultation_tool", legal["allowed_tools"])
        self.assertNotIn("task_create_tool", legal["allowed_tools"])

    def test_agent_tool_boundaries_are_enforced(self):
        self.assertTrue(agent_allows_tool("knowledge_agent", "document_search_tool"))
        self.assertFalse(agent_allows_tool("knowledge_agent", "task_create_tool"))
        self.assertTrue(agent_allows_tool("legal_compliance_agent", "document_risk_tool"))
        self.assertTrue(agent_allows_tool("legal_compliance_agent", "legal_contract_review_tool"))
        self.assertFalse(agent_allows_tool("legal_compliance_agent", "task_create_tool"))
        self.assertTrue(agent_allows_tool("workflow_agent", "task_create_tool"))
        self.assertTrue(agent_allows_tool("workflow_agent", "task_query_tool"))
        self.assertFalse(agent_allows_tool("workflow_agent", "document_risk_tool"))

    def test_legacy_worker_names_normalize_to_current_roles(self):
        self.assertEqual(canonical_agent_type("document_agent"), "knowledge_agent")
        self.assertEqual(canonical_agent_type("task_agent"), "workflow_agent")
        plan, error = self.service._validate_supervisor_plan(
            {
                "intent": "审查合同并生成跟进任务",
                "workers": ["legal_compliance_agent", "workflow_agent"],
                "dependencies": [{"from": "legal_compliance_agent", "to": "workflow_agent"}],
                "risk_level": "medium",
                "expected_artifacts": ["document", "task"],
            }
        )
        self.assertIsNone(error)
        self.assertEqual(plan["workers"], ["legal_compliance_agent", "workflow_agent"])
        self.assertEqual(
            plan["dependencies"],
            [{"from": "legal_compliance_agent", "to": "workflow_agent"}],
        )

    def test_domain_plans_keep_analysis_before_workflow_actions(self):
        self.assertEqual(
            self.service._build_supervisor_worker_plan("审查这份合同并提示风险条款"),
            ["legal_compliance_agent"],
        )
        self.assertEqual(
            self.service._build_supervisor_worker_plan("总结文档 1 并提取风险，再创建跟进任务"),
            ["knowledge_agent", "workflow_agent"],
        )
        self.assertEqual(
            self.service._build_supervisor_worker_plan("查询我未完成的任务"),
            ["workflow_agent"],
        )

    async def test_single_domain_request_uses_direct_route_without_supervisor_llm(self):
        generate = AsyncMock(side_effect=AssertionError("Supervisor LLM must not run for a single domain"))
        with patch("app.services.agent.agent_service.llm_service.generate", new=generate):
            plan = await self.service._plan_with_supervisor("查询合同 12 的交付日期", user_id=1)
        generate.assert_not_awaited()
        self.assertEqual(plan["workers"], ["legal_compliance_agent"])
        self.assertEqual(plan["plan_source"], "deterministic_direct_route")


if __name__ == "__main__":
    unittest.main()
