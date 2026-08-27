"""RAG 链路观测辅助：只保留可聚合、不会泄露正文的摘要字段。"""

from __future__ import annotations

import hashlib
import contextvars


TRACE_VERSION = 1
_last_trace: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "rag_last_retrieval_trace", default=None,
)


def query_summary(query: str, *, variant_count: int = 0) -> dict:
    value = query or ""
    return {
        "length": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "variant_count": int(variant_count),
    }


def set_last_trace(trace: dict | None) -> None:
    _last_trace.set(trace)


def get_last_trace() -> dict | None:
    return _last_trace.get()


def score_margin(chunks: list[dict]) -> float:
    scores = sorted(
        (float(item.get("retrieval_score") or 0.0) for item in chunks),
        reverse=True,
    )
    if len(scores) < 2:
        return round(scores[0], 4) if scores else 0.0
    return round(scores[0] - scores[1], 4)
