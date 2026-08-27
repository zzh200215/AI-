"""统一工具契约：每个工具的声明式元数据（读/写、审批、超时、重试、幂等、成本、副作用、补偿、审计）。

安全默认：工具未声明契约时按“写操作、需审批、不可重试、不可补偿”处理，绝不默认放行。
真实工具必须显式声明 ``contract``；``AgentToolExecutor`` 是唯一执行入口，依据契约执行
超时/重试/幂等/审批/审计。禁止工具实现自身绕过执行器直接产生副作用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 未知成本：显式为空，禁止伪造。
_UNKNOWN_COST: dict[str, Any] = {"currency": None, "estimate": None, "note": "unknown"}


@dataclass(frozen=True)
class ToolContract:
    name: str
    # 读/写分类：read_only 表示“不改变外部状态”。写工具默认需审批。
    read_only: bool = True
    # 审批：None 表示按项目既有策略（HIGH_RISK_TOOLS）决定；显式 True/False 覆盖。
    requires_approval: bool | None = None
    version: str = "1"
    # 输入/输出 JSON Schema（由工具 parameters 派生，此处仅记录）。
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    # 执行控制。
    timeout_seconds: int | None = None          # None → 使用 AGENT_TOOL_TIMEOUT_SECONDS
    max_retries: int = 0                        # 0 = 不重试
    backoff_base_seconds: float = 2.0
    retryable: bool = False                     # 仅暂时性错误类别可重试
    # 幂等：写工具支持幂等键（由执行器按 run/step/tool/input_hash 维护）。
    idempotency_keyed: bool = False
    # 有副作用且无可靠幂等能力 → 标记为不可安全重试。
    safely_retryable: bool = True
    cancellable: bool = False
    compensable: bool = False
    compensation_handler: str | None = None     # 方法名；None 且 compensable=True 时不可补偿
    side_effect: str = "none"
    cost: dict[str, Any] | None = field(default_factory=lambda: dict(_UNKNOWN_COST))
    audit_level: str = "summary"                # "summary" | "full"
    sensitive_fields: tuple[str, ...] = ()


# 未声明契约的安全兜底：视为写操作、需审批、不可重试、不可补偿。
DEFAULT_CONTRACT = ToolContract(
    name="",
    read_only=False,
    requires_approval=True,
    max_retries=0,
    retryable=False,
    idempotency_keyed=False,
    safely_retryable=False,
    cancellable=False,
    compensable=False,
    side_effect="unknown",
    cost=_UNKNOWN_COST,
)


def resolve_contract(tool: Any) -> ToolContract:
    """读取工具声明的契约；未声明时返回安全兜底 DEFAULT_CONTRACT。"""
    contract = getattr(tool, "contract", None)
    if isinstance(contract, ToolContract) and contract.name:
        return contract
    return DEFAULT_CONTRACT


def requires_approval_for(tool_name: str, contract: ToolContract | None) -> bool:
    """审批决策：契约显式声明优先；否则回退到项目既有名称策略（HIGH_RISK_TOOLS）。

    保持向后兼容：未迁移的 legacy 工具/测试 FakeTool 仍按既有名称策略判定，
    已声明契约的写工具一律需审批。
    """
    from app.mcp.policy import policy_engine

    # ``DEFAULT_CONTRACT`` represents a legacy/test tool without a declared
    # contract. Do not let its fail-closed write defaults override the
    # versioned MCP policy for a tool that already has an explicit rule.
    has_declared_contract = contract is not None and bool(contract.name)
    if has_declared_contract and contract.requires_approval is not None:
        return contract.requires_approval
    if has_declared_contract and not contract.read_only:
        return True
    # 回退：既有 AgentApprovalService.HIGH_RISK_TOOLS
    return policy_engine.evaluate(
        agent_type="general_agent", tool_name=tool_name
    ).requires_approval
