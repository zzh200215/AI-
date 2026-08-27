import asyncio
import hashlib
import json
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.circuit_breaker import build_circuit_breaker, counts_toward_breaker
from app.core.config import get_settings
from app.core.llm_provider_adapter import provider_adapter
from app.core.model_policy import (
    ModelError,
    ModelErrorKind,
    ModelRequest,
    TaskPolicy,
    classify_error,
    get_task_policy,
    new_trace_id,
)
from app.core.observability_sanitizer import sanitize_observability_error_message, sanitize_observability_excerpt
from app.core.response_cache import build_response_cache
from app.core.structured_output import build_repair_prompt, normalize_schema, parse_structured_output
from app.services.llm.llm_governance_service import LLMGovernanceError, llm_governance_service
from app.services.llm.llm_outbound_gate import BLOCK_CODE, OutboundGateResult, outbound_gate
from app.services.llm.model_release_service import ModelReleaseDecision, model_release_service

settings = get_settings()
OPENAI_COMPATIBLE_EMBED_BATCH_SIZE = 10
DEFAULT_REQUEST_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.5

_COMPLEX_REQUEST_MARKERS = (
    "合同", "法律", "仲裁", "诉讼", "风险", "对比", "比较", "差异", "条款", "流程",
    "分析", "总结", "方案", "证据", "责任", "条件", "金额", "日期", "分别", "以及",
)
_PRIMARY_ACTION_PREFIXES = ("legal_", "agent_", "agentic_", "rag_", "text_to_sql")
_PRIMARY_ACTIONS = {"generate_with_images", "embedding"}


@dataclass(frozen=True)
class _ModelTarget:
    role: str
    model: str
    provider: str
    base_url: str
    api_key: str


@dataclass(frozen=True)
class _RoutePlan:
    """Serving route plus an optional fire-and-forget shadow comparison target."""

    targets: tuple[_ModelTarget, ...]
    decision: ModelReleaseDecision | None = None
    shadow_target: _ModelTarget | None = None

ACTION_PROMPT_TEMPLATE_MAP = {
    "document_summary": "document_summary",
    "document_risk_extract": "document_risk_extract",
    "document_todo_extract": "document_todo_extract",
    "document_clause_extract": "document_clause_extract",
    "document_compare": "document_compare",
    "meeting_summary": "meeting_summary",
    "meeting_decision_extract": "meeting_decision_extract",
    "meeting_topic_extract": "meeting_topic_extract",
    "email_generate": "email_generate",
    "email_reply": "email_reply",
    "email_tone_switch": "email_tone_switch",
    "email_thread_summary": "email_thread_summary",
    "email_polish": "email_polish",
    "task_extract_from_chat": "task_extract_from_chat",
    "task_decompose": "task_decompose",
    "rag_answer": "rag_answer",
    "legal_consultation": "legal_consultation",
    "legal_contract_review": "legal_contract_review",
    "legal_draft_generation": "legal_draft_generation",
    "legal_followup": "legal_followup",
    "legal_contract_compare": "legal_contract_compare",
    "agent_plan": "agent_system_prompt",
    "agent_plan_preview": "agent_plan_preview",
    "embedding": None,
}


