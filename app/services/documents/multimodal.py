"""DocumentService adapter for multimodal analysis and evidence lookup."""

from sqlalchemy.orm import Session

from app.services.documents.multimodal_analysis import (
    locate_evidence_pages,
    multimodal_document_analyzer,
)


class MultimodalMixin:
    def get_multimodal_analysis(
        self,
        document_id: int,
        db: Session,
        *,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> dict | None:
        document = self.get(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        if document is None:
            raise ValueError("Document not found")
        return multimodal_document_analyzer.get_cached(db, document)

    async def analyze_multimodal(
        self,
        document_id: int,
        db: Session,
        *,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        use_vision_model: bool | None = None,
        force: bool = False,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> dict:
        document = self.get(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        if document is None:
            raise ValueError("Document not found")
        return await multimodal_document_analyzer.analyze(
            document=document,
            db=db,
            user_id=user_id or document.user_id,
            use_vision_model=use_vision_model,
            force=force,
            page_start=page_start,
            page_end=page_end,
        )

    def locate_multimodal_evidence(
        self,
        document_id: int,
        query: str,
        db: Session,
        *,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        limit: int = 10,
    ) -> dict:
        document = self.get(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        if document is None:
            raise ValueError("Document not found")
        analysis = multimodal_document_analyzer.get_cached(db, document)
        if analysis is None:
            return {
                "document_id": document_id,
                "query": query,
                "matches": [],
                "analysis_available": False,
                "message": "请先执行多模态文档分析。",
            }
        return {
            "document_id": document_id,
            "query": query,
            "matches": locate_evidence_pages(analysis, query, limit=limit),
            "analysis_available": True,
            "ocr_confidence": analysis.get("ocr_confidence"),
            "review_required": analysis.get("review_required", False),
        }
