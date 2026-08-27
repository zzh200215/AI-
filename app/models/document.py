from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import relationship

from app.core.database import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(128), nullable=True, index=True)
    permission_scope = Column(String(32), default="private", nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    documents = relationship("Document", back_populates="knowledge_base")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True, index=True)
    knowledge_base_id = Column(Integer, ForeignKey("knowledge_bases.id"), nullable=True, index=True)
    parent_document_id = Column(Integer, ForeignKey("documents.id"), nullable=True, index=True)
    version_number = Column(Integer, default=1, nullable=False)
    title = Column(String(256), nullable=False)
    # 历史列：新文档只存 object_key（file_path 可为空，仅存量行保留本地路径）。
    file_path = Column(String(512), nullable=True)
    file_type = Column(String(32), nullable=False)
    content_hash = Column(String(128), nullable=True, index=True)
    # 存储抽象：只存 object_key/内容元数据，禁止存本地绝对路径或云厂商 URL/二进制。
    object_key = Column(String(512), nullable=True, index=True)
    mime_type = Column(String(128), nullable=True)
    size = Column(Integer, nullable=True)
    # 状态机（最终态 status + 处理阶段 current_stage + 失败信息）
    current_stage = Column(String(64), nullable=False, server_default=text("'uploaded'"), default="uploaded")
    failure_stage = Column(String(64), nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(String(512), nullable=True)
    retry_count = Column(Integer, nullable=False, server_default=text("0"), default=0)
    # 可重建性：解析/OCR/切分/索引产物版本
    parser_version = Column(String(64), nullable=True)
    ocr_version = Column(String(64), nullable=True)
    chunker_version = Column(String(64), nullable=True)
    index_version = Column(String(64), nullable=True)
    embedding_model = Column(String(128), nullable=True)
    last_processed_at = Column(DateTime(timezone=True), nullable=True)
    classification = Column(String(128), nullable=True, index=True)
    tags = Column(Text, nullable=True)
    permission_scope = Column(String(32), default="private", nullable=False, index=True)
    sensitivity_level = Column(String(32), default="internal", nullable=False, index=True)
    permission_users = Column(Text, nullable=True)
    permission_roles = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
    download_enabled = Column(Boolean, nullable=False, default=True)
    watermark_required = Column(Boolean, nullable=False, default=False)
    status = Column(String(32), default="pending", nullable=False)
    summary = Column(Text, nullable=True)
    # 乐观锁版本号（version_id_col）：多人/多任务并发编辑时防丢失更新。
    # 与业务上的 version_number（文档版本迭代号）相互独立。
    version = Column(Integer, nullable=False, server_default=text("1"), default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __mapper_args__ = {"version_id_col": version}

    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    parent_document = relationship("Document", remote_side=[id])
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")
    parse_jobs = relationship("DocumentParseJob", back_populates="document", cascade="all, delete-orphan")
    multimodal_analyses = relationship("DocumentMultimodalAnalysis", back_populates="document", cascade="all, delete-orphan")
    qa_records = relationship("DocumentQARecord", back_populates="document", cascade="all, delete-orphan")


class DocumentAccessRule(Base):
    __tablename__ = "document_access_rules"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    subject_type = Column(String(32), nullable=False, index=True)
    subject_value = Column(String(128), nullable=False, index=True)
    permission = Column(String(32), default="read", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    page_number = Column(Integer, nullable=True)
    section_title = Column(String(256), nullable=True)
    section_path = Column(Text, nullable=True)
    segment_type = Column(String(64), nullable=True)
    table_like = Column(Boolean, nullable=False, default=False)
    visual_tags = Column(Text, nullable=True)
    ocr_quality = Column(Float, nullable=True)
    embedding_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_index"),
    )

    document = relationship("Document", back_populates="chunks")


class DocumentParseJob(Base):
    __tablename__ = "document_parse_jobs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    job_type = Column(String(64), nullable=False, index=True)
    task_id = Column(String(128), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="pending")
    progress = Column(Integer, nullable=True)
    current_step = Column(String(128), nullable=True)
    message = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    result_summary = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    # 租约（lease）：worker 领取任务后持有，超期可被回收重新领取
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    document = relationship("Document", back_populates="parse_jobs")


class DocumentParseArtifact(Base):
    """解析/切分/索引产物版本记录：可重建性与版本守卫的依据。

    以 (document_id, version_number) 唯一；任一输入版本变化（content_hash /
    parser_version / ocr_version / chunker_version / index_version / embedding_model）
    即可判定旧产物失效并重跑对应阶段。
    """

    __tablename__ = "document_parse_artifacts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    content_hash = Column(String(128), nullable=True)
    object_key = Column(String(512), nullable=True)
    parser_version = Column(String(64), nullable=True)
    ocr_version = Column(String(64), nullable=True)
    chunker_version = Column(String(64), nullable=True)
    index_version = Column(String(64), nullable=True)
    embedding_model = Column(String(128), nullable=True)
    # 解析产物（segments JSON）的存储 key 与内容指纹
    artifact_object_key = Column(String(512), nullable=True)
    artifact_hash = Column(String(64), nullable=True)
    chunk_count = Column(Integer, nullable=True)
    # 已索引的切分指纹：与当前 chunks 一致时跳过重复索引
    indexed_chunks_hash = Column(String(64), nullable=True)
    processing_ms = Column(Integer, nullable=True)
    error_code = Column(String(64), nullable=True)
    error_summary = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_parse_artifacts_doc_version"),
    )

    document = relationship("Document")


class DocumentMultimodalAnalysis(Base):
    """版面/OCR/印章/表格分析产物，按文档版本保存以支持证据回放。"""

    __tablename__ = "document_multimodal_analyses"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="ready", index=True)
    parser_version = Column(String(64), nullable=True)
    vision_model = Column(String(128), nullable=True)
    page_count = Column(Integer, nullable=False, default=0)
    ocr_confidence = Column(Float, nullable=True)
    review_required = Column(Boolean, nullable=False, default=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    document = relationship("Document", back_populates="multimodal_analyses")

    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_multimodal_doc_version"),
    )


