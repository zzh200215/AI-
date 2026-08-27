<template>
  <div class="legal-workspace">
    <div class="legal-banner">
      <el-alert type="warning" :closable="false" show-icon>
        <template #title>
          <span style="font-weight:600">AI 辅助结果，不构成正式法律意见；高风险事项请提交审核律师。</span>
        </template>
      </el-alert>
    </div>

    <div class="case-bar">
      <span class="case-label">当前案件</span>
      <el-select v-model="currentCaseId" placeholder="选择案件（咨询/审查/文书将归档到该案件）" clearable filterable class="case-select">
        <el-option v-for="c in cases" :key="c.id" :label="`#${c.id} ${c.title}`" :value="c.id">
          <span>{{ c.title }}</span>
          <span class="case-count">咨询{{ c.item_counts?.consultations ?? 0 }} · 审查{{ c.item_counts?.reviews ?? 0 }} · 文书{{ c.item_counts?.drafts ?? 0 }}</span>
        </el-option>
      </el-select>
      <el-button size="small" type="primary" plain @click="caseDialog.open()">新建案件</el-button>
    </div>

    <el-tabs v-model="activeTab" class="legal-tabs">
      <el-tab-pane label="法律咨询" name="consultation">
        <LegalConsultationsTab
          :case-id="currentCaseId"
          :on-review-submitted="() => reviewTabRef?.refresh()"
          @go-to-draft="handleGoToDraftFromConsult"
          @go-to-review="handleGoToReviewFromConsult"
        />
      </el-tab-pane>

      <el-tab-pane label="合同审查" name="contract">
        <LegalContractTab ref="contractsTabRef" :case-id="currentCaseId" :download-text="downloadText" />
      </el-tab-pane>

      <el-tab-pane label="文书草稿" name="draft">
        <LegalDraftsTab ref="draftsTabRef" :case-id="currentCaseId" :download-text="downloadText" />
      </el-tab-pane>

      <el-tab-pane label="法源管理" name="sources">
        <LegalSourcesTab />
      </el-tab-pane>

      <el-tab-pane label="律师审核" name="review">
        <LegalReviewTab ref="reviewTabRef" />
      </el-tab-pane>

      <el-tab-pane label="计时计费" name="billing" lazy>
        <AsyncLegalBilling :org-id="currentOrgId" :case-id="currentCaseId" />
      </el-tab-pane>

      <el-tab-pane label="关键日期" name="deadlines" lazy>
        <AsyncLegalDeadlines :org-id="currentOrgId" :case-id="currentCaseId" />
      </el-tab-pane>

      <el-tab-pane label="合同台账" name="contracts" lazy>
        <AsyncLegalContracts :org-id="currentOrgId" :case-id="currentCaseId" />
      </el-tab-pane>

      <el-tab-pane label="客户门户" name="portal" lazy>
        <!-- lazy：避免工作台加载时用默认 orgId=1 提前发出错误的 portal-branding 请求 -->
        <LegalPortalTab :organization-id="currentOrgId" :case-id="currentCaseId" />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="sourceDetailVisible" title="引用依据核对" width="640px">
      <template v-if="sourceDetail">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="法源名称">{{ sourceDetail.title }}</el-descriptions-item>
          <el-descriptions-item label="引用条款">{{ sourceDetail.citation || '—' }}</el-descriptions-item>
          <el-descriptions-item label="版本">
            <span v-if="sourceDetail.version">{{ sourceDetail.version }}</span>
            <span v-else class="muted">—</span>
          </el-descriptions-item>
          <el-descriptions-item label="效力状态">
            <el-tag v-if="sourceDetail.status" :type="sourceStatusType(sourceDetail.status)" size="small">{{ sourceStatusLabel(sourceDetail.status) }}</el-tag>
            <span v-else class="muted">未标注</span>
          </el-descriptions-item>
          <el-descriptions-item label="生效日期">
            <span v-if="sourceDetail.effective_date">{{ sourceDetail.effective_date }}</span>
            <span v-else class="muted">—</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="sourceDetail.jurisdiction" label="适用地域">{{ sourceDetail.jurisdiction }}</el-descriptions-item>
          <el-descriptions-item v-if="sourceDetail.verification?.verification_note" label="核验提示">
            <el-tag :type="verificationTagType(sourceDetail.verification)" size="small" effect="plain">{{ sourceDetail.verification.verification_note }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item v-if="sourceDetail.verification?.recommended_source" label="建议引用现行版本">
            <span>{{ sourceDetail.verification.recommended_source.title }}</span>
            <el-tag size="small" type="success" style="margin-left: 6px">{{ sourceDetail.verification.recommended_source.version }}</el-tag>
            <el-button size="small" link type="primary" style="margin-left: 6px" @click="openRecommendedSource(sourceDetail.verification.recommended_source)">查看</el-button>
          </el-descriptions-item>
        </el-descriptions>
      </template>
      <el-divider content-position="left">条文（供核对原文）</el-divider>
      <div v-loading="sourceDetailLoading" class="article-list">
        <template v-if="sourceDetailArticles.length">
          <div v-for="article in sourceDetailArticles" :key="article.id" class="article-entry">
            <strong>{{ article.article_number }}</strong>
            <span v-if="article.title" class="article-title">{{ article.title }}</span>
            <p class="article-content">{{ article.content }}</p>
          </div>
        </template>
        <el-empty v-else-if="!sourceDetailLoading" description="该法源暂无条文明细" :image-size="48" />
      </div>
    </el-dialog>

    <CaseCreateDialog ref="caseDialog" :org-id="currentOrgId" @created="handleCaseCreated" />
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElUpload } from 'element-plus/es/components/upload/index'
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index'
import { ElAlert } from 'element-plus/es/components/alert/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCol } from 'element-plus/es/components/col/index'
import { ElRow } from 'element-plus/es/components/row/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElDivider } from 'element-plus/es/components/divider/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTabs, ElTabPane } from 'element-plus/es/components/tabs/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import { LocationInformation, Upload } from '@element-plus/icons-vue'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/upload/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/divider/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tabs/style/css'
import 'element-plus/es/components/tag/style/css'
import { legalWorkspace } from '../api'
import { useQuery } from '../query/useQuery'
import { qk } from '../query/keys'

