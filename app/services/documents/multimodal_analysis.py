"""多模态法律文档理解。

该模块把扫描合同最容易丢失的四类信息统一成可回放产物：
版面阅读顺序、词级 OCR 质量、印章/签字区域、表格条款及页码证据。
OCR 和视觉模型都是可选依赖；没有视觉模型时仍返回确定性的版面结果，
不会把模型猜测当作证据。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.telemetry import observe_span
from app.models.document import Document, DocumentMultimodalAnalysis
from app.services.documents.document_parsing import (
    DocumentParsePermanentError,
    _build_ocr_image_variants,
    _collect_ocr_words,
    _file_to_data_url,
    _group_ocr_words_into_rows,
    _load_ocr_dependencies,
    _normalize_text,
)
from app.services.documents.document_pipeline import resolve_local_path
from app.services.llm.llm_service import llm_service

settings = get_settings()
PARSER_VERSION = "multimodal-layout-v1"

_CLAUSE_TERMS = {
    "付款": "payment",
    "支付": "payment",
    "交付": "delivery",
    "验收": "acceptance",
    "违约": "breach",
    "赔偿": "liability",
    "责任": "liability",
    "保密": "confidentiality",
    "知识产权": "ip",
    "解除": "termination",
    "终止": "termination",
    "争议": "dispute_resolution",
    "管辖": "dispute_resolution",
    "期限": "term",
    "有效期": "term",
}
_REGION_TERMS = {
    "seal": re.compile(r"(盖章|盖印|公章|印章|签章|骑缝章|合同章|财务章)"),
    "signature": re.compile(r"(签字|签署|签名|签约人|授权代表|法定代表人|代表签字)"),
}


def _clamp(value: float | int | None, default: float = 0.0) -> float:
    try:
        number = float(value) if value is not None else default
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _word_confidence(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # pytesseract 返回 0-100；部分 OCR 引擎已经返回 0-1。
    return _clamp(number / 100.0 if number > 1 else number)


def _bbox(words: Iterable[dict]) -> dict[str, int] | None:
    values = list(words)
    if not values:
        return None
    left = min(int(item.get("left", 0)) for item in values)
    top = min(int(item.get("top", 0)) for item in values)
    right = max(int(item.get("right", left)) for item in values)
    bottom = max(int(item.get("top", top)) + int(item.get("height", 0)) for item in values)
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def validate_ocr_confidence(
    words: list[dict],
    text: str = "",
    *,
    min_word_confidence: float | None = None,
    low_confidence_ratio: float | None = None,
) -> dict[str, Any]:
    """校验词级 OCR 置信度并给出是否需要人工复核的结论。"""

    min_conf = _clamp(
        settings.OCR_MIN_WORD_CONFIDENCE if min_word_confidence is None else min_word_confidence,
        0.65,
    )
    max_low_ratio = _clamp(
        settings.OCR_LOW_CONFIDENCE_RATIO if low_confidence_ratio is None else low_confidence_ratio,
        0.25,
    )
    scored = []
    low_spans = []
    for word in words:
        conf = _word_confidence(word.get("conf"))
        if conf is None:
            continue
        scored.append(conf)
        if conf < min_conf:
            low_spans.append(
                {
                    "text": str(word.get("text") or ""),
                    "confidence": round(conf, 4),
                    "bbox": {
                        "left": int(word.get("left", 0)),
                        "top": int(word.get("top", 0)),
                        "right": int(word.get("right", 0)),
                        "bottom": int(word.get("top", 0)) + int(word.get("height", 0)),
                    },
                }
            )
    readable = _readable_ratio(text)
    if scored:
        average = sum(scored) / len(scored)
        low_ratio = len(low_spans) / len(scored)
    else:
        # Native text没有词级数据时保守使用字符可读率，不伪造高分。
        average = readable
        low_ratio = 0.0 if text.strip() else 1.0
    confidence = _clamp(average * 0.75 + readable * 0.25)
    review_required = (
        not text.strip()
        or confidence < _clamp(settings.OCR_REVIEW_CONFIDENCE, 0.78)
        or low_ratio > max_low_ratio
    )
    status = "low" if confidence < 0.5 else "review" if review_required else "high"
    return {
        "confidence": round(confidence, 4),
        "average_word_confidence": round(_clamp(average), 4),
        "readable_ratio": round(readable, 4),
        "low_confidence_ratio": round(_clamp(low_ratio), 4),
        "low_confidence_spans": low_spans[:100],
        "status": status,
        "review_required": review_required,
        "word_count": len(words),
    }


def _readable_ratio(text: str) -> float:
    normalized = _normalize_text(text or "")
    if not normalized:
        return 0.0
    meaningful = len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", normalized))
    total = len(re.findall(r"\S", normalized))
    return _clamp(meaningful / total if total else 0.0)


def _row_text(row: list[dict]) -> str:
    return " ".join(str(word.get("text") or "").strip() for word in row if str(word.get("text") or "").strip()).strip()


def _layout_blocks(words: list[dict]) -> list[dict]:
    rows = _group_ocr_words_into_rows(words)
    blocks = []
    for index, row in enumerate(rows):
        text = _row_text(row)
        if not text:
            continue
        confidences = [_word_confidence(item.get("conf")) for item in row]
        scored = [item for item in confidences if item is not None]
        blocks.append(
            {
                "order": index,
                "text": text,
                "bbox": _bbox(row),
                "confidence": round(sum(scored) / len(scored), 4) if scored else None,
                "word_count": len(row),
            }
        )
    return blocks


def parse_layout(words: list[dict]) -> dict:
    """返回稳定的版面阅读顺序结构，供 OCR/视觉模型结果合并。"""
    return {"reading_order": "top_to_bottom_left_to_right", "blocks": _layout_blocks(words)}


def identify_signature_seal_regions(
    words: list[dict],
    *,
    page_width: int | None = None,
    page_height: int | None = None,
    ocr_confidence: float | None = None,
) -> list[dict]:
    """根据 OCR 词和相邻版面行定位印章/签字候选区域。

    这是证据候选而非法律事实：没有 OCR 文字线索时不会声称“检测到印章”。
    """

    regions = []
    for row in _group_ocr_words_into_rows(words):
        text = _row_text(row)
        if not text:
            continue
        for region_type, pattern in _REGION_TERMS.items():
            match = pattern.search(text)
            if not match:
                continue
            matched_words = [word for word in row if pattern.search(str(word.get("text") or ""))]
            box = _bbox(matched_words or row)
            row_conf = validate_ocr_confidence(row, text)["confidence"]
            score = _clamp(0.55 + 0.25 * row_conf + (0.1 if match.group(1) in {"公章", "签字", "签名"} else 0.0))
            vertical_region = None
            if box and page_height:
                center = (box["top"] + box["bottom"]) / 2 / max(1, page_height)
                vertical_region = "top" if center < 0.33 else "middle" if center < 0.66 else "bottom"
            regions.append(
                {
                    "type": region_type,
                    "label": "印章" if region_type == "seal" else "签字",
                    "text": text[:240],
                    "bbox": box,
                    "page_width": page_width,
                    "page_height": page_height,
                    "vertical_region": vertical_region,
                    "region": vertical_region,
                    "confidence": round(_clamp(score * (0.8 + 0.2 * _clamp(ocr_confidence, 1.0))), 4),
                    "source": "ocr_layout",
                }
            )
    return regions


def _split_row_cells(row: list[dict]) -> list[str]:
    if not row:
        return []
    values = []
    current = [row[0]]
    # 大间距通常表示扫描表格的列边界。
    widths = [max(1, int(item.get("width", 1))) for item in row]
    gap_threshold = max(14, int(sum(widths) / len(widths) * 0.8))
    right = int(row[0].get("right", 0))
    for word in row[1:]:
        left = int(word.get("left", 0))
        if left - right > gap_threshold:
            values.append(" ".join(str(item.get("text") or "") for item in current).strip())
            current = [word]
        else:
            current.append(word)
        right = max(right, int(word.get("right", left)))
    values.append(" ".join(str(item.get("text") or "") for item in current).strip())
    return [value for value in values if value]


def extract_table_clauses(
    words: list[dict],
    *,
    page_number: int,
    ocr_confidence: float | None = None,
) -> tuple[list[dict], list[dict]]:
    """从 OCR 行恢复表格，并将含法律义务的行转成可引用条款。"""

    rows = _group_ocr_words_into_rows(words)
    table_rows = []
    for row in rows:
        cells = _split_row_cells(row)
        if len(cells) >= 2:
            table_rows.append({"cells": cells, "text": " | ".join(cells), "bbox": _bbox(row), "words": row})
    if len(table_rows) < 2:
        return [], []

    headers = table_rows[0]["cells"]
    tables = [
        {
            "page_number": page_number,
            "headers": headers,
            "rows": [{"cells": item["cells"], "text": item["text"]} for item in table_rows[1:]],
            "bbox": _bbox(word for item in table_rows for word in item["words"]),
            "confidence": round(_clamp(ocr_confidence), 4) if ocr_confidence is not None else None,
            "source": "ocr_layout",
        }
    ]
    clauses = []
    for index, item in enumerate(table_rows[1:], start=1):
        text = item["text"]
        matched = [(term, kind) for term, kind in _CLAUSE_TERMS.items() if term in text]
        if not matched:
            # 表头本身是法律字段时，即使内容没有关键词也保留为可核验条款。
            matched = [(header, _CLAUSE_TERMS[term]) for header in headers for term in _CLAUSE_TERMS if term in header]
        if not matched:
            continue
        terms = list(dict.fromkeys(kind for _, kind in matched))
        clauses.append(
            {
                "clause_type": terms[0],
                "clause_types": terms,
                "clause_no": f"表格第{index}行",
                "title": "、".join(term for term, _ in matched[:3]),
                "content": text[:1200],
                "page_number": page_number,
                "bbox": item["bbox"],
                "confidence": round(_clamp((ocr_confidence or 0.5) * 0.9), 4),
                "source": "table_ocr",
            }
        )
    return tables, clauses


def locate_evidence_pages(analysis: dict, query: str, *, limit: int = 10) -> list[dict]:
    """在多模态产物中定位原文页码、版面框和置信度。"""

    needle = _normalize_text(query or "")
    if not needle:
        return []
    terms = [term for term in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", needle) if term]
    hits = []
    for page in analysis.get("pages") or []:
        page_text = str(page.get("text") or "")
        haystack = _normalize_text(page_text)
        exact = needle in haystack
        score = 1.0 if exact else (sum(1 for term in terms if term in haystack) / max(1, len(terms)))
        if score <= 0:
            for clause in page.get("clauses") or []:
                clause_text = str(clause.get("content") or "")
                clause_score = sum(1 for term in terms if term in clause_text) / max(1, len(terms))
                if clause_score > score:
                    score = clause_score
        matched_region = None
        if score <= 0:
            for region in page.get("regions") or []:
                region_text = f"{region.get('label', '')} {region.get('text', '')}"
                region_score = sum(1 for term in terms if term in region_text) / max(1, len(terms))
                if region_score > score:
                    score = region_score
                    matched_region = region
            for region in (page.get("vision") or {}).get("regions") or []:
                region_text = f"{region.get('label', '')} {region.get('evidence', '')}"
                region_score = sum(1 for term in terms if term in region_text) / max(1, len(terms))
                if region_score > score:
                    score = region_score
                    matched_region = region
        if score <= 0:
            continue
        excerpt = page_text[:300]
        if exact:
            start = page_text.find(needle)
            if start < 0:
                start = haystack.find(needle)
            if start < 0:
                start = 0
            excerpt = page_text[max(0, start - 80): start + len(needle) + 180]
        blocks = page.get("layout", {}).get("blocks") or []
        block = next((item for item in blocks if any(term in item.get("text", "") for term in terms)), None)
        hits.append(
            {
                "page_number": page.get("page_number"),
                "excerpt": (excerpt if page_text else str((matched_region or {}).get("evidence") or (matched_region or {}).get("text") or ""))[:400],
                "bbox": (block or matched_region or {}).get("bbox"),
                "ocr_confidence": page.get("ocr", {}).get("confidence"),
                "match_score": round(score, 4),
                "exact": exact,
                "source": "multimodal_page",
            }
        )
    hits.sort(key=lambda item: (-float(item.get("match_score") or 0), int(item.get("page_number") or 0)))
    return hits[: max(1, min(limit, 100))]


# 领域命名别名：便于任务/评测代码直接调用，不让业务层依赖私有 OCR 实现名。
validate_ocr = validate_ocr_confidence
detect_signature_seal_regions = identify_signature_seal_regions
extract_table_legal_clauses = extract_table_clauses
locate_evidence = locate_evidence_pages


def _build_text_from_rows(rows: list[list[dict]]) -> str:
    return _normalize_text("\n".join(_row_text(row) for row in rows if _row_text(row)))


def _native_page(page: Any, page_number: int) -> dict:
    text = _normalize_text(page.extract_text() or "")
    quality = validate_ocr_confidence([], text)
    return {
        "page_number": page_number,
        "text": text,
        "source": "native_text",
        "width": getattr(page, "width", None),
        "height": getattr(page, "height", None),
        "layout": {"reading_order": "top_to_bottom_left_to_right", "blocks": []},
        "ocr": quality,
        "regions": [],
        "tables": [],
        "clauses": [],
        "warnings": [],
    }


def _ocr_image_data(image) -> tuple[str, list[dict], dict]:
    _, _, _, _, pytesseract = _load_ocr_dependencies()
    if pytesseract is None:
        raise DocumentParsePermanentError("当前环境未启用 OCR，无法完成版面分析。")
    output_type = getattr(getattr(pytesseract, "Output", None), "DICT", None)
    best: tuple[str, list[dict], dict] | None = None
    for variant in _build_ocr_image_variants(image):
        for lang in ("chi_sim+eng", "eng", None):
            try:
                kwargs = {"output_type": output_type} if output_type is not None else {}
                if lang:
                    kwargs["lang"] = lang
                data = pytesseract.image_to_data(variant, **kwargs)
                words = _collect_ocr_words(data if isinstance(data, dict) else {})
                rows = _group_ocr_words_into_rows(words)
                text = _build_text_from_rows(rows)
                quality = validate_ocr_confidence(words, text)
                candidate = (text, words, quality)
                if best is None or (quality["confidence"], len(text)) > (best[2]["confidence"], len(best[0])):
                    best = candidate
                if text and quality["status"] == "high":
                    return candidate
            except Exception:
                continue
    if best is None or not best[0]:
        raise DocumentParsePermanentError("OCR 未识别到可用文本。")
    return best


def _vision_prompt() -> str:
    return (
        "你是法律文档版面校验器。只返回 JSON，不要猜测不可见内容。"
        "字段：regions（type=seal/signature，label，evidence，confidence 0-1），"
        "tables（标题和页内字段），warnings。只报告图像中明确可见的区域。"
    )


def _normalize_vision_regions(items: list[Any]) -> list[dict]:
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        region_type = str(item.get("type") or "").strip().lower()
        if region_type not in {"seal", "signature", "stamp", "sign"}:
            continue
        if region_type == "stamp":
            region_type = "seal"
        if region_type == "sign":
            region_type = "signature"
        bbox = item.get("bbox")
        if not isinstance(bbox, dict):
            bbox = None
        normalized.append(
            {
                "type": region_type,
                "label": str(item.get("label") or ("印章" if region_type == "seal" else "签字"))[:64],
                "evidence": str(item.get("evidence") or item.get("text") or "")[:240],
                "confidence": round(_clamp(item.get("confidence"), 0.5), 4),
                "bbox": bbox,
            }
        )
    return normalized[:20]


class MultimodalDocumentAnalyzer:
    async def analyze(
        self,
        *,
        document: Document,
        db: Session,
        user_id: int | None = None,
        use_vision_model: bool | None = None,
        force: bool = False,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> dict:
        existing = (
            db.query(DocumentMultimodalAnalysis)
            .filter(
                DocumentMultimodalAnalysis.document_id == document.id,
                DocumentMultimodalAnalysis.version_number == document.version_number,
            )
            .first()
        )
        if existing is not None and not force:
            try:
                payload = json.loads(existing.payload_json or "{}")
            except json.JSONDecodeError:
                payload = {}
            payload["replayed"] = True
            return payload

        path = resolve_local_path(document)
        with observe_span(
            "document.multimodal.analyze",
            {
                "document.id": document.id,
                "document.file_type": (document.file_type or "")[:16],
                "document.version": document.version_number,
            },
        ) as span:
            pages = self._extract_pages(path, document.file_type, page_start=page_start, page_end=page_end)
            if span is not None:
                try:
                    span.set_attribute("document.page_count", len(pages))
                    span.set_attribute("document.ocr_review_required", any((page.get("ocr") or {}).get("review_required") for page in pages))
                except Exception:
                    pass
        warnings = []
        vision_enabled = settings.MULTIMODAL_USE_VISION_MODEL if use_vision_model is None else bool(use_vision_model)
        if vision_enabled and pages:
            try:
                with observe_span("document.multimodal.vision", {"document.id": document.id}):
                    await self._enrich_with_vision(pages, path, document.file_type, user_id=user_id)
            except Exception as exc:  # 视觉模型不可用不影响 OCR 证据产物
                warnings.append({"stage": "vision_model", "message": str(exc)[:240]})

        confidences = [float((page.get("ocr") or {}).get("confidence") or 0.0) for page in pages]
        overall = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
        review_required = bool(warnings) or any((page.get("ocr") or {}).get("review_required") for page in pages)
        vision_regions = []
        for page in pages:
            for item in (page.get("vision") or {}).get("regions") or []:
                if not isinstance(item, dict):
                    continue
                vision_regions.append({**item, "page_number": page.get("page_number"), "source": "vision_model"})
        result = {
            "document_id": document.id,
            "version_number": document.version_number,
            "parser_version": PARSER_VERSION,
            "vision_model": getattr(settings, "LLM_VISION_MODEL", "") or getattr(settings, "LLM_MODEL", ""),
            "page_count": len(pages),
            "ocr_confidence": overall,
            "review_required": review_required,
            "pages": pages,
            "regions": [region for page in pages for region in page.get("regions") or []] + vision_regions,
            "tables": [table for page in pages for table in page.get("tables") or []],
            "clauses": [clause for page in pages for clause in page.get("clauses") or []],
            "warnings": warnings,
        }
        self._persist(db, result)
        return result

    def _extract_pages(
        self,
        path: Path,
        file_type: str,
        *,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> list[dict]:
        normalized = (file_type or path.suffix).lower()
        start = max(1, page_start or 1)
        end = max(start, page_end or 10**9)
        pages: list[dict] = []
        if normalized in {"pdf", ".pdf"}:
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                max_pages = settings.MULTIMODAL_MAX_PAGES or len(pdf.pages)
                for number, pdf_page in enumerate(pdf.pages[:max_pages], start=1):
                    if number < start or number > end:
                        continue
                    page = _native_page(pdf_page, number)
                    # 扫描页或原生文本质量不足时执行一次带坐标 OCR。
                    if not page["text"] or page["ocr"]["review_required"]:
                        try:
                            rendered = pdf_page.to_image(resolution=max(120, settings.OCR_PDF_RENDER_DPI))
                            image = getattr(rendered, "original", None)
                            if image is not None:
                                text, words, quality = _ocr_image_data(image)
                                page.update(
                                    {
                                        "text": text,
                                        "source": "ocr",
                                        "width": getattr(image, "width", None),
                                        "height": getattr(image, "height", None),
                                        "layout": {
                                            "reading_order": "top_to_bottom_left_to_right",
                                            "blocks": _layout_blocks(words),
                                        },
                                        "ocr": quality,
                                    }
                                )
                                page["regions"] = identify_signature_seal_regions(
                                    words,
                                    page_width=getattr(image, "width", None),
                                    page_height=getattr(image, "height", None),
                                    ocr_confidence=quality["confidence"],
                                )
                                page["tables"], page["clauses"] = extract_table_clauses(
                                    words, page_number=number, ocr_confidence=quality["confidence"]
                                )
                        except Exception as exc:
                            page["warnings"].append({"stage": "ocr", "message": str(exc)[:240]})
                    pages.append(page)
            return pages

        if normalized in {"png", ".png", "jpg", ".jpg", "jpeg", ".jpeg", "bmp", ".bmp", "webp", ".webp"}:
            if start > 1 or end < 1:
                return []
            image_module, *_ = _load_ocr_dependencies()
            if image_module is None:
                return [
                    {
                        "page_number": 1,
                        "text": "",
                        "source": "ocr_unavailable",
                        "width": None,
                        "height": None,
                        "layout": {"reading_order": "top_to_bottom_left_to_right", "blocks": []},
                        "ocr": validate_ocr_confidence([], ""),
                        "regions": [],
                        "tables": [],
                        "clauses": [],
                        "warnings": [{"stage": "ocr", "message": "当前环境未启用 OCR。"}],
                    }
                ]
            with image_module.open(path) as image:
                try:
                    text, words, quality = _ocr_image_data(image)
                except Exception as exc:
                    return [
                        {
                            "page_number": 1,
                            "text": "",
                            "source": "ocr_unavailable",
                            "width": getattr(image, "width", None),
                            "height": getattr(image, "height", None),
                            "layout": {"reading_order": "top_to_bottom_left_to_right", "blocks": []},
                            "ocr": validate_ocr_confidence([], ""),
                            "regions": [],
                            "tables": [],
                            "clauses": [],
                            "warnings": [{"stage": "ocr", "message": str(exc)[:240]}],
                        }
                    ]
                page = {
                    "page_number": 1,
                    "text": text,
                    "source": "ocr",
                    "width": getattr(image, "width", None),
                    "height": getattr(image, "height", None),
                    "layout": {"reading_order": "top_to_bottom_left_to_right", "blocks": _layout_blocks(words)},
                    "ocr": quality,
                    "regions": identify_signature_seal_regions(
                        words, page_width=getattr(image, "width", None), page_height=getattr(image, "height", None), ocr_confidence=quality["confidence"]
                    ),
                    "tables": [],
                    "clauses": [],
                    "warnings": [],
                }
                page["tables"], page["clauses"] = extract_table_clauses(
                    words, page_number=1, ocr_confidence=quality["confidence"]
                )
                return [page]
        raise ValueError(f"不支持多模态分析文件类型：{file_type}")

    async def _enrich_with_vision(self, pages: list[dict], path: Path, file_type: str, *, user_id: int | None) -> None:
        # 只把扫描页交给视觉模型，且最多 8 页，避免重复传输整份合同。
        targets = [page for page in pages if page.get("source") in {"ocr", "ocr_unavailable"}][:8]
        if not targets:
            return
        image_url = _file_to_data_url(str(path), file_type)
        raw = await llm_service.generate_with_images(
            _vision_prompt(),
            image_urls=[image_url],
            temperature=0.1,
            action="document_multimodal_layout",
            user_id=user_id,
        )
        try:
            parsed = llm_service.parse_json_object(raw)
        except Exception:
            parsed = {}
        if not isinstance(parsed, dict):
            return
        model_regions = parsed.get("regions") if isinstance(parsed.get("regions"), list) else []
        model_warnings = parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else []
        for page in targets:
            page.setdefault("vision", {})["model_used"] = True
            page["vision"]["warnings"] = [str(item)[:240] for item in model_warnings[:10]]
            # 模型坐标没有页码时只作为辅助线索，不能覆盖 OCR 定位。
            page["vision"]["regions"] = _normalize_vision_regions(model_regions)

    @staticmethod
    def _persist(db: Session, result: dict) -> None:
        row = (
            db.query(DocumentMultimodalAnalysis)
            .filter(
                DocumentMultimodalAnalysis.document_id == result["document_id"],
                DocumentMultimodalAnalysis.version_number == result["version_number"],
            )
            .first()
        )
        if row is None:
            row = DocumentMultimodalAnalysis(
                document_id=result["document_id"], version_number=result["version_number"]
            )
            db.add(row)
        row.status = "review_required" if result["review_required"] else "ready"
        row.parser_version = result["parser_version"]
        row.vision_model = result.get("vision_model")
        row.page_count = result["page_count"]
        row.ocr_confidence = result["ocr_confidence"]
        row.review_required = bool(result["review_required"])
        row.payload_json = json.dumps(result, ensure_ascii=False, default=str)
        db.commit()

    def get_cached(self, db: Session, document: Document) -> dict | None:
        row = (
            db.query(DocumentMultimodalAnalysis)
            .filter(
                DocumentMultimodalAnalysis.document_id == document.id,
                DocumentMultimodalAnalysis.version_number == document.version_number,
            )
            .first()
        )
        if row is None:
            return None
        try:
            return json.loads(row.payload_json or "{}")
        except json.JSONDecodeError:
            return None


multimodal_document_analyzer = MultimodalDocumentAnalyzer()
