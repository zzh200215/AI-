<template>
  <div class="documents-page">
    <div class="page-header">
      <div class="upload-console">
        <el-upload
          class="upload-dropzone"
          drag
          multiple
          :auto-upload="false"
          :show-file-list="false"
          :on-change="onFileChange"
        >
          <div class="upload-dropzone-inner">
            <strong>拖拽法律资料到这里</strong>
            <span>支持法规、案例、合同模板、文书模板等文档</span>
          </div>
        </el-upload>
        <div class="upload-console-foot">
          <span class="toolbar-meta">{{ selectedFiles.length ? `已选 ${selectedFiles.length} 份` : '文件不会自动上传，确认后进入解析队列' }}</span>
          <el-button type="primary" :loading="uploading" :disabled="!selectedFiles.length" @click="uploadAndAnalyze">
            {{ selectedFiles.length > 1 ? '批量上传并分析' : '上传并分析' }}
          </el-button>
        </div>
      </div>
    </div>

    <div class="overview-metrics">
      <div class="metric-tile">
        <span>文档总数</span>
        <strong>{{ documentTotal || documents.length }}</strong>
      </div>
      <div class="metric-tile">
        <span>当前风险点</span>
        <strong>{{ analysis?.risks?.length || 0 }}</strong>
      </div>
      <div class="metric-tile">
        <span>待办提取</span>
        <strong>{{ analysis?.todos?.length || 0 }}</strong>
      </div>
      <div class="metric-tile">
        <span>问答记录</span>
        <strong>{{ qaRecords.length }}</strong>
      </div>
    </div>

    <div class="layout-grid">
      <DocumentSidebar />
      <DocumentWorkspace />
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElUpload } from 'element-plus/es/components/upload/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/upload/style/css'
import DocumentSidebar from '../components/documents/DocumentSidebar.vue'
import DocumentWorkspace from '../components/documents/DocumentWorkspace.vue'
import { useDocuments } from '../composables/useDocuments'

const route = useRoute()
const {
  selectedFiles, uploading, onFileChange, uploadAndAnalyze,
  documentTotal, documents, analysis, qaRecords,
  initialize, loadDocumentFromRoute, clearAnalysisPolling,
} = useDocuments()

onMounted(async () => {
  await initialize(route.query.documentId)
})

watch(
  () => route.query.documentId,
  async (value, oldValue) => {
    if (value === oldValue) return
    await loadDocumentFromRoute(value)
  }
)

onUnmounted(() => {
  clearAnalysisPolling()
})
</script>

<style scoped>
.documents-page {
  display: grid;
  gap: var(--space-6);
  max-width: 1600px;
}
.page-header {
  display: block;
  padding: var(--space-5);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}
.page-header h3 {
  margin: var(--space-1) 0 var(--space-2);
  color: var(--color-text);
  font-size: var(--text-3xl);
  font-weight: 600;
}
.page-header p {
  max-width: 720px;
  color: var(--color-text-secondary);
  line-height: 1.6;
  font-size: var(--text-sm);
}
.section-eyebrow {
  margin-bottom: 4px;
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-muted);
}
.overview-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-4);
}
.metric-tile {
  padding: var(--space-5) var(--space-5);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-light);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
  display: grid;
  gap: var(--space-1);
  transition: all var(--transition-fast);
}
.metric-tile:hover {
  box-shadow: var(--shadow-card-hover);
  border-color: var(--color-border-hover);
}
.metric-tile span {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.metric-tile strong {
  font-size: var(--text-3xl);
  line-height: var(--text-3xl-lh);
  color: var(--color-text);
  font-weight: 600;
}
.upload-console {
  width: 100%;
  display: grid;
  gap: var(--space-2);
}
.upload-dropzone {
  width: 100%;
}
:deep(.upload-dropzone .el-upload) {
  width: 100%;
}
:deep(.upload-dropzone .el-upload-dragger) {
  width: 100%;
  height: 104px;
  border-radius: var(--radius-md);
  border-color: var(--color-border-hover);
  background: var(--color-surface-subtle);
  padding: 0;
  transition: border-color var(--transition-fast), background var(--transition-fast);
}
/* 拖拽区不加彩色底与外发光环：整块蓝面是页面里最抢眼的“模板色块” */
:deep(.upload-dropzone .el-upload-dragger:hover) {
  border-color: var(--color-primary);
  background: var(--color-bg);
}
.upload-dropzone-inner {
  height: 100%;
  display: grid;
  place-content: center;
  gap: var(--space-1);
  text-align: center;
}
.upload-dropzone-inner strong {
  color: var(--color-text);
  font-size: var(--text-base);
  font-weight: 500;
}
.upload-dropzone-inner span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.upload-console-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.toolbar-meta {
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.layout-grid {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: var(--space-6);
}
@media (max-width: 1100px) {
  .overview-metrics,
  .layout-grid {
    grid-template-columns: 1fr;
  }
  .page-header {
    display: grid;
  }
}
</style>
