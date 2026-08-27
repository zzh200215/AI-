# 配置管理指南

## 概述

本项目使用基于 Pydantic 的配置管理系统，支持环境变量和 `.env` 文件配置。

## 快速开始

### 1. 创建配置文件

```bash
# 复制示例配置
cp .env.example .env
```

### 2. 配置必需项

编辑 `.env` 文件，至少配置以下必需项：

```env
# 安全密钥（至少32字符）
SECRET_KEY=your-strong-random-secret-key-here

# LLM API密钥
LLM_API_KEY=your-dashscope-api-key-here

# 管理员账号
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-strong-admin-password
```

### 3. 运行配置检查

```bash
# 诊断配置问题
python scripts/check_config.py
```

## 配置项说明

### 核心配置

#### 数据库配置

```env
# MySQL数据库连接
DATABASE_URL=mysql+pymysql://root:password@localhost:3306/aibg

# Docker环境使用容器主机名
DATABASE_URL_DOCKER=mysql+pymysql://root:password@mysql:3306/aibg
```

#### Redis配置

```env
REDIS_URL=redis://localhost:6379/0
```

#### 安全密钥配置

```env
# JWT签名密钥（必需，至少32字符）
SECRET_KEY=your-secret-key

# 连接器凭证加密密钥（可选，为空时从SECRET_KEY派生）
# 生成方法：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
CONNECTOR_CREDENTIAL_ENCRYPTION_KEY=

# 法律数据加密密钥（可选）
LEGAL_DATA_ENCRYPTION_KEY=
# 生产轮换：{"v1":"<base64-32-byte-key>","v2":"<base64-32-byte-key>"}
LEGAL_DATA_ENCRYPTION_KEYS_JSON=
LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION=v1

# 统一密钥提供方（P1-A）：env（默认）/ kms（云 KMS/Secret Manager 适配器骨架）
SECRET_PROVIDER=env
SECRET_KMS_REGION=
SECRET_KMS_ENDPOINT=
SECRET_KMS_PREFIX=aibg
```

密钥读取统一走 `app/core/secrets`（SecretProvider 接口）：默认 env 实现与上述环境变量
语义完全兼容；`kms` 为可插拔骨架，未配置 `SECRET_KMS_REGION`/`SECRET_KMS_ENDPOINT`
时构造即报错（不伪造已接入真实云 KMS）。密钥轮换流程、审计与受控摘除见
`docs/secret-management.md`，轮换命令 `python -B scripts/rotate_encryption_key.py --help`。

### 文件上传安全（P1-B）

所有上传端点共用统一安全入口（内容嗅探 MIME + 流式大小上限 + zip-bomb 审查 +
病毒扫描），详见 `docs/upload-security.md`。配置项：

```env
# 单文件大小上限（MB）
DOCUMENT_MAX_UPLOAD_MB=50
# 批量上传总大小上限（MB）：batch-upload 逐文件累计，超过即整体拒绝
DOCUMENT_MAX_BATCH_TOTAL_MB=200
# 扩展名白名单（逗号分隔，与内容嗅探交叉校验；不信任客户端 Content-Type）
DOCUMENT_ALLOWED_EXTENSIONS=pdf,docx,xlsx,md,txt,png,jpg,jpeg,bmp,webp
# 病毒扫描：默认关闭（Noop"未配置扫描器"，不伪造结果）；生产开启 + ClamAV clamd
DOCUMENT_VIRUS_SCAN_ENABLED=false
DOCUMENT_CLAMAV_SOCKET=/var/run/clamav/clamd.ctl
# zip-bomb 防护（docx/xlsx 等 ZIP 容器，只读中央目录不实际解压）
DOCUMENT_ZIP_MAX_ENTRIES=500
DOCUMENT_ZIP_MAX_TOTAL_UNCOMPRESSED_MB=200
DOCUMENT_ZIP_MAX_COMPRESSION_RATIO=1000.0
DOCUMENT_ZIP_MAX_NESTING=2
```

### LLM配置

