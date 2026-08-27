"""Versioned model rollout control plane and deterministic request routing signals."""

from __future__ import annotations

import hashlib
import json
import math
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.time import utc_now
from app.models.llm_call_log import LLMCallLog
from app.models.model_release import ModelRelease

settings = get_settings()

_COMPLEXITY_ORDER = {"simple": 0, "medium": 1, "complex": 2}
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_HIGH_RISK_ACTION_PREFIXES = ("legal_", "agent_", "agentic_", "rag_", "text_to_sql")
_COMPLEX_MARKERS = (
    "合同", "法律", "仲裁", "诉讼", "风险", "对比", "比较", "差异", "条款", "流程",
    "分析", "总结", "方案", "证据", "责任", "条件", "金额", "日期",
)


@dataclass(frozen=True)
class RoutingSignals:
    complexity: str
    risk_level: str
    latency_budget_ms: int


@dataclass(frozen=True)
class ModelReleaseDecision:
    release_version: str
    candidate_model: str
    candidate_provider: str | None
    candidate_base_url: str | None
    bucket: int
    serve_candidate: bool
    shadow_candidate: bool
    eligible: bool
    reason: str
    signals: RoutingSignals


def _rank(value: str, order: dict[str, int]) -> int:
    return order.get(str(value or "").lower(), -1)