import AiOutputFeedback from '../components/AiOutputFeedback.vue'
import { useLegalConsultations } from '../composables/useLegalConsultations'
import { useLegalSourceDetail } from '../composables/useLegalSourceDetail'
import { useQuota } from '../composables/useQuota'
import { useContractReviews } from '../composables/useContractReviews'
import { useLegalDrafts } from '../composables/useLegalDrafts'
import { useContractComparison } from '../composables/useContractComparison'
import LegalPortalTab from '../components/legal/LegalPortalTab.vue'
import LegalReviewTab from '../components/legal/LegalReviewTab.vue'
import {
  categoryLabel,
  clauseLabel,
  formatDate,
  riskLabel,
  riskTagType,
  sourceStatusLabel,
  sourceStatusType,
  statusLabel,
  statusTagType,
  useContractRiskPresentation,
} from '../composables/useLegalWorkspacePresentation'
import LegalSourcesTab from '../components/legal/LegalSourcesTab.vue'
import CaseCreateDialog from '../components/legal/CaseCreateDialog.vue'
import LegalConsultationsTab from '../components/legal/LegalConsultationsTab.vue'
import LegalDraftsTab from '../components/legal/LegalDraftsTab.vue'
import LegalContractTab from '../components/legal/LegalContractTab.vue'

// 三大重量级视图（账单/日期/台账）仅在对应 tab 首次激活时才加载，避免并入工作台首包 chunk
const AsyncLegalBilling = defineAsyncComponent(() => import('./LegalBilling.vue'))
const AsyncLegalDeadlines = defineAsyncComponent(() => import('./LegalDeadlines.vue'))
const AsyncLegalContracts = defineAsyncComponent(() => import('./LegalContracts.vue'))

const activeTab = ref('consultation')
const currentCaseId = ref(null)
const reviewTabRef = ref(null)
const caseDialog = ref(null)

// M-3 B 组：结果卡剩余额度提示（配额来自 /billing/subscriptions/quota）
const { loadQuota } = useQuota()

// 概览 / 案件列表走统一查询层：同 key 去重、缓存（staleTime）、离线暂停
const overviewQuery = useQuery({
  key: qk.legal.overview(),
  fetcher: () => legalWorkspace.getLegalOverview(),
  staleTime: 60 * 1000,
})