class DocumentQARecord(Base):
    __tablename__ = "document_qa_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=True, index=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    citations = Column(Text, nullable=True)
    hit_chunks = Column(Text, nullable=True)
    model_name = Column(String(128), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    source = Column(String(32), nullable=False, default="document")
    feedback_value = Column(String(16), nullable=True, index=True)
    feedback_reason = Column(String(64), nullable=True)
    feedback_note = Column(Text, nullable=True)
    feedback_status = Column(String(16), nullable=True, index=True)
    feedback_created_at = Column(DateTime(timezone=True), nullable=True, index=True)
    feedback_resolved_at = Column(DateTime(timezone=True), nullable=True)
    feedback_resolution_note = Column(Text, nullable=True)
    feedback_resolved_by = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    document = relationship("Document", back_populates="qa_records")


class DocumentConflictCase(Base):
    __tablename__ = "document_conflict_cases"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True, index=True)
    document_ids_json = Column(Text, nullable=False)
    conflict_json = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="pending_confirmation", index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True, index=True)
    resolution_note = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DocumentAssistantArtifact(Base):
    __tablename__ = "document_assistant_artifacts"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source_document_id = Column(Integer, ForeignKey("documents.id"), nullable=True, index=True)
    title = Column(String(256), nullable=False)
    artifact_type = Column(String(32), nullable=False, index=True)
    content = Column(Text, nullable=False)
    language = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DocumentAssistantRevision(Base):
    __tablename__ = "document_assistant_revisions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    artifact_id = Column(Integer, ForeignKey("document_assistant_artifacts.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    turn_index = Column(Integer, nullable=False)
    instruction = Column(Text, nullable=True)
    title = Column(String(256), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
