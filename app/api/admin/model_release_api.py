"""Admin API for versioned model canaries, shadow traffic and rollback controls."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.api_response import api_error
from app.core.auth import require_admin_user
from app.core.database import get_db
from app.models.user import User
from app.services.llm.model_release_service import model_release_service
from app.services.observability.oplog_service import oplog_service

router = APIRouter()


class ModelReleaseDraftRequest(BaseModel):
    version: str = Field(min_length=1, max_length=128)
    action: str = Field(default="*", min_length=1, max_length=128)
    candidate_model: str = Field(min_length=1, max_length=128)
    candidate_provider: str | None = Field(default=None, max_length=64)
    candidate_base_url: str | None = Field(default=None, max_length=512)
    max_complexity: str = "complex"
    max_risk_level: str = "medium"
    expected_latency_ms: int | None = Field(default=None, ge=1)
    max_cost_ratio: float = Field(default=1.0, gt=0)
    rollout_percentage: int = Field(default=0, ge=0, le=100)
    shadow_percentage: int = Field(default=0, ge=0, le=100)
    min_sample_size: int = Field(default=100, ge=1)
    max_error_rate: float = Field(default=0.05, ge=0, le=1)
    max_p95_latency_ms: int | None = Field(default=None, ge=1)
    evaluation_gate: dict[str, Any] | None = None


class EvaluationGateRequest(BaseModel):
    report_ref: str = Field(min_length=1, max_length=512)
    baseline_score: float
    candidate_score: float
    max_regression: float = Field(default=0, ge=0, le=1)


class GuardrailRequest(BaseModel):
    auto_rollback: bool = True


@router.get("/")
def list_model_releases(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    _ = current_user
    return [model_release_service.serialize(row) for row in model_release_service.list_releases(db, limit=limit)]


@router.post("/")
def create_model_release(
    req: ModelReleaseDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    try:
        row = model_release_service.create_draft(db, spec=req.model_dump(), actor_id=current_user.id)
    except ValueError as exc:
        raise api_error(400, "模型发布配置不合法", code="MODEL_RELEASE_INVALID", detail=str(exc)) from exc
    oplog_service.log(
        module="model_release",
        action="model_release_draft_created",
        db=db,
        user_id=current_user.id,
        target_type="model_release",
        target_id=row.id,
        detail=f"version={row.version}; action={row.action}; model={row.candidate_model}",
    )
    return model_release_service.serialize(row)


@router.post("/{version}/evaluation-gate")
def record_model_evaluation_gate(
    version: str,
    req: EvaluationGateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    try:
        row = model_release_service.record_evaluation_gate(
            db,
            version=version,
            gate=req.model_dump(),
            actor_id=current_user.id,
        )
    except ValueError as exc:
        code = "MODEL_RELEASE_NOT_FOUND" if str(exc) == "Model release not found" else "MODEL_EVALUATION_GATE_INVALID"
        raise api_error(
            404 if code == "MODEL_RELEASE_NOT_FOUND" else 400, "模型评测门禁更新失败", code=code, detail=str(exc)
        ) from exc
    oplog_service.log(
        module="model_release",
        action="model_release_evaluation_gate_recorded",
        db=db,
        user_id=current_user.id,
        target_type="model_release",
        target_id=row.id,
        detail=f"version={row.version}; evaluation_status={row.evaluation_status}",
    )
    return model_release_service.serialize(row)


@router.post("/{version}/activate")
def activate_model_release(
    version: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    try:
        row = model_release_service.activate(db, version=version, actor_id=current_user.id)
    except ValueError as exc:
        code = "MODEL_RELEASE_NOT_FOUND" if str(exc) == "Model release not found" else "MODEL_RELEASE_ACTIVATE_INVALID"
        raise api_error(
            404 if code == "MODEL_RELEASE_NOT_FOUND" else 400, "模型发布激活失败", code=code, detail=str(exc)
        ) from exc
    oplog_service.log(
        module="model_release",
        action="model_release_activated",
        db=db,
        user_id=current_user.id,
        target_type="model_release",
        target_id=row.id,
        detail=f"version={row.version}; rollout={row.rollout_percentage}; shadow={row.shadow_percentage}",
    )
    return model_release_service.serialize(row)


@router.post("/{version}/rollback")
def rollback_model_release(
    version: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    try:
        row = model_release_service.rollback(db, version=version, actor_id=current_user.id)
    except ValueError as exc:
        code = "MODEL_RELEASE_NOT_FOUND" if str(exc) == "Model release not found" else "MODEL_RELEASE_ROLLBACK_INVALID"
        raise api_error(
            404 if code == "MODEL_RELEASE_NOT_FOUND" else 400, "模型回滚失败", code=code, detail=str(exc)
        ) from exc
    oplog_service.log(
        module="model_release",
        action="model_release_rolled_back",
        db=db,
        user_id=current_user.id,
        target_type="model_release",
        target_id=row.id,
        detail=f"version={row.version}",
    )
    return model_release_service.serialize(row)


@router.post("/{version}/guardrail")
def assess_model_release_guardrail(
    version: str,
    req: GuardrailRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    try:
        result = model_release_service.assess_guardrail(db, version=version, auto_rollback=req.auto_rollback)
    except ValueError as exc:
        raise api_error(404, "模型发布不存在", code="MODEL_RELEASE_NOT_FOUND", detail=str(exc)) from exc
    oplog_service.log(
        module="model_release",
        action="model_release_guardrail_assessed",
        db=db,
        user_id=current_user.id,
        target_type="model_release",
        target_id=None,
        detail=f"version={version}; status={result['status']}; breaches={','.join(result['breaches']) or 'none'}; auto_rolled_back={result['auto_rolled_back']}",
    )
    return result
