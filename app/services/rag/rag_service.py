import asyncio
import logging
import threading
import time

from app.core.async_utils import run_async
from app.core.config import get_settings
from app.core.llm_client import llm_client
from app.services.llm.prompt_service import prompt_service
from app.services.rag._helpers import (
    build_citation_locator,
    build_visual_context_summary,
    compact_metadata,
    content_hash,
    estimate_tokens,
    excerpt,
    looks_like_refusal,
    metadata_hash,
    metadata_matches_where,
    normalize_query,
    parse_llm_json,
    should_use_llm_rewrite,
)
from app.services.rag._scoring import (
    distance_score,
    extract_query_units,
    query_mentions_page,
    query_mentions_table_capture,
    query_mentions_visual_region,
    query_prefers_list_segment,
    query_prefers_ocr_segment,
    query_prefers_table_like,
    query_prefers_visual_evidence,
    route_consistency,
    top_score_margin,
    visual_region_alias_text,
    visual_tag_match_bonus,
)
from app.services.rag.answer import AnswerMixin
from app.services.rag.indexing import IndexingMixin
from app.services.rag.query_rewrite import QueryRewriteMixin
from app.services.rag.rag_runtime import resolve_runtime_config
from app.services.rag.rerank import build_reranker
from app.services.rag.retrieval import RetrievalMixin
from app.services.rag.vector_store import build_vector_store
from app.services.rag.trace import TRACE_VERSION, query_summary, score_margin, set_last_trace
from app.core.telemetry import observe_span

settings = get_settings()
logger = logging.getLogger(__name__)