```env
# 提供商类型：openai_compatible 或 ollama
LLM_PROVIDER=openai_compatible

# API配置
LLM_API_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=your-api-key

# 模型选择
LLM_MODEL=qwen-plus
EMBEDDING_MODEL=text-embedding-v3

# 短、低风险文本默认优先走小模型；复杂/法律/Agent/RAG 请求固定使用 LLM_MODEL。
# 小模型地址和密钥为空时复用主模型的 OpenAI 兼容地址及密钥。
# qwen-turbo 仅是千问小模型示例，免费额度以账号实际套餐为准。
LLM_MODEL_ROUTING_ENABLED=true
LLM_SMALL_MODEL=qwen-turbo
LLM_SMALL_MODEL_PROVIDER=openai_compatible
LLM_SMALL_MODEL_API_BASE_URL=
LLM_SMALL_MODEL_API_KEY=
LLM_SIMPLE_REQUEST_MAX_CHARS=600
LLM_PRIMARY_REQUEST_RETRIES=2
LLM_FALLBACK_REQUEST_RETRIES=1
LLM_REQUEST_TIMEOUT_SECONDS=60
LLM_MODEL_FALLBACK_ENABLED=true
LLM_SMALL_MODEL_FALLBACK_TO_PRIMARY=true
# 版本化模型灰度：规则由管理员 API 存储，候选密钥只从部署环境读取。
LLM_MODEL_RELEASES_ENABLED=true
LLM_CANARY_API_KEY=
LLM_ROUTING_ALERT_MIN_REQUESTS=10
LLM_ROUTING_ALERT_PRIMARY_FAILURE_RATE=0.20
LLM_ROUTING_ALERT_FALLBACK_FAILURE_RATE=0.30

# 定价配置（JSON格式）
LLM_MODEL_PRICING={"qwen-plus":{"input_per_1k":0.004,"output_per_1k":0.012}}
```

模型降级只针对连接、读取超时、协议异常和服务端 5xx：主模型失败后切换小模型；短请求的小模型失败可回切主模型。4xx 参数/鉴权错误以及本地限流、Token 预算拒绝不会绕过治理。流式回答只有在第一个输出分片前失败时才切换模型。

模型升级使用 `model_releases` 的版本化发布记录，不直接替换 `LLM_MODEL`。候选模型需经过离线评测门禁后才可获得服务流量；A/B 分桶、Shadow Traffic、线上错误率/p95/成本门禁和回滚说明见 `docs/MODEL_ROUTING_AND_ROLLOUT.md`。候选模型定价缺失时，成本约束会拒绝其获得流量。

管理员可通过 `GET /api/analytics/llm-routing/stats` 查看路由后的运行统计：小模型首选命中率、主模型初次调用失败率、降级次数与成功率、按模型成本占比，以及按 action 的尝试平均耗时。统计依赖迁移 `20260730_0051` 后新增的调用日志字段；迁移前的历史日志保留可查，但不会纳入这些路由指标。

`GET /api/analytics/llm-routing/health` 返回最近 1–24 小时的路由健康快照。窗口内请求数达到 `LLM_ROUTING_ALERT_MIN_REQUESTS` 后，主模型初次调用失败率达到 `LLM_ROUTING_ALERT_PRIMARY_FAILURE_RATE`，或备用模型失败率达到 `LLM_ROUTING_ALERT_FALLBACK_FAILURE_RATE`，将返回 `degraded`；运营告警任务会将其作为脱敏高优先级告警推送至已配置的 Webhook。

#### LLM 出站数据保护（P0）

所有出站 LLM 请求（chat/generate/structured_generate/视觉/embedding/流式）在发送前
经过统一安全网关（`app/services/llm/llm_outbound_gate.py`）：按 action 分级 +
PII 检测/脱敏 + 极敏感拦截。配置项：

```env
# PII 检测/脱敏开关：默认启用；仅开发调试可关闭，生产不得关闭。
LLM_OUTBOUND_DLP_ENABLED=true
# DLP 审计开关：关闭时跳过 data_level/pii 统计等审计字段（基础 LLMCallLog 审计照旧）。
LLM_OUTBOUND_AUDIT_ENABLED=true
# highly_sensitive 显式放行 action 名单（JSON 数组）。默认空 = 全部拦截（deny-by-default）。
LLM_OUTBOUND_HIGHLY_SENSITIVE_ACTIONS_JSON=[]
# action → 基础数据等级覆盖（JSON 对象，键为精确 action，值必须为
# public/internal/sensitive/highly_sensitive 之一）。
LLM_OUTBOUND_ACTION_DATA_LEVEL_JSON={}
# 检测服务异常时的默认行为：block（fail closed，阻断全部出站并记录原因）/
# warn（放行并记录，仅逃生通道，不保证 PII 不外泄）。
LLM_OUTBOUND_DLP_FAILURE_ACTION=block
# 规则检测器版本（审计/对账用）。
LLM_OUTBOUND_DLP_RULES_VERSION=rule-based-v1
```