const currentOrgId = computed(() => overviewQuery.data.value?.organization_id || 1)

const casesQuery = useQuery({
  key: () => qk.legal.cases(currentOrgId.value),
  fetcher: () => legalWorkspace.listCases(currentOrgId.value),
  staleTime: 30 * 1000,
  enabled: () => overviewQuery.isSuccess.value === true,
})

const cases = computed(() => casesQuery.data.value || [])

// 首次进入自动选中进行中案件（与旧行为一致）
watch(casesQuery.data, (data) => {
  if (!currentCaseId.value && data?.length) {
    const active = data.find((c) => c.status === 'in_progress') || data[0]
    currentCaseId.value = active.id
  }
})

const handleCaseCreated = async (caseId) => {
  currentCaseId.value = caseId
  await casesQuery.refetch()
}

const CATEGORY_TO_DRAFT_TYPE = {
  labor_dispute: 'labor_arbitration_application',
  private_lending: 'private_lending_complaint',
  consumer_dispute: 'consumer_complaint',
}

const draftsTabRef = ref(null)

const handleGoToDraftFromConsult = (consultResult) => {
  if (!consultResult) return
  const type = CATEGORY_TO_DRAFT_TYPE[consultResult.category]
  const facts = (consultResult.known_facts || []).join('；')
  draftsTabRef.value?.prefill(type, facts ? { 事实与理由: facts } : {})
  activeTab.value = 'draft'
  ElMessage.info(type ? '已按咨询分类选择文书类型并带入案情，请补充当事人等必填字段' : '已带入咨询案情，请选择文书类型')
}

const contractsTabRef = ref(null)

const handleGoToReviewFromConsult = (consultResult) => {
  if (!consultResult) return
  const facts = (consultResult.known_facts || []).join('\n')
  contractsTabRef.value?.prefill(`${categoryLabel(consultResult.category)}关联审查`, facts)
  activeTab.value = 'contract'
  ElMessage.info('已带入咨询案情，请粘贴合同全文后开始审查')
}


