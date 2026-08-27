from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.api_response import api_error, paginated_payload
from app.core.auth import get_current_user, require_admin_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.user import User
from app.services.documents.document_qa_service import document_qa_service
from app.services.observability.analytics_service import analytics_service

router = APIRouter()


class RetryTaskRunRequest(BaseModel):
    source: str
    task_key: str


class ResolveFeedbackRequest(BaseModel):
    resolution_note: str | None = None


class FeedbackEvalBundleRequest(BaseModel):
    days: int = 30


class MCPPolicyDraftRequest(BaseModel):
    policy: dict


class AgentEvalReviewRequest(BaseModel):
    evaluation_input: dict
    expected_outcome: dict
    review_note: str | None = None


class AgentEvalRejectRequest(BaseModel):
    review_note: str | None = None


@router.get("/tokens/my-stats")
def my_token_stats(
    days: int = Query(30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return analytics_service.get_user_token_stats(current_user.id, db, days=days)


@router.get("/tokens/global-stats")
def global_token_stats(
    days: int = Query(30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    return analytics_service.get_global_token_stats(db, days=days)


@router.get("/experiments/overview")
def experiment_overview(
    days: int = Query(30, description="Prompt 版本流量统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    return analytics_service.get_experiment_overview(db=db, days=days)


@router.get("/llm-calls")
def list_llm_calls(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    module_name: str | None = Query(None, description="模块名称"),
    action: str | None = Query(None, description="调用动作"),
    status: str | None = Query(None, description="success/error"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    rows, total = analytics_service.list_llm_calls(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        limit=1000,
        module_name=module_name,
        action=action,
        status=status,
        page=page,
        page_size=page_size,
    )
    items = [
        {
            "id": row.id,
            "module_name": row.module_name,
            "action": row.action,
            "model_name": row.model_name,
            "prompt_template": row.prompt_template,
            "prompt_version": row.prompt_version,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "duration_ms": row.duration_ms,
            "status": row.status,
            "routing_role": row.routing_role,
            "routing_stage": row.routing_stage,
            "model_release_version": row.model_release_version,
            "experiment_bucket": row.experiment_bucket,
            "traffic_type": row.traffic_type,
            "routing_reason": row.routing_reason,
            "request_complexity": row.request_complexity,
            "risk_level": row.risk_level,
            "error_message": row.error_message if current_user.role == "admin" else None,
            "request_excerpt": row.request_excerpt if current_user.role == "admin" else None,
            "response_excerpt": row.response_excerpt if current_user.role == "admin" else None,
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.get("/llm-calls/stats")
def llm_call_stats(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    module_name: str | None = Query(None, description="模块名称"),
    action: str | None = Query(None, description="调用动作"),
    status: str | None = Query(None, description="success/error"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    return analytics_service.get_llm_call_stats(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        module_name=module_name,
        action=action,
        status=status,
    )


@router.get("/llm-billing/stats")
def llm_billing_stats(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    module_name: str | None = Query(None, description="模块名称"),
    action: str | None = Query(None, description="调用动作"),
    status: str | None = Query(None, description="success/error"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    return analytics_service.get_llm_billing_stats(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        module_name=module_name,
        action=action,
        status=status,
    )


@router.get("/llm-routing/stats")
def llm_routing_stats(
    days: int = Query(30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    return analytics_service.get_llm_routing_stats(
        db=db,
        include_all_users=True,
        days=days,
    )


@router.get("/llm-routing/health")
def llm_routing_health(
    hours: int = Query(1, ge=1, le=24, description="统计窗口小时数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    return analytics_service.get_llm_routing_health(db=db, hours=hours)


@router.get("/llm-pricing")
def llm_pricing(current_user: User = Depends(get_current_user)):
    return analytics_service.get_model_pricing()


@router.get("/oplogs")
def list_operation_logs(
    module: str | None = Query(None, description="按模块筛选"),
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")
    if module == "system" and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    logs, total = analytics_service.list_operation_logs(
        db,
        user_id=current_user.id,
        module=module,
        include_all_users=include_all_users,
        days=days,
        limit=1000,
        page=page,
        page_size=page_size,
    )
    items = [
        {
            "id": log.id,
            "module": log.module,
            "action": log.action,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "detail": log.detail,
            "ip_address": log.ip_address,
            "created_at": log.created_at,
        }
        for log in logs
    ]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.get("/oplogs/stats")
def operation_stats(
    days: int = Query(30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return analytics_service.get_operation_stats(current_user.id, db, days=days)


@router.get("/alerts")
def list_alerts(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    source: str | None = Query(None, description="async_task 或 agent"),
    category: str | None = Query(None, description="告警分类"),
    severity: str | None = Query(None, description="告警级别"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    rows = analytics_service.list_alerts(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        limit=1000,
        source=source,
        category=category,
        severity=severity,
    )
    total = len(rows)
    items = rows[(page - 1) * page_size : page * page_size]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.get("/alerts/stats")
def alert_stats(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    source: str | None = Query(None, description="async_task 或 agent"),
    category: str | None = Query(None, description="告警分类"),
    severity: str | None = Query(None, description="告警级别"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    return analytics_service.get_alert_stats(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        source=source,
        category=category,
        severity=severity,
    )


@router.get("/task-runs")
def list_task_runs(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    source: str | None = Query(None, description="async_task 或 agent"),
    status: str | None = Query(None, description="pending/running/succeeded/failed"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    rows = analytics_service.list_task_runs(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        limit=1000,
        source=source,
        status=status,
    )
    total = len(rows)
    items = rows[(page - 1) * page_size : page * page_size]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.post("/task-runs/retry")
def retry_task_run(
    req: RetryTaskRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return analytics_service.retry_task_run(
            db=db,
            source=req.source,
            task_key=req.task_key,
            user_id=current_user.id,
        )
    except ValueError as e:
        raise api_error(404, "任务运行记录不存在或不可重试", code="TASK_RUN_RETRY_NOT_AVAILABLE", detail=str(e))


@router.get("/feedback")
def list_feedback(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    feedback_value: str | None = Query(None, description="positive 或 negative"),
    feedback_status: str | None = Query(None, description="open 或 resolved"),
    source: str | None = Query(None, description="document/chat/ws_chat"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    rows, total = analytics_service.list_feedback_records(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        limit=1000,
        feedback_value=feedback_value,
        feedback_status=feedback_status,
        source=source,
        page=page,
        page_size=page_size,
    )
    items = [
        {
            "id": row.id,
            "document_id": row.document_id,
            "document_title": row.document.title if row.document else None,
            "user_id": row.user_id,
            "question": row.question,
            "answer": row.answer,
            "source": row.source,
            "feedback_value": row.feedback_value,
            "feedback_reason": row.feedback_reason,
            "feedback_note": row.feedback_note,
            "feedback_status": row.feedback_status,
            "feedback_created_at": row.feedback_created_at,
            "feedback_resolved_at": row.feedback_resolved_at,
            "feedback_resolution_note": row.feedback_resolution_note,
            "feedback_resolved_by": row.feedback_resolved_by,
            "latency_ms": row.latency_ms,
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.get("/qa-replays")
def list_qa_replays(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    source: str | None = Query(None, description="document/chat/ws_chat"),
    feedback_status: str | None = Query(None, description="open 或 resolved"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    payload = analytics_service.list_qa_replays(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        page=page,
        page_size=page_size,
        source=source,
        feedback_status=feedback_status,
    )
    return paginated_payload(
        payload["items"],
        total=payload["total"],
        page=payload["page"],
        page_size=payload["page_size"],
    )


@router.get("/feedback/stats")
def feedback_stats(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    feedback_value: str | None = Query(None, description="positive 或 negative"),
    feedback_status: str | None = Query(None, description="open 或 resolved"),
    source: str | None = Query(None, description="document/chat/ws_chat"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    return analytics_service.get_feedback_stats(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        feedback_value=feedback_value,
        feedback_status=feedback_status,
        source=source,
    )


@router.post("/feedback/{qa_record_id}/resolve")
def resolve_feedback(
    qa_record_id: int,
    req: ResolveFeedbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    try:
        record = document_qa_service.resolve_feedback(
            qa_record_id=qa_record_id,
            resolver_id=current_user.id,
            db=db,
            resolution_note=req.resolution_note,
        )
        analytics_service.create_operation_log(
            module="document",
            action="document_qa_feedback_resolved",
            db=db,
            user_id=current_user.id,
            target_type="document_qa_record",
            target_id=record.id,
            detail=f"status={record.feedback_status}; document_id={record.document_id}",
        )
        return {
            "id": record.id,
            "feedback_status": record.feedback_status,
            "feedback_resolved_at": record.feedback_resolved_at,
            "feedback_resolution_note": record.feedback_resolution_note,
            "feedback_resolved_by": record.feedback_resolved_by,
        }
    except ValueError as e:
        detail = str(e)
        if detail == "QA record not found":
            raise api_error(404, "问答记录不存在", code="QA_RECORD_NOT_FOUND", detail=detail)
        raise api_error(400, "反馈处理失败", code="DOCUMENT_QA_FEEDBACK_RESOLVE_INVALID", detail=detail)


@router.get("/tool-health")
def tool_health(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")
    return analytics_service.get_tool_health(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
    )


@router.post("/feedback/export-eval-bundle")
def export_feedback_eval_bundle(
    req: FeedbackEvalBundleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    return analytics_service.export_feedback_eval_bundle(
        db=db,
        user_id=current_user.id,
        include_all_users=True,
        days=req.days,
    )


@router.get("/mcp-policies")
def list_mcp_policy_versions(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    from app.services.agent.mcp_policy_service import mcp_policy_service

    _ = current_user
    return [
        {
            "id": row.id,
            "version": row.version,
            "schema_version": row.schema_version,
            "status": row.status,
            "checksum": row.checksum,
            "created_by": row.created_by,
            "activated_by": row.activated_by,
            "created_at": row.created_at,
            "activated_at": row.activated_at,
        }
        for row in mcp_policy_service.list_versions(db, limit=limit)
    ]


@router.post("/mcp-policies")
def save_mcp_policy_draft(
    req: MCPPolicyDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    from app.services.agent.mcp_policy_service import mcp_policy_service

    try:
        row = mcp_policy_service.save_draft(db, document=req.policy, actor_id=current_user.id)
    except ValueError as exc:
        raise api_error(400, "MCP 策略校验失败", code="MCP_POLICY_INVALID", detail=str(exc)) from exc
    return {"id": row.id, "version": row.version, "status": row.status, "checksum": row.checksum}


@router.post("/mcp-policies/{version}/activate")
def activate_mcp_policy(
    version: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    from app.services.agent.mcp_policy_service import mcp_policy_service

    try:
        row = mcp_policy_service.activate(db, version=version, actor_id=current_user.id)
    except ValueError as exc:
        raise api_error(400, "MCP 策略激活失败", code="MCP_POLICY_ACTIVATE_INVALID", detail=str(exc)) from exc
    return {"id": row.id, "version": row.version, "status": row.status, "activated_at": row.activated_at}


@router.get("/mcp-policies/replay/{run_id}")
def replay_mcp_policy(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    from app.services.agent.mcp_policy_service import mcp_policy_service

    _ = current_user
    return mcp_policy_service.replay_run(db, run_id=run_id)


@router.get("/agent-evals/candidates")
def list_agent_eval_candidates(
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    from app.services.agent.online_eval_service import online_eval_service

    _ = current_user
    rows = online_eval_service.list_candidates(db, status=status, limit=limit)
    return [
        {
            "id": row.id,
            "agent_run_id": row.agent_run_id,
            "trace_id": row.trace_id,
            "failure_type": row.failure_type,
            "status": row.status,
            "goal_hash": row.goal_hash,
            "evidence_json": row.evidence_json,
            "review_note": row.review_note,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/agent-evals/candidates/{candidate_id}/approve")
def approve_agent_eval_candidate(
    candidate_id: int,
    req: AgentEvalReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    from app.services.agent.online_eval_service import online_eval_service

    try:
        row = online_eval_service.approve(
            db,
            candidate_id=candidate_id,
            reviewer_id=current_user.id,
            evaluation_input=req.evaluation_input,
            expected_outcome=req.expected_outcome,
            review_note=req.review_note,
        )
    except ValueError as exc:
        raise api_error(400, "Agent 评测样本审核失败", code="AGENT_EVAL_REVIEW_INVALID", detail=str(exc)) from exc
    return {"id": row.id, "status": row.status, "approved_at": row.approved_at}


@router.post("/agent-evals/candidates/{candidate_id}/reject")
def reject_agent_eval_candidate(
    candidate_id: int,
    req: AgentEvalRejectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    from app.services.agent.online_eval_service import online_eval_service

    try:
        row = online_eval_service.reject(
            db, candidate_id=candidate_id, reviewer_id=current_user.id, review_note=req.review_note
        )
    except ValueError as exc:
        raise api_error(404, "Agent 评测样本不存在", code="AGENT_EVAL_CANDIDATE_NOT_FOUND", detail=str(exc)) from exc
    return {"id": row.id, "status": row.status}


@router.post("/agent-evals/export")
def export_agent_eval_dataset(db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    from app.services.agent.online_eval_service import online_eval_service

    _ = current_user
    return online_eval_service.export_approved(db)


@router.post("/oplogs")
def create_operation_log(
    request: Request,
    module: str,
    action: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    target_type: str | None = None,
    target_id: int | None = None,
    detail: str | None = None,
):
    ip_address = request.client.host if request.client else None
    log = analytics_service.create_operation_log(
        module=module,
        action=action,
        db=db,
        user_id=current_user.id,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip_address=ip_address,
    )
    return {"id": log.id, "detail": "已记录"}


# ── P1 SLO / 运营分析（读预聚合表，不扫描明细）───────────────────────────────

_SLO_METRIC_LABELS = {
    "api_request_duration": "API p95 延迟（ms，快照直方图聚合）",
    "llm_calls": "LLM 成功率（success / 非 blocked）",
    "doc_parse_jobs": "文档解析成功率（succeeded / 已开始且终态）",
    "agent_runs": "Agent 完成率（completed / 终态，cancelled 按配置排除）",
    "notification_deliveries": "通知投递率（sent+delivered / sent+delivered+failed+dead_letter）",
    "task_outcomes": "任务成功率（succeeded / succeeded+failed）",
    "model_cost": "模型成本（cost_ledger llm_call，Decimal）",
    "task_backlog": "任务积压量（DB 口径 gauge）",
    "broker_backlog": "任务积压量（Redis Broker LLEN gauge）",
}


@router.get("/slo/overview")
def slo_overview(
    days: int = Query(30, ge=1, le=365, description="统计天数"),
    org_id: int | None = Query(None, description="按组织过滤（管理员可查任意组织）"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    """SLO 每日系列（读 ops_metric_daily 预聚合表）。"""
    from app.services.observability.ops_aggregation_service import ops_aggregation_service

    result: dict = {}
    for metric in sorted(_SLO_METRIC_LABELS):
        result[metric] = {
            "description": _SLO_METRIC_LABELS[metric],
            "series": ops_aggregation_service.slo_series(
                db,
                metric_name=metric,
                days=days,
                org_id=org_id,
            ),
        }
    return {"days": days, "org_id": org_id, "metrics": result}


@router.get("/slo/status")
def slo_status(
    days: int = Query(7, ge=1, le=365, description="窗口天数"),
    org_id: int | None = Query(None, description="按组织过滤"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    """SLO 达成状态：按 (org, labels) 汇总 numerator/denominator 与配置目标对比。"""
    from app.services.observability.ops_aggregation_service import ops_aggregation_service

    settings = get_settings()
    targets = {
        "api_request_duration": None,  # p95 目标 ms，见 latency_p95
        "llm_calls": float(settings.SLO_LLM_SUCCESS_RATE),
        "doc_parse_jobs": float(settings.SLO_DOC_PARSE_RATE),
        "agent_runs": float(settings.SLO_AGENT_COMPLETION_RATE),
        "notification_deliveries": float(settings.SLO_NOTIFICATION_DELIVERY_RATE),
        "task_outcomes": None,
        "model_cost": None,
        "task_backlog": None,
        "broker_backlog": None,
    }
    result: dict = {}
    for metric, target in targets.items():
        stats = ops_aggregation_service.slo_rates(
            db,
            metric_name=metric,
            days=days,
            org_id=org_id,
        )
        items = []
        for item in stats["items"]:
            attained = None
            if target is not None and item["rate"] is not None:
                attained = item["rate"] >= target
            items.append({**item, "target": target, "attained": attained})
        result[metric] = {
            "description": _SLO_METRIC_LABELS.get(metric, ""),
            "items": items,
        }
    # API p95 目标：取窗口内各桶 p95 的最大值对比（近似口径，见交付文档）。
    p95_series = ops_aggregation_service.slo_series(
        db,
        metric_name="api_request_duration",
        days=days,
        org_id=org_id,
    )
    p95_values = [item["p95_value"] for item in p95_series if item.get("p95_value") is not None]
    result["api_request_duration"]["latency_p95_ms"] = max(p95_values) if p95_values else None
    result["api_request_duration"]["target_p95_ms"] = settings.SLO_API_P95_MS
    return {"days": days, "org_id": org_id, "metrics": result}
