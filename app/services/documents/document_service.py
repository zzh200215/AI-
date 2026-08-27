import logging

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.document import Document
from app.services.documents.conflict import ConflictMixin
from app.services.documents.document_parsing import (
    _extract_segments,
    _prepare_chunks_for_indexing,
    _split_text,
    extract_file_text,  # noqa: F401 - 重新导出，供 app/api/legal/legal_api.py 等模块导入
)
from app.services.documents.document_pipeline import (
    run_chunk,
    run_index,
    run_parse,
)
from app.services.documents.extraction import ExtractionMixin
from app.services.documents.ingest import IngestMixin
from app.services.documents.multimodal import MultimodalMixin
from app.services.documents.queries import QueriesMixin
from app.services.documents.read_analyze import ReadAnalyzeMixin
from app.services.rag.rag_service import rag_service
from app.services.storage.storage_service import storage_service

UPLOAD_DIR = storage_service.ensure_dir(storage_service.base_dir())
settings = get_settings()
logger = logging.getLogger(__name__)

DOCUMENT_STATUS_PARSED = "parsed"
DOCUMENT_STATUS_INDEXED = "indexed"
IMAGE_FILE_TYPES = {"png", ".png", "jpg", ".jpg", "jpeg", ".jpeg", "bmp", ".bmp", "webp", ".webp"}
VISION_SUPPORTED_FILE_TYPES = IMAGE_FILE_TYPES | {"pdf", ".pdf"}


def _try_index_document(document_id: int, chunks: list[dict], *, user_id: int | None = None,
                        knowledge_base_id: int | None = None) -> Exception | None:
    try:
        rag_service.index_document(
            document_id,
            _prepare_chunks_for_indexing(document_id, chunks),
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            document_status=DOCUMENT_STATUS_INDEXED,
        )
        return None
    except Exception as exc:
        return exc


class DocumentService(IngestMixin, ReadAnalyzeMixin, MultimodalMixin, ConflictMixin, ExtractionMixin, QueriesMixin):
    def _run_sync_pipeline(
        self, db: Session, doc: Document, *, user_id: int | None = None, knowledge_base_id: int | None = None
    ) -> dict:
        """同步快路径：复用与异步一致的幂等阶段函数。

        引用 document_service 模块级名称（_extract_segments/_split_text/_try_index_document），
        保持既有测试 patch(document_service._extract_segments / rag_service.index_document) 兼容。
        """
        parse_result = run_parse(db, doc.id, extract_segments=_extract_segments, return_segments=True)
        if parse_result["status"] == "skipped":
            return parse_result
        segments = parse_result.get("segments_payload")
        chunk_result = run_chunk(db, doc.id, segments=segments, split_text=_split_text)
        if chunk_result["status"] == "skipped":
            return chunk_result
        return run_index(
            db,
            doc.id,
            segments=segments,
            index_chunks=_try_index_document,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
        )


document_service = DocumentService()