分级规则（集中定义于 `app/core/data_levels.py`，禁止业务代码散落字符串判断）：

- 基础等级：`embedding → public`；`chat/chat_stream/generate/generate_with_images/rag_* → internal`；
  `legal_*/document_*/meeting_*/email_*/task_*/agent_* → sensitive`；未知 action → `sensitive`（deny-by-default）。
- 内容升级：命中任何 PII 规则 → 至少 `sensitive`；命中 high/critical 严重度规则
  （身份证/银行卡/令牌/密码）→ `highly_sensitive`。
- 极敏感默认拦截：`highly_sensitive` 内容仅当 action 在放行名单内才允许发送，且仍先脱敏。

审计：`llm_call_logs` 新增 `provider`、`data_level`、`pii_hit_codes`（仅规则 code，JSON）、
`pii_hit_count`、`redacted_count`、`blocked_reason`（迁移 `20261110_0082`）。
审计日志与请求摘录均不含原始 PII 与完整提示词。

详见 `docs/llm-outbound-data-protection.md`。

### Webhook 统一验签（P1-C）

所有入站回调（签署/支付/飞书）统一经 `app/core/webhook_verifier.py` 验签，
详见 `docs/webhook-security.md`。共享配置：

```env
# 时间戳新鲜度窗口（秒）：过期/未来超出即拒绝（签署/支付/飞书共用默认）
WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS=300
# nonce 去重 TTL（秒）：webhook_nonces 表（DB 共享存储，多实例有效）
WEBHOOK_REPLAY_TTL_SECONDS=3600
```

端点专属密钥（未配置即 fail-closed 拒绝）：`SIGNING_WEBHOOK_SECRETS_JSON`
（签署）、`PAYMENT_WEBHOOK_SECRET`（支付，见"支付/订阅可靠性"节）、
`FEISHU_EVENT_ENCRYPT_KEY` + `FEISHU_CALLBACK_VERIFY`（飞书，生产建议 v2）。

### 安全测试与攻击面（P1-D）

自动化攻击面回归见 `docs/security-testing-attack-surface.md`。新增配置：

```env
# SSRF 防护：出站 URL 目标校验（fail-closed）；仅内网直连出站场景显式关闭（属降级）
SSRF_GUARD_ENABLED=true
# JWT issuer/audience（可选，需成对配置）：签发强制写入、校验强制核对
JWT_ISSUER=
JWT_AUDIENCE=
```

### 向量数据库配置

```env
# 提供商：chroma 或 qdrant
VECTOR_STORE_PROVIDER=chroma

# Chroma配置
CHROMA_PERSIST_DIR=./data/chroma_db

# Qdrant配置（使用qdrant时需要）
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=your-qdrant-key
```

### 管理员配置

```env
ADMIN_USERNAME=admin
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=strong-password-here
```

## 配置验证

系统在启动时会自动验证配置：

### 必需配置检查

- `SECRET_KEY`: 至少32字符
- `LLM_API_KEY`: 至少16字符，不能为示例值
- 加密密钥长度验证

### JSON格式验证

- `LLM_MODEL_PRICING`: 必须为有效JSON对象
- `SIGNING_WEBHOOK_SECRETS_JSON`: 必须为有效JSON对象

### 生产环境检查

运行 `scripts/check_config.py` 会检查：

- 是否使用SQLite（生产环境建议MySQL/PostgreSQL）
- 是否配置Redis
- 管理员账号是否配置

## 使用配置诊断工具

```bash
# 运行完整诊断
python scripts/check_config.py
```

诊断工具会检查：

1. **环境文件检查**: 确认 `.env` 文件存在
2. **配置加载检查**: 验证配置是否能正确加载
3. **健康状态检查**: 检查生产环境必需配置
4. **密钥生成**: 提供安全密钥生成示例

