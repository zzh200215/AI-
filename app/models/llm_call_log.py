from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base


class LLMCallLog(Base):
    __tablename__ = "llm_call_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    module_name = Column(String(64), nullable=False, index=True)
    action = Column(String(128), nullable=False, index=True)
    model_name = Column(String(128), nullable=False)
    prompt_template = Column(String(128), nullable=True, index=True)
    prompt_version = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    # 请求前统一 token 估算（治理层 enforce_* 计算），与实际 input/output 同记录以对比偏差。
    estimated_input_tokens = Column(Integer, nullable=True)
    estimated_output_tokens = Column(Integer, nullable=True)
    # 逻辑请求内的第几次 attempt（1 起）；重试/fallback 行与最终成功行据此区分。
    attempt_number = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False, default="success", index=True)
    request_id = Column(String(36), nullable=True, index=True)
    # P1 链路关联：trace_id/task_id/agent_run_id/org 由统一上下文写入，缺失为 NULL。
    trace_id = Column(String(64), nullable=True, index=True)
    task_id = Column(String(128), nullable=True, index=True)
    agent_run_id = Column(Integer, nullable=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    error_category = Column(String(32), nullable=True, index=True,
                            comment="稳定错误类别（classify_error_category 枚举），供聚合标签")
    routing_role = Column(String(16), nullable=True, index=True)
    routing_stage = Column(String(16), nullable=True, index=True)
    # Versioned model rollout attribution.  Shadow calls are audited but never billed to users.
    model_release_version = Column(String(128), nullable=True, index=True)
    experiment_bucket = Column(Integer, nullable=True)
    traffic_type = Column(String(16), nullable=True, index=True)
    routing_reason = Column(String(512), nullable=True)
    request_complexity = Column(String(16), nullable=True)
    risk_level = Column(String(16), nullable=True)
    error_message = Column(Text, nullable=True)
    request_excerpt = Column(Text, nullable=True)
    response_excerpt = Column(Text, nullable=True)
    # P0 出站数据保护审计：目标提供方 / 数据等级 / PII 命中规则 / 命中与脱敏数量 / 拦截原因。
    # 命中规则只存规则 code（JSON 数组字符串），绝不存原始 PII 或完整提示词。
    provider = Column(String(64), nullable=True)
    data_level = Column(String(32), nullable=True, index=True)
    pii_hit_codes = Column(Text, nullable=True)
    pii_hit_count = Column(Integer, nullable=False, default=0)
    redacted_count = Column(Integer, nullable=False, default=0)
    blocked_reason = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
