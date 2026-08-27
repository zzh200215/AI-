from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func

from app.core.database import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=True, index=True)
    goal = Column(Text, nullable=False)
    status = Column(String(32), default="running", nullable=False)
    result = Column(Text, nullable=True)
    workflow_state = Column(Text, nullable=True)
    workflow_state_updated_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    cancel_reason = Column(Text, nullable=True)
    final_answer = Column(Text, nullable=True)
    last_observation = Column(Text, nullable=True)
    failure_reason = Column(Text, nullable=True)
    total_steps = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    # 长流程权限快照：Agent 执行期间权限范围保持稳定，硬撤销立即终止。
    authorization_snapshot_id = Column(String(64), nullable=True, index=True)
    # 可观测性与租户隔离：run 级 trace_id 与所属组织。
    trace_id = Column(String(64), nullable=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    # A2A 谱系：顶层 Run 由 supervisor_agent 发起；子 Run 始终继承父 Run 的
    # 用户、组织、trace 与授权快照，不能借委派获得更宽权限。
    agent_type = Column(String(64), nullable=True, index=True)
    parent_run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=True, index=True)
    delegation_id = Column(String(64), nullable=True, unique=True, index=True)
    run_deadline_at = Column(DateTime(timezone=True), nullable=True)
    retry_of_run_id = Column(Integer, nullable=True)
    compensation_status = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)


class ToolCallLog(Base):
    __tablename__ = "tool_call_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    agent_run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=False, index=True)
    step = Column(Integer, nullable=True)
    action_type = Column(String(32), nullable=True)
    thought = Column(Text, nullable=True)
    tool_name = Column(String(128), nullable=False)
    input_params = Column(Text, nullable=True)
    raw_decision = Column(Text, nullable=True)
    observation = Column(Text, nullable=True)
    output_result = Column(Text, nullable=True)
    status = Column(String(32), default="success", nullable=False)
    error = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AgentApprovalRequest(Base):
    __tablename__ = "agent_approval_requests"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    agent_run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    tool_name = Column(String(128), nullable=False, index=True)
    agent_type = Column(String(64), nullable=True, index=True)
    input_params = Column(Text, nullable=True)
    risk_level = Column(String(32), default="high", nullable=False)
    status = Column(String(32), default="pending", nullable=False, index=True)
    approval_token = Column(String(128), nullable=False, unique=True, index=True)
    decision_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    decided_at = Column(DateTime(timezone=True), nullable=True)
    # 审批生命周期加固：绑定步骤与参数摘要，支持过期/撤销/操作者追溯。
    step_id = Column(Integer, nullable=True)
    param_digest = Column(String(64), nullable=True)
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoke_reason = Column(Text, nullable=True)
    policy_version = Column(String(64), nullable=True, index=True)
    data_scope = Column(String(64), nullable=True)


class AgentAuditEvent(Base):
    """结构化审计事件：run/step/trace 维度，记录计划决策、权限决策、工具执行、
    审批、状态变更、重试/超时/取消/补偿与错误分类。可查询，不做事件溯源。"""

    __tablename__ = "agent_audit_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=True, index=True)
    step = Column(Integer, nullable=True)
    trace_id = Column(String(64), nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    organization_id = Column(Integer, nullable=True)
    tool_name = Column(String(128), nullable=True)
    tool_version = Column(String(32), nullable=True)
    decision_json = Column(Text, nullable=True)
    summary_json = Column(Text, nullable=True)
    error_category = Column(String(64), nullable=True)
    status = Column(String(32), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class A2ADelegation(Base):
    """Internal A2A control-plane record.

    The task body stays on the child ``AgentRun`` under the same authorization
    boundary. This ledger deliberately stores only a redacted summary and a
    content hash so audit replay does not become a second copy of legal data.
    """

    __tablename__ = "a2a_delegations"
    __table_args__ = (UniqueConstraint("parent_run_id", "idempotency_key", name="uq_a2a_delegations_parent_key"),)

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    delegation_id = Column(String(64), nullable=False, unique=True, index=True)
    parent_run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=False, index=True)
    child_run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=True, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    trace_id = Column(String(64), nullable=True, index=True)
    authorization_snapshot_id = Column(String(64), nullable=True, index=True)
    from_agent_type = Column(String(64), nullable=False, index=True)
    to_agent_type = Column(String(64), nullable=False, index=True)
    task_type = Column(String(64), nullable=False, default="analysis")
    status = Column(String(32), nullable=False, default="accepted", index=True)
    idempotency_key = Column(String(128), nullable=True)
    input_hash = Column(String(64), nullable=False, index=True)
    task_summary_json = Column(Text, nullable=True)
    result_summary_json = Column(Text, nullable=True)
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
