"""答案与引用簇：由检索结果构造带引用的回答、上下文拼装与置信度估计。

这些方法原本是 ``RAGService`` 上的答案生成辅助方法，以 mixin 形式抽离以削减上帝类规模。
它们依赖宿主实例的状态与委托方法（``self.collection``、``self.get_runtime_config``、
``self._build_where``、``self._extract_query_units`` 等，定义于 ``RAGService`` 或
``RetrievalMixin``），因此仍通过 ``self.`` 访问，行为不变。
"""

import asyncio
import logging
import time

from app.core.config import get_settings
from app.services.llm.llm_observability_service import llm_observability_service
from app.services.rag.trace import TRACE_VERSION, query_summary

settings = get_settings()
logger = logging.getLogger(__name__)


class AnswerMixin:
    def _build_citations(self, chunks: list[dict]) -> list[dict]:
        """引用覆盖全部喂给模型的 context chunk（片段 N ↔ 引用 N），避免模型引用无对应引用。"""
        citations = []
        for chunk in chunks:
            metadata = chunk.get("metadata") or {}
            citations.append(
                {
                    "document_id": metadata.get("document_id"),
                    "chunk_id": metadata.get("chunk_id"),
                    "embedding_id": metadata.get("embedding_id"),
                    "chunk_index": metadata.get("chunk_index"),
                    "page_number": metadata.get("page_number"),
                    "section_title": metadata.get("section_title"),
                    "locator": self._build_citation_locator(metadata),
                    "document_anchor": {
                        "document_id": metadata.get("document_id"),
                        "page_number": metadata.get("page_number"),
                        "chunk_index": metadata.get("chunk_index"),
                        "section_path": metadata.get("section_path"),
                    },
                    "visual_evidence": metadata.get("visual_evidence"),
                    "visual_region": metadata.get("visual_region"),
                    "source_text": self._excerpt(chunk["content"]),
                    "retrieval_score": chunk.get("retrieval_score"),
                    "rrf_score": chunk.get("rrf_score"),
                }
            )
        return citations

    def _build_response(
        self,
        *,
        answer: str,
        citations: list[dict],
        hit_chunks: list[dict],
        context_chunks: list[dict],
        confidence: float,
        can_answer: bool,
        refusal_reason: str | None = None,
        started: float,
        retrieval_duration_ms: int = 0,
        generation_duration_ms: int = 0,
        runtime_config: dict | None = None,
        retrieval_trace: dict | None = None,
        degradation_reason: str | None = None,
    ) -> dict:
        latency_ms = int((time.time() - started) * 1000)
        rerank_duration_ms = max(latency_ms - retrieval_duration_ms - generation_duration_ms, 0)
        response = {
            "answer": answer,
            "citations": citations,
            "confidence": round(max(0.0, min(confidence, 1.0)), 2),
            "can_answer": can_answer,
            "refusal_reason": refusal_reason,
            "hit_chunks": hit_chunks,
            "context_chunks": context_chunks,
            "latency_ms": latency_ms,
            "observability": {
                "retrieval_duration_ms": retrieval_duration_ms,
                "rerank_duration_ms": rerank_duration_ms,
                "generation_duration_ms": generation_duration_ms,
                "hit_chunk_count": len(hit_chunks),
                "context_chunk_count": len(context_chunks),
                "citation_count": len(citations),
                "result_status": "answered" if can_answer else "refused",
                "refusal_reason": refusal_reason,
                "degradation_reason": (
                    degradation_reason
                    or ((retrieval_trace or {}).get("retrieval") or {}).get("rerank_status") == "fallback"
                    and "rerank_unavailable"
                    or refusal_reason
                ),
                "trace_version": TRACE_VERSION,
            },
            "runtime_config": runtime_config or self.get_runtime_config(),
        }
        if retrieval_trace:
            response["retrieval_trace"] = retrieval_trace
            response["observability"]["retrieval_trace"] = retrieval_trace
        return response

    def _record_pipeline_log(
        self,
        query: str,
        document_id: int | None,
        user_id: int | None,
        runtime_config: dict,
        result: dict,
    ) -> None:
        observability = result.get("observability") or {}
        llm_observability_service.log_event(
            module_name="document",
            action="rag_pipeline",
            model_name=settings.LLM_MODEL,
            status=observability.get("result_status") or ("success" if result.get("can_answer") else "refused"),
            duration_ms=result.get("latency_ms"),
            user_id=user_id,
            request_excerpt={
                "query_sha256": query_summary(query)["sha256"],
                "query_length": len(query or ""),
                "document_id": document_id,
                **runtime_config,
            },
            response_excerpt={**observability, "retrieval_trace": result.get("retrieval_trace")},
        )

    def _build_prompt_context(self, chunks: list[dict]) -> str:
        # 上下文处理：token 预算裁剪，避免拼接超限导致生成退化
        max_tokens = settings.RAG_CONTEXT_MAX_TOKENS
        blocks = []
        used_tokens = 0
        for index, chunk in enumerate(chunks, start=1):
            metadata = chunk.get("metadata") or {}
            parts = [f"片段 {index}"]
            if metadata.get("page_number") is not None:
                parts.append(f"page:{metadata['page_number']}")
            if metadata.get("section_title"):
                parts.append(f"section:{metadata['section_title']}")
            if metadata.get("section_path"):
                parts.append(f"path:{metadata['section_path']}")
            if metadata.get("segment_type"):
                parts.append(f"type:{metadata['segment_type']}")
            if metadata.get("visual_tags"):
                parts.append(f"tags:{metadata['visual_tags']}")
            if metadata.get("visual_region"):
                parts.append(f"region:{metadata['visual_region']}")
            if metadata.get("chunk_index") is not None:
                parts.append(f"chunk:{metadata['chunk_index']}")
            summary = self._build_visual_context_summary(metadata)
            block_content = chunk.get("content") or ""
            if summary:
                block_content = f"{summary}\n{block_content}".strip()
            if metadata.get("visual_evidence"):
                block_content = f"{block_content}\n[视觉证据]\n{metadata['visual_evidence']}".strip()
            header = f"[{' | '.join(parts)}]"
            block = f"{header}\n{block_content}"
            est_tokens = self._estimate_tokens(block)
            if max_tokens and used_tokens + est_tokens > max_tokens:
                remaining = max_tokens - used_tokens
                if remaining >= 40:
                    keep_chars = max(int(remaining * 1.5), 0)
                    blocks.append(f"{header}\n{block_content[:keep_chars]}")
                break
            blocks.append(block)
            used_tokens += est_tokens
        return "\n\n".join(blocks)

    async def _expand_context_chunks(
        self,
        chunks: list[dict],
        *,
        document_id: int | None,
        user_id: int | None,
        neighbor_window: int,
        max_chunks: int,
        knowledge_base_id: int | None = None,
        document_status: str | None = None,
        authorized_document_ids: list[int] | None = None,
    ) -> list[dict]:
        if not chunks:
            return []
        if document_id is None or (neighbor_window <= 0 and max_chunks <= len(chunks)):
            return chunks[:max_chunks]

        where = self._build_where(
            document_id=document_id,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            document_status=document_status,
            authorized_document_ids=authorized_document_ids,
        )
        try:
            rows = await asyncio.to_thread(
                self.collection.get,
                where=where,
                include=["documents", "metadatas"],
            )
        except Exception as exc:
            logger.warning(
                "Vector store context expansion unavailable; using retrieved chunks only (%s)",
                type(exc).__name__,
            )
            return chunks[:max_chunks]
        ids = rows.get("ids") or []
        documents = rows.get("documents") or []
        metadatas = rows.get("metadatas") or []
        by_index: dict[int, dict] = {}
        for index, chunk_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else {}
            chunk_index = metadata.get("chunk_index") if isinstance(metadata, dict) else None
            if chunk_index is None:
                continue
            by_index[int(chunk_index)] = {
                "id": chunk_id,
                "content": documents[index] if index < len(documents) else "",
                "metadata": metadata or {},
                "distance": None,
                "retrieval_score": 0.0,
                "retrieval_routes": ["context_expand"],
                "dense_score": 0.0,
                "keyword_score": 0.0,
                "structure_score": 0.0,
                "matched_variants": [],
            }

        selected: dict[str, tuple[int, int, dict]] = {}
        for rank, chunk in enumerate(chunks):
            metadata = chunk.get("metadata") or {}
            seed_index = metadata.get("chunk_index")
            seed_id = str(chunk.get("id"))
            selected.setdefault(seed_id, (rank, int(seed_index) if seed_index is not None else rank, chunk))
            if seed_index is None:
                continue
            for offset in range(-neighbor_window, neighbor_window + 1):
                neighbor = by_index.get(int(seed_index) + offset)
                if not neighbor:
                    continue
                neighbor_id = str(neighbor.get("id"))
                selected.setdefault(neighbor_id, (rank, int(seed_index) + offset, neighbor))

        ordered = sorted(selected.values(), key=lambda item: (item[0], item[1]))
        return [item[2] for item in ordered[:max_chunks]]

    def _is_answer_grounded(self, answer: str, chunks: list[dict], *, can_answer: bool) -> bool:
        if not can_answer:
            return True
        answer_terms = self._extract_query_units(answer)
        if not answer_terms:
            return True
        corpus_parts = []
        # grounding 覆盖全部喂给模型的 context chunk，避免答案仅由后半段片段支持却被误判为不可靠
        for chunk in chunks:
            metadata = chunk.get("metadata") or {}
            corpus_parts.append(chunk.get("content") or "")
            corpus_parts.append(self._build_keyword_corpus(chunk.get("content") or "", metadata))
        corpus = "\n".join(part for part in corpus_parts if part)
        matched_terms = sum(1 for term in answer_terms if term in corpus)
        return matched_terms >= min(2, len(answer_terms))

    def _keyword_overlap_score(self, query: str, chunks: list[dict]) -> float:
        terms = self._extract_query_units(query)
        if not terms:
            return 0.5
        matched = 0
        corpus = "\n".join(chunk["content"] for chunk in chunks[:3])
        for term in terms:
            if term in corpus:
                matched += 1
        return matched / max(len(terms), 1)

    def _estimate_confidence(self, query: str, chunks: list[dict]) -> float:
        keyword_score = self._keyword_overlap_score(query, chunks)
        distance_scores = [self._distance_score(chunk.get("distance")) for chunk in chunks[:3]]
        distance_score = sum(distance_scores) / len(distance_scores) if distance_scores else 0.0
        margin = self._top_score_margin(chunks)
        route = self._route_consistency(chunks)
        return (keyword_score * 0.45) + (distance_score * 0.30) + (margin * 0.15) + (route * 0.10)