### 输出示例

```
============================================================
  配置健康检查
============================================================
✓ 整体状态: HEALTHY
  使用配置文件: /path/to/.env

✓ 所有配置检查通过
```

## 常见问题

### 1. 配置加载失败

**错误**: `配置加载失败：LLM_API_KEY必须配置有效的API密钥`

**解决**:
- 检查 `.env` 文件是否存在
- 确认 `LLM_API_KEY` 不是示例值（如 `your-api-key`）
- 确认API密钥长度至少16字符

### 2. SECRET_KEY验证失败

**错误**: `SECRET_KEY长度至少需要32字符以确保安全性`

**解决**:
```bash
# 生成强随机密钥
python -c "import secrets; print(secrets.token_urlsafe(32))"

# 将生成的密钥复制到.env文件
SECRET_KEY=生成的密钥
```

### 3. JSON格式错误

**错误**: `LLM_MODEL_PRICING格式错误`

**解决**:
- 确保值是有效的JSON格式
- 不要有多余的逗号或引号
- 可以使用在线JSON验证工具检查

### 4. 生产环境警告

**警告**: `使用默认SQLite数据库，生产环境请配置MySQL/PostgreSQL`

**解决**:
```env
# 配置MySQL
DATABASE_URL=mysql+pymysql://user:pass@host:3306/dbname
```

## API密钥安全

### 最佳实践

1. **不要提交实际密钥到版本控制**
   - `.env` 文件已在 `.gitignore` 中
   - 只提交 `.env.example` 模板

2. **使用强随机密钥**
   ```bash
   # SECRET_KEY
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   
   # Fernet密钥
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

3. **定期轮换密钥**
   - 生产环境建议定期更换API密钥
   - 配置独立的加密密钥便于轮换

4. **权限控制**
   ```bash
   # Linux/Mac: 限制.env文件权限
   chmod 600 .env
   ```

## 环境变量优先级

配置加载顺序（后者覆盖前者）：

1. 代码中的默认值
2. `.env` 文件
3. 系统环境变量

示例：
```bash
# 临时覆盖配置
LLM_MODEL=qwen-turbo python -m uvicorn app.main:app
```

## 开发环境 vs 生产环境

### 开发环境

```env
DATABASE_URL=sqlite:///./data/app.db
DATABASE_ECHO=true  # 显示SQL日志
LLM_PROVIDER=ollama  # 使用本地模型
```

### 生产环境

```env
DATABASE_URL=mysql+pymysql://user:pass@host:3306/db
DATABASE_ECHO=false
LLM_PROVIDER=openai_compatible
# 配置所有必需的加密密钥
CONNECTOR_CREDENTIAL_ENCRYPTION_KEY=...
LEGAL_DATA_ENCRYPTION_KEY=...
```

## 代码使用示例

### 获取配置

```python
from app.core.config import get_settings

settings = get_settings()
print(settings.LLM_MODEL)
```

### 健康检查

```python
from app.core.config import check_config_health

health = check_config_health()
print(health['status'])  # healthy, warning, unhealthy, error
print(health['issues'])  # 问题列表
print(health['warnings'])  # 警告列表
```

## 配置扩展

如需添加新配置项：

1. 在 `app/core/config.py` 的 `Settings` 类中添加字段
2. 在 `.env.example` 中添加说明和默认值
3. 如需验证，添加 `@field_validator` 装饰器
4. 更新本文档

示例：
```python
class Settings(BaseSettings):
    # 新增配置
    NEW_CONFIG_ITEM: str = "default_value"
    
    @field_validator("NEW_CONFIG_ITEM")
    @classmethod
    def validate_new_config(cls, v: str) -> str:
        if not v:
            raise ValueError("NEW_CONFIG_ITEM不能为空")
        return v
```

## API 契约与 OpenAPI 门禁（P1）

OpenAPI 规范**完全由代码与 schema 自动生成**，禁止手工维护文档：

```bash
# 1. 导出当前规范到基准快照（提交到仓库）
python scripts/export_openapi.py            # → docs/openapi-snapshot.json

