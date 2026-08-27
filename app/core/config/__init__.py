"""应用配置：按领域拆分后的统一入口。

- 各领域配置定义在 app/core/config/<domain>.py（DatabaseSettings/LLMSettings/...），
  每个领域独立可测，环境变量名与历史单体 Settings 完全一致。
- 最终 ``Settings`` 通过多继承组合各领域，所有字段仍是真实 pydantic 字段，
  ``settings.DATABASE_URL``、``settings.RAG_TOP_K`` 等访问方式与 patch.object 全部兼容。
- 严格生产校验在 ``validate_production_or_raise``（应用启动生命周期调用），
  开发/测试环境不受影响。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from app.core.config.base import CoreSettings
from app.core.config.database import DatabaseSettings
from app.core.config.legal import LegalSettings
from app.core.config.llm import LLMSettings
from app.core.config.messaging import MessagingSettings
from app.core.config.observability import ObservabilitySettings
from app.core.config.payment import PaymentSettings
from app.core.config.rag import RAGSettings
from app.core.config.reliability import ReliabilitySettings
from app.core.config.security import SecuritySettings
from app.core.config.sql import SQLSettings
from app.core.config.storage import StorageSettings
from app.core.config.tasks import TaskSettings

# 敏感字段：用于 redacted_dict() 脱敏展示，避免日志/诊断泄露密钥。
SENSITIVE_FIELDS = frozenset(
    {
        "SECRET_KEY",
        "LLM_API_KEY",
        "LLM_SMALL_MODEL_API_KEY",
        "LLM_CANARY_API_KEY",
        "CONNECTOR_CREDENTIAL_ENCRYPTION_KEY",
        "LDAP_BIND_PASSWORD",
        "WECOM_SECRET",
        "DINGTALK_APP_SECRET",
        "WECHAT_APP_SECRET",
        "SMTP_PASSWORD",
        "STRIPE_SECRET_KEY",
        "PAYMENT_WEBHOOK_SECRET",
        "FEISHU_EVENT_ENCRYPT_KEY",
        "FEISHU_APP_SECRET",
        "QDRANT_API_KEY",
        "NEO4J_PASSWORD",
        "SIGNING_WEBHOOK_SECRETS_JSON",
        "SIGNING_FADADA_API_KEY",
        "LEGAL_DATA_ENCRYPTION_KEY",
        "LEGAL_DATA_ENCRYPTION_KEYS_JSON",
        "ADMIN_PASSWORD",
        "STORAGE_MINIO_SECRET_KEY",
        "STORAGE_S3_SECRET_KEY",
        "STORAGE_OSS_SECRET_KEY",
        "SQL_DATABASE_URL",
    }
)


class Settings(
    CoreSettings,
    DatabaseSettings,
    LegalSettings,
    LLMSettings,
    RAGSettings,
    StorageSettings,
    SecuritySettings,
    PaymentSettings,
    ObservabilitySettings,
    TaskSettings,
    ReliabilitySettings,
    SQLSettings,
    MessagingSettings,
):
    """合并后的应用配置单例结构。

    字段全部来自各领域配置基类（多继承），访问方式与拆分前完全一致。
    """

    def get_env_file_path(self) -> Optional[str]:
        """返回实际使用的.env文件路径"""
        env_path = ".env"
        if os.path.exists(env_path):
            return os.path.abspath(env_path)
        return None

    def validate_required_for_production(self) -> list[str]:
        """检查生产环境必需配置，返回缺失项列表（不抛出）。"""
        issues: list[str] = []

        if not self.LLM_API_KEY:
            issues.append("LLM_API_KEY未配置")

        if self.DATABASE_URL == "sqlite:///./data/app.db":
            issues.append("生产环境应使用MySQL/PostgreSQL而非SQLite")

        if not self.REDIS_URL:
            issues.append("REDIS_URL未配置")

        if not self.ADMIN_USERNAME or not self.ADMIN_PASSWORD:
            issues.append("管理员账号未配置（ADMIN_USERNAME/ADMIN_PASSWORD）")
        if self.ENVIRONMENT.lower() in {"production", "prod", "pilot"} and not (
            self.LEGAL_DATA_ENCRYPTION_KEY or self.LEGAL_DATA_ENCRYPTION_KEYS_JSON
        ):
            issues.append("试点/生产环境必须配置独立的LEGAL_DATA_ENCRYPTION_KEY或版本化密钥环")

        # 启用了外部服务但其关键配置缺失时，启动必须失败（服务未启用则不强制）。
        if self.STRIPE_SECRET_KEY and not self.PAYMENT_CHECKOUT_BASE_URL:
            issues.append("启用了Stripe支付但PAYMENT_CHECKOUT_BASE_URL未配置")
        if self.SMTP_HOST and not (self.SMTP_USERNAME and self.SMTP_PASSWORD):
            issues.append("启用了SMTP但SMTP_USERNAME/SMTP_PASSWORD未配置")

        return issues

    def validate_production_or_raise(self) -> None:
        """生产/试点环境严格启动校验；开发/测试环境不做此校验。

        在应用启动生命周期调用（app/main.py）。
        """
        if self.ENVIRONMENT.lower() not in {"production", "prod", "pilot"}:
            return
        issues = self.validate_required_for_production()
        if issues:
            raise RuntimeError("生产环境配置校验失败：" + "; ".join(issues))

    def redacted_dict(self) -> dict:
        """返回全量配置 dict，敏感字段已脱敏（用于诊断/配置面板，不落日志）。"""
        payload = {name: getattr(self, name) for name in self.model_fields}
        for name in SENSITIVE_FIELDS:
            value = payload.get(name)
            if value:
                text = str(value)
                payload[name] = text[:4] + "****" if len(text) > 4 else "****"
        return payload


@lru_cache()
def get_settings() -> Settings:
    """获取应用配置单例"""
    try:
        s = Settings()
        return s
    except ValueError as e:
        # 配置验证失败，提供清晰的错误信息
        raise RuntimeError(
            f"配置加载失败：{e}\n"
            f"请检查.env文件并确保所有必需配置正确设置。"
        ) from e
    except Exception as e:
        raise RuntimeError(
            f"配置初始化错误：{e}\n"
            f"请确保.env文件存在且格式正确。"
        ) from e


def check_config_health() -> dict:
    """检查配置健康状态，返回诊断信息"""
    result = {
        "status": "healthy",
        "issues": [],
        "warnings": [],
        "env_file": None,
    }

    try:
        settings = get_settings()
        result["env_file"] = settings.get_env_file_path()

        # 检查生产环境必需配置
        prod_issues = settings.validate_required_for_production()
        if prod_issues:
            result["warnings"].extend(prod_issues)

        # 检查API连通性相关配置
        if not settings.LLM_API_BASE_URL:
            result["issues"].append("LLM_API_BASE_URL未配置")

        if settings.VECTOR_STORE_PROVIDER == "qdrant" and not settings.QDRANT_URL:
            result["issues"].append("使用Qdrant但未配置QDRANT_URL")

        # 更新总体状态
        if result["issues"]:
            result["status"] = "unhealthy"
        elif result["warnings"]:
            result["status"] = "warning"

    except Exception as e:
        result["status"] = "error"
        result["issues"].append(f"配置加载失败: {str(e)}")

    return result