class RAGService(RetrievalMixin, AnswerMixin, IndexingMixin, QueryRewriteMixin):
    def __init__(self):
        self.store = build_vector_store()
        self.client = getattr(self.store, "client", None)
        self.collection = self.store.collection
        # RAG③：内存 BM25 关键词索引（懒构建、索引后失效重建，替代每次全表扫描）
        self._bm25_index = None
        self._bm25_items: list[tuple] = []
        self._bm25_stale = True
        # 并发构建锁 + 版本号（epoch）+ TTL：避免并发重复构建、陈旧构建覆盖新索引、长时间不失效导致数据过期
        self._bm25_lock = threading.Lock()
        self._bm25_epoch = 0
        self._bm25_built_at = 0.0
        # RAG④：可插拔重排（懒构建——按当前 RAG_RERANK_ENGINE 选 bge/llm/heuristic）
        self._reranker = None

    def _get_reranker(self):
        if self._reranker is None:
            self._reranker = build_reranker(self)
        return self._reranker

    def get_runtime_config(
        self,
        *,
        top_k: int | None = None,
        confidence_threshold: float | None = None,
        min_recall_candidates: int | None = None,
        recall_multiplier: int | None = None,
        query_variant_limit: int | None = None,
        context_neighbor_window: int | None = None,
        context_max_chunks: int | None = None,
    ) -> dict:
        return resolve_runtime_config(
            settings,
            top_k=top_k,
            confidence_threshold=confidence_threshold,
            min_recall_candidates=min_recall_candidates,
            recall_multiplier=recall_multiplier,
            query_variant_limit=query_variant_limit,
            context_neighbor_window=context_neighbor_window,
            context_max_chunks=context_max_chunks,
        )

    def _metadata_hash(self, metadata: dict) -> str:
        return metadata_hash(metadata)

    @staticmethod
    def _content_hash(text: str) -> str:
        return content_hash(text)

    @staticmethod
    def _compact_metadata(metadata: dict) -> dict:
        return compact_metadata(metadata)

    def _build_where(self, document_id: int | None = None, user_id: int | None = None,
                     knowledge_base_id: int | None = None, document_status: str | None = None,
                     authorized_document_ids: list[int] | None = None) -> dict | None:
        """构造检索过滤子句。

        传入 authorized_document_ids 时，用“document_id IN 授权集”作为权限过滤，
        取代 user_id == 当前用户（后者会漏掉共享文档）。空授权集返回恒假子句。
        """
        clauses = []
        if authorized_document_ids is not None:
            ids = [int(item) for item in authorized_document_ids]
            clauses.append({"document_id": {"$in": ids}} if ids else {"document_id": -1})
            if document_id is not None:
                clauses.append({"document_id": document_id})
        else:
            if document_id is not None:
                clauses.append({"document_id": document_id})
            if user_id is not None:
                clauses.append({"user_id": user_id})
        if knowledge_base_id is not None:
            clauses.append({"knowledge_base_id": knowledge_base_id})
        if document_status is not None:
            clauses.append({"document_status": document_status})
        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    async def search_async(
        self,
        query: str,
        document_id: int | None = None,
        top_k: int | None = None,
        user_id: int | None = None,
        min_recall_candidates: int | None = None,
        recall_multiplier: int | None = None,
        query_variant_limit: int | None = None,
        *,
        knowledge_base_id: int | None = None,
        document_status: str | None = None,
        authorized_document_ids: list[int] | None = None,
    ) -> list[dict]:
        chunks, trace = await self._search_with_trace_async(
            query,
            document_id=document_id,
            top_k=top_k,
            user_id=user_id,
            min_recall_candidates=min_recall_candidates,
            recall_multiplier=recall_multiplier,
            query_variant_limit=query_variant_limit,
            knowledge_base_id=knowledge_base_id,
            document_status=document_status,
            authorized_document_ids=authorized_document_ids,
        )
        set_last_trace(trace)
        return chunks

    async def _search_with_trace_async(
        self,
        query: str,
        document_id: int | None = None,
        top_k: int | None = None,
        user_id: int | None = None,
        min_recall_candidates: int | None = None,
        recall_multiplier: int | None = None,
        query_variant_limit: int | None = None,
        *,
        knowledge_base_id: int | None = None,
        document_status: str | None = None,
        authorized_document_ids: list[int] | None = None,
    ) -> tuple[list[dict], dict]:
        runtime_config = self.get_runtime_config(
            top_k=top_k,
            min_recall_candidates=min_recall_candidates,
            recall_multiplier=recall_multiplier,
            query_variant_limit=query_variant_limit,
        )
        where = self._build_where(
            document_id=document_id,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            document_status=document_status,
            authorized_document_ids=authorized_document_ids,
        )
        with observe_span("rag.plan", {"rag.query_variant_limit": runtime_config["query_variant_limit"]}):
            query_variants = self._rewrite_queries(query, limit=runtime_config["query_variant_limit"])
            if settings.RAG_QUERY_REWRITE_LLM_ENABLED:
                for llm_variant in await self._rewrite_query_llm(query, user_id):
                    if llm_variant not in query_variants:
                        query_variants.append(llm_variant)
        candidate_limit = max(
            runtime_config["top_k"] * runtime_config["recall_multiplier"],
            runtime_config["min_recall_candidates"],
        )
        retrieval_started = time.time()
        with observe_span("rag.retrieve", {"rag.top_k": runtime_config["top_k"]}):
            dense_candidates, keyword_candidates = await asyncio.gather(
                self._dense_multi_recall(query_variants, where=where, candidate_limit=candidate_limit, user_id=user_id),
                self._keyword_multi_recall(query_variants, where=where, candidate_limit=candidate_limit),
            )
            fused_candidates = self._rrf_fuse_candidates(
                dense_candidates=dense_candidates,
                keyword_candidates=keyword_candidates,
            )
        rerank_started = time.time()
        rerank_status = "success"
        with observe_span("rag.rerank", {"rag.candidate_count": len(fused_candidates)}):
            try:
                chunks = await self._get_reranker().rerank(
                    query=query,
                    query_variants=query_variants,
                    candidates=fused_candidates,
                    top_k=runtime_config["top_k"],
                    user_id=user_id,
                )
            except Exception:
                rerank_status = "fallback"
                logger.exception("RAG rerank failed; returning fused candidates")
                chunks = fused_candidates[: runtime_config["top_k"]]
        retrieval_trace = {
            "trace_version": TRACE_VERSION,
            "query": query_summary(query, variant_count=len(query_variants)),
            "retrieval": {
                "query_mode": "llm" if settings.RAG_QUERY_REWRITE_LLM_ENABLED else "rule",
                "dense_candidates": len(dense_candidates),
                "keyword_candidates": len(keyword_candidates),
                "fused_candidates": len(fused_candidates),
                "reranked_candidates": len(chunks),
                "duration_ms": int((time.time() - retrieval_started) * 1000),
                "rerank_duration_ms": int((time.time() - rerank_started) * 1000),
                "status": "success" if dense_candidates or keyword_candidates else "degraded",
                "rerank_status": rerank_status,
                "top_score": round(float((chunks[0].get("retrieval_score") or 0.0)), 4) if chunks else 0.0,
                "score_margin": score_margin(chunks),
            },
        }
        return chunks, retrieval_trace

    def search(
        self,
        query: str,
        document_id: int | None = None,
        top_k: int | None = None,
        user_id: int | None = None,
        min_recall_candidates: int | None = None,
        recall_multiplier: int | None = None,
        query_variant_limit: int | None = None,
        *,
        knowledge_base_id: int | None = None,
        document_status: str | None = None,
        authorized_document_ids: list[int] | None = None,
    ) -> list[dict]:
        return run_async(
            self.search_async(
                query,
                document_id=document_id,
                top_k=top_k,
                user_id=user_id,
                min_recall_candidates=min_recall_candidates,
                recall_multiplier=recall_multiplier,
                query_variant_limit=query_variant_limit,
                knowledge_base_id=knowledge_base_id,
                document_status=document_status,
                authorized_document_ids=authorized_document_ids,
            )
        )

    async def answer_async(
        self,
        query: str,
        document_id: int | None = None,
        user_id: int | None = None,
        top_k: int | None = None,
        confidence_threshold: float | None = None,
        min_recall_candidates: int | None = None,
        recall_multiplier: int | None = None,
        query_variant_limit: int | None = None,
        context_neighbor_window: int | None = None,
        context_max_chunks: int | None = None,
        *,
        knowledge_base_id: int | None = None,
        document_status: str | None = None,
        authorized_document_ids: list[int] | None = None,
        conversation_history: list[dict] | None = None,
    ) -> dict:
        runtime_config = self.get_runtime_config(
            top_k=top_k,
            confidence_threshold=confidence_threshold,
            min_recall_candidates=min_recall_candidates,
            recall_multiplier=recall_multiplier,
            query_variant_limit=query_variant_limit,
            context_neighbor_window=context_neighbor_window,
            context_max_chunks=context_max_chunks,
        )
        started = time.time()
        retrieval_started = time.time()
        chunks = await self.search_async(
            query,
            document_id=document_id,
            top_k=runtime_config["top_k"],
            user_id=user_id,
            min_recall_candidates=runtime_config["min_recall_candidates"],
            recall_multiplier=runtime_config["recall_multiplier"],
            query_variant_limit=runtime_config["query_variant_limit"],
            knowledge_base_id=knowledge_base_id,
            document_status=document_status,
            authorized_document_ids=authorized_document_ids,
        )
        retrieval_duration_ms = int((time.time() - retrieval_started) * 1000)
        retrieval_trace = {
            "trace_version": TRACE_VERSION,
            "query": query_summary(query),
            "retrieval": {
                "reranked_candidates": len(chunks),
                "duration_ms": retrieval_duration_ms,
                "status": "success" if chunks else "degraded",
                "top_score": round(float((chunks[0].get("retrieval_score") or 0.0)), 4) if chunks else 0.0,
                "score_margin": score_margin(chunks),
            },
        }
        return await self.answer_from_chunks_async(
            query,
            chunks=chunks,
            document_id=document_id,
            user_id=user_id,
            runtime_config=runtime_config,
            started=started,
            retrieval_duration_ms=retrieval_duration_ms,
            knowledge_base_id=knowledge_base_id,
            document_status=document_status,
            authorized_document_ids=authorized_document_ids,
            conversation_history=conversation_history,
            retrieval_trace=retrieval_trace,
        )

    async def answer_from_chunks_async(
        self,
        query: str,
        *,
        chunks: list[dict],
        document_id: int | None,
        user_id: int | None,
        runtime_config: dict,
        started: float | None = None,
        retrieval_duration_ms: int = 0,
        log_query: str | None = None,
        knowledge_base_id: int | None = None,
        document_status: str | None = None,
        authorized_document_ids: list[int] | None = None,
        conversation_history: list[dict] | None = None,
        retrieval_trace: dict | None = None,
    ) -> dict:
        """Generate a grounded answer from already retrieved chunks.

        Agentic retrieval loops call this method after evidence selection so the
        final answer uses the selected evidence instead of issuing a duplicate
        retrieval request.
        """
        started = started or time.time()
        if not chunks:
            result = self._build_response(
                answer="根据当前文档内容，无法确认该问题。",
                citations=[],
                hit_chunks=[],
                context_chunks=[],
                confidence=0.0,
                can_answer=False,
                refusal_reason="no_retrieval_hits",
                started=started,
                retrieval_duration_ms=retrieval_duration_ms,
                generation_duration_ms=0,
                runtime_config=runtime_config,
                retrieval_trace=retrieval_trace,
            )
            self._record_pipeline_log(log_query or query, document_id, user_id, runtime_config, result)
            return result

        context_chunks = await self._expand_context_chunks(
            chunks,
            document_id=document_id,
            user_id=user_id,
            neighbor_window=runtime_config["context_neighbor_window"],
            max_chunks=runtime_config["context_max_chunks"],
            knowledge_base_id=knowledge_base_id,
            document_status=document_status,
            authorized_document_ids=authorized_document_ids,
        )
        context = self._build_prompt_context(context_chunks)
        if conversation_history:
            # 会话记忆：注入最近对话作为上文，帮助理解追问
            recent = [m for m in conversation_history[-4:] if m.get("content")]
            conv_text = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in recent)
            if conv_text:
                context = f"[对话上下文]\n{conv_text}\n\n{context}"
        prompt = prompt_service.render_by_name(
            "rag_answer",
            user_id=user_id,
            question=query,
            context=context,
        )
        citations = self._build_citations(context_chunks)
        confidence = self._estimate_confidence(query, chunks)
        if confidence < runtime_config["confidence_threshold"]:
            result = self._build_response(
                answer="根据当前文档内容，无法确认该问题。",
                citations=citations[:2],
                hit_chunks=chunks,
                context_chunks=context_chunks,
                confidence=confidence,
                can_answer=False,
                refusal_reason="low_confidence",
                started=started,
                retrieval_duration_ms=retrieval_duration_ms,
                generation_duration_ms=0,
                runtime_config=runtime_config,
                retrieval_trace=retrieval_trace,
            )
            self._record_pipeline_log(log_query or query, document_id, user_id, runtime_config, result)
            return result

        metadata = prompt_service.get_template_metadata("rag_answer", user_id=user_id)
        generation_started = time.time()
        with observe_span("rag.generate", {"rag.model": settings.LLM_MODEL}):
            answer = await llm_client.generate(
                prompt,
                temperature=0.3,
                action="rag_answer",
                user_id=user_id,
                prompt_template=metadata.get("prompt_template"),
                prompt_version=metadata.get("prompt_version"),
            )
        generation_duration_ms = int((time.time() - generation_started) * 1000)
        normalized_answer = answer.strip()
        can_answer = not self._looks_like_refusal(normalized_answer)
        refusal_reason = None
        if not can_answer:
            normalized_answer = "根据当前文档内容，无法确认该问题。"
            confidence = min(confidence, 0.3)
            refusal_reason = "model_refusal"
        citation_grounded = self._is_answer_grounded(normalized_answer, context_chunks, can_answer=can_answer)
        if can_answer and not citation_grounded:
            normalized_answer = "根据当前文档内容，暂时无法给出可引用支持的确定答案。"
            confidence = min(confidence, 0.3)
            can_answer = False
            refusal_reason = "insufficient_citation_grounding"
        result = self._build_response(
            answer=normalized_answer,
            citations=citations,
            hit_chunks=chunks,
            context_chunks=context_chunks,
            confidence=confidence,
            can_answer=can_answer,
            refusal_reason=refusal_reason,
            started=started,
            retrieval_duration_ms=retrieval_duration_ms,
            generation_duration_ms=generation_duration_ms,
            runtime_config=runtime_config,
            retrieval_trace=retrieval_trace,
        )
        self._record_pipeline_log(log_query or query, document_id, user_id, runtime_config, result)
        return result

    def answer(
        self,
        query: str,
        document_id: int | None = None,
        user_id: int | None = None,
        top_k: int | None = None,
        confidence_threshold: float | None = None,
        min_recall_candidates: int | None = None,
        recall_multiplier: int | None = None,
        query_variant_limit: int | None = None,
        context_neighbor_window: int | None = None,
        context_max_chunks: int | None = None,
        *,
        knowledge_base_id: int | None = None,
        document_status: str | None = None,
        authorized_document_ids: list[int] | None = None,
        conversation_history: list[dict] | None = None,
    ) -> dict:
        return run_async(
            self.answer_async(
                query,
                document_id=document_id,
                user_id=user_id,
                top_k=top_k,
                confidence_threshold=confidence_threshold,
                min_recall_candidates=min_recall_candidates,
                recall_multiplier=recall_multiplier,
                query_variant_limit=query_variant_limit,
                context_neighbor_window=context_neighbor_window,
                context_max_chunks=context_max_chunks,
                knowledge_base_id=knowledge_base_id,
                document_status=document_status,
                authorized_document_ids=authorized_document_ids,
                conversation_history=conversation_history,
            )
        )

    @staticmethod
    def _excerpt(text: str, limit: int = 240) -> str:
        return excerpt(text, limit=limit)

    @staticmethod
    def _build_citation_locator(metadata: dict) -> str:
        return build_citation_locator(metadata)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return estimate_tokens(text)

    @staticmethod
    def _build_visual_context_summary(metadata: dict) -> str:
        return build_visual_context_summary(metadata)

    @staticmethod
    def _looks_like_refusal(answer: str) -> bool:
        return looks_like_refusal(answer)

    @staticmethod
    def _normalize_query(query: str) -> str:
        return normalize_query(query)

    @staticmethod
    def _should_use_llm_rewrite(query: str) -> bool:
        return should_use_llm_rewrite(query)

    @staticmethod
    def _parse_llm_json(raw: str) -> dict:
        return parse_llm_json(raw)

    @staticmethod
    def _metadata_matches_where(metadata: dict, where: dict | None) -> bool:
        return metadata_matches_where(metadata, where)

    @staticmethod
    def _distance_score(distance: float | None) -> float:
        return distance_score(distance)

    @staticmethod
    def _visual_region_alias_text(region: str | None) -> str:
        return visual_region_alias_text(region)

    @staticmethod
    def _visual_tag_match_bonus(query_variants: list[str], metadata: dict) -> float:
        return visual_tag_match_bonus(query_variants, metadata)

    @staticmethod
    def _query_prefers_table_like(query_variants: list[str]) -> bool:
        return query_prefers_table_like(query_variants)

    @staticmethod
    def _query_prefers_list_segment(query_variants: list[str]) -> bool:
        return query_prefers_list_segment(query_variants)

    @staticmethod
    def _query_prefers_ocr_segment(query_variants: list[str]) -> bool:
        return query_prefers_ocr_segment(query_variants)

    @staticmethod
    def _query_mentions_page(query_variants: list[str]) -> bool:
        return query_mentions_page(query_variants)

    @staticmethod
    def _query_prefers_visual_evidence(query_variants: list[str]) -> bool:
        return query_prefers_visual_evidence(query_variants)

    @staticmethod
    def _query_mentions_table_capture(query_variants: list[str]) -> bool:
        return query_mentions_table_capture(query_variants)

    @staticmethod
    def _query_mentions_visual_region(query_variants: list[str], region: str | None) -> bool:
        return query_mentions_visual_region(query_variants, region)

    @staticmethod
    def _extract_query_units(query: str) -> set[str]:
        return extract_query_units(query)

    @staticmethod
    def _top_score_margin(chunks: list[dict]) -> float:
        return top_score_margin(chunks)

    @staticmethod
    def _route_consistency(chunks: list[dict]) -> float:
        return route_consistency(chunks)


rag_service = RAGService()
