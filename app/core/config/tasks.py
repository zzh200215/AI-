"""Background task / agent runtime settings."""

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class TaskSettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    AGENT_TOOL_TIMEOUT_SECONDS: int = Field(default=45, ge=5, le=300)
    AGENT_PARALLEL_MAX_WORKERS: int = Field(default=2, ge=1, le=4)

    # Agent Run 可靠性：run/step 超时、工具重试、审批过期、幂等
    AGENT_RUN_DEADLINE_SECONDS: int = Field(default=600, ge=30, le=86400)
    AGENT_STEP_DEADLINE_SECONDS: int = Field(default=120, ge=10, le=3600)
    AGENT_TOOL_MAX_RETRIES: int = Field(default=1, ge=0, le=5)
    AGENT_TOOL_BACKOFF_BASE_SECONDS: int = Field(default=2, ge=1, le=60)
    AGENT_APPROVAL_EXPIRE_SECONDS: int = Field(default=3600, ge=60, le=604800)
    AGENT_TOOL_IDEMPOTENCY_ENABLED: bool = Field(default=True)
    # 内部 A2A 控制面：防止 Agent 递归委派放大资源与权限风险。
    A2A_MAX_DELEGATION_DEPTH: int = Field(default=3, ge=1, le=10)

    # 文档处理任务：重试策略与 lease（租约）回收
    DOCUMENT_TASK_MAX_RETRIES: int = Field(default=2, ge=0, le=10)
    DOCUMENT_TASK_BACKOFF_BASE_SECONDS: int = Field(default=5, ge=1, le=3600)
    DOCUMENT_JOB_LEASE_TTL_SECONDS: int = Field(default=300, ge=30, le=86400)
    DOCUMENT_JOB_LEASE_RENEW_INTERVAL_SECONDS: int = Field(default=60, ge=10, le=3600)
