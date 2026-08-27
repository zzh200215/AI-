import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.llm_client import ModelGateway
from app.core.llm_client import settings as gateway_settings
from app.core.model_policy import ModelRequest
from app.models.llm_call_log import LLMCallLog
from app.models.token_usage import TokenUsage
from app.services.llm.model_release_service import (
    ModelReleaseDecision,
    RoutingSignals,
    model_release_service,
)
from app.services.llm.model_release_service import (
    settings as release_settings,
)

PRICING = (
    '{"qwen-plus":{"input_per_1k":0.004,"output_per_1k":0.012},'
    '"qwen-turbo":{"input_per_1k":0.001,"output_per_1k":0.003},'
    '"qwen-max":{"input_per_1k":0.008,"output_per_1k":0.024}}'
)


class ModelReleaseServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.setting_patches = [
            patch.object(release_settings, "LLM_MODEL_RELEASES_ENABLED", True),
            patch.object(release_settings, "LLM_SIMPLE_REQUEST_MAX_CHARS", 600),
            patch.object(release_settings, "LLM_REQUEST_TIMEOUT_SECONDS", 60),
            patch.object(release_settings, "LLM_MODEL_PRICING", PRICING),
        ]
        for item in self.setting_patches:
            item.start()

    def tearDown(self):
        self.db.close()
        for item in reversed(self.setting_patches):
            item.stop()

    def _spec(self, **overrides):
        spec = {
            "version": "qwen-max-2026-08-26",
            "action": "email_generate",
            "candidate_model": "qwen-max",
            "max_complexity": "complex",
            "max_risk_level": "high",
            "expected_latency_ms": 30000,
            "max_cost_ratio": 2.0,
            "rollout_percentage": 50,
            "shadow_percentage": 100,
            "min_sample_size": 3,
            "max_error_rate": 0.2,
            "max_p95_latency_ms": 1000,
            "evaluation_gate": {
                "report_ref": "eval/reports/qwen-max-v3.json",
                "baseline_score": 0.9,
                "candidate_score": 0.91,
                "max_regression": 0.02,
            },
        }
        spec.update(overrides)
        return spec

    def test_visible_rollout_requires_passed_offline_evaluation(self):
        release = model_release_service.create_draft(
            self.db,
            spec=self._spec(evaluation_gate=None),
            actor_id=None,
        )
        with self.assertRaisesRegex(ValueError, "offline evaluation"):
            model_release_service.activate(self.db, version=release.version, actor_id=None)

    def test_assignment_is_stable_and_respects_risk_latency_and_cost(self):
        release = model_release_service.create_draft(self.db, spec=self._spec(), actor_id=None)
        model_release_service.activate(self.db, version=release.version, actor_id=None)

        kwargs = {
            "action": "email_generate",
            "source_text": "请生成一封简短的项目进度邮件",
            "data_level": "internal",
            "latency_budget_ms": 60000,
            "user_id": 42,
            "request_id": "request-42",
            "baseline_model": "qwen-plus",
            "estimated_input_tokens": 1000,
            "estimated_output_tokens": 1000,
            "db": self.db,
        }
        first = model_release_service.resolve(**kwargs)
        second = model_release_service.resolve(**kwargs)

        self.assertIsNotNone(first)
        self.assertEqual(first.bucket, second.bucket)
        self.assertEqual(first.serve_candidate, second.serve_candidate)
        self.assertTrue(first.serve_candidate or first.shadow_candidate)
        self.assertTrue(first.eligible)

        restricted = model_release_service.create_draft(
            self.db,
            spec=self._spec(
                version="qwen-max-legal-limited",
                action="legal_consultation",
                max_risk_level="medium",
                rollout_percentage=100,
            ),
            actor_id=None,
        )
        model_release_service.activate(self.db, version=restricted.version, actor_id=None)
        decision = model_release_service.resolve(
            **{
                **kwargs,
                "action": "legal_consultation",
                "source_text": "请分析合同解除与违约责任",
                "request_id": "legal-request",
            }
        )
        self.assertFalse(decision.eligible)
        self.assertFalse(decision.serve_candidate)
        self.assertIn("risk_exceeds_candidate", decision.reason)

    def test_guardrail_rolls_back_active_release_after_error_budget_breach(self):
        release = model_release_service.create_draft(
            self.db,
            spec=self._spec(rollout_percentage=100),
            actor_id=None,
        )
        model_release_service.activate(self.db, version=release.version, actor_id=None)
        self.db.add_all(
            [
                LLMCallLog(
                    module_name="email",
                    action="email_generate",
                    model_name="qwen-max",
                    status=status,
                    input_tokens=100,
                    output_tokens=100,
                    duration_ms=duration,
                    model_release_version=release.version,
                    traffic_type="serving",
                )
                for status, duration in (("success", 500), ("error", 600), ("error", 700))
            ]
        )
        self.db.commit()

        result = model_release_service.assess_guardrail(self.db, version=release.version, auto_rollback=True)
        self.db.refresh(release)

        self.assertEqual(result["status"], "breached")
        self.assertIn("error_rate_exceeded", result["breaches"])
        self.assertTrue(result["auto_rolled_back"])
        self.assertEqual(release.status, "rolled_back")

    def test_rollback_restores_previous_release_for_the_same_action(self):
        first = model_release_service.create_draft(
            self.db,
            spec=self._spec(version="qwen-max-v1", rollout_percentage=100),
            actor_id=None,
        )
        model_release_service.activate(self.db, version=first.version, actor_id=None)
        second = model_release_service.create_draft(
            self.db,
            spec=self._spec(version="qwen-max-v2", rollout_percentage=100),
            actor_id=None,
        )
        model_release_service.activate(self.db, version=second.version, actor_id=None)
        self.db.refresh(first)
        self.assertEqual(first.status, "superseded")

        model_release_service.rollback(self.db, version=second.version, actor_id=None)
        self.db.refresh(first)
        self.db.refresh(second)

        self.assertEqual(second.status, "rolled_back")
        self.assertEqual(first.status, "active")


class ModelGatewayReleaseRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setting_patches = [
            patch.object(gateway_settings, "LLM_PROVIDER", "openai_compatible"),
            patch.object(gateway_settings, "LLM_API_BASE_URL", "https://primary.example/v1"),
            patch.object(gateway_settings, "LLM_API_KEY", "primary-key"),
            patch.object(gateway_settings, "LLM_MODEL", "qwen-plus"),
            patch.object(gateway_settings, "LLM_MODEL_ROUTING_ENABLED", True),
            patch.object(gateway_settings, "LLM_SMALL_MODEL", "qwen-turbo"),
            patch.object(gateway_settings, "LLM_SMALL_MODEL_PROVIDER", "openai_compatible"),
            patch.object(gateway_settings, "LLM_SMALL_MODEL_API_BASE_URL", "https://small.example/v1"),
            patch.object(gateway_settings, "LLM_SMALL_MODEL_API_KEY", "small-key"),
            patch.object(gateway_settings, "LLM_CANARY_API_KEY", "canary-key"),
            patch.object(gateway_settings, "LLM_SIMPLE_REQUEST_MAX_CHARS", 600),
            patch.object(gateway_settings, "LLM_PRIMARY_REQUEST_RETRIES", 1),
            patch.object(gateway_settings, "LLM_FALLBACK_REQUEST_RETRIES", 1),
            patch.object(gateway_settings, "LLM_MODEL_FALLBACK_ENABLED", True),
            patch.object(gateway_settings, "LLM_SMALL_MODEL_FALLBACK_TO_PRIMARY", True),
        ]
        for item in self.setting_patches:
            item.start()
        self.gateway = ModelGateway()

    def tearDown(self):
        for item in reversed(self.setting_patches):
            item.stop()

    @staticmethod
    def _decision(*, serve: bool, shadow: bool) -> ModelReleaseDecision:
        return ModelReleaseDecision(
            release_version="qwen-max-v3",
            candidate_model="qwen-max",
            candidate_provider="openai_compatible",
            candidate_base_url="https://canary.example/v1",
            bucket=17,
            serve_candidate=serve,
            shadow_candidate=shadow,
            eligible=True,
            reason="complexity=simple;risk=low;ab_candidate"
            if serve
            else "complexity=simple;risk=low;shadow_candidate",
            signals=RoutingSignals(complexity="simple", risk_level="low", latency_budget_ms=60000),
        )

    def _request(self) -> ModelRequest:
        return ModelRequest(
            request_type="generate",
            prompt="生成一封短邮件",
            action="email_generate",
            user_id=7,
            request_id="request-0001",
            trace_id="request-0001",
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            data_level="internal",
        )

    async def test_ab_candidate_is_served_through_canary_target(self):
        request = self._request()
        with (
            patch(
                "app.core.llm_client.model_release_service.resolve",
                return_value=self._decision(serve=True, shadow=False),
            ),
            patch.object(self.gateway, "_request_text_once", new=AsyncMock(return_value="candidate response")) as call,
        ):
            result = await self.gateway._request_text_with_routing(source_text=request.prompt or "", request=request)

        self.assertEqual(result, "candidate response")
        self.assertEqual(call.await_count, 1)
        self.assertEqual(call.call_args.kwargs["target"].role, "canary")
        self.assertEqual(call.call_args.kwargs["target"].api_key, "canary-key")

    async def test_shadow_request_is_not_billable_and_does_not_delay_serving_response(self):
        request = self._request()
        with (
            patch(
                "app.core.llm_client.model_release_service.resolve",
                return_value=self._decision(serve=False, shadow=True),
            ),
            patch.object(self.gateway, "_request_text_once", new=AsyncMock(return_value="response")) as call,
        ):
            result = await self.gateway._request_text_with_routing(source_text=request.prompt or "", request=request)
            await asyncio.sleep(0)

        self.assertEqual(result, "response")
        self.assertEqual(call.await_count, 2)
        self.assertEqual(call.call_args_list[0].kwargs["traffic_type"], "serving")
        self.assertEqual(call.call_args_list[1].kwargs["traffic_type"], "shadow")
        self.assertFalse(call.call_args_list[1].kwargs["billable"])
        self.assertEqual(call.call_args_list[1].kwargs["target"].role, "canary")

    async def test_shadow_audit_does_not_create_token_usage(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        with patch("app.core.database.SessionLocal", testing_session):
            self.gateway._record_usage(
                {},
                "qwen-max",
                "email_generate",
                12,
                7,
                prompt_template="test",
                request_id="shadow-request",
                traffic_type="shadow",
                model_release_version="qwen-max-v3",
                billable=False,
            )

        db = testing_session()
        try:
            self.assertEqual(db.query(TokenUsage).count(), 0)
            row = db.query(LLMCallLog).one()
        finally:
            db.close()
        self.assertEqual(row.traffic_type, "shadow")
        self.assertEqual(row.model_release_version, "qwen-max-v3")


if __name__ == "__main__":
    unittest.main()
