"""Controlled Agentic RAG orchestration built on top of the existing RAG service.

The graph is intentionally bounded: it may refine a retrieval query once, but
never bypasses document permissions, citation grounding, or refusal checks in
``RAGService``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.async_utils import run_async
from app.core.config import get_settings
from app.core.telemetry import observe_span
from app.services.llm.llm_observability_service import llm_observability_service
from app.services.llm.llm_service import llm_service
from app.services.rag.rag_service import rag_service
from app.services.rag.trace import get_last_trace
from app.workflows.langgraph_compat import GRAPH_END, GRAPH_START, StateGraph, workflow_engine_name


class AgenticRAGService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._workflow = self._build_workflow()

    @staticmethod
    def _should_use_model_planner(query: str) -> bool:
        normalized = query or ""
        complex_markers = ("比较", "对比", "差异", "风险", "条件", "流程", "哪些", "分别", "以及", "和")
        return len(normalized) >= 24 or any(marker in normalized for marker in complex_markers)

    async def _plan_query(self, query: str, *, user_id: int | None, refinement: bool = False) -> tuple[str, str]:
        if not self.settings.AGENTIC_RAG_PLANNER_ENABLED or not self._should_use_model_planner(query):
            return query, "rule"
        prompt = (
            "你是企业知识库检索规划器，只能优化检索表达，不回答用户问题，不添加事实。\n"
            "输出 JSON：{\"search_query\": \"不超过300字的检索问题\"}。\n"
            f"原始问题：{query}\n"
            + ("上一轮证据不足，请将问题改写为更利于定位制度、条款、日期、金额、责任人或例外条件的检索表达。" if refinement else "请保留原问题的业务实体、时间、数值和约束。")
        )
        try:
            with observe_span("rag.plan", {"rag.planner": "llm"}):
                raw = await llm_service.generate(prompt, temperature=0.0, action="agentic_rag_plan", user_id=user_id)
            payload = llm_service.parse_json_object(raw)
            search_query = str(payload.get("search_query") or "").strip()
            if search_query and len(search_query) <= 300:
                return search_query, "llm"
        except Exception:
            pass
        return query, "rule_fallback"

    @staticmethod
    def _rule_refine(query: str) -> str:
        suffix = " 关键条款 条件 例外 日期 金额 责任人"
        return f"{query}{suffix}"[:300]

    def _build_workflow(self):
        graph = StateGraph(dict)
        graph.add_node("plan", self._workflow_plan)
        graph.add_node("retrieve", self._workflow_retrieve)
        graph.add_node("assess_evidence", self._workflow_assess_evidence)
        graph.add_node("refine", self._workflow_refine)
        graph.add_node("generate", self._workflow_generate)
        graph.add_edge(GRAPH_START, "plan")
        graph.add_edge("plan", "retrieve")
        graph.add_edge("retrieve", "assess_evidence")
        graph.add_conditional_edges(
            "assess_evidence",
            self._route_after_assessment,
            {"refine": "refine", "generate": "generate"},
        )
        graph.add_edge("refine", "retrieve")
        graph.add_edge("generate", GRAPH_END)
        return graph.compile()

    async def _workflow_plan(self, state: dict[str, Any]) -> dict[str, Any]:
        query, mode = await self._plan_query(state["question"], user_id=state.get("user_id"))
        state["active_query"] = query
        state["trace"].append({"node": "plan", "mode": mode, "query_changed": query != state["question"]})
        return state

    async def _workflow_retrieve(self, state: dict[str, Any]) -> dict[str, Any]:
        started = time.time()
        search_query = state["active_query"]
        # 会话记忆：追问消歧——用上一轮用户问题补全当前检索表达式
        history = state.get("conversation_history") or []
        last_user_question = next(
            (m.get("content") for m in reversed(history) if m.get("role") == "user"), None,
        )
        if last_user_question and last_user_question != state["question"]:
            search_query = f"{last_user_question} {search_query}"
        chunks = await rag_service.search_async(
            search_query,
            document_id=state.get("document_id"),
            top_k=state["runtime_config"]["top_k"],
            user_id=state.get("user_id"),
            min_recall_candidates=state["runtime_config"]["min_recall_candidates"],
            recall_multiplier=state["runtime_config"]["recall_multiplier"],
            query_variant_limit=state["runtime_config"]["query_variant_limit"],
            knowledge_base_id=state.get("knowledge_base_id"),
            document_status=state.get("document_status"),
            authorized_document_ids=state.get("authorized_document_ids"),
        )
        duration_ms = int((time.time() - started) * 1000)
        confidence = rag_service._estimate_confidence(state["question"], chunks) if chunks else 0.0
        state["round"] += 1
        state["retrieval_duration_ms"] += duration_ms
        state["latest_chunks"] = chunks
        retrieval_trace = get_last_trace() or {
            "trace_version": 1,
            "retrieval": {"reranked_candidates": len(chunks)},
        }
        state["latest_retrieval_trace"] = retrieval_trace
        state["latest_confidence"] = confidence
        if confidence >= state.get("best_confidence", -1.0):
            state["best_chunks"] = chunks
            state["best_confidence"] = confidence
        state["trace"].append(
            {
                "node": "retrieve",
                "round": state["round"],
                "hit_count": len(chunks),
                "confidence": round(confidence, 4),
                "duration_ms": duration_ms,
                "retrieval_trace": retrieval_trace.get("retrieval", {}),
            }
        )
        return state

    async def _workflow_assess_evidence(self, state: dict[str, Any]) -> dict[str, Any]:
        threshold = max(float(state["runtime_config"]["confidence_threshold"]), 0.45)
        evidence_ready = bool(state["latest_chunks"]) and state["latest_confidence"] >= threshold
        state["evidence_ready"] = evidence_ready
        state["trace"].append(
            {
                "node": "assess_evidence",
                "round": state["round"],
                "decision": "generate" if evidence_ready else "refine_or_refuse",
                "threshold": threshold,
            }
        )
        return state

    def _route_after_assessment(self, state: dict[str, Any]) -> str:
        if not state["evidence_ready"] and state["round"] < state["max_rounds"]:
            return "refine"
        return "generate"

    async def _workflow_refine(self, state: dict[str, Any]) -> dict[str, Any]:
        refined_query, mode = await self._plan_query(
            state["question"],
            user_id=state.get("user_id"),
            refinement=True,
        )
        if refined_query == state["active_query"]:
            refined_query = self._rule_refine(state["question"])
            mode = "rule_refinement"
        state["active_query"] = refined_query
        state["trace"].append({"node": "refine", "round": state["round"], "mode": mode})
        return state

    async def _workflow_generate(self, state: dict[str, Any]) -> dict[str, Any]:
        with observe_span("rag.agentic", {"rag.rounds": state["round"]}):
            result = await rag_service.answer_from_chunks_async(
                state["question"],
                chunks=state["best_chunks"],
                document_id=state.get("document_id"),
                user_id=state.get("user_id"),
                runtime_config=state["runtime_config"],
                started=state["started"],
                retrieval_duration_ms=state["retrieval_duration_ms"],
                log_query=state["question"],
                knowledge_base_id=state.get("knowledge_base_id"),
                document_status=state.get("document_status"),
                authorized_document_ids=state.get("authorized_document_ids"),
                conversation_history=state.get("conversation_history"),
                retrieval_trace=state.get("latest_retrieval_trace"),
            )
        trace = {
            "enabled": True,
            "trace_version": 1,
            "workflow_engine": workflow_engine_name(),
            "retrieval_rounds": state["round"],
            "final_evidence_confidence": round(max(state.get("best_confidence", 0.0), 0.0), 4),
            "steps": state["trace"],
            "generation": {
                "status": "success" if result.get("can_answer") else "degraded",
                "duration_ms": result.get("observability", {}).get("generation_duration_ms", 0),
                "degradation_reason": result.get("refusal_reason"),
            },
        }
        result["agentic_rag"] = trace
        result.setdefault("observability", {})["trace_version"] = 1
        result.setdefault("observability", {})["degradation_reason"] = result.get("refusal_reason")
        result.setdefault("observability", {})["agentic_retrieval_rounds"] = state["round"]
        llm_observability_service.log_event(
            module_name="document",
            action="agentic_rag",
            model_name=self.settings.LLM_MODEL,
            status="success" if result.get("can_answer") else "refused",
            duration_ms=result.get("latency_ms"),
            user_id=state.get("user_id"),
            request_excerpt={"document_id": state.get("document_id"), "max_rounds": state["max_rounds"]},
            response_excerpt={"rounds": state["round"], "confidence": trace["final_evidence_confidence"]},
        )
        state["result"] = result
        return state

    async def answer_async(
        self,
        question: str,
        *,
        document_id: int | None = None,
        user_id: int | None = None,
        knowledge_base_id: int | None = None,
        document_status: str | None = None,
        authorized_document_ids: list[int] | None = None,
        conversation_history: list[dict] | None = None,
        **runtime_overrides: Any,
    ) -> dict[str, Any]:
        runtime_config = rag_service.get_runtime_config(**runtime_overrides)
        if not self.settings.AGENTIC_RAG_ENABLED:
            result = await rag_service.answer_async(
                question,
                document_id=document_id,
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
                document_status=document_status,
                authorized_document_ids=authorized_document_ids,
                conversation_history=conversation_history,
                **runtime_overrides,
            )
            result["agentic_rag"] = {"enabled": False, "reason": "feature_disabled"}
            return result
        state = {
            "question": question,
            "document_id": document_id,
            "user_id": user_id,
            "knowledge_base_id": knowledge_base_id,
            "document_status": document_status,
            "authorized_document_ids": authorized_document_ids,
            "conversation_history": conversation_history,
            "runtime_config": runtime_config,
            "max_rounds": self.settings.AGENTIC_RAG_MAX_RETRIEVAL_ROUNDS,
            "round": 0,
            "retrieval_duration_ms": 0,
            "latest_chunks": [],
            "latest_confidence": 0.0,
            "best_chunks": [],
            "best_confidence": -1.0,
            "trace": [],
            "started": time.time(),
            "result": None,
            "latest_retrieval_trace": None,
        }
        final_state = await self._workflow.ainvoke(state)
        return final_state["result"]

    def answer(self, question: str, **kwargs: Any) -> dict[str, Any]:
        return run_async(self.answer_async(question, **kwargs))


agentic_rag_service = AgenticRAGService()
