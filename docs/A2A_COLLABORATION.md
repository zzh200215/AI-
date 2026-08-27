# 企业级 A2A 协作

本项目的 A2A 是内部控制面：它让稳定的 Supervisor/Worker 编排具备标准化发现、受约束的任务委派和跨 Agent 回放能力，但不把本地 Agent 暴露成未经配置的远程执行端点。

## Agent Card

- `GET /api/agent/cards` 返回受认证的 Card 列表；`GET /api/agent/cards/{agent_type}` 返回一个 Card。
- Card 使用 A2A 常见字段：`protocolVersion`、`skills`、`securitySchemes`、输入输出模式与运行能力。
- `x-aibg` 扩展声明当前 MCP 策略实际授予的工具、数据域、执行模式、内部集成面和审计方式。它不会声称存在外部 JSON-RPC 服务。
- 已披露的集成面为合同工作台、法务知识库、内部流程与客户门户；门户访问仅通过受控流程中介，不创建新的直接写入权限。

## 委派与授权

`POST /api/agent/runs/{run_id}/delegations` 创建子 Run 后立即通过接收 Agent 的既有受控工作流执行；`GET /api/agent/runs/{run_id}/delegations` 查询直属委派。

- 只能委派到注册的 `knowledge_agent`、`legal_compliance_agent` 或 `workflow_agent`。
- 子 Run 原样继承父 Run 的用户、组织、`trace_id`、授权快照和截止时间。A2A 仅接受绑定了有效授权快照的父 Run；快照失效、租户变化、父 Run 非活跃、循环谱系或超过 `A2A_MAX_DELEGATION_DEPTH` 时一律拒绝。
- 任务正文只保存在子 Run 内；委派台账和审计事件仅保存哈希、长度和结构化状态摘要。
- `idempotency_key` 在同一父 Run 下可重放相同任务，不同任务复用同一键会返回冲突。
- 受派 `workflow_agent` 触发 MCP 审批时，委派记录会转为 `awaiting_approval`；使用原审批恢复 API 后，子 Run 的终态会自动同步回委派台账。
- 委派并不绕过 MCP Policy-as-Code。接收 Agent 后续的工具调用仍必须经 MCP 权限、数据域、风险阈值和审批检查。

## 跨 Agent 审计

`GET /api/agent/runs/{run_id}/a2a-audit` 返回指定 Run 所在谱系的委派记录和全部结构化 Agent 审计事件，可根据共享 trace 串起创建、接收、执行、失败与完成。

相关事件为：`a2a_delegation_created`、`a2a_delegation_accepted`、`a2a_delegation_completed`、`a2a_delegation_failed`、`a2a_delegation_denied`。

部署本版本前执行：

```powershell
python -m alembic upgrade head
```
