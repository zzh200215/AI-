# 模型路由与灰度发布

## 目标

文本模型调用现在经过两层决策：基础路由先按请求复杂度和风险选择小模型或主模型；已激活的模型发布记录再在满足能力、时延和成本约束时进行 A/B 分桶或影子对照。候选模型的密钥不会写入数据库，只能经部署环境或密钥服务注入。

专用视觉和嵌入模型仍固定使用 `LLM_VISION_MODEL` / `EMBEDDING_MODEL`，避免把候选文本模型误用于不兼容的能力。

## 发布记录

`model_releases` 是版本化控制面。每条记录包括：

- `version`：不可复用的模型发布版本。
- `action`：精确 action 或 `*` 默认范围。
- `candidate_model`、provider、base URL：候选部署坐标，不含密钥。
- `max_complexity`、`max_risk_level`、`expected_latency_ms`、`max_cost_ratio`：候选模型的准入边界。
- `rollout_percentage`：稳定 A/B 分桶中真正服务候选模型的比例。
- `shadow_percentage`：控制组中异步发送到候选模型、但不影响用户响应的比例。
- `evaluation_gate`：离线评测报告引用、基线分数、候选分数和允许回归阈值。
- 在线门禁：最小样本量、最大错误率、p95 时延与成本倍率。

分桶键为 `release_version + action + user_id`；匿名调用使用 request ID。相同用户在同一发布中始终落入同一变体，避免一次会话内版本跳变。

## 安全与计费边界

候选模型只有同时满足以下条件才会获得流量：

1. 请求复杂度不超过 `max_complexity`。
2. 风险等级不超过 `max_risk_level`。法律、Agent、RAG、SQL 等动作默认归为高风险；敏感数据等级会继续上调风险。
3. 声明的候选时延不超过任务的超时预算。
4. `LLM_MODEL_PRICING` 已同时配置基线和候选模型，且预估成本倍率不超过 `max_cost_ratio`。

影子请求复用已通过 DLP 脱敏的载荷，但 `billable=False`：不会创建 `TokenUsage` 或成本台账记录，也不会影响主请求的结果、错误或时延。它仍写入 `LLMCallLog`，以便评估真实延迟、错误率和成本。

## 发布流程

1. 在离线评测中对候选模型和当前基线运行同一题集，并保留报告路径或 URI。
2. 创建草稿发布。首先使用 `rollout_percentage=0`、较小 `shadow_percentage` 启动影子流量；影子阶段不向用户暴露候选结果。
3. 写入离线评测门禁。任何 `rollout_percentage > 0` 的发布都必须有通过的门禁，候选分数不得低于 `baseline_score - max_regression`。
4. 激活发布后逐步扩大 `rollout_percentage`，并按版本执行线上门禁检查。
5. 门禁违反错误率、p95 时延或成本阈值时，执行检查接口会自动将发布标记为 `rolled_back`。若该 action 有上一条被替换的发布版本，会自动恢复该版本；否则后续调用立即回到基础路由。也可人工回滚。

主要管理接口：

```text
POST /api/admin/model-releases/
POST /api/admin/model-releases/{version}/evaluation-gate
POST /api/admin/model-releases/{version}/activate
POST /api/admin/model-releases/{version}/guardrail
POST /api/admin/model-releases/{version}/rollback
GET  /api/admin/model-releases/
```

管理员可通过 `GET /api/analytics/llm-calls?scope=all` 回放 `model_release_version`、`experiment_bucket`、`traffic_type`、`routing_reason`、`request_complexity` 和 `risk_level`，不包含提示词正文。

提示词版本不复用此表。现有 `/api/prompts/{template_id}/rollout`、`/activate` 和 `/rollback` 继续提供稳定用户分桶和一键回滚；调用日志中的 `prompt_template` / `prompt_version` 会与模型发布字段共同形成完整版本链。

## 配置与部署

```env
LLM_MODEL_RELEASES_ENABLED=true
# 为空时复用 LLM_API_KEY；候选模型使用独立账户时，通过部署密钥注入。
LLM_CANARY_API_KEY=
# 基线和候选模型都必须有定价，成本门禁才会放行候选流量。
LLM_MODEL_PRICING={"qwen-plus":{"input_per_1k":0.004,"output_per_1k":0.012},"qwen-max":{"input_per_1k":0.008,"output_per_1k":0.024}}
```

先执行迁移：

```bash
python -m alembic upgrade head
```

`LLM_MODEL_RELEASES_ENABLED=false` 会立即跳过发布控制面，保留既有的小模型/主模型路由与故障回退，可用作紧急总开关。
