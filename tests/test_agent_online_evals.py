import asyncio
import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import User
from app.services.agent.agent_observability import (
    observe_tool_call,
    record_state_transition,
    record_tool_outcome,
)
from app.services.agent.online_eval_service import (
    APPROVED,
    EXPORTED,
    PENDING_REVIEW,
    online_eval_service,
)
from eval.agent_online_eval import evaluate_outcome, regression_gate, run_cases


class OnlineEvalCandidateTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.user = User(username="eval-admin", email="eval-admin@example.com", hashed_password="hash")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()

    def test_failure_capture_is_redacted_and_deduplicated(self):
        kwargs = {
            "run_id": 7,
            "trace_id": "trace-7",
            "user_id": self.user.id,
            "organization_id": 3,
            "failure_type": "tool_document_search_tool_timeout",
            "goal": "sensitive legal question",
            "evidence": {"error_category": "timeout", "token": "must-not-persist"},
        }
        first = online_eval_service.capture_failure(self.db, **kwargs)
        second = online_eval_service.capture_failure(self.db, **kwargs)

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.status, PENDING_REVIEW)
        self.assertNotIn("sensitive legal question", first.goal_hash)
        self.assertEqual(json.loads(first.evidence_json)["token"], "****redacted****")

    def test_only_reviewed_candidates_are_exported_as_executable_cases(self):
        candidate = online_eval_service.capture_failure(
            self.db,
            run_id=8,
            trace_id="trace-8",
            user_id=self.user.id,
            organization_id=None,
            failure_type="run_error",
            goal="original production content is not copied",
            evidence={"status": "error"},
        )
        empty = online_eval_service.export_approved(self.db)
        self.assertEqual(empty["cases"], [])

        approved = online_eval_service.approve(
            self.db,
            candidate_id=candidate.id,
            reviewer_id=self.user.id,
            evaluation_input={"goal": "reviewed regression scenario", "max_steps": 2},
            expected_outcome={"terminal_status": "completed", "required_tool_names": ["document_search_tool"]},
            review_note="Reviewed by legal QA",
        )
        self.assertEqual(approved.status, APPROVED)

        exported = online_eval_service.export_approved(self.db)
        self.assertEqual(exported["cases"][0]["input"]["goal"], "reviewed regression scenario")
        self.assertEqual(approved.status, EXPORTED)
        self.assertEqual(len(online_eval_service.export_approved(self.db)["cases"]), 1)


class AgentTraceSemanticsTests(unittest.TestCase):
    def test_tool_and_state_spans_are_structured_without_payload_content(self):
        spans = []

        class Span:
            def __init__(self):
                self.attributes = {}

            def set_attribute(self, key, value):
                self.attributes[key] = value

        @contextmanager
        def fake_observe(name, attributes):
            span = Span()
            spans.append((name, attributes, span))
            yield span

        with patch("app.services.agent.agent_observability.observe_span", fake_observe):
            with observe_tool_call(
                run_id=1, trace_id="trace-1", step=2, tool_name="document_search_tool",
                agent_type="knowledge_agent", read_only=True,
            ) as span:
                record_tool_outcome(span, success=False, status="error", duration_ms=12, error_category="timeout")
            record_state_transition(run_id=1, trace_id="trace-1", from_status="running", to_status="error")

        self.assertEqual([item[0] for item in spans], ["agent.tool_call", "agent.state_transition"])
        self.assertEqual(spans[0][1]["agent.tool.name"], "document_search_tool")
        self.assertNotIn("input", spans[0][1])
        self.assertEqual(spans[0][2].attributes["agent.error.category"], "timeout")


class AgentOnlineRegressionGateTests(unittest.TestCase):
    def test_gate_blocks_unexpected_terminal_status_or_required_tool(self):
        passed = evaluate_outcome(
            {"status": "completed", "tool_names": ["document_search_tool"], "failure_categories": []},
            {"terminal_status": "completed", "required_tool_names": ["document_search_tool"]},
        )
        failed = evaluate_outcome(
            {"status": "error", "tool_names": [], "failure_categories": ["timeout"]},
            {"terminal_status": "completed", "required_tool_names": ["document_search_tool"]},
        )
        gate = regression_gate([passed, failed])

        self.assertTrue(passed["passed"])
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["failure_count"], 1)

    def test_async_runner_is_gated(self):
        async def fake_run(case_input):
            return {"status": "completed", "tool_names": ["document_search_tool"], "failure_categories": []}

        result = asyncio.run(run_cases(
            [{
                "id": "case-1",
                "input": {"goal": "reviewed"},
                "expected": {"terminal_status": "completed", "required_tool_names": ["document_search_tool"]},
            }],
            fake_run,
        ))
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
