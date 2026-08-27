# 多模态法律文档理解

扫描合同解析由 `app.services.documents.multimodal_analysis` 统一产出，产物按 `document_id + version_number` 持久化在 `document_multimodal_analyses`，旧版本不会覆盖新版本。

## 接口

- `POST /api/documents/{document_id}/multimodal-analyze`
  - `use_vision_model`：是否调用现有 `LLM_VISION_MODEL`（失败时保留 OCR 产物并标记 warning）。
  - `page_start/page_end`：1-based 页码范围，适合先审阅签署页。
  - `force`：忽略同版本缓存重新解析。
- `POST /api/documents/{document_id}/evidence-locate`
  - 输入关键词，返回页码、摘录、OCR 置信度和版面 bbox。

## 产物语义

- `pages[].layout.blocks`：OCR 行按从上到下、从左到右的阅读顺序及 bbox。
- `pages[].ocr`：平均词置信度、低置信度比例、低置信度词和 `review_required`。
- `regions`：OCR 线索和视觉模型线索分开标记，`source=vision_model` 仅作为辅助，不覆盖 OCR 定位。
- `tables` / `clauses`：扫描表格的列、行及付款/交付/违约等法律义务行，均带页码和 bbox。

部署时先执行 `python -m alembic upgrade head`。未安装 Tesseract 时，分析接口会返回可解释的 warning，不会把空结果当作高置信度证据。

