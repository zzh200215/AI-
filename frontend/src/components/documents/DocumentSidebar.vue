<template>
  <el-card class="sidebar-card">
    <template #header>
      <div class="card-header-inline">
        <div>
          <span>文档资源栏</span>
        </div>
        <el-button text @click="fetchDocuments">刷新</el-button>
      </div>
    </template>

    <div class="governance-box">
      <div class="compare-title">知识库与权限</div>
      <p class="toolbar-meta" style="margin:0 0 4px">法规、案例与模板入库后自动关联法律咨询和合同审查。</p>
      <el-select v-model="uploadForm.knowledge_base_name" filterable allow-create default-first-option placeholder="知识库名称" style="width: 100%">
        <el-option v-for="item in knowledgeBases" :key="item.id" :label="item.name" :value="item.name" />
      </el-select>
      <el-select v-model="uploadForm.classification" placeholder="文档分类" style="width: 100%">
        <el-option v-for="item in classificationOptions" :key="item" :label="item" :value="item" />
      </el-select>
      <el-input v-model="uploadForm.tags" placeholder="标签，使用逗号分隔" />
      <el-select v-model="uploadForm.sensitivity_level" placeholder="敏感级别" style="width: 100%">
        <el-option label="内部" value="internal" />
        <el-option label="受限" value="restricted" />
        <el-option label="机密" value="confidential" />
      </el-select>
      <el-select v-model="uploadForm.permission_scope" placeholder="权限范围" style="width: 100%">
        <el-option label="仅自己" value="private" />
        <el-option label="公开" value="public" />
        <el-option label="限制用户" value="restricted" />
        <el-option label="角色可见" value="role" />
      </el-select>
      <el-input v-model="uploadForm.permission_users" placeholder="用户 ID，逗号分隔" />
      <el-input v-model="uploadForm.permission_roles" placeholder="角色，逗号分隔" />
    </div>

    <div class="filter-box">
      <div class="compare-title">筛选条件</div>
      <el-select v-model="filters.knowledge_base_id" clearable placeholder="知识库筛选" style="width: 100%" @change="handleFilterChange">
        <el-option v-for="item in knowledgeBases" :key="item.id" :label="item.name" :value="item.id" />
      </el-select>
      <el-select v-model="filters.classification" clearable placeholder="分类筛选" style="width: 100%" @change="handleFilterChange">
        <el-option v-for="item in classificationOptions" :key="item" :label="item" :value="item" />
      </el-select>
      <el-select v-model="filters.sensitivity_level" clearable placeholder="敏感级别" style="width: 100%" @change="handleFilterChange">
        <el-option label="内部" value="internal" />
        <el-option label="受限" value="restricted" />
        <el-option label="机密" value="confidential" />
      </el-select>
      <el-input v-model="filters.q" placeholder="标题搜索" @change="handleFilterChange" />
    </div>

    <div class="retrieval-box">
      <div class="compare-title">向量检索参数</div>
      <div class="retrieval-form-grid">
        <label>
          <span>Top K</span>
          <el-input v-model="retrievalForm.topK" placeholder="8" />
        </label>
        <label>
          <span>Rerank Top N</span>
          <el-input v-model="retrievalForm.rerankTopN" placeholder="5" />
        </label>
      </div>
      <el-select v-model="retrievalForm.rewriteMode" placeholder="Query Rewrite" style="width: 100%">
        <el-option label="自动改写" value="auto" />
        <el-option label="保留原问" value="off" />
      </el-select>
      <el-select v-model="retrievalForm.contextExpand" placeholder="邻近 Chunk 扩展" style="width: 100%">
        <el-option label="前后各 1 段" value="1" />
        <el-option label="前后各 2 段" value="2" />
        <el-option label="不扩展" value="0" />
      </el-select>
      <p>当前问答接口保持原有调用方式，参数用于呈现检索策略配置。</p>
    </div>

    <div v-if="docMeta" class="current-doc">
      <div class="current-doc-head">
        <div class="doc-title">{{ docMeta.title }}</div>
        <el-button size="small" type="primary" plain :loading="downloading" @click="downloadCurrentDocument">
          下载原文
        </el-button>
      </div>
      <div class="doc-tags">
        <el-tag type="primary">{{ docMeta.file_type }}</el-tag>
        <el-tag>{{ docMeta.status }}</el-tag>
        <el-tag v-if="docMeta.version_number">v{{ docMeta.version_number }}</el-tag>
        <el-tag v-if="docMeta.classification" type="success">{{ docMeta.classification }}</el-tag>
        <el-tag v-if="docMeta.sensitivity_level" :type="docMeta.sensitivity_level === 'confidential' ? 'danger' : docMeta.sensitivity_level === 'restricted' ? 'warning' : 'info'">
          {{ docMeta.sensitivity_level }}
        </el-tag>
      </div>
      <div class="download-governance">
        <div class="download-governance-copy">
          <strong>受控下载</strong>
          <span>下载时重新校验权限；可处理格式会生成带下载人标识的副本。</span>
        </div>
        <div class="download-policy-controls">
          <label>
            <span>允许下载</span>
            <el-switch
              :model-value="docMeta.download_enabled !== false"
              :loading="downloadPolicySaving"
              @change="(value) => updateDownloadPolicy('download_enabled', value)"
            />
          </label>
          <label>
            <span>下载水印</span>
            <el-switch
              :model-value="Boolean(docMeta.watermark_required)"
              :disabled="docMeta.download_enabled === false"
              :loading="downloadPolicySaving"
              @change="(value) => updateDownloadPolicy('watermark_required', value)"
            />
          </label>
        </div>
      </div>
    </div>

    <div class="compare-box">
      <div class="compare-title">多文档对比</div>
      <el-select
        v-model="compareSelection"
        multiple
        collapse-tags
        collapse-tags-tooltip
        placeholder="选择 2 份以上文档"
        style="width: 100%"
      >
        <el-option
          v-for="item in documents"
          :key="item.id"
          :label="item.title"
          :value="item.id"
        />
      </el-select>
      <el-button
        class="compare-button"
        type="warning"
        :loading="compareLoading"
        :disabled="compareSelection.length < 2"
        @click="runCompare"
      >
        生成对比
      </el-button>
    </div>

    <div class="list-section-head">
      <strong>文档列表</strong>
      <span>{{ documentTotal || documents.length }} 份</span>
    </div>

    <div class="doc-list">
      <button
        v-for="item in documents"
        :key="item.id"
        class="doc-item"
        :class="{ active: item.id === docId }"
        @click="selectDocument(item)"
      >
        <strong>{{ item.title }}</strong>
        <span>{{ item.file_type }} · {{ item.status }}<template v-if="item.version_number"> · v{{ item.version_number }}</template></span>
        <div class="doc-item-foot">
          <span>{{ item.classification || '未分类' }}</span>
        </div>
      </button>
    </div>

    <el-pagination
      small
      background
      layout="prev, pager, next"
      :current-page="documentPage"
      :page-size="documentPageSize"
      :total="documentTotal"
      class="app-pagination-end"
      @current-change="handleDocumentPageChange"
    />
  </el-card>
