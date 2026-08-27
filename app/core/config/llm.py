"""LLM provider / model routing / rate-limit settings."""

import json

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class LLMSettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    LLM_PROVIDER: str = "openai_compatible"
    LLM_API_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_API_KEY: str = ""
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    LLM_MODEL: str = "qwen-plus"
    LLM_VISION_MODEL: str = ""
    EMBEDDING_MODEL: str = "text-embedding-v3"
    # 简单请求优先走小模型；主模型服务不可用时可降级到小模型。
    # 小模型地址和密钥留空时复用 LLM_API_BASE_URL / LLM_API_KEY。
    LLM_MODEL_ROUTING_ENABLED: bool = True
    LLM_SMALL_MODEL: str = ""
    LLM_SMALL_MODEL_PROVIDER: str = "openai_compatible"
    LLM_SMALL_MODEL_API_BASE_URL: str = ""
    LLM_SMALL_MODEL_API_KEY: str = ""
    LLM_SIMPLE_REQUEST_MAX_CHARS: int = Field(default=600, ge=64, le=4000)
    LLM_PRIMARY_REQUEST_RETRIES: int = Field(default=2, ge=1, le=5)
    LLM_FALLBACK_REQUEST_RETRIES: int = Field(default=1, ge=1, le=3)
    LLM_REQUEST_TIMEOUT_SECONDS: int = Field(default=60, ge=5, le=180)
    LLM_MODEL_FALLBACK_ENABLED: bool = True
    LLM_SMALL_MODEL_FALLBACK_TO_PRIMARY: bool = True
    # 模型发布控制面：发布规则本身存储在 model_releases，凭据始终只来自环境/密钥服务。
    # 候选模型未单独配 Key 时复用 LLM_API_KEY；不要把任何密钥写进发布记录。
    LLM_MODEL_RELEASES_ENABLED: bool = True
    LLM_CANARY_API_KEY: str = ""
    LLM_ROUTING_ALERT_MIN_REQUESTS: int = Field(default=10, ge=1, le=10000)
    LLM_ROUTING_ALERT_PRIMARY_FAILURE_RATE: float = Field(default=0.2, ge=0.0, le=1.0)
    LLM_ROUTING_ALERT_FALLBACK_FAILURE_RATE: float = Field(default=0.3, ge=0.0, le=1.0)
    LLM_PRICE_CURRENCY: str = "CNY"
    LLM_MODEL_PRICING: str = (
        '{"qwen-plus":{"input_per_1k":0.004,"output_per_1k":0.012},'
        '"text-embedding-v3":{"input_per_1k":0.0005,"output_per_1k":0.0}}'
    )
    LLM_RATE_LIMIT_WINDOW_SECONDS: int = 60
    LLM_RATE_LIMIT_MAX_REQUESTS: int = 20
    LLM_DAILY_REQUEST_LIMIT: int = 200
    LLM_DAILY_TOKEN_LIMIT: int = 300000
    LLM_ESTIMATED_CHARS_PER_TOKEN: int = 4
    LLM_ESTIMATED_COMPLETION_TOKENS: int = 1200
    LLM_LIMIT_REDIS_PREFIX: str = "aibg:llm-governance"

    # 独立预算桶 / 限流桶：category → 限额（JSON dict）。未配置的 category 回退到下方全局默认值。
    # budget category: text/embedding/vision/rerank；rate-limit category: chat/embedding/vision/rerank。
    # 例：LLM_BUDGET_LIMITS_JSON={"text":{"daily_requests":200,"daily_tokens":300000}}
    #     LLM_RATE_LIMIT_CONFIG_JSON={"chat":{"window_seconds":60,"max_requests":20}}
    LLM_BUDGET_LIMITS_JSON: str = "{}"
    LLM_RATE_LIMIT_CONFIG_JSON: str = "{}"
    # LLM 响应缓存：仅显式标记 cacheable 的幂等请求默认走缓存；聊天/含敏感上下文默认不缓存。
    # 默认进程内 LRU + TTL（多实例各持一份，无跨实例失效）；启用 Redis 后跨实例共享（非强一致）。
    LLM_RESPONSE_CACHE_ENABLED: bool = True
    LLM_RESPONSE_CACHE_TTL_SECONDS: int = Field(default=3600, ge=1, le=86400)
    LLM_RESPONSE_CACHE_CAPACITY: int = Field(default=256, ge=1, le=10000)
    LLM_RESPONSE_CACHE_REDIS_ENABLED: bool = False
    LLM_RESPONSE_CACHE_REDIS_PREFIX: str = "aibg:llm-response-cache"

    # ── P0 出站数据保护（统一安全网关：分级 + PII 检测/脱敏 + 极敏感拦截）──────────
    # 出站 LLM 请求统一安全网关开关：启用时对所有出站请求做数据分级 + PII 检测/脱敏。
    # 默认启用；关闭仅用于开发调试，生产不得关闭。
    LLM_OUTBOUND_DLP_ENABLED: bool = True
    # DLP 审计开关：关闭时跳过 data_level/pii 统计等审计字段（基础 LLMCallLog 审计照旧）。
    LLM_OUTBOUND_AUDIT_ENABLED: bool = True
    # highly_sensitive 显式放行 action 名单（JSON 数组）。默认空 = 全部拦截（deny-by-default）。
    # 例：LLM_OUTBOUND_HIGHLY_SENSITIVE_ACTIONS_JSON=["legal_contract_review"]
    LLM_OUTBOUND_HIGHLY_SENSITIVE_ACTIONS_JSON: str = "[]"
    # action → 基础数据等级覆盖（JSON 对象，键为精确 action，值为
    # public/internal/sensitive/highly_sensitive）。未配置的 action 走内置安全默认。
    # 例：LLM_OUTBOUND_ACTION_DATA_LEVEL_JSON={"chat":"internal","legal_contract_review":"sensitive"}
    LLM_OUTBOUND_ACTION_DATA_LEVEL_JSON: str = "{}"
    # 检测服务异常时的默认行为：block（fail closed，阻断全部出站并记录原因）/
    # warn（放行并记录，仅逃生通道，不保证 PII 不外泄）。
    LLM_OUTBOUND_DLP_FAILURE_ACTION: str = Field(default="block", pattern="^(block|warn)$")
    # 规则检测器版本（审计/对账用）。
    LLM_OUTBOUND_DLP_RULES_VERSION: str = "rule-based-v1"

    # 供应商熔断：仅超时/传输/5xx 计入；参数/鉴权/权限/内容拦截不计入。
    CIRCUIT_BREAKER_ENABLED: bool = True
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = Field(default=5, ge=1, le=100)
    CIRCUIT_BREAKER_COOLDOWN_SECONDS: float = Field(default=30.0, ge=0.0, le=3600.0)
    CIRCUIT_BREAKER_HALF_OPEN_MAX_CONCURRENCY: int = Field(default=1, ge=1, le=10)
    # 可选 Redis 后端（默认关闭，进程内即可运行）；跨实例不承诺强一致。
    CIRCUIT_BREAKER_REDIS_ENABLED: bool = False
    CIRCUIT_BREAKER_REDIS_PREFIX: str = "aibg:circuit-breaker"

    # 对话记忆调优参数
    CONVERSATION_MEMORY_RECENT_MESSAGES: int = Field(default=12, ge=4, le=40)
    CONVERSATION_MEMORY_SUMMARY_TRIGGER: int = Field(default=18, ge=8, le=100)
    CONVERSATION_MEMORY_SUMMARY_MAX_CHARS: int = Field(default=2400, ge=400, le=8000)
    CONVERSATION_MEMORY_MAX_PREFERENCES: int = Field(default=12, ge=1, le=50)
    CONVERSATION_MEMORY_AUTO_PREFERENCE_ENABLED: bool = True
    CONVERSATION_MEMORY_AUTO_PREFERENCE_MAX_ITEMS: int = Field(default=3, ge=1, le=5)

    @field_validator("LLM_API_KEY")
    @classmethod
    def validate_llm_api_key(cls, v: str) -> str:
        if not v or v in {"your-api-key", "your-dashscope-api-key", "sk-xxxxx"}:
            raise ValueError(
                "LLM_API_KEY必须配置有效的API密钥。请在.env文件中设置正确的值。"
            )
        if len(v) < 16:
            raise ValueError("LLM_API_KEY长度不足，请检查是否正确配置")
        return v

    @field_validator("LLM_MODEL_PRICING")
    @classmethod
    def validate_pricing_json(cls, v: str) -> str:
        try:
            pricing = json.loads(v)
            if not isinstance(pricing, dict):
                raise ValueError("LLM_MODEL_PRICING必须是有效的JSON对象")
            for model, prices in pricing.items():
                if not isinstance(prices, dict):
                    raise ValueError(f"模型 {model} 的定价配置必须是对象")
                if "input_per_1k" not in prices or "output_per_1k" not in prices:
                    raise ValueError(f"模型 {model} 缺少必需的定价字段")
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM_MODEL_PRICING格式错误：{e}")
        return v

    @field_validator("LLM_BUDGET_LIMITS_JSON")
    @classmethod
    def validate_budget_limits_json(cls, v: str) -> str:
        cfg = cls._parse_category_json(v, "LLM_BUDGET_LIMITS_JSON")
        allowed = {"daily_requests", "daily_tokens"}
        for category, limits in cfg.items():
            if not isinstance(limits, dict):
                raise ValueError(f"预算桶 {category} 的限额必须是对象")
            unknown = set(limits) - allowed
            if unknown:
                raise ValueError(f"预算桶 {category} 含未知限额键：{sorted(unknown)}")
        return v

    @field_validator("LLM_RATE_LIMIT_CONFIG_JSON")
    @classmethod
    def validate_rate_limit_config_json(cls, v: str) -> str:
        cfg = cls._parse_category_json(v, "LLM_RATE_LIMIT_CONFIG_JSON")
        allowed = {"window_seconds", "max_requests"}
        for category, limits in cfg.items():
            if not isinstance(limits, dict):
                raise ValueError(f"限流桶 {category} 的配置必须是对象")
            unknown = set(limits) - allowed
            if unknown:
                raise ValueError(f"限流桶 {category} 含未知配置键：{sorted(unknown)}")
        return v

    @field_validator("LLM_OUTBOUND_HIGHLY_SENSITIVE_ACTIONS_JSON")
    @classmethod
    def validate_highly_sensitive_actions_json(cls, v: str) -> str:
        if not v:
            return v
        try:
            actions = json.loads(v)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM_OUTBOUND_HIGHLY_SENSITIVE_ACTIONS_JSON格式错误：{e}") from e
        if not isinstance(actions, list) or not all(isinstance(item, str) and item.strip() for item in actions):
            raise ValueError("LLM_OUTBOUND_HIGHLY_SENSITIVE_ACTIONS_JSON必须是非空字符串数组")
        return v

    @field_validator("LLM_OUTBOUND_ACTION_DATA_LEVEL_JSON")
    @classmethod
    def validate_action_data_level_json(cls, v: str) -> str:
        if not v:
            return v
        try:
            cfg = json.loads(v)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM_OUTBOUND_ACTION_DATA_LEVEL_JSON格式错误：{e}") from e
        allowed = {"public", "internal", "sensitive", "highly_sensitive"}
        if not isinstance(cfg, dict):
            raise ValueError("LLM_OUTBOUND_ACTION_DATA_LEVEL_JSON必须是JSON对象")
        for action, level in cfg.items():
            if not isinstance(action, str) or not action.strip():
                raise ValueError("LLM_OUTBOUND_ACTION_DATA_LEVEL_JSON的键必须是action字符串")
            if level not in allowed:
                raise ValueError(f"action {action} 的数据等级必须是 {sorted(allowed)} 之一")
        return v

    @staticmethod
    def _parse_category_json(v: str, name: str) -> dict:
        try:
            cfg = json.loads(v)
        except json.JSONDecodeError as e:
            raise ValueError(f"{name}格式错误：{e}")
        if not isinstance(cfg, dict):
            raise ValueError(f"{name}必须是有效的JSON对象")
        return cfg
