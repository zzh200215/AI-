import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.mcp.permission_guard import DECISION_KIND_POLICY, permission_guard
from app.mcp.policy import DEFAULT_POLICY_DOCUMENT, policy_engine
from app.models.agent import AgentAuditEvent, AgentRun
from app.models.user import User
from app.services.agent.mcp_policy_service import mcp_policy_service


class MCPPolicyTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:", future=True,
            connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.user = User(username="policy-admin", email="policy-admin@example.com", hashed_password="hash")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()

    def test_policy_decision_contains_identity_scope_risk_and_version(self):
        decision = permission_guard.check_tool_execution(
            agent_type="workflow_agent",
            tool_name="task_create_tool",
            db=self.db,
            agent_run_id=None,
            user_id=self.user.id,
            organization_id=10,
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.requires_approval)
        self.assertEqual(decision.decision_kind, DECISION_KIND_POLICY)
        self.assertEqual(decision.policy_version, "mcp_policy_v1")
        self.assertEqual(decision.data_scope, "tasks")
        self.assertEqual(decision.risk_level, "high")

    def test_dynamic_scope_and_risk_threshold_are_fail_closed(self):
        scope = policy_engine.evaluate(
            agent_type="workflow_agent", tool_name="task_create_tool",
            db=self.db, context={"data_scopes": ["documents"]},
        )
        risk = policy_engine.evaluate(
            agent_type="workflow_agent", tool_name="task_create_tool",
            db=self.db, context={"risk_threshold": "medium"},
        )
        self.assertFalse(scope.allowed)
        self.assertEqual(scope.error_code, "MCP_DATA_SCOPE_DENIED")
        self.assertFalse(risk.allowed)
        self.assertEqual(risk.error_code, "MCP_RISK_THRESHOLD_EXCEEDED")

    def test_draft_activation_is_versioned_and_runtime_reads_active_policy(self):
        document = json.loads(json.dumps(DEFAULT_POLICY_DOCUMENT))
        document["version"] = "mcp_policy_test_v2"
        document["rules"] = [rule for rule in document["rules"] if rule["tool"] != "task_create_tool"]
        draft = mcp_policy_service.save_draft(self.db, document=document, actor_id=self.user.id)
        self.assertEqual(draft.status, "draft")
        mcp_policy_service.activate(self.db, version=draft.version, actor_id=self.user.id)

        denied = policy_engine.evaluate(agent_type="workflow_agent", tool_name="task_create_tool", db=self.db)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.error_code, "MCP_POLICY_DENIED")
        self.assertEqual(denied.policy_version, "mcp_policy_test_v2")

    def test_audit_replay_detects_policy_version_drift(self):
        run = AgentRun(user_id=self.user.id, goal="g", status="running", trace_id="trace-policy")
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        event = AgentAuditEvent(
            run_id=run.id, trace_id=run.trace_id, event_type="permission_decision",
            tool_name="task_query_tool",
            decision_json=json.dumps({
                "allowed": True,
                "agent_type": "workflow_agent",
                "tool_name": "task_query_tool",
                "policy_version": "mcp_policy_old",
            }),
        )
        self.db.add(event)
        self.db.commit()

        replay = mcp_policy_service.replay_run(self.db, run_id=run.id)
        self.assertEqual(replay["decision_count"], 1)
        self.assertEqual(replay["drift_count"], 1)
        self.assertTrue(replay["decisions"][0]["drifted"])


if __name__ == "__main__":
    unittest.main()