</template>

<script setup>
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElOption, ElSelect } from 'element-plus/es/components/select/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElSwitch } from 'element-plus/es/components/switch/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/switch/style/css'
import 'element-plus/es/components/tag/style/css'
import { useDocuments } from '../../composables/useDocuments'

const {
  docId, docMeta, documents, knowledgeBases, uploadForm, filters, retrievalForm,
  classificationOptions, documentPage, documentPageSize, documentTotal,
  downloadPolicySaving, downloading, compareSelection, compareLoading,
  fetchDocuments, handleDocumentPageChange, handleFilterChange,
  downloadCurrentDocument, updateDownloadPolicy, runCompare, selectDocument,
} = useDocuments()
</script>

<style scoped>
.sidebar-card {
  position: sticky;
  top: 0;
  align-self: start;
}
.card-header-inline {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
}
.section-eyebrow {
  margin-bottom: 4px;
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-muted);
}
.toolbar-meta {
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.current-doc,
.compare-box,
.governance-box,
.filter-box,
.retrieval-box {
  margin-bottom: var(--space-4);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  background: #ffffff;
  box-shadow: var(--shadow-xs);
}
.current-doc {
  background: var(--color-primary-light);
}
.current-doc-head {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: var(--space-2);
}
.compare-box {
  background: var(--color-warning-light);
  display: grid;
  gap: var(--space-2);
}
.governance-box {
  background: var(--color-surface-subtle);
  display: grid;
  gap: var(--space-2);
}
.filter-box {
  background: var(--color-bg-alt);
  display: grid;
  gap: var(--space-2);
}
.retrieval-box {
  display: grid;
  gap: var(--space-2);
}
.retrieval-box p {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
}
.retrieval-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
}
.retrieval-form-grid label {
  display: grid;
  gap: var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.compare-title {
  font-size: var(--text-sm);
  font-weight: 700;
  color: var(--color-text);
}
.list-section-head {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
  margin: var(--space-2) 0 var(--space-3);
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.compare-button {
  width: 100%;
}
.doc-title {
  font-weight: 700;
  color: var(--color-text);
}
.doc-tags {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-2);
  flex-wrap: wrap;
}
.download-governance {
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px dashed rgba(29, 78, 216, 0.22);
  display: grid;
  gap: var(--space-3);
}
.download-governance-copy {
  display: grid;
  gap: 2px;
}
.download-governance-copy strong {
  color: var(--color-text);
  font-size: var(--text-sm);
}
.download-governance-copy span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.5;
}
.download-policy-controls {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
}
.download-policy-controls label {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
}
.doc-list {
  display: grid;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
}
.doc-item {
  width: 100%;
  text-align: left;
  padding: var(--space-4) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  background: var(--color-surface);
  cursor: pointer;
  display: grid;
  gap: var(--space-1);
  transition: all var(--transition-fast);
}
.doc-item:hover {
  border-color: var(--color-primary);
  background: var(--color-primary-light);
  box-shadow: var(--shadow-sm);
}
.doc-item:active {
  transform: translateY(0);
}
.doc-item.active {
  border-color: var(--color-primary);
  background: var(--color-primary-light);
  box-shadow: 0 0 0 2px var(--color-primary-subtle);
}
.doc-item strong {
  color: var(--color-text);
}
.doc-item span {
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.doc-item-foot {
  display: flex;
  justify-content: space-between;
  gap: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  flex-wrap: wrap;
}
@media (max-width: 1100px) {
  .sidebar-card {
    position: static;
  }
}
</style>