class ModelReleaseService:
    def classify_request(
        self,
        *,
        action: str,
        source_text: str,
        data_level: str | None,
        latency_budget_ms: int,
    ) -> RoutingSignals:
        normalized_action = (action or "").lower()
        text = source_text or ""
        normalized_level = (data_level or "").lower()
        if normalized_action.startswith(_HIGH_RISK_ACTION_PREFIXES) or normalized_level in {"sensitive", "highly_sensitive"}:
            risk_level = "high" if normalized_level != "highly_sensitive" else "critical"
        elif normalized_action.startswith("document_") or any(marker in text for marker in _COMPLEX_MARKERS):
            risk_level = "medium"
        else:
            risk_level = "low"

        if normalized_action.startswith(_HIGH_RISK_ACTION_PREFIXES) or len(text) > settings.LLM_SIMPLE_REQUEST_MAX_CHARS or any(
            marker in text for marker in _COMPLEX_MARKERS
        ):
            complexity = "complex"
        elif len(text) > max(120, settings.LLM_SIMPLE_REQUEST_MAX_CHARS // 2):
            complexity = "medium"
        else:
            complexity = "simple"
        return RoutingSignals(
            complexity=complexity,
            risk_level=risk_level,
            latency_budget_ms=max(1, int(latency_budget_ms or settings.LLM_REQUEST_TIMEOUT_SECONDS * 1000)),
        )

    @staticmethod
    def _stable_bucket(*, version: str, action: str, user_id: int | None, request_id: str) -> int:
        identity = str(user_id) if user_id is not None else str(request_id or "anonymous")
        digest = hashlib.sha256(f"{version}:{action}:{identity}".encode()).hexdigest()
        return int(digest[:8], 16) % 100

    @staticmethod
    def _parse_pricing() -> dict[str, dict[str, float]]:
        try:
            payload = json.loads(settings.LLM_MODEL_PRICING)
        except (TypeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        result: dict[str, dict[str, float]] = {}
        for model, value in payload.items():
            if not isinstance(value, dict):
                continue
            try:
                result[str(model)] = {
                    "input_per_1k": float(value.get("input_per_1k") or 0),
                    "output_per_1k": float(value.get("output_per_1k") or 0),
                }
            except (TypeError, ValueError):
                continue
        return result

    def _estimated_cost(self, *, model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
        price = self._parse_pricing().get(model)
        if not price:
            return None
        return (
            max(0, int(input_tokens or 0)) / 1000 * price["input_per_1k"]
            + max(0, int(output_tokens or 0)) / 1000 * price["output_per_1k"]
        )

    @staticmethod
    def _normalise_gate(gate: dict[str, Any] | None) -> tuple[str, str | None]:
        if not gate:
            return "pending", None
        if not isinstance(gate, dict):
            raise ValueError("evaluation_gate must be an object")
        report_ref = str(gate.get("report_ref") or "").strip()
        if not report_ref:
            raise ValueError("evaluation_gate.report_ref is required")
        try:
            baseline_score = float(gate["baseline_score"])
            candidate_score = float(gate["candidate_score"])
            max_regression = float(gate.get("max_regression", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("evaluation_gate requires numeric baseline_score, candidate_score and max_regression") from exc
        if not 0 <= max_regression <= 1:
            raise ValueError("evaluation_gate.max_regression must be between 0 and 1")
        passed = candidate_score >= baseline_score - max_regression
        payload = {
            "report_ref": report_ref,
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "max_regression": max_regression,
            "passed": passed,
        }
        return ("passed" if passed else "failed"), json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _validate_spec(self, spec: dict[str, Any]) -> None:
        version = str(spec.get("version") or "").strip()
        candidate_model = str(spec.get("candidate_model") or "").strip()
        action = str(spec.get("action") or "*").strip()
        if not version or not candidate_model or not action:
            raise ValueError("version, action and candidate_model are required")
        if _rank(spec.get("max_complexity", "complex"), _COMPLEXITY_ORDER) < 0:
            raise ValueError("max_complexity must be simple, medium or complex")
        if _rank(spec.get("max_risk_level", "medium"), _RISK_ORDER) < 0:
            raise ValueError("max_risk_level must be low, medium, high or critical")
        for field in ("rollout_percentage", "shadow_percentage"):
            value = int(spec.get(field, 0) or 0)
            if not 0 <= value <= 100:
                raise ValueError(f"{field} must be between 0 and 100")
        if int(spec.get("min_sample_size", 100) or 0) < 1:
            raise ValueError("min_sample_size must be positive")
        if not 0 <= float(spec.get("max_error_rate", 0.05)) <= 1:
            raise ValueError("max_error_rate must be between 0 and 1")
        if float(spec.get("max_cost_ratio", 1.0)) <= 0:
            raise ValueError("max_cost_ratio must be positive")
        for field in ("expected_latency_ms", "max_p95_latency_ms"):
            value = spec.get(field)
            if value is not None and int(value) < 1:
                raise ValueError(f"{field} must be positive when provided")

    def create_draft(self, db: Session, *, spec: dict[str, Any], actor_id: int | None) -> ModelRelease:
        self._validate_spec(spec)
        if db.query(ModelRelease).filter(ModelRelease.version == str(spec["version"]).strip()).first():
            raise ValueError("Model release version already exists")
        gate_status, gate_json = self._normalise_gate(spec.get("evaluation_gate"))
        row = ModelRelease(
            version=str(spec["version"]).strip(),
            action=str(spec.get("action") or "*").strip(),
            candidate_model=str(spec["candidate_model"]).strip(),
            candidate_provider=str(spec.get("candidate_provider") or "").strip() or None,
            candidate_base_url=str(spec.get("candidate_base_url") or "").strip() or None,
            max_complexity=str(spec.get("max_complexity") or "complex").lower(),
            max_risk_level=str(spec.get("max_risk_level") or "medium").lower(),
            expected_latency_ms=spec.get("expected_latency_ms"),
            max_cost_ratio=float(spec.get("max_cost_ratio", 1.0)),
            rollout_percentage=int(spec.get("rollout_percentage", 0)),
            shadow_percentage=int(spec.get("shadow_percentage", 0)),
            min_sample_size=int(spec.get("min_sample_size", 100)),
            max_error_rate=float(spec.get("max_error_rate", 0.05)),
            max_p95_latency_ms=spec.get("max_p95_latency_ms"),
            evaluation_status=gate_status,
            evaluation_gate_json=gate_json,
            status="draft",
            created_by=actor_id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def record_evaluation_gate(
        self, db: Session, *, version: str, gate: dict[str, Any], actor_id: int | None
    ) -> ModelRelease:
        row = self.get_by_version(db, version)
        if not row:
            raise ValueError("Model release not found")
        if row.status == "active":
            raise ValueError("Cannot change evaluation gate while release is active")
        status, payload = self._normalise_gate(gate)
        row.evaluation_status = status
        row.evaluation_gate_json = payload
        row.created_by = actor_id or row.created_by
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def activate(self, db: Session, *, version: str, actor_id: int | None) -> ModelRelease:
        row = self.get_by_version(db, version)
        if not row:
            raise ValueError("Model release not found")
        if row.status not in {"draft", "rolled_back"}:
            raise ValueError("Only draft or rolled-back releases can be activated")
        if row.rollout_percentage > 0 and row.evaluation_status != "passed":
            raise ValueError("A passed offline evaluation gate is required before serving traffic")
        active_rows = (
            db.query(ModelRelease)
            .filter(ModelRelease.action == row.action, ModelRelease.status == "active", ModelRelease.id != row.id)
            .all()
        )
        previous_release = active_rows[0] if active_rows else None
        for active in active_rows:
            active.status = "superseded"
            active.rolled_back_at = utc_now()
            active.rolled_back_by = actor_id
            db.add(active)
        row.status = "active"
        row.previous_release_id = previous_release.id if previous_release else None
        row.activated_at = utc_now()
        row.activated_by = actor_id
        row.rolled_back_at = None
        row.rolled_back_by = None
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def rollback(self, db: Session, *, version: str, actor_id: int | None) -> ModelRelease:
        row = self.get_by_version(db, version)
        if not row:
            raise ValueError("Model release not found")
        if row.status != "active":
            raise ValueError("Only an active model release can be rolled back")
        row.status = "rolled_back"
        row.rolled_back_at = utc_now()
        row.rolled_back_by = actor_id
        db.add(row)
        if row.previous_release_id:
            previous = db.query(ModelRelease).filter(ModelRelease.id == row.previous_release_id).first()
            if previous and previous.status == "superseded":
                previous.status = "active"
                previous.activated_at = utc_now()
                previous.activated_by = actor_id
                previous.rolled_back_at = None
                previous.rolled_back_by = None
                db.add(previous)
        db.commit()
        db.refresh(row)
        return row

    def get_by_version(self, db: Session, version: str) -> ModelRelease | None:
        return db.query(ModelRelease).filter(ModelRelease.version == version).first()

    def list_releases(self, db: Session, *, limit: int = 100) -> list[ModelRelease]:
        return db.query(ModelRelease).order_by(ModelRelease.created_at.desc(), ModelRelease.id.desc()).limit(limit).all()

    def _resolve_active_release(self, db: Session, action: str) -> ModelRelease | None:
        rows = (
            db.query(ModelRelease)
            .filter(ModelRelease.status == "active", ModelRelease.action.in_([action, "*"]))
            .order_by(ModelRelease.activated_at.desc(), ModelRelease.id.desc())
            .all()
        )
        if not rows:
            return None
        return next((row for row in rows if row.action == action), rows[0])

    def resolve(
        self,
        *,
        action: str,
        source_text: str,
        data_level: str | None,
        latency_budget_ms: int,
        user_id: int | None,
        request_id: str,
        baseline_model: str,
        estimated_input_tokens: int | None,
        estimated_output_tokens: int | None,
        db: Session | None = None,
    ) -> ModelReleaseDecision | None:
        if not settings.LLM_MODEL_RELEASES_ENABLED:
            return None
        owns_session = db is None
        db = db or SessionLocal()
        try:
            release = self._resolve_active_release(db, action)
            if not release:
                return None
            signals = self.classify_request(
                action=action,
                source_text=source_text,
                data_level=data_level,
                latency_budget_ms=latency_budget_ms,
            )
            reasons = [f"complexity={signals.complexity}", f"risk={signals.risk_level}"]
            eligible = True
            if _rank(signals.complexity, _COMPLEXITY_ORDER) > _rank(release.max_complexity, _COMPLEXITY_ORDER):
                eligible = False
                reasons.append("complexity_exceeds_candidate")
            if _rank(signals.risk_level, _RISK_ORDER) > _rank(release.max_risk_level, _RISK_ORDER):
                eligible = False
                reasons.append("risk_exceeds_candidate")
            if release.expected_latency_ms and release.expected_latency_ms > signals.latency_budget_ms:
                eligible = False
                reasons.append("latency_budget_exceeded")
            candidate_cost = self._estimated_cost(
                model=release.candidate_model,
                input_tokens=estimated_input_tokens,
                output_tokens=estimated_output_tokens,
            )
            baseline_cost = self._estimated_cost(
                model=baseline_model,
                input_tokens=estimated_input_tokens,
                output_tokens=estimated_output_tokens,
            )
            if candidate_cost is None or baseline_cost is None:
                eligible = False
                reasons.append("pricing_missing")
            elif baseline_cost > 0 and candidate_cost / baseline_cost > float(release.max_cost_ratio):
                eligible = False
                reasons.append("cost_ratio_exceeded")
            bucket = self._stable_bucket(
                version=release.version, action=action, user_id=user_id, request_id=request_id,
            )
            serve_candidate = eligible and bucket < int(release.rollout_percentage or 0)
            shadow_candidate = eligible and not serve_candidate and bucket < int(release.shadow_percentage or 0)
            if serve_candidate:
                reasons.append("ab_candidate")
            elif shadow_candidate:
                reasons.append("shadow_candidate")
            else:
                reasons.append("control")
            return ModelReleaseDecision(
                release_version=release.version,
                candidate_model=release.candidate_model,
                candidate_provider=release.candidate_provider,
                candidate_base_url=release.candidate_base_url,
                bucket=bucket,
                serve_candidate=serve_candidate,
                shadow_candidate=shadow_candidate,
                eligible=eligible,
                reason=";".join(reasons),
                signals=signals,
            )
        except Exception:
            # Rollout control must fail open to the established routing path.  In particular,
            # deploys before the migration must not make LLM requests unavailable.
            with suppress(Exception):
                db.rollback()
            return None
        finally:
            if owns_session:
                db.close()

    @staticmethod
    def _p95(values: list[int]) -> int | None:
        if not values:
            return None
        values = sorted(values)
        return values[max(0, math.ceil(len(values) * 0.95) - 1)]

    def _row_cost(self, row: LLMCallLog) -> float | None:
        return self._estimated_cost(
            model=row.model_name or "",
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
        )

    def assess_guardrail(self, db: Session, *, version: str, auto_rollback: bool = True) -> dict[str, Any]:
        release = self.get_by_version(db, version)
        if not release:
            raise ValueError("Model release not found")
        rows = (
            db.query(LLMCallLog)
            .filter(
                LLMCallLog.model_release_version == release.version,
                LLMCallLog.traffic_type == "serving",
            )
            .order_by(LLMCallLog.created_at.asc(), LLMCallLog.id.asc())
            .all()
        )
        candidate_rows = [row for row in rows if row.model_name == release.candidate_model]
        control_rows = [row for row in rows if row.model_name != release.candidate_model]
        candidate_count = len(candidate_rows)
        candidate_error_rate = (
            sum(row.status != "success" for row in candidate_rows) / candidate_count if candidate_count else 0.0
        )
        candidate_p95 = self._p95([int(row.duration_ms or 0) for row in candidate_rows])
        candidate_costs = [cost for row in candidate_rows if (cost := self._row_cost(row)) is not None]
        control_costs = [cost for row in control_rows if (cost := self._row_cost(row)) is not None]
        candidate_avg_cost = sum(candidate_costs) / len(candidate_costs) if candidate_costs else None
        control_avg_cost = sum(control_costs) / len(control_costs) if control_costs else None
        cost_ratio = (
            candidate_avg_cost / control_avg_cost
            if candidate_avg_cost is not None and control_avg_cost is not None and control_avg_cost > 0
            else None
        )
        breaches: list[str] = []
        if candidate_count >= int(release.min_sample_size):
            if candidate_error_rate > float(release.max_error_rate):
                breaches.append("error_rate_exceeded")
            if release.max_p95_latency_ms and candidate_p95 and candidate_p95 > int(release.max_p95_latency_ms):
                breaches.append("p95_latency_exceeded")
            if cost_ratio is not None and cost_ratio > float(release.max_cost_ratio):
                breaches.append("cost_ratio_exceeded")
        status = "insufficient_data" if candidate_count < int(release.min_sample_size) else ("breached" if breaches else "healthy")
        auto_rolled_back = False
        if auto_rollback and breaches and release.status == "active":
            self.rollback(db, version=release.version, actor_id=None)
            auto_rolled_back = True
        return {
            "version": release.version,
            "status": status,
            "candidate_requests": candidate_count,
            "control_requests": len(control_rows),
            "minimum_requests": int(release.min_sample_size),
            "candidate_error_rate": round(candidate_error_rate, 4),
            "candidate_p95_latency_ms": candidate_p95,
            "candidate_avg_cost": round(candidate_avg_cost, 6) if candidate_avg_cost is not None else None,
            "control_avg_cost": round(control_avg_cost, 6) if control_avg_cost is not None else None,
            "cost_ratio": round(cost_ratio, 4) if cost_ratio is not None else None,
            "breaches": breaches,
            "auto_rolled_back": auto_rolled_back,
        }

    def serialize(self, row: ModelRelease) -> dict[str, Any]:
        try:
            gate = json.loads(row.evaluation_gate_json) if row.evaluation_gate_json else None
        except json.JSONDecodeError:
            gate = None
        return {
            "id": row.id,
            "version": row.version,
            "action": row.action,
            "candidate_model": row.candidate_model,
            "candidate_provider": row.candidate_provider,
            "candidate_base_url": row.candidate_base_url,
            "max_complexity": row.max_complexity,
            "max_risk_level": row.max_risk_level,
            "expected_latency_ms": row.expected_latency_ms,
            "max_cost_ratio": row.max_cost_ratio,
            "rollout_percentage": row.rollout_percentage,
            "shadow_percentage": row.shadow_percentage,
            "min_sample_size": row.min_sample_size,
            "max_error_rate": row.max_error_rate,
            "max_p95_latency_ms": row.max_p95_latency_ms,
            "evaluation_status": row.evaluation_status,
            "evaluation_gate": gate,
            "status": row.status,
            "previous_release_id": row.previous_release_id,
            "created_by": row.created_by,
            "activated_by": row.activated_by,
            "rolled_back_by": row.rolled_back_by,
            "created_at": row.created_at,
            "activated_at": row.activated_at,
            "rolled_back_at": row.rolled_back_at,
        }


model_release_service = ModelReleaseService()