const downloadText = (filename, text) => {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

const confidenceTagType = (score) => {
  if (score >= 80) return 'success'
  if (score >= 55) return 'warning'
  return 'danger'
}

const {
  sourceDetailVisible,
  sourceDetail,
  sourceDetailArticles,
  sourceDetailLoading,
  openSourceDetail,
  openRecommendedSource,
  verificationTagType,
} = useLegalSourceDetail()

onMounted(() => {
  loadQuota()
})
</script>

<style scoped>
.legal-workspace {
  max-width: 1200px;
  margin: 0 auto;
}

.legal-banner {
  margin-bottom: 20px;
}

.case-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
  padding: 10px 14px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.case-select {
  width: 340px;
  max-width: 100%;
}

.case-label {
  font-weight: 600;
  font-size: 14px;
  color: var(--color-text);
}

.case-count {
  float: right;
  margin-left: 16px;
  color: var(--color-text-muted);
  font-size: 12px;
}

.legal-tabs :deep(.el-tabs__item) {
  font-size: 15px;
  font-weight: 600;
}

.tab-panel {
  display: grid;
  gap: 20px;
}

.card-title {
  font-weight: 700;
  font-size: 15px;
}

.result-card {
  margin-top: 4px;
}

.result-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.history-card {
  margin-top: 4px;
}

.muted {
  color: var(--color-text-muted);
  font-size: 13px;
}

.missing-item {
  color: var(--el-color-danger);
  font-weight: 500;
}

.ref-item {
  margin-bottom: 6px;
}

.reference-list {
  margin-bottom: 8px;
}

.ref-title {
  font-weight: 600;
  margin-right: 8px;
}

.ref-citation {
  color: var(--color-text-muted);
  font-size: 12px;
}

.ref-version {
  margin-left: 8px;
  color: var(--color-text-muted);
  font-size: 12px;
}

.verification-tag {
  margin-left: 8px;
}

.article-list {
  display: grid;
  gap: 12px;
  max-height: 360px;
  overflow-y: auto;
}

.article-entry {
  background: var(--el-fill-color-light);
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 13px;
}

.article-title {
  margin-left: 8px;
  color: var(--color-text-secondary);
}

.article-content {
  margin: 6px 0 0;
  line-height: 1.7;
  color: var(--color-text-secondary);
}

.summary-text {
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.6;
  margin: 0 0 12px;
}

.missing-warn {
  padding: 12px;
  background: var(--el-color-danger-light-9);
  border-radius: 6px;
  margin-bottom: 12px;
  font-size: 13px;
}

.draft-content {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 14px;
  line-height: 1.8;
  background: var(--el-fill-color-light);
  padding: 20px;
  border-radius: 8px;
  margin: 0;
  font-family: inherit;
}

.compare-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.compare-col {
  min-width: 0;
}

.upload-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.followup-section {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.filter-row {
  display: flex;
  gap: 12px;
  margin-bottom: 8px;
}

.source-snippet {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.required-hint {
  display: block;
  font-size: 12px;
  color: var(--el-color-danger);
  margin-top: 2px;
}

.contract-content {
  max-height: 400px;
  overflow-y: auto;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}

.contract-paragraph {
  margin: 0 0 12px;
  padding: 8px 12px;
  font-size: 14px;
  line-height: 1.8;
  font-family: inherit;
  white-space: pre-wrap;
  word-break: break-all;
  border-left: 3px solid transparent;
  transition: all 0.3s ease;
  border-radius: 4px;
}

.contract-paragraph.highlighted {
  background: var(--el-color-warning-light-9);
  border-left-color: var(--el-color-warning);
  box-shadow: 0 0 0 3px var(--el-color-warning-light-9);
  animation: highlightPulse 0.6s ease-in-out;
}

@keyframes highlightPulse {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-4px); }
  75% { transform: translateX(4px); }
}

.import-result {
  margin-top: 16px;
  padding: 12px;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  font-size: 13px;
}

.score-breakdown {
  font-size: 12px;
  color: var(--color-text-muted);
  font-family: monospace;
}

.metrics-grid-inline {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.stat-mini {
  display: grid;
  gap: 4px;
  text-align: center;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.stat-mini span {
  font-size: 12px;
  color: var(--color-text-muted);
}

.stat-mini strong {
  font-size: 22px;
  font-weight: 600;
}

.return-reason-list {
  display: grid;
  gap: 8px;
}

.return-reason-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
  padding: 8px 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}

.review-detail {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  padding: 16px;
  background: var(--el-fill-color-light);
}

.review-detail-content {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 13px;
  line-height: 1.7;
  background: #fff;
  padding: 12px;
  border-radius: 6px;
  margin: 8px 0 0;
  max-height: 300px;
  overflow-y: auto;
}

.history-timeline {
  margin-top: 8px;
  display: grid;
  gap: 10px;
}

.history-entry {
  background: #fff;
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 13px;
}

.history-transition {
  margin-left: 8px;
  color: var(--color-text-secondary);
}

.history-time {
  margin-left: 8px;
  color: var(--color-text-muted);
  font-size: 12px;
}

.history-note {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
}

@media (max-width: 900px) {
  .review-detail {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .case-bar {
    flex-direction: column;
    align-items: stretch;
  }
  .case-select {
    width: 100%;
  }
  .result-header {
    gap: 6px;
  }
  .result-header :deep(.el-button + .el-button) {
    margin-left: 0;
  }
  .legal-tabs :deep(.el-tabs__header) {
    margin-bottom: 12px;
  }
  .contract-content pre,
  .draft-content {
    font-size: 12px;
  }
}

.comment-box {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.version-panel {
  padding: 16px;
  background: var(--el-fill-color-light);
  display: grid;
  gap: 16px;
}

.resubmit-form {
  padding: 12px;
  background: var(--el-color-warning-light-9);
  border-radius: 6px;
}

.version-list {
  margin-top: 8px;
  display: grid;
  gap: 10px;
}

.version-entry {
  background: #fff;
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 13px;
}

.version-time, .version-status {
  margin-left: 8px;
  color: var(--color-text-muted);
}

.version-content {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  line-height: 1.6;
  background: var(--el-fill-color-light);
  padding: 8px;
  border-radius: 4px;
  margin: 6px 0 0;
  max-height: 150px;
  overflow-y: auto;
}

</style>
