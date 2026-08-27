from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, func

from app.core.database import Base


class ModelRelease(Base):
    """A versioned, auditable candidate-model rollout for one LLM action scope.

    Provider credentials deliberately stay in environment/secret storage.  A release
    only stores the public deployment coordinates and traffic/guardrail policy.
    """

    __tablename__ = "model_releases"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    version = Column(String(128), nullable=False, unique=True, index=True)
    action = Column(String(128), nullable=False, index=True, default="*")
    candidate_model = Column(String(128), nullable=False)
    candidate_provider = Column(String(64), nullable=True)
    candidate_base_url = Column(String(512), nullable=True)

    # Runtime eligibility: candidate must satisfy these declared capacity limits.
    max_complexity = Column(String(16), nullable=False, default="complex")
    max_risk_level = Column(String(16), nullable=False, default="medium")
    expected_latency_ms = Column(Integer, nullable=True)
    max_cost_ratio = Column(Float, nullable=False, default=1.0)

    # Delivery and online guardrails.
    rollout_percentage = Column(Integer, nullable=False, default=0)
    shadow_percentage = Column(Integer, nullable=False, default=0)
    min_sample_size = Column(Integer, nullable=False, default=100)
    max_error_rate = Column(Float, nullable=False, default=0.05)
    max_p95_latency_ms = Column(Integer, nullable=True)
    evaluation_status = Column(String(16), nullable=False, default="pending", index=True)
    evaluation_gate_json = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="draft", index=True)
    previous_release_id = Column(Integer, ForeignKey("model_releases.id"), nullable=True, index=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    activated_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    rolled_back_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    activated_at = Column(DateTime(timezone=True), nullable=True, index=True)
    rolled_back_at = Column(DateTime(timezone=True), nullable=True)
