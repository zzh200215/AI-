"""File / OCR / ASR / backup storage settings."""

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class StorageSettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    STORAGE_PROVIDER: str = "local"  # local | minio | s3 | oss（本地默认可用，云后端需安装对应可选 SDK）
    STORAGE_LOCAL_DIR: str = "./data/uploads"
    # 可选云存储：minio / aws s3 / aliyun oss 适配器配置（SDK 未安装时启用会抛 StorageBackendUnavailable）
    STORAGE_MINIO_ENDPOINT: str = ""
    STORAGE_MINIO_ACCESS_KEY: str = ""
    STORAGE_MINIO_SECRET_KEY: str = ""
    STORAGE_MINIO_BUCKET: str = "documents"
    STORAGE_MINIO_SECURE: bool = True
    STORAGE_S3_ENDPOINT_URL: str = ""
    STORAGE_S3_REGION: str = ""
    STORAGE_S3_ACCESS_KEY: str = ""
    STORAGE_S3_SECRET_KEY: str = ""
    STORAGE_S3_BUCKET: str = "documents"
    STORAGE_OSS_ENDPOINT: str = ""
    STORAGE_OSS_ACCESS_KEY: str = ""
    STORAGE_OSS_SECRET_KEY: str = ""
    STORAGE_OSS_BUCKET: str = "documents"
    # 文档上传安全（流式读取、大小/MIME/zip-bomb 防护）
    DOCUMENT_MAX_UPLOAD_MB: int = Field(default=50, ge=1, le=1024)
    # 批量上传总大小上限（batch-upload 逐文件累计，超过即整体拒绝；单文件仍受 DOCUMENT_MAX_UPLOAD_MB 限制）
    DOCUMENT_MAX_BATCH_TOTAL_MB: int = Field(default=200, ge=1, le=8192)
    DOCUMENT_ALLOWED_EXTENSIONS: str = "pdf,docx,xlsx,md,txt,png,jpg,jpeg,bmp,webp"
    DOCUMENT_VIRUS_SCAN_ENABLED: bool = False
    DOCUMENT_CLAMAV_SOCKET: str = "/var/run/clamav/clamd.ctl"
    DOCUMENT_ZIP_MAX_ENTRIES: int = Field(default=500, ge=1, le=10000)
    DOCUMENT_ZIP_MAX_TOTAL_UNCOMPRESSED_MB: int = Field(default=200, ge=1, le=4096)
    DOCUMENT_ZIP_MAX_COMPRESSION_RATIO: float = Field(default=1000.0, ge=1.0)
    DOCUMENT_ZIP_MAX_NESTING: int = Field(default=2, ge=1, le=8)
    # 备份（等保自评差距 #1 整改：每日全量定时备份，见 app/tasks.create_pilot_backup_task）
    BACKUP_OUTPUT_DIR: str = "data/backups"
    BACKUP_DATA_DIRS: list[str] = ["data/uploads", "data/chroma_db"]
    # 异地副本目标目录（等保差距 #1 异地部分）：非空时备份完成后复制一份到此（可指向挂载的对象存储/NAS）。
    BACKUP_OFFSITE_DIR: str = ""
    # 备份保留份数（等保运维：避免每日全量无限累积占盘）：保留最近 N 份，清理更旧的；0=从不清理。
    BACKUP_RETENTION_COUNT: int = Field(default=7, ge=0, le=365)
    # 评测 bundle 导出目录（#90：测试用临时目录，避免写仓库内跟踪文件；空=默认 eval/bundles/feedback_autogen）
    EVAL_BUNDLE_OUTPUT_DIR: str = ""

    # OCR 文档解析
    OCR_ENABLE_IMAGE_PREPROCESS: bool = True
    OCR_PDF_RENDER_DPI: int = 200
    OCR_MIN_TEXT_LENGTH: int = 24
    OCR_MIN_READABLE_RATIO: float = 0.45
    # 多模态合同解析：低于该词级置信度的 OCR 结果进入人工复核。
    OCR_MIN_WORD_CONFIDENCE: float = Field(default=0.65, ge=0.0, le=1.0)
    OCR_LOW_CONFIDENCE_RATIO: float = Field(default=0.25, ge=0.0, le=1.0)
    OCR_REVIEW_CONFIDENCE: float = Field(default=0.78, ge=0.0, le=1.0)
    # 版面分析最多处理的页数，避免超大扫描合同阻塞请求；0 表示不限制。
    MULTIMODAL_MAX_PAGES: int = Field(default=80, ge=0, le=10000)
    MULTIMODAL_USE_VISION_MODEL: bool = True

    # 会议录音转写（faster-whisper）
    MEETING_ASR_ENABLED: bool = True
    MEETING_ASR_MODEL: str = "small"
    MEETING_ASR_DEVICE: str = "cpu"
    MEETING_ASR_COMPUTE_TYPE: str = "int8"
    MEETING_ASR_DOWNLOAD_ROOT: str = ""