# 2. 检查漂移（CI job: contract-check）
python scripts/check_openapi_contract.py    # 0=通过；1=breaking 未批准；2=缺快照
```

- 快照路径：`docs/openapi-snapshot.json`（含 `x-error-codes` 错误码注册表扩展）。
- breaking 分类：删除 endpoint/method/参数/响应/必填字段/枚举值/错误码 → 必须
  `docs/openapi-breaking-approvals.json` 显式批准（kind/target/reason/approved_until）或 `--update` 重基线；
  纯新增（新端点/新可选字段/新错误码）→ 放行并打印。
- operationId 必须唯一（重复即 CI 失败）。
- WebSocket 无法由 OpenAPI 表达：契约基准为 `docs/websocket-protocol.md` +
  `docs/ws-events.schema.json`，CI job `ws-protocol-check`（pytest）校验与实现常量一致。
- 版本策略：内部 API 无版本前缀（首方 UI），响应头 `X-API-Version: 1` 声明 envelope 版本；
  开放平台外部 API 使用路径版本 `/api/open/v1/*`；OpenAPI `info.x-api-version = "1"`。

## 前端工程化与请求状态（P1/P2）

- 统一查询层 `frontend/src/query/`（零依赖）：`useQuery`（同 key 去重、缓存 staleTime、
  幂等 GET 指数退避重试、AbortController 取消、离线暂停/恢复、loading/error/stale/offline 状态、
  request_id/trace_id/ETag 元数据）、`useMutation`（Idempotency-Key 自动生成与复用、进行中连点合并、
  不自动重试、成功后精准失效）、`cache.js`（invalidate 谓词）、`keys.js`（query key 工厂）。
- 401 单飞刷新：`frontend/src/api/http.js` + `refresh.js`——并发 401 共享一次
  `POST /auth/refresh`（后端单次轮换），刷新失败只登出一次；登录时持久化 `refresh_token`。
- 错误规范化：`frontend/src/api/errors.js` 将后端稳定错误码映射为用户文案
  （network/timeout/offline/cancelled/unauthorized/forbidden/conflict/validation/rate_limit/server）。
- 长任务：`frontend/src/composables/useAsyncJob.js`（WS 事件优先 / 指数退避轮询降级；
  终态/卸载/离线/页面隐藏即停；job_id 恢复）；WS 客户端 `frontend/src/utils/wsClient.js`
  实现 P1 协议（welcome/ack/ping/subscribe/resume/断线重连）。
- Capability 共享契约：`frontend/src/auth/capabilities.js` 为前端单一契约（role→capability 字典），
  `useCapabilities()` 提供 can/canAny/canAll；权限未知（auth 未就绪）默认不放行；
  路由 `meta.capability` 由 App.vue 统一渲染 403 状态。**后端依赖**：`/auth/me` 返回 capabilities 后
  前端删除映射表直接读取后端列表（后端任务，未在本次实现）。
- 类型生成：`node scripts/gen-api-types.mjs`（读 `docs/openapi-snapshot.json` 生成
  `frontend/src/types/api.gen.js` JSDoc typedef，`--check` 做新鲜度门禁）；生成文件禁止手工编辑。
- Bundle 预算：`node scripts/check-bundle-budget.mjs`（入口 JS / 最大页面 chunk / 总资产，
  预算可经环境变量覆盖）；CI job `frontend-build` 运行 lint + 单测 + build + types:check + bundle:check。

## 相关文件

- `app/core/config.py`: 配置类定义和验证逻辑
- `.env.example`: 配置模板
- `scripts/check_config.py`: 配置诊断工具
- `scripts/export_openapi.py` / `scripts/check_openapi_contract.py`: OpenAPI 导出与门禁
- `docs/openapi-snapshot.json`: 契约基准快照
- `docs/websocket-protocol.md` / `docs/ws-events.schema.json`: WebSocket 协议契约
- `frontend/src/query/`、`frontend/src/api/{http,errors,refresh}.js`、`frontend/src/utils/{network,wsClient}.js`:
  前端请求状态层
- `frontend/src/auth/capabilities.js`、`frontend/src/composables/useCapabilities.js`: capability 契约
- `frontend/scripts/{gen-api-types,check-bundle-budget}.mjs`: 类型生成与 bundle 预算
- `docs/CONFIG.md`: 本文档