class ModelGateway:
    """供应商无关的模型网关：chat/generate/vision/embedding 的唯一平台入口。

    路由、重试、fallback、治理、观测均在此收敛。底层按供应商目标隔离复用
    httpx.AsyncClient 连接池（_get_client/close）。LLMClient 为其兼容别名。
    """

    def __init__(self):
        self.provider = settings.LLM_PROVIDER
        self.base_url = self._resolve_base_url(provider=self.provider).rstrip("/")
        self.api_key = settings.LLM_API_KEY
        self.llm_model = settings.LLM_MODEL
        self.vision_model = settings.LLM_VISION_MODEL or settings.LLM_MODEL
        self.embedding_model = settings.EMBEDDING_MODEL
        self.primary_target = _ModelTarget(
            role="primary",
            model=self.llm_model,
            provider=self.provider,
            base_url=self.base_url,
            api_key=self.api_key,
        )
        self.small_target = self._build_small_target()
        # 连接池：key=(provider, base_url, api_key, timeout)，按供应商目标隔离复用。
        self._clients: dict[tuple[str, str, str, float], httpx.AsyncClient] = {}
        self._started = False
        self.circuit_breaker = build_circuit_breaker()
        # LLM 响应缓存：仅显式 cacheable 请求命中/写入（进程内 LRU + 可选 Redis）。
        self.response_cache = build_response_cache()

    def start(self) -> None:
        """幂等启动：预建主/小模型连接池客户端；真实连接在首次请求时建立。"""
        self._get_client(self.primary_target, timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS)
        if self.small_target is not None:
            self._get_client(self.small_target, timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS)
        self._started = True

    async def close(self) -> None:
        """关闭全部供应商连接池客户端并清空（幂等；可安全重复调用/测试内调用）。"""
        clients, self._clients = list(self._clients.values()), {}
        self._started = False
        for client in clients:
            aclose = getattr(client, "aclose", None)
            if aclose is None:
                continue
            with suppress(Exception):
                await aclose()

    async def shutdown(self) -> None:
        """lifespan 关闭入口；等价于 close。"""
        await self.close()

    def _get_client(self, target: _ModelTarget, *, timeout: float) -> httpx.AsyncClient:
        key = (target.provider, target.base_url, target.api_key, timeout)
        client = self._clients.get(key)
        if client is None:
            client = httpx.AsyncClient(timeout=timeout)
            self._clients[key] = client
        return client

    def _resolve_base_url(self, *, provider: str, configured_url: str = "") -> str:
        if configured_url:
            return configured_url
        if provider == "ollama":
            return settings.OLLAMA_BASE_URL
        return settings.LLM_API_BASE_URL or settings.OLLAMA_BASE_URL

    def _build_small_target(self) -> _ModelTarget | None:
        model = str(settings.LLM_SMALL_MODEL or "").strip()
        if not settings.LLM_MODEL_ROUTING_ENABLED or not model:
            return None
        provider = str(settings.LLM_SMALL_MODEL_PROVIDER or "openai_compatible").strip()
        base_url = self._resolve_base_url(
            provider=provider,
            configured_url=str(settings.LLM_SMALL_MODEL_API_BASE_URL or "").strip(),
        ).rstrip("/")
        api_key = str(settings.LLM_SMALL_MODEL_API_KEY or "").strip() or self.api_key
        return _ModelTarget("small", model, provider, base_url, api_key)

    def _build_headers(self, target: _ModelTarget | None = None) -> dict[str, str]:
        target = target or self.primary_target
        return provider_adapter(target.provider).headers(target.api_key)

    def _chat_url(self, target: _ModelTarget | None = None) -> str:
        target = target or self.primary_target
        return provider_adapter(target.provider).chat_url(target.base_url)

    def _generate_url(self, target: _ModelTarget | None = None) -> str:
        target = target or self.primary_target
        return provider_adapter(target.provider).generate_url(target.base_url)

    def _embedding_url(self) -> str:
        return provider_adapter(self.provider).embedding_url(self.base_url)

    def _build_chat_payload(self, messages: list[dict], stream: bool, temperature: float, target: _ModelTarget | None = None, max_tokens: int | None = None) -> dict:
        target = target or self.primary_target
        return provider_adapter(target.provider).chat_payload(
            target.model, self._normalize_messages(messages), stream, temperature, max_tokens=max_tokens
        )

    def _build_generate_payload(self, prompt: str, temperature: float, target: _ModelTarget | None = None, max_tokens: int | None = None) -> dict:
        target = target or self.primary_target
        return provider_adapter(target.provider).generate_payload(target.model, prompt, temperature, max_tokens=max_tokens)

    def _build_multimodal_generate_payload(
        self,
        *,
        prompt: str,
        image_urls: list[str],
        temperature: float,
        max_tokens: int | None = None,
    ) -> dict:
        content = [{"type": "text", "text": prompt}]
        for image_url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": image_url}})
        payload = {
            "model": self.vision_model,
            "messages": [{"role": "user", "content": content}],
            "stream": False,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

    def _build_embed_payload(self, texts: list[str]) -> dict:
        return provider_adapter(self.provider).embedding_payload(self.embedding_model, texts)

    @staticmethod
    def _normalize_messages(messages: list[dict]) -> list[dict]:
        normalized = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user")
            content = message.get("content") or ""
            if isinstance(content, list):
                parts = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = str(item.get("type") or "")
                    if item_type == "text":
                        parts.append({"type": "text", "text": str(item.get("text") or "")})
                    elif item_type == "image_url":
                        image_url = item.get("image_url")
                        url = str(image_url.get("url") or "") if isinstance(image_url, dict) else str(image_url or "")
                        if url:
                            parts.append({"type": "image_url", "image_url": {"url": url}})
                normalized.append({"role": role, "content": parts})
            else:
                normalized.append({"role": role, "content": str(content)})
        return normalized

    @staticmethod
    def _resolve_temperature(policy: TaskPolicy, requested: float | None) -> float:
        if requested is not None:
            return requested
        if policy.temperature is not None:
            return policy.temperature
        return 0.7

    def _policy_timeout(self, policy: TaskPolicy) -> float:
        return policy.timeout_seconds if policy.timeout_seconds is not None else settings.LLM_REQUEST_TIMEOUT_SECONDS

    def _policy_retries(self, policy: TaskPolicy, target: _ModelTarget) -> int:
        if target.role == "primary":
            return policy.max_retries if policy.max_retries is not None else settings.LLM_PRIMARY_REQUEST_RETRIES
        return policy.fallback_max_retries if policy.fallback_max_retries is not None else settings.LLM_FALLBACK_REQUEST_RETRIES

    def _build_request(
        self,
        *,
        request_type: str,
        action: str,
        user_id: int | None,
        prompt_template: str | None,
        prompt_version: int | None,
        trace_id: str | None,
        temperature: float | None = None,
        messages: list[dict] | None = None,
        prompt: str | None = None,
        image_urls: list[str] | None = None,
        texts: list[str] | None = None,
        estimated_input_tokens: int | None = None,
        estimated_output_tokens: int | None = None,
        data_level: str | None = None,
        pii_hit_codes: str | None = None,
        pii_hit_count: int = 0,
        redacted_count: int = 0,
    ) -> ModelRequest:
        request_id = trace_id or new_trace_id()
        return ModelRequest(
            request_type=request_type,
            messages=messages,
            prompt=prompt,
            image_urls=image_urls,
            texts=texts,
            temperature=temperature,
            action=action,
            user_id=user_id,
            prompt_template=prompt_template,
            prompt_version=prompt_version,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            trace_id=request_id,
            request_id=request_id,
            data_level=data_level,
            pii_hit_codes=pii_hit_codes,
            pii_hit_count=pii_hit_count,
            redacted_count=redacted_count,
        )

    # ── P0 出站数据保护：统一网关接入 ──────────────────────────────────────────
    @staticmethod
    def _gate_audit_fields(result: OutboundGateResult) -> dict:
        """把网关结果转成 ModelRequest/审计可用的结构化字段（只含规则 code，无原始文本）。"""
        return {
            "data_level": result.data_level.value if result.data_level is not None else None,
            "pii_hit_codes": json.dumps(list(result.pii_hit_codes), ensure_ascii=False) if result.pii_hit_codes else None,
            "pii_hit_count": result.pii_hit_count,
            "redacted_count": result.redacted_count,
        }

    def _apply_outbound_gate(
        self,
        *,
        pieces: list[str | None],
        action: str,
        user_id: int | None,
        request_id: str,
        model_name: str,
    ) -> tuple[list[str], OutboundGateResult]:
        """统一出站保护：分级 + PII 检测/脱敏 + 极敏感拦截（全部出站请求必经）。

        放行时返回 ``(safe_pieces, result)``（命中时已脱敏，未命中原样返回）；
        拦截时先落审计（status=blocked，仅元数据），再抛出稳定业务错误。
        """
        from app.services.llm.llm_observability_service import llm_observability_service

        safe_pieces, result = outbound_gate.guard(pieces=pieces, action=action)
        if result.blocked:
            if settings.LLM_OUTBOUND_AUDIT_ENABLED:
                llm_observability_service.log_event(
                    module_name=self._infer_module_name(action),
                    action=action,
                    model_name=model_name,
                    status="blocked",
                    user_id=user_id,
                    error_message=f"{BLOCK_CODE}: {result.blocked_reason}",
                    request_excerpt={
                        "action": action,
                        "data_level": result.data_level.value,
                        "blocked_reason": result.blocked_reason,
                        "detector_error": result.detector_error,
                        "pii_hit_codes": list(result.pii_hit_codes),
                        "pii_hit_count": result.pii_hit_count,
                        "rules_version": settings.LLM_OUTBOUND_DLP_RULES_VERSION,
                    },
                    response_excerpt=None,
                    provider=settings.LLM_PROVIDER,
                    data_level=result.data_level.value,
                    pii_hit_codes=json.dumps(list(result.pii_hit_codes), ensure_ascii=False) if result.pii_hit_codes else None,
                    pii_hit_count=result.pii_hit_count,
                    redacted_count=0,
                    blocked_reason=result.blocked_reason,
                )
            raise LLMGovernanceError(
                status_code=403,
                code=BLOCK_CODE,
                message="请求包含极敏感或受保护数据，已被出站保护网关拦截",
                detail={
                    "action": action,
                    "data_level": result.data_level.value,
                    "reason": result.blocked_reason,
                    "detector_error": result.detector_error,
                },
            )
        return safe_pieces, result

    @staticmethod
    def _extract_message_pieces(messages: list[dict]) -> list[str]:
        """按固定顺序提取 messages 中的全部文本片段（str content 或 text 类型 part）。"""
        pieces: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                pieces.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        pieces.append(str(item.get("text") or ""))
        return pieces

    @staticmethod
    def _rebuild_messages(messages: list[dict], safe_pieces: list[str]) -> list[dict]:
        """按 _extract_message_pieces 相同顺序回填脱敏后的文本片段（保持消息结构不变）。"""
        iterator = iter(safe_pieces)
        rebuilt: list[dict] = []
        for message in messages:
            if not isinstance(message, dict):
                rebuilt.append(message)
                continue
            content = message.get("content")
            if isinstance(content, str):
                rebuilt.append({**message, "content": next(iterator)})
            elif isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append({**item, "text": next(iterator)})
                    else:
                        parts.append(item)
                rebuilt.append({**message, "content": parts})
            else:
                rebuilt.append(message)
        return rebuilt

    async def _post_json_with_retry(
        self,
        client: httpx.AsyncClient,
        *,
        url: str,
        payload: dict,
        headers: dict[str, str],
        retries: int = DEFAULT_REQUEST_RETRIES,
    ) -> dict:
        # P1：LLM 供应商请求子 span（仅 model/provider 元数据，绝不记录 prompt/正文）。
        from app.core.telemetry import observe_span

        with observe_span("llm.http_request", attributes={
            "model": getattr(self, "model", None) or "unknown",
            "provider": getattr(self, "provider", None) or "unknown",
        }):
            last_error: Exception | None = None
            for attempt in range(1, retries + 1):
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    return resp.json()
                except Exception as exc:
                    # 只对明确瞬态错误（超时/传输/5xx/429）重试；参数/鉴权/权限/内容拦截等直接抛出。
                    if not classify_error(exc).retryable:
                        raise
                    last_error = exc
                    if attempt == retries:
                        break
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
            if last_error is not None:
                raise last_error
            raise RuntimeError("Request failed without a captured exception")

    def _extract_usage(self, data: dict, provider: str | None = None) -> tuple[int, int]:
        return provider_adapter(provider or self.provider).extract_usage(data)

    def _extract_chat_content(self, data: dict, provider: str | None = None) -> str:
        return provider_adapter(provider or self.provider).extract_chat_content(data)

    def _extract_generate_content(self, data: dict, provider: str | None = None) -> str:
        if (provider or self.provider) == "ollama":
            return data.get("response", "")
        return self._extract_chat_content(data, provider=provider)

    @staticmethod
    def _chunk_texts(texts: list[str], batch_size: int) -> list[list[str]]:
        return [texts[index : index + batch_size] for index in range(0, len(texts), batch_size)]

    def _record_usage(
        self,
        data: dict,
        model: str,
        action: str,
        duration_ms: int,
        user_id: int | None = None,
        request_excerpt: str | None = None,
        response_excerpt: str | None = None,
        error_message: str | None = None,
        status: str = "success",
        prompt_template: str | None = None,
        prompt_version: int | None = None,
        provider: str | None = None,
        request_id: str | None = None,
        routing_role: str | None = None,
        routing_stage: str | None = None,
        attempt_number: int | None = 1,
        budget_category: str | None = None,
        estimated_input_tokens: int | None = None,
        estimated_output_tokens: int | None = None,
        data_level: str | None = None,
        pii_hit_codes: str | None = None,
        pii_hit_count: int = 0,
        redacted_count: int = 0,
        model_release_version: str | None = None,
        experiment_bucket: int | None = None,
        traffic_type: str | None = None,
        routing_reason: str | None = None,
        request_complexity: str | None = None,
        risk_level: str | None = None,
        billable: bool = True,
    ):
        try:
            # P0 出站审计：请求级上下文（API/Celery headers 传播）补齐租户/链路标识。
            from app.core.database import SessionLocal
            from app.core.obs_context import get_context
            from app.models.llm_call_log import LLMCallLog
            from app.services.billing.token_service import token_service
            from app.services.llm.prompt_service import prompt_service

            obs_ctx = get_context()

            budget_category = budget_category or get_task_policy(action).budget_category
            prompt_tokens, completion_tokens = self._extract_usage(data, provider=provider)
            if prompt_template is None:
                mapped_template = ACTION_PROMPT_TEMPLATE_MAP.get(action)
                if mapped_template:
                    metadata = prompt_service.get_template_metadata(mapped_template, user_id=user_id)
                    prompt_template = metadata.get("prompt_template")
                    prompt_version = metadata.get("prompt_version")
            module_name = self._infer_module_name(action)

            db = SessionLocal()
            try:
                if billable:
                    token_service.record(
                        model=model,
                        db=db,
                        user_id=user_id,
                        action=action,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        duration_ms=duration_ms,
                        budget_category=budget_category,
                        attempt_number=attempt_number,
                    )
                db.add(
                    LLMCallLog(
                        user_id=user_id,
                        module_name=module_name,
                        action=action,
                        model_name=model,
                        prompt_template=prompt_template,
                        prompt_version=prompt_version,
                        input_tokens=prompt_tokens,
                        output_tokens=completion_tokens,
                        estimated_input_tokens=estimated_input_tokens,
                        estimated_output_tokens=estimated_output_tokens,
                        attempt_number=attempt_number,
                        duration_ms=duration_ms,
                        status=status,
                        request_id=request_id,
                        trace_id=obs_ctx.trace_id,
                        task_id=obs_ctx.task_id,
                        agent_run_id=obs_ctx.agent_run_id,
                        organization_id=obs_ctx.org_id,
                        routing_role=routing_role,
                        routing_stage=routing_stage,
                        model_release_version=model_release_version,
                        experiment_bucket=experiment_bucket,
                        traffic_type=traffic_type,
                        routing_reason=routing_reason,
                        request_complexity=request_complexity,
                        risk_level=risk_level,
                        error_message=sanitize_observability_error_message(action, error_message),
                        request_excerpt=sanitize_observability_excerpt(action, request_excerpt, kind="request"),
                        response_excerpt=sanitize_observability_excerpt(action, response_excerpt, kind="response"),
                        provider=provider,
                        data_level=data_level,
                        pii_hit_codes=pii_hit_codes,
                        pii_hit_count=pii_hit_count,
                        redacted_count=redacted_count,
                    )
                )
                db.commit()
            finally:
                db.close()
        except Exception:
            pass

    def _sanitize_observability_excerpt(self, action: str, excerpt: str | None, *, kind: str) -> str | None:
        return sanitize_observability_excerpt(action, excerpt, kind=kind)

    @staticmethod
    def _infer_module_name(action: str) -> str:
        if action == "embedding":
            return "document"
        if action.startswith("document_") or action.startswith("rag_"):
            return "document"
        if action.startswith("meeting_"):
            return "meeting"
        if action.startswith("email_"):
            return "email"
        if action.startswith("task_"):
            return "task"
        if action.startswith("agent_"):
            return "agent"
        if action.startswith("chat"):
            return "chat"
        if action.startswith("legal_"):
            return "legal"
        return "system"

    @staticmethod
    def _messages_to_text(messages: list[dict]) -> str:
        return "\n".join(str(item.get("content") or "") for item in messages if isinstance(item, dict))

    def _select_text_target(self, text: str, action: str, policy: TaskPolicy | None = None) -> _ModelTarget:
        """Route short, low-risk text requests to the configured small model."""
        if not self.small_target:
            return self.primary_target
        policy = policy or get_task_policy(action)
        normalized_action = (action or "").lower()
        normalized_text = text or ""
        if (
            policy.model_tier == "primary"
            or normalized_action in _PRIMARY_ACTIONS
            or normalized_action.startswith(_PRIMARY_ACTION_PREFIXES)
            or len(normalized_text) > settings.LLM_SIMPLE_REQUEST_MAX_CHARS
            or any(marker in normalized_text for marker in _COMPLEX_REQUEST_MARKERS)
        ):
            return self.primary_target
        return self.small_target

    @staticmethod
    def _same_target(left: _ModelTarget, right: _ModelTarget) -> bool:
        return (left.model, left.provider, left.base_url, left.api_key) == (right.model, right.provider, right.base_url, right.api_key)

    def _circuit_key(self, target: _ModelTarget, task: str) -> str:
        return self.circuit_breaker.key(provider=target.provider, base_url=target.base_url, task=task)

    def _circuit_open_error(self, *, task: str, request_id: str, trace_id: str) -> ModelError:
        return ModelError(
            kind=ModelErrorKind.CIRCUIT_OPEN,
            message=f"没有可用的模型目标（{task} 能力已熔断）",
            request_id=request_id,
            trace_id=trace_id,
        )

    def _filter_available_targets(self, targets: list[_ModelTarget], task: str) -> list[_ModelTarget]:
        return [target for target in targets if self.circuit_breaker.can_attempt(self._circuit_key(target, task))]

    def _candidate_targets(self, text: str, action: str, policy: TaskPolicy | None = None) -> list[_ModelTarget]:
        policy = policy or get_task_policy(action)
        primary_choice = self._select_text_target(text, action, policy=policy)
        targets = [primary_choice]
        if not policy.fallback_enabled or not settings.LLM_MODEL_FALLBACK_ENABLED or not self.small_target:
            return self._filter_available_targets(targets, policy.task)
        alternate = self.small_target if primary_choice.role == "primary" else self.primary_target
        if primary_choice.role == "small" and not settings.LLM_SMALL_MODEL_FALLBACK_TO_PRIMARY:
            return self._filter_available_targets(targets, policy.task)
        if not self._same_target(primary_choice, alternate):
            targets.append(alternate)
        return self._filter_available_targets(targets, policy.task)

    def _target_from_release(self, decision: ModelReleaseDecision) -> _ModelTarget:
        provider = decision.candidate_provider or self.primary_target.provider
        base_url = self._resolve_base_url(
            provider=provider,
            configured_url=decision.candidate_base_url or "",
        ).rstrip("/")
        # Candidate credentials must remain in deployment secret storage.  A dedicated
        # secret is optional so same-provider upgrades can safely reuse the primary key.
        api_key = str(settings.LLM_CANARY_API_KEY or "").strip() or self.primary_target.api_key
        return _ModelTarget(
            role="canary",
            model=decision.candidate_model,
            provider=provider,
            base_url=base_url,
            api_key=api_key,
        )

    def _build_route_plan(self, *, source_text: str, request: ModelRequest, policy: TaskPolicy | None = None) -> _RoutePlan:
        policy = policy or get_task_policy(request.action)
        baseline_targets = self._candidate_targets(source_text, request.action, policy=policy)
        baseline_model = baseline_targets[0].model if baseline_targets else self.primary_target.model
        decision = model_release_service.resolve(
            action=request.action,
            source_text=source_text,
            data_level=request.data_level,
            latency_budget_ms=int(self._policy_timeout(policy) * 1000),
            user_id=request.user_id,
            request_id=request.request_id,
            baseline_model=baseline_model,
            estimated_input_tokens=request.estimated_input_tokens,
            estimated_output_tokens=request.estimated_output_tokens,
        )
        if not decision:
            return _RoutePlan(targets=tuple(baseline_targets))

        candidate = self._target_from_release(decision)
        candidate_available = self.circuit_breaker.can_attempt(self._circuit_key(candidate, policy.task))
        if decision.serve_candidate and candidate_available:
            targets = [candidate]
            if policy.fallback_enabled and settings.LLM_MODEL_FALLBACK_ENABLED:
                targets.extend(target for target in baseline_targets if not self._same_target(target, candidate))
            return _RoutePlan(targets=tuple(targets), decision=decision)
        shadow_target = candidate if decision.shadow_candidate and candidate_available else None
        return _RoutePlan(targets=tuple(baseline_targets), decision=decision, shadow_target=shadow_target)

    @staticmethod
    def _release_log_fields(
        decision: ModelReleaseDecision | None,
        *,
        traffic_type: str = "serving",
    ) -> dict[str, str | int | None]:
        if decision is None:
            return {"traffic_type": traffic_type}
        return {
            "model_release_version": decision.release_version,
            "experiment_bucket": decision.bucket,
            "traffic_type": traffic_type,
            "routing_reason": decision.reason,
            "request_complexity": decision.signals.complexity,
            "risk_level": decision.signals.risk_level,
        }

    def _cache_model_for_plan(self, plan: _RoutePlan, *, fallback_model: str) -> str:
        return plan.targets[0].model if plan.targets else fallback_model

    def _cache_model(self, source_text: str, action: str) -> str:
        """按路由规则解析将使用的模型名，作为缓存键的"模型不可变版本"锚点。"""
        return self._select_text_target(source_text, action).model

    def _cache_key(
        self,
        *,
        task: str,
        request: ModelRequest,
        model: str,
        permission_fingerprint: str | None,
        schema: dict | None = None,
    ) -> str:
        """构造响应缓存键：task + 规范化请求 + 模型 + prompt 版本 + 权限指纹 → sha256。

        规范化请求与权限指纹只参与摘要计算；缓存键本身是不可逆 digest，
        绝不写入 prompt 原文、角色、资源范围或用户隐私文本。
        """
        if request.request_type in ("chat", "chat_stream"):
            normalized = json.dumps(request.messages or [], ensure_ascii=False, sort_keys=True)
        else:
            normalized = json.dumps({"prompt": request.prompt or "", "schema": schema}, ensure_ascii=False, sort_keys=True)
        perm_digest = hashlib.sha256(str(permission_fingerprint or "public").encode("utf-8")).hexdigest()
        payload = json.dumps(
            {
                "task": task,
                "request": normalized,
                "model": model,
                "prompt_version": request.prompt_version or "",
                "permission": perm_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_provider_failure(exc: Exception) -> bool:
        return classify_error(exc).retryable

    def _record_target_usage(
        self,
        data: dict,
        target: _ModelTarget,
        action: str,
        duration_ms: int,
        user_id: int | None,
        **kwargs,
    ) -> None:
        # 审计要求"目标模型/提供方"：总是记录实际发送目标（主/小/备选模型）的提供方。
        kwargs["provider"] = target.provider
        self._record_usage(data, target.model, action, duration_ms, user_id, **kwargs)

    async def _request_text_once(
        self,
        *,
        target: _ModelTarget,
        request: ModelRequest,
        policy: TaskPolicy,
        routing_stage: str,
        attempt_number: int = 1,
        decision: ModelReleaseDecision | None = None,
        traffic_type: str = "serving",
        billable: bool = True,
    ) -> str:
        is_chat = request.request_type in {"chat", "chat_stream"}
        temperature = self._resolve_temperature(policy, request.temperature)
        max_tokens = policy.max_tokens
        request_excerpt = json.dumps(request.messages, ensure_ascii=False) if is_chat else str(request.prompt or "")
        url = self._chat_url(target) if is_chat else self._generate_url(target)
        payload = (
            self._build_chat_payload(request.messages or [], stream=False, temperature=temperature, target=target, max_tokens=max_tokens)
            if is_chat
            else self._build_generate_payload(request.prompt or "", temperature=temperature, target=target, max_tokens=max_tokens)
        )
        start = time.time()
        retries = self._policy_retries(policy, target)
        circuit_key = self._circuit_key(target, policy.task)
        try:
            client = self._get_client(target, timeout=self._policy_timeout(policy))
            data = await self._post_json_with_retry(
                client, url=url, payload=payload, headers=self._build_headers(target), retries=retries,
            )
        except Exception as exc:
            self.circuit_breaker.record_failure(
                circuit_key, counts=counts_toward_breaker(classify_error(exc).kind),
            )
            self._record_target_usage(
                {}, target, request.action, int((time.time() - start) * 1000), request.user_id,
                request_excerpt=request_excerpt, error_message=str(exc), status="error",
                prompt_template=request.prompt_template, prompt_version=request.prompt_version,
                request_id=request.request_id, routing_role=target.role, routing_stage=routing_stage,
                attempt_number=attempt_number,
                estimated_input_tokens=request.estimated_input_tokens,
                estimated_output_tokens=request.estimated_output_tokens,
                data_level=request.data_level, pii_hit_codes=request.pii_hit_codes,
                pii_hit_count=request.pii_hit_count, redacted_count=request.redacted_count,
                billable=billable,
                **self._release_log_fields(decision, traffic_type=traffic_type),
            )
            raise
        self.circuit_breaker.record_success(circuit_key)
        response_excerpt = (
            self._extract_chat_content(data, provider=target.provider)
            if is_chat else self._extract_generate_content(data, provider=target.provider)
        )
        self._record_target_usage(
            data, target, request.action, int((time.time() - start) * 1000), request.user_id,
            request_excerpt=request_excerpt, response_excerpt=response_excerpt,
            prompt_template=request.prompt_template, prompt_version=request.prompt_version,
            request_id=request.request_id, routing_role=target.role, routing_stage=routing_stage,
            attempt_number=attempt_number,
            estimated_input_tokens=request.estimated_input_tokens,
            estimated_output_tokens=request.estimated_output_tokens,
            data_level=request.data_level, pii_hit_codes=request.pii_hit_codes,
            pii_hit_count=request.pii_hit_count, redacted_count=request.redacted_count,
            billable=billable,
            **self._release_log_fields(decision, traffic_type=traffic_type),
        )
        return response_excerpt

    async def _run_shadow_request(
        self,
        *,
        target: _ModelTarget,
        request: ModelRequest,
        policy: TaskPolicy,
        decision: ModelReleaseDecision,
    ) -> None:
        try:
            await self._request_text_once(
                target=target,
                request=request,
                policy=policy,
                routing_stage="shadow",
                attempt_number=1,
                decision=decision,
                traffic_type="shadow",
                billable=False,
            )
        except Exception:
            # The comparison is intentionally non-blocking and may not change the served result.
            return

    def _schedule_shadow_request(
        self,
        *,
        target: _ModelTarget | None,
        request: ModelRequest,
        policy: TaskPolicy,
        decision: ModelReleaseDecision | None,
    ) -> None:
        if target is None or decision is None:
            return
        task = asyncio.create_task(
            self._run_shadow_request(target=target, request=request, policy=policy, decision=decision)
        )
        # Consume unexpected task-level errors defensively; _run_shadow_request already
        # handles provider failures, but cancellation must never leak to the request task.
        task.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)

    async def _request_text_with_routing(
        self,
        *,
        source_text: str,
        request: ModelRequest,
        route_plan: _RoutePlan | None = None,
    ) -> str:
        policy = get_task_policy(request.action)
        plan = route_plan or self._build_route_plan(source_text=source_text, request=request, policy=policy)
        targets = plan.targets
        if not targets:
            raise self._circuit_open_error(
                task=policy.task, request_id=request.request_id, trace_id=request.trace_id,
            )
        last_error: Exception | None = None
        for index, target in enumerate(targets):
            try:
                response = await self._request_text_once(
                    target=target,
                    request=request,
                    policy=policy,
                    routing_stage="initial" if index == 0 else "fallback",
                    attempt_number=index + 1,
                    decision=plan.decision,
                    traffic_type="serving",
                )
                self._schedule_shadow_request(
                    target=plan.shadow_target,
                    request=request,
                    policy=policy,
                    decision=plan.decision,
                )
                return response
            except Exception as exc:
                last_error = exc
                if index == len(targets) - 1 or not classify_error(exc).retryable:
                    raise
        raise last_error or RuntimeError("No LLM target is available")

    async def chat(
        self,
        messages: list[dict],
        stream: bool = False,
        temperature: float | None = None,
        action: str = "chat",
        user_id: int | None = None,
        prompt_template: str | None = None,
        prompt_version: int | None = None,
        trace_id: str | None = None,
        cacheable: bool = False,
        permission_fingerprint: str | None = None,
    ) -> str:
        enforcement = llm_governance_service.enforce_chat_request(messages=messages, user_id=user_id, action=action)
        # P0 出站保护：分级 + PII 检测/脱敏（拦截时内部审计并抛错）。
        safe_pieces, gate_result = self._apply_outbound_gate(
            pieces=self._extract_message_pieces(messages or []),
            action=action,
            user_id=user_id,
            request_id=trace_id or new_trace_id(),
            model_name=settings.LLM_MODEL,
        )
        safe_messages = self._rebuild_messages(messages, safe_pieces) if messages else messages
        request = self._build_request(
            request_type="chat", messages=safe_messages, temperature=temperature,
            action=action, user_id=user_id, prompt_template=prompt_template,
            prompt_version=prompt_version, trace_id=trace_id,
            estimated_input_tokens=enforcement.get("estimated_input_tokens"),
            estimated_output_tokens=enforcement.get("estimated_output_tokens"),
            **self._gate_audit_fields(gate_result),
        )
        source_text = self._messages_to_text(safe_messages)
        route_plan = self._build_route_plan(source_text=source_text, request=request)
        cache_key = None
        if cacheable and not stream and settings.LLM_RESPONSE_CACHE_ENABLED:
            policy = get_task_policy(action)
            cache_key = self._cache_key(
                task=policy.task, request=request,
                model=self._cache_model_for_plan(
                    route_plan, fallback_model=self._cache_model(source_text, action),
                ),
                permission_fingerprint=permission_fingerprint,
            )
            cached = self.response_cache.get(cache_key)
            if cached is not None:
                return cached
        raw = await self._request_text_with_routing(
            source_text=source_text, request=request, route_plan=route_plan,
        )
        if cache_key is not None:
            self.response_cache.put(cache_key, raw)
        return raw

    async def generate(
        self,
        prompt: str,
        temperature: float | None = None,
        action: str = "generate",
        user_id: int | None = None,
        prompt_template: str | None = None,
        prompt_version: int | None = None,
        trace_id: str | None = None,
        cacheable: bool = False,
        permission_fingerprint: str | None = None,
    ) -> str:
        enforcement = llm_governance_service.enforce_generate_request(prompt=prompt, user_id=user_id, action=action)
        # P0 出站保护：分级 + PII 检测/脱敏（拦截时内部审计并抛错）。
        safe_pieces, gate_result = self._apply_outbound_gate(
            pieces=[prompt],
            action=action,
            user_id=user_id,
            request_id=trace_id or new_trace_id(),
            model_name=settings.LLM_MODEL,
        )
        safe_prompt = safe_pieces[0] if safe_pieces else (prompt or "")
        request = self._build_request(
            request_type="generate", prompt=safe_prompt, temperature=temperature,
            action=action, user_id=user_id, prompt_template=prompt_template,
            prompt_version=prompt_version, trace_id=trace_id,
            estimated_input_tokens=enforcement.get("estimated_input_tokens"),
            estimated_output_tokens=enforcement.get("estimated_output_tokens"),
            **self._gate_audit_fields(gate_result),
        )
        route_plan = self._build_route_plan(source_text=safe_prompt, request=request)
        cache_key = None
        if cacheable and settings.LLM_RESPONSE_CACHE_ENABLED:
            policy = get_task_policy(action)
            cache_key = self._cache_key(
                task=policy.task, request=request,
                model=self._cache_model_for_plan(
                    route_plan, fallback_model=self._cache_model(safe_prompt, action),
                ),
                permission_fingerprint=permission_fingerprint,
            )
            cached = self.response_cache.get(cache_key)
            if cached is not None:
                return cached
        raw = await self._request_text_with_routing(
            source_text=safe_prompt, request=request, route_plan=route_plan,
        )
        if cache_key is not None:
            self.response_cache.put(cache_key, raw)
        return raw

    async def structured_generate(
        self,
        prompt: str,
        *,
        schema: dict | type,
        temperature: float | None = None,
        action: str = "generate",
        user_id: int | None = None,
        prompt_template: str | None = None,
        prompt_version: int | None = None,
        trace_id: str | None = None,
        cacheable: bool = False,
        permission_fingerprint: str | None = None,
    ) -> Any:
        """供应商无关的结构化输出：按 JSON Schema/Pydantic 模型校验后返回 JSON 值。

        流程：一次初始请求 → 无模型修复（去代码块/前后噪声）→ 至多
        ``structured_repair_max_attempts`` 次受 TaskPolicy 控制的修复请求
        （携带原始 schema，同样受权限/预算/限流约束）。最终仍失败时抛出
        ``ModelError``，kind 为 invalid_response / schema_validation_failed /
        repair_failed（均不可重试）。修复阶段的供应商/传输层错误原样向上抛，
        与 ``generate`` 行为一致。

        ``cacheable=True`` 时按 task+规范化请求+schema+模型+prompt 版本+权限指纹
        缓存通过校验的原始输出；命中时先过治理门禁再复用，不缓存权限校验结果。
        """
        spec = normalize_schema(schema)
        enforcement = llm_governance_service.enforce_generate_request(prompt=prompt, user_id=user_id, action=action)
        policy = get_task_policy(action)
        # P0 出站保护：初始请求同样过统一网关（拦截时内部审计并抛错）。
        safe_pieces, gate_result = self._apply_outbound_gate(
            pieces=[prompt],
            action=action,
            user_id=user_id,
            request_id=trace_id or new_trace_id(),
            model_name=settings.LLM_MODEL,
        )
        safe_prompt = safe_pieces[0] if safe_pieces else (prompt or "")
        request = self._build_request(
            request_type="generate", prompt=safe_prompt, temperature=temperature,
            action=action, user_id=user_id, prompt_template=prompt_template,
            prompt_version=prompt_version, trace_id=trace_id,
            estimated_input_tokens=enforcement.get("estimated_input_tokens"),
            estimated_output_tokens=enforcement.get("estimated_output_tokens"),
            **self._gate_audit_fields(gate_result),
        )
        route_plan = self._build_route_plan(source_text=safe_prompt, request=request, policy=policy)
        cache_key = None
        if cacheable and settings.LLM_RESPONSE_CACHE_ENABLED:
            cache_key = self._cache_key(
                task=policy.task, request=request,
                model=self._cache_model_for_plan(
                    route_plan, fallback_model=self._cache_model(safe_prompt, action),
                ),
                permission_fingerprint=permission_fingerprint,
                schema=spec.json_schema,
            )
            cached = self.response_cache.get(cache_key)
            if cached is not None:
                cached_data, _ = parse_structured_output(cached, spec)
                if cached_data is not None:
                    return cached_data
        raw = await self._request_text_with_routing(
            source_text=safe_prompt, request=request, route_plan=route_plan,
        )
        data, failure_kind = parse_structured_output(raw, spec)
        if data is not None:
            if cache_key is not None:
                self.response_cache.put(cache_key, raw)
            return data

        if policy.structured_repair_enabled:
            candidate_raw = raw
            for _ in range(max(1, policy.structured_repair_max_attempts)):
                repair_prompt = build_repair_prompt(spec.json_schema, candidate_raw)
                # 修复请求同样受权限/预算/限流约束（同一 action/user），不绕过治理；
                # P0 出站保护：修复载荷同样过统一网关（含脱敏，防止模型输出回显 PII）。
                repair_enforcement = llm_governance_service.enforce_generate_request(prompt=repair_prompt, user_id=user_id, action=action)
                safe_repair, repair_gate = self._apply_outbound_gate(
                    pieces=[repair_prompt],
                    action=action,
                    user_id=user_id,
                    request_id=request.trace_id,
                    model_name=settings.LLM_MODEL,
                )
                safe_repair_prompt = safe_repair[0] if safe_repair else repair_prompt
                repair_request = self._build_request(
                    request_type="generate", prompt=safe_repair_prompt, temperature=0.0,
                    action=action, user_id=user_id, prompt_template=None, prompt_version=None,
                    trace_id=request.trace_id,
                    estimated_input_tokens=repair_enforcement.get("estimated_input_tokens"),
                    estimated_output_tokens=repair_enforcement.get("estimated_output_tokens"),
                    **self._gate_audit_fields(repair_gate),
                )
                candidate_raw = await self._request_text_with_routing(source_text=safe_repair_prompt, request=repair_request)
                repaired_data, sub_kind = parse_structured_output(candidate_raw, spec)
                if repaired_data is not None:
                    if cache_key is not None:
                        self.response_cache.put(cache_key, candidate_raw)
                    return repaired_data
            last_kind = sub_kind or failure_kind or ModelErrorKind.INVALID_RESPONSE
            raise ModelError(
                kind=ModelErrorKind.REPAIR_FAILED,
                message=f"结构化修复后仍无法得到符合 Schema 的输出（{last_kind.value}）",
                request_id=request.request_id,
                trace_id=request.trace_id,
            )

        raise ModelError(
            kind=failure_kind or ModelErrorKind.INVALID_RESPONSE,
            message="模型输出不符合结构化要求且修复未启用",
            request_id=request.request_id,
            trace_id=request.trace_id,
        )

    async def generate_with_images(
        self,
        prompt: str,
        image_urls: list[str],
        temperature: float | None = None,
        action: str = "generate_with_images",
        user_id: int | None = None,
        prompt_template: str | None = None,
        prompt_version: int | None = None,
        trace_id: str | None = None,
    ) -> str:
        enforcement = llm_governance_service.enforce_generate_request(prompt=prompt, user_id=user_id, action=action)
        policy = get_task_policy(action)
        # P0 出站保护：分级 + PII 检测/脱敏（拦截时内部审计并抛错）。
        safe_pieces, gate_result = self._apply_outbound_gate(
            pieces=[prompt],
            action=action,
            user_id=user_id,
            request_id=trace_id or new_trace_id(),
            model_name=self.vision_model,
        )
        safe_prompt = safe_pieces[0] if safe_pieces else (prompt or "")
        request = self._build_request(
            request_type="vision", prompt=safe_prompt, image_urls=image_urls, temperature=temperature,
            action=action, user_id=user_id, prompt_template=prompt_template,
            prompt_version=prompt_version, trace_id=trace_id,
            estimated_input_tokens=enforcement.get("estimated_input_tokens"),
            estimated_output_tokens=enforcement.get("estimated_output_tokens"),
            **self._gate_audit_fields(gate_result),
        )
        resolved_temperature = self._resolve_temperature(policy, request.temperature)
        circuit_key = self._circuit_key(self.primary_target, policy.task)
        if not self.circuit_breaker.can_attempt(circuit_key):
            raise self._circuit_open_error(
                task=policy.task, request_id=request.request_id, trace_id=request.trace_id,
            )
        url = self._generate_url()
        payload = self._build_multimodal_generate_payload(
            prompt=safe_prompt,
            image_urls=image_urls,
            temperature=resolved_temperature,
            max_tokens=policy.max_tokens,
        )
        headers = self._build_headers()
        start = time.time()
        request_excerpt = json.dumps(
            {"prompt": safe_prompt[:500], "image_count": len(image_urls)},
            ensure_ascii=False,
        )
        retries = self._policy_retries(policy, self.primary_target)
        try:
            client = self._get_client(self.primary_target, timeout=self._policy_timeout(policy))
            data = await self._post_json_with_retry(client, url=url, payload=payload, headers=headers, retries=retries)
        except Exception as exc:
            self.circuit_breaker.record_failure(
                circuit_key, counts=counts_toward_breaker(classify_error(exc).kind),
            )
            duration_ms = int((time.time() - start) * 1000)
            self._record_usage(
                {},
                self.vision_model,
                action,
                duration_ms,
                user_id,
                request_excerpt=request_excerpt,
                error_message=str(exc),
                status="error",
                prompt_template=prompt_template,
                prompt_version=prompt_version,
                request_id=request.request_id,
                attempt_number=1,
                estimated_input_tokens=request.estimated_input_tokens,
                estimated_output_tokens=request.estimated_output_tokens,
                data_level=request.data_level, pii_hit_codes=request.pii_hit_codes,
                pii_hit_count=request.pii_hit_count, redacted_count=request.redacted_count,
            )
            raise
        self.circuit_breaker.record_success(circuit_key)
        duration_ms = int((time.time() - start) * 1000)
        response_excerpt = self._extract_generate_content(data)
        self._record_usage(
            data,
            self.vision_model,
            action,
            duration_ms,
            user_id,
            request_excerpt=request_excerpt,
            response_excerpt=response_excerpt,
            prompt_template=prompt_template,
            prompt_version=prompt_version,
            request_id=request.request_id,
            attempt_number=1,
            estimated_input_tokens=request.estimated_input_tokens,
            estimated_output_tokens=request.estimated_output_tokens,
            data_level=request.data_level, pii_hit_codes=request.pii_hit_codes,
            pii_hit_count=request.pii_hit_count, redacted_count=request.redacted_count,
        )
        return response_excerpt

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
        action: str = "chat_stream",
        user_id: int | None = None,
        prompt_template: str | None = None,
        prompt_version: int | None = None,
        trace_id: str | None = None,
    ):
        enforcement = llm_governance_service.enforce_chat_request(messages=messages, user_id=user_id, action=action)
        policy = get_task_policy(action)
        # P0 出站保护：流式请求同样过统一网关（拦截时内部审计并抛错）。
        safe_pieces, gate_result = self._apply_outbound_gate(
            pieces=self._extract_message_pieces(messages or []),
            action=action,
            user_id=user_id,
            request_id=trace_id or new_trace_id(),
            model_name=settings.LLM_MODEL,
        )
        safe_messages = self._rebuild_messages(messages, safe_pieces) if messages else messages
        request = self._build_request(
            request_type="chat_stream", messages=safe_messages, temperature=temperature,
            action=action, user_id=user_id, prompt_template=prompt_template,
            prompt_version=prompt_version, trace_id=trace_id,
            estimated_input_tokens=enforcement.get("estimated_input_tokens"),
            estimated_output_tokens=enforcement.get("estimated_output_tokens"),
            **self._gate_audit_fields(gate_result),
        )
        resolved_temperature = self._resolve_temperature(policy, request.temperature)
        max_tokens = policy.max_tokens
        request_excerpt = json.dumps(safe_messages, ensure_ascii=False)
        source_text = self._messages_to_text(safe_messages)
        route_plan = self._build_route_plan(source_text=source_text, request=request, policy=policy)
        targets = route_plan.targets
        if not targets:
            raise self._circuit_open_error(
                task=policy.task, request_id=request.request_id, trace_id=request.trace_id,
            )

        async def stream_from_target(target: _ModelTarget, routing_stage: str, attempt_number: int):
            start = time.time()
            full_response = ""
            last_data: dict = {}
            retries = self._policy_retries(policy, target)
            circuit_key = self._circuit_key(target, policy.task)
            try:
                client = self._get_client(target, timeout=self._policy_timeout(policy))
                # Stream connection failures use the same bounded retry policy as normal requests.
                for attempt in range(1, retries + 1):
                    try:
                        async with client.stream(
                            "POST",
                            self._chat_url(target),
                            json=self._build_chat_payload(safe_messages, stream=True, temperature=resolved_temperature, target=target, max_tokens=max_tokens),
                            headers=self._build_headers(target),
                        ) as resp:
                            resp.raise_for_status()
                            async for line in resp.aiter_lines():
                                if not line.strip():
                                    continue
                                adapter = provider_adapter(target.provider)
                                if target.provider == "ollama":
                                    payload_text = line
                                else:
                                    if not line.startswith("data:"):
                                        continue
                                    payload_text = line[5:].strip()
                                    if payload_text == "[DONE]":
                                        break
                                last_data = json.loads(payload_text)
                                content, done = adapter.extract_stream_chunk(last_data)
                                if content:
                                    full_response += content
                                    yield content
                                if done:
                                    break
                            self.circuit_breaker.record_success(circuit_key)
                            self._record_target_usage(
                                last_data, target, action, int((time.time() - start) * 1000), user_id,
                                request_excerpt=request_excerpt, response_excerpt=full_response,
                                prompt_template=prompt_template, prompt_version=prompt_version,
                                request_id=request.request_id, routing_role=target.role, routing_stage=routing_stage,
                                attempt_number=attempt_number,
                                estimated_input_tokens=request.estimated_input_tokens,
                                estimated_output_tokens=request.estimated_output_tokens,
                                data_level=request.data_level, pii_hit_codes=request.pii_hit_codes,
                                pii_hit_count=request.pii_hit_count, redacted_count=request.redacted_count,
                                **self._release_log_fields(route_plan.decision),
                            )
                            return
                    except Exception as exc:
                        # 已向客户端产出任何内容（full_response 非空）后禁止内部重试：
                        # 重试会从头重放流，导致客户端收到重复内容。
                        if not classify_error(exc).retryable or attempt == retries or full_response:
                            raise
                        await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
            except Exception as exc:
                self.circuit_breaker.record_failure(
                    circuit_key, counts=counts_toward_breaker(classify_error(exc).kind),
                )
                self._record_target_usage(
                    {}, target, action, int((time.time() - start) * 1000), user_id,
                    request_excerpt=request_excerpt, response_excerpt=full_response, error_message=str(exc), status="error",
                    prompt_template=prompt_template, prompt_version=prompt_version,
                    request_id=request.request_id, routing_role=target.role, routing_stage=routing_stage,
                    attempt_number=attempt_number,
                    estimated_input_tokens=request.estimated_input_tokens,
                    estimated_output_tokens=request.estimated_output_tokens,
                    data_level=request.data_level, pii_hit_codes=request.pii_hit_codes,
                    pii_hit_count=request.pii_hit_count, redacted_count=request.redacted_count,
                    **self._release_log_fields(route_plan.decision),
                )
                raise

        for index, target in enumerate(targets):
            emitted = False
            try:
                async for chunk in stream_from_target(target, "initial" if index == 0 else "fallback", attempt_number=index + 1):
                    emitted = True
                    yield chunk
                self._schedule_shadow_request(
                    target=route_plan.shadow_target,
                    request=request,
                    policy=policy,
                    decision=route_plan.decision,
                )
                return
            except Exception as exc:
                # 已输出内容后不能无缝切换模型，否则会造成重复或前后文不一致。
                if emitted or index == len(targets) - 1 or not classify_error(exc).retryable:
                    raise

    async def embed(
        self,
        texts: list[str],
        *,
        user_id: int | None = None,
        action: str = "embedding",
        trace_id: str | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        enforcement = llm_governance_service.enforce_embedding_request(texts=texts, user_id=user_id, action=action)
        policy = get_task_policy(action)
        # P0 出站保护：批量向量化文本同样过统一网关（拦截时内部审计并抛错）。
        safe_pieces, gate_result = self._apply_outbound_gate(
            pieces=texts,
            action=action,
            user_id=user_id,
            request_id=trace_id or new_trace_id(),
            model_name=self.embedding_model,
        )
        safe_texts = safe_pieces
        request = self._build_request(
            request_type="embedding", texts=safe_texts, action=action, user_id=user_id,
            prompt_template=None, prompt_version=None, trace_id=trace_id,
            estimated_input_tokens=enforcement.get("estimated_input_tokens"),
            estimated_output_tokens=enforcement.get("estimated_output_tokens"),
            **self._gate_audit_fields(gate_result),
        )
        circuit_key = self._circuit_key(self.primary_target, policy.task)
        if not self.circuit_breaker.can_attempt(circuit_key):
            raise self._circuit_open_error(
                task=policy.task, request_id=request.request_id, trace_id=request.trace_id,
            )

        url = self._embedding_url()
        headers = self._build_headers()
        batches = [safe_texts]
        adapter = provider_adapter(self.provider)
        if not adapter.uses_native_embedding_batch:
            batches = self._chunk_texts(safe_texts, OPENAI_COMPATIBLE_EMBED_BATCH_SIZE)

        embeddings: list[list[float]] = []
        client = self._get_client(self.primary_target, timeout=self._policy_timeout(policy))
        retries = self._policy_retries(policy, self.primary_target)
        for batch in batches:
            payload = self._build_embed_payload(batch)
            start = time.time()
            request_excerpt = json.dumps({"input_count": len(batch), "sample": batch[:2]}, ensure_ascii=False)
            try:
                data = await self._post_json_with_retry(client, url=url, payload=payload, headers=headers, retries=retries)
            except Exception as exc:
                self.circuit_breaker.record_failure(
                    circuit_key, counts=counts_toward_breaker(classify_error(exc).kind),
                )
                duration_ms = int((time.time() - start) * 1000)
                self._record_usage(
                    {},
                    self.embedding_model,
                    "embedding",
                    duration_ms,
                    user_id,
                    request_excerpt=request_excerpt,
                    error_message=str(exc),
                    status="error",
                    request_id=request.request_id,
                    attempt_number=1,
                    estimated_input_tokens=request.estimated_input_tokens,
                    estimated_output_tokens=request.estimated_output_tokens,
                    data_level=request.data_level, pii_hit_codes=request.pii_hit_codes,
                    pii_hit_count=request.pii_hit_count, redacted_count=request.redacted_count,
                )
                raise
            self.circuit_breaker.record_success(circuit_key)

            duration_ms = int((time.time() - start) * 1000)
            response_excerpt = f"embedding_count={len(batch)}"
            self._record_usage(
                data,
                self.embedding_model,
                "embedding",
                duration_ms,
                user_id,
                request_excerpt=request_excerpt,
                response_excerpt=response_excerpt,
                request_id=request.request_id,
                attempt_number=1,
                estimated_input_tokens=request.estimated_input_tokens,
                estimated_output_tokens=request.estimated_output_tokens,
                data_level=request.data_level, pii_hit_codes=request.pii_hit_codes,
                pii_hit_count=request.pii_hit_count, redacted_count=request.redacted_count,
            )
            embeddings.extend(adapter.extract_embeddings(data))
        return embeddings


model_gateway = ModelGateway()
# 兼容别名：既有业务对 LLMClient / llm_client 的引用保持不变。
LLMClient = ModelGateway
llm_client = model_gateway
