from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func

from app.core.database import Base


class AgentEvalCandidate(Base):
    """A review-gated online failure sample for Agent regression evaluation."""

    __tablename__ = "agent_eval_candidates"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_agent_eval_candidates_dedupe_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=True, index=True)
    trace_id = Column(String(64), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    source_event_id = Column(Integer, ForeignKey("agent_audit_events.id"), nullable=True, index=True)
    failure_type = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending_review", index=True)
    goal_hash = Column(String(64), nullable=True)
    evidence_json = Column(Text, nullable=True)
    evaluation_input_json = Column(Text, nullable=True)
    expected_outcome_json = Column(Text, nullable=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    review_note = Column(Text, nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    exported_at = Column(DateTime(timezone=True), nullable=True)
    dedupe_key = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=True)
