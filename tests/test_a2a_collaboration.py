"""Internal A2A cards, delegated run lineage, and redacted audit replay."""

import asyncio
import json
import unittest
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.agent.agent_api import _serialize_delegation
from app.core.database import Base
from app.core.time import utc_now
from app.models.agent import A2ADelegation, AgentAuditEvent, AgentRun
from app.models.security_auth import AuthorizationSnapshot
from app.models.user import User
from app.services.agent.a2a_service import A2ACollaborationService, A2ADelegationError
from app.services.agent.agent_service import AgentService


class A2ACollaborationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.user = User(username="a2a-user", email="a2a@example.com", hashed_password="h")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.service = A2ACollaborationService(max_delegation_depth=3)

    def tearDown(self):
        self.db.close()

    def _snapshot(self, value="a2a-snapshot"):
        snapshot = AuthorizationSnapshot(
            snapshot_id=value,
            user_id=self.user.id,
            organization_id=None,
            token_version=0,
            snapshot_hash="a" * 64,
            expires_at=utc_now() + timedelta(hours=1),
        )
        self.db.add(snapshot)
        self.db.commit()
        return snapshot.snapshot_id

    def _run(self, **overrides):
        values = {
            "user_id": self.user.id,
            "goal": "审查客户合同",
            "status": "running",
            "trace_id": "trace-a2a-001",
            "agent_type": "supervisor_agent",
            "authorization_snapshot_id": self._snapshot(f"a2a-snapshot-{uuid.uuid4().hex}"),
        }
        values.update(overrides)
        run = AgentRun(**values)
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def test_agent_card_declares_protocol_security_policy_and_surfaces(self):
        card = self.service.build_agent_card(agent_type="legal_compliance_agent", db=self.db)
        self.assertEqual(card["protocolVersion"], "0.3.0")
        self.assertIn("securitySchemes", card)
        self.assertIn("defaultInputModes", card)
        self.assertTrue(card["skills"])
        extension = card["x-aibg"]
        self.assertEqual(extension["agent_type"], "legal_compliance_agent")
        self.assertIn("legal_contract_review_tool", extension["allowed_tools"])
        self.assertIn("legal", extension["data_domains"])
        self.assertIn("contract_workbench", {item["id"] for item in extension["integration_surfaces"]})

    def test_delegation_inherits_identity_trace_org_and_snapshot(self):
        snapshot_id = self._snapshot()
        parent = self._run(organization_id=7, authorization_snapshot_id=snapshot_id)
        # Snapshot tests without an organization are valid, so give the current user the same tenant.
        self.user.organization_id = 7
        self.db.add(self.user)
        self.db.commit()

        delegation = self.service.create_delegation(
            db=self.db,
            parent_run=parent,
            user=self.user,
            to_agent_type="legal_compliance_agent",
            task="请提取违约条款并标注证据页码",
            task_type="contract_review",
            idempotency_key="contract-001",
        )
        child = self.db.query(AgentRun).filter(AgentRun.id == delegation.child_run_id).first()

        self.assertEqual(delegation.status, "accepted")
        self.assertEqual(child.parent_run_id, parent.id)
        self.assertEqual(child.agent_type, "legal_compliance_agent")
        self.assertEqual(child.user_id, parent.user_id)
        self.assertEqual(child.organization_id, parent.organization_id)
        self.assertEqual(child.trace_id, parent.trace_id)
        self.assertEqual(child.authorization_snapshot_id, parent.authorization_snapshot_id)
        self.assertEqual(child.delegation_id, delegation.delegation_id)
        serialized = _serialize_delegation(delegation).model_dump()
        self.assertTrue(serialized["authorization_snapshot_bound"])
        self.assertNotIn("authorization_snapshot_id", serialized)

    def test_idempotent_replay_returns_same_delegation_and_conflict_is_rejected(self):
        parent = self._run()
        first = self.service.create_delegation(
            db=self.db,
            parent_run=parent,
            user=self.user,
            to_agent_type="knowledge_agent",
            task="检索保密义务",
            idempotency_key="same-key",
        )
        replay = self.service.create_delegation(
            db=self.db,
            parent_run=parent,
            user=self.user,
            to_agent_type="knowledge_agent",
            task="检索保密义务",
            idempotency_key="same-key",
        )
        self.assertEqual(first.delegation_id, replay.delegation_id)
        self.assertEqual(self.db.query(A2ADelegation).count(), 1)
        with self.assertRaises(A2ADelegationError) as raised:
            self.service.create_delegation(
                db=self.db,
                parent_run=parent,
                user=self.user,
                to_agent_type="knowledge_agent",
                task="检索付款义务",
                idempotency_key="same-key",
            )
        self.assertEqual(raised.exception.code, "A2A_IDEMPOTENCY_CONFLICT")

    def test_denies_unknown_target_cross_tenant_and_over_depth(self):
        parent = self._run()
        with self.assertRaises(A2ADelegationError) as target_error:
            self.service.create_delegation(
                db=self.db,
                parent_run=parent,
                user=self.user,
                to_agent_type="unknown_agent",
                task="x",
            )
        self.assertEqual(target_error.exception.code, "A2A_TARGET_NOT_FOUND")

        foreign_parent = self._run(organization_id=99)
        with self.assertRaises(A2ADelegationError) as tenant_error:
            self.service.create_delegation(
                db=self.db,
                parent_run=foreign_parent,
                user=self.user,
                to_agent_type="knowledge_agent",
                task="x",
            )
        self.assertEqual(tenant_error.exception.code, "A2A_ORGANIZATION_MISMATCH")

        root = self._run(trace_id="depth-root")
        depth_one = self._run(trace_id="depth-root", parent_run_id=root.id, agent_type="knowledge_agent")
        shallow_service = A2ACollaborationService(max_delegation_depth=1)
        with self.assertRaises(A2ADelegationError) as depth_error:
            shallow_service.create_delegation(
                db=self.db,
                parent_run=depth_one,
                user=self.user,
                to_agent_type="legal_compliance_agent",
                task="x",
            )
        self.assertEqual(depth_error.exception.code, "A2A_MAX_DEPTH_EXCEEDED")

    def test_delegation_requires_live_authorization_snapshot(self):
        parent = self._run()
        snapshot = (
            self.db.query(AuthorizationSnapshot)
            .filter(AuthorizationSnapshot.snapshot_id == parent.authorization_snapshot_id)
            .first()
        )
        snapshot.revoked_at = utc_now()
        self.db.add(snapshot)
        self.db.commit()
        with self.assertRaises(A2ADelegationError) as raised:
            self.service.create_delegation(
                db=self.db,
                parent_run=parent,
                user=self.user,
                to_agent_type="knowledge_agent",
                task="检索保密义务",
            )
        self.assertEqual(raised.exception.code, "A2A_AUTHZ_SNAPSHOT_INVALID")

    def test_audit_replay_is_linked_and_does_not_copy_task_or_result_content(self):
        parent = self._run()
        secret_task = "客户秘密文本-secret-contract-body"
        delegation = self.service.create_delegation(
            db=self.db,
            parent_run=parent,
            user=self.user,
            to_agent_type="knowledge_agent",
            task=secret_task,
        )
        self.service.complete_delegation(
            db=self.db,
            delegation_id=delegation.delegation_id,
            succeeded=True,
            result_summary={"evidence": "secret-evidence-body", "page": 4},
        )
        delegations, events = self.service.audit_replay(db=self.db, run_id=parent.id)

        self.assertEqual([item.delegation_id for item in delegations], [delegation.delegation_id])
        self.assertEqual(
            [item.event_type for item in events if item.event_type.startswith("a2a_")],
            ["a2a_delegation_created", "a2a_delegation_accepted", "a2a_delegation_completed"],
        )
        audit_payload = "\n".join(
            (event.decision_json or "") + (event.summary_json or "") for event in self.db.query(AgentAuditEvent).all()
        )
        self.assertNotIn(secret_task, audit_payload)
        self.assertNotIn("secret-evidence-body", audit_payload)
        self.assertIn("input_hash", audit_payload)
        stored_summary = json.loads(self.db.query(A2ADelegation).first().result_summary_json)
        self.assertNotIn("secret-evidence-body", json.dumps(stored_summary))

    def test_dispatch_uses_delegated_worker_and_syncs_terminal_status(self):
        parent = self._run()
        delegation = self.service.create_delegation(
            db=self.db,
            parent_run=parent,
            user=self.user,
            to_agent_type="legal_compliance_agent",
            task="审查合同解除条款",
        )
        child = self.db.query(AgentRun).filter(AgentRun.id == delegation.child_run_id).first()

        async def complete_child(**kwargs):
            self.assertIs(kwargs["existing_run"], child)
            self.assertEqual(kwargs["forced_worker_agent"], "legal_compliance_agent")
            child.status = "completed"
            child.final_answer = "审查完成"
            self.db.add(child)
            self.db.commit()
            return child

        with patch(
            "app.services.agent.agent_service.agent_service.run",
            new=AsyncMock(side_effect=complete_child),
        ):
            synchronized = asyncio.run(self.service.dispatch_delegation(db=self.db, delegation=delegation, max_steps=3))

        self.assertEqual(synchronized.status, "completed")
        events = self.db.query(AgentAuditEvent).order_by(AgentAuditEvent.id).all()
        self.assertEqual(
            [event.event_type for event in events if event.event_type.startswith("a2a_")],
            [
                "a2a_delegation_created",
                "a2a_delegation_accepted",
                "a2a_delegation_dispatched",
                "a2a_delegation_completed",
            ],
        )

    def test_existing_child_run_executes_with_only_its_delegated_worker(self):
        parent = self._run()
        delegation = self.service.create_delegation(
            db=self.db,
            parent_run=parent,
            user=self.user,
            to_agent_type="legal_compliance_agent",
            task="审查合同解除条款",
        )
        child = self.db.query(AgentRun).filter(AgentRun.id == delegation.child_run_id).first()
        worker_service = AgentService()
        finish = json.dumps(
            {
                "thought": "已完成合同条款审查。",
                "action_type": "finish",
                "answer": "合同解除条款审查完成。",
            }
        )

        with patch(
            "app.services.agent.agent_service.llm_service.chat",
            new=AsyncMock(return_value=finish),
        ):
            result = asyncio.run(
                worker_service.run(
                    goal=child.goal,
                    user_id=self.user.id,
                    db=self.db,
                    session_id=child.session_id,
                    existing_run=child,
                    forced_worker_agent="legal_compliance_agent",
                )
            )

        self.assertEqual(result.id, child.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.agent_type, "legal_compliance_agent")
        workflow_state = json.loads(result.workflow_state)
        self.assertEqual(workflow_state["worker_plan"], ["legal_compliance_agent"])
        synchronized = self.db.query(A2ADelegation).filter(A2ADelegation.id == delegation.id).first()
        self.assertEqual(synchronized.status, "completed")

    def test_awaiting_approval_child_is_projected_before_terminal_sync(self):
        parent = self._run()
        delegation = self.service.create_delegation(
            db=self.db,
            parent_run=parent,
            user=self.user,
            to_agent_type="workflow_agent",
            task="创建合同续签跟进任务",
        )
        child = self.db.query(AgentRun).filter(AgentRun.id == delegation.child_run_id).first()
        child.status = "awaiting_approval"
        self.db.add(child)
        self.db.commit()

        waiting = self.service.sync_from_child_run(db=self.db, child_run=child)
        self.assertEqual(waiting.status, "awaiting_approval")

        child.status = "completed"
        child.final_answer = "审批后任务创建完成"
        self.db.add(child)
        self.db.commit()
        completed = self.service.sync_from_child_run(db=self.db, child_run=child)
        self.assertEqual(completed.status, "completed")
        events = self.db.query(AgentAuditEvent).order_by(AgentAuditEvent.id).all()
        self.assertIn("a2a_delegation_awaiting_approval", [event.event_type for event in events])


if __name__ == "__main__":
    unittest.main()
