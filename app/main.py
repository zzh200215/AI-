from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import redis
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from sqlalchemy import text
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException

import app.tasks  # noqa: F401  (显式注册 Celery 任务：应用进程内 .delay() 需任务已注册)
from app.api.admin import analytics_api, dashboard_api, model_release_api, pilot_feedback_api, prompt_api
from app.api.agent import agent_api, mcp_api
from app.api.auth import account_deletion_api, auth_api
from app.api.billing import platform_payment_api, subscription_api
from app.api.channels import feishu_api, miniapp_api, outbound_api
from app.api.conversation import chat_api, memory_api, ws_api
from app.api.developer import api_key_api, legal_platform_api
from app.api.documents import document_api, document_conflict_api
from app.api.legal import (
    legal_api,
    legal_approval_api,
    legal_billing_api,
    legal_case_api,
    legal_contract_api,
    legal_domain_api,
    legal_portal_api,
    org_member_api,
)
from app.api.org import org_api
from app.api.tasks import task_api
from app.core.api_response import (
    ApiResponseMiddleware,
    http_exception_handler,
    stale_data_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.model_gateway import model_gateway
from app.core.obs_middleware import ObservabilityContextMiddleware
from app.core.oplog_middleware import OperationLogMiddleware
from app.core.telemetry import init_telemetry


@asynccontextmanager
async def lifespan(app: FastAPI):
    model_gateway.start()
    yield
    await model_gateway.close()


app = FastAPI(
    title="律智检｜法律文书与合同审查智能体平台",
    description="法律检索、合同审查、文书草稿与律师审核工作台",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(auth_api.router, prefix="/api/auth", tags=["Auth"])
app.include_router(account_deletion_api.router, prefix="/api/auth", tags=["Account Deletion"])
app.include_router(chat_api.router, prefix="/api/chat", tags=["Chat"])
app.include_router(memory_api.router, prefix="/api/memory", tags=["Conversation Memory"])
app.include_router(document_api.router, prefix="/api/documents", tags=["Documents"])
app.include_router(legal_api.router, prefix="/api/legal", tags=["Legal Workspace"])
app.include_router(outbound_api.router, prefix="/api/outbound", tags=["Outbound Email"])
app.include_router(task_api.router, prefix="/api/tasks", tags=["Tasks"])
app.include_router(pilot_feedback_api.router, prefix="/api/pilot", tags=["Pilot Feedback"])
app.include_router(document_conflict_api.router, prefix="/api/document-conflicts", tags=["Document Conflicts"])
app.include_router(agent_api.router, prefix="/api/agent", tags=["Agent"])
app.include_router(mcp_api.router, prefix="/api/mcp", tags=["MCP"])
app.include_router(org_api.router, prefix="/api/org", tags=["Org"])
app.include_router(org_member_api.router, prefix="/api/legal", tags=["Legal Org Members"])
app.include_router(legal_case_api.router, prefix="/api/legal", tags=["Legal Cases"])
app.include_router(legal_approval_api.router, prefix="/api/legal", tags=["Legal Approval"])
app.include_router(subscription_api.router, prefix="/api/billing", tags=["Subscription"])
app.include_router(platform_payment_api.router, prefix="/api/billing", tags=["Platform Payments"])
app.include_router(feishu_api.router, prefix="/api/feishu", tags=["Feishu"])
app.include_router(miniapp_api.router, prefix="/api/miniapp", tags=["Mini App"])
app.include_router(dashboard_api.router, prefix="/api/admin", tags=["Admin Dashboard"])
app.include_router(model_release_api.router, prefix="/api/admin/model-releases", tags=["Model Releases"])
app.include_router(prompt_api.router, prefix="/api/prompts", tags=["Prompts"])
app.include_router(analytics_api.router, prefix="/api/analytics", tags=["Analytics"])
app.include_router(api_key_api.router, prefix="/api/developer", tags=["Open API Keys"])
app.include_router(legal_billing_api.router, prefix="/api/legal", tags=["Legal Billing"])
app.include_router(legal_portal_api.router, prefix="/api/legal", tags=["Legal Portal"])
app.include_router(legal_contract_api.router, prefix="/api/legal", tags=["Legal Contracts"])
app.include_router(legal_domain_api.router, prefix="/api/legal", tags=["Legal Domain Model"])
app.include_router(legal_platform_api.router, prefix="/api/developer", tags=["Developer Platform"])
app.include_router(legal_platform_api.open_router, prefix="/api/open", tags=["Open API"])
app.include_router(ws_api.router, prefix="/api", tags=["WebSocket"])

app.add_middleware(ApiResponseMiddleware)
app.add_middleware(OperationLogMiddleware)
app.add_middleware(ObservabilityContextMiddleware)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
# 乐观锁冲突（StaleDataError）→ 409；比通用 Exception 更具体，注册顺序无关。
app.add_exception_handler(StaleDataError, stale_data_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)
settings = get_settings()
# 生产/试点环境严格配置校验：关键配置缺失则启动失败；开发/测试不校验。
settings.validate_production_or_raise()
init_telemetry(app)


# ── P1: 统一 OpenAPI 契约（代码自动生成，禁止手维护文档）────────────────────
_IDEMPOTENCY_HEADER = {
    "name": "Idempotency-Key",
    "in": "header",
    "required": False,
    "schema": {"type": "string", "maxLength": 128},
    "description": "幂等键：同 key + 同请求指纹重放原结果；同 key + 异指纹返回 409",
}
_IF_MATCH_HEADER = {
    "name": "If-Match",
    "in": "header",
    "required": False,
    "schema": {"type": "string", "pattern": '^"v\\d+"$'},
    "description": 'ETag 值（形如 "v{n}"）：与资源当前版本不匹配返回 409 CONCURRENT_UPDATE_CONFLICT',
}
# 声明 202 + JobOut 的异步创建端点（(method, path) 名单，与服务端实际行为一致）。
_ASYNC_CREATE_ENDPOINTS = {
    ("post", "/api/open/v1/contract-reviews"),
    ("get", "/api/developer/orgs/{org_id}/security-audit/export"),
    ("post", "/api/documents/{document_id}/summarize"),
    ("post", "/api/documents/{document_id}/analyze"),
}
# 声明 If-Match 的版本化更新端点（服务端已实现校验的名单）。
# 注意：org 更新真实路由为 /api/org/organizations/{org_id}（org_api.router 挂载于 /api/org）。
_IF_MATCH_ENDPOINTS = {
    ("put", "/api/tasks/{task_id}"),
    ("patch", "/api/tasks/{task_id}"),
    ("put", "/api/org/organizations/{org_id}"),
}


def _inject_unified_contract(schema: dict) -> dict:
    from app.schemas.api import ErrorEnvelope, JobOut, PagePayload, SuccessEnvelope

    schema["info"]["x-api-version"] = "1"
    # 错误码注册表随规范固化（contract gate 检测错误码删除/变更这一 breaking change）
    from app.core import error_codes as error_codes_module

    schema["x-error-codes"] = sorted(
        {value for name, value in vars(error_codes_module).items() if name.isupper() and isinstance(value, str)}
    )
    components = schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    for model in (SuccessEnvelope, ErrorEnvelope, PagePayload, JobOut):
        schemas[model.__name__] = model.model_json_schema(ref_template="#/components/schemas/{model}")
    components.setdefault("securitySchemes", {})["ApiKeyHeader"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "开放平台 API Key（/api/open/* 必填）",
    }

    for path, methods in schema.get("paths", {}).items():
        for method, op in methods.items():
            if not isinstance(op, dict) or "operationId" not in op:
                continue
            if path.startswith("/api/open/"):
                op.setdefault("security", [{"ApiKeyHeader": []}])
            if method.lower() in ("post", "put", "patch", "delete"):
                params = op.setdefault("parameters", [])
                if not any(p.get("name") == "Idempotency-Key" for p in params):
                    params.append(_IDEMPOTENCY_HEADER)
            if (method.lower(), path) in _IF_MATCH_ENDPOINTS:
                params = op.setdefault("parameters", [])
                if not any(p.get("name") == "If-Match" for p in params):
                    params.append(_IF_MATCH_HEADER)
            if (method.lower(), path) in _ASYNC_CREATE_ENDPOINTS:
                op.setdefault("responses", {})["202"] = {
                    "description": "已接受：任务已创建，通过 status_url 查询结果",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/JobOut"}}},
                }
    return schema


def custom_openapi():
    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    app.openapi_schema = _inject_unified_contract(schema)
    return app.openapi_schema


app.openapi = custom_openapi


@app.get("/")
def root():
    return {"message": "律智检法律工作台正在运行"}


@app.get("/api/health")
def health_check():
    checks = {}

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception:
        checks["database"] = {"status": "error"}
    finally:
        db.close()

    try:
        redis.from_url(settings.REDIS_URL).ping()
        checks["redis"] = {"status": "ok"}
    except Exception:
        checks["redis"] = {"status": "error"}

    model_headers = (
        {"Authorization": f"Bearer {settings.LLM_API_KEY}"}
        if settings.LLM_PROVIDER != "ollama" and settings.LLM_API_KEY
        else {}
    )
    model_health_url = (
        f"{settings.LLM_API_BASE_URL.rstrip('/')}/models"
        if settings.LLM_PROVIDER != "ollama"
        else f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/tags"
    )
    provider_label = "llm_provider" if settings.LLM_PROVIDER != "ollama" else "ollama"
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(model_health_url, headers=model_headers)
            response.raise_for_status()
        checks[provider_label] = {"status": "ok", "provider": settings.LLM_PROVIDER}
    except Exception:
        checks[provider_label] = {"status": "error", "provider": settings.LLM_PROVIDER}

    overall_status = "ok" if all(item["status"] == "ok" for item in checks.values()) else "degraded"
    return {
        "status": overall_status,
        "timestamp": datetime.now(UTC).isoformat(),
        "checks": checks,
    }


@app.get("/api/health/live")
def liveness_check():
    """Process-level probe: does not depend on external services."""
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}


@app.get("/api/health/ready")
def readiness_check():
    """Dependency probe used by deployment orchestration."""
    checks = {}
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
    finally:
        db.close()
    try:
        redis.from_url(settings.REDIS_URL).ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"
    status = "ok" if all(value == "ok" for value in checks.values()) else "degraded"
    return {"status": status, "checks": checks, "timestamp": datetime.now(UTC).isoformat()}
