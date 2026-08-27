<template>
  <div class="main-column">
    <el-card class="toolbar-card">
      <template #header>
        <div class="card-header-inline">
          <div>
            <span>单文档分析工作区</span>
          </div>
          <el-space>
            <el-button type="primary" plain :disabled="!docId" @click="openAgentDemo">
              发送到 Agent 演示
            </el-button>
            <el-button :disabled="!docId" :loading="loading" @click="runAnalysis">重新分析</el-button>
            <el-button type="warning" :disabled="!docId" :loading="creatingTasks" @click="createTasks">
              提取待办并创建任务
            </el-button>
          </el-space>
        </div>
      </template>
      <div class="toolbar-meta">
        <span v-if="docId">当前文档 ID: {{ docId }}</span>
        <span v-else>先上传或选择一份文档</span>
      </div>
      <div v-if="analysisTask.taskId" class="async-status app-state-banner">
        <StatusTag kind="async" :status="analysisTask.state" />
        <span>{{ analysisTaskMessage }}</span>
      </div>
    </el-card>

    <div v-if="docId" class="workspace-strip">
      <div class="workspace-tile">
        <span>当前文档</span>
        <strong>{{ docMeta?.title || `文档 ${docId}` }}</strong>
        <p>{{ docMeta?.classification || '未分类' }} · {{ docMeta?.permission_scope || '未设权限' }}</p>
      </div>
      <div class="workspace-tile">
        <span>问答与引用</span>
        <strong>{{ qaRecords.length }}</strong>
        <p>累计问答记录，支持引用溯源与反馈闭环</p>
      </div>
      <div class="workspace-tile">
        <span>解析与版本</span>
        <strong>{{ parseJobs.length }} / {{ versions.length }}</strong>
        <p>后台任务数 / 当前版本记录</p>
      </div>
    </div>

    <el-card v-if="docId" class="panel-card">
      <template #header>
        <div class="card-header-inline">
          <span>文档问答</span>
          <el-tag size="small" type="info">带引用回答</el-tag>
        </div>
      </template>
      <div class="qa-compose">
        <el-input
          v-model="qaQuestion"
          type="textarea"
          :rows="3"
          :maxlength="500"
          show-word-limit
          placeholder="例如：该条款的法律依据是什么？违约责任如何界定？适用哪条司法解释？"
        />
        <div class="qa-compose-actions">
          <span class="toolbar-meta">提问法律依据、条款适用、风险识别等问题时，系统会优先返回引用片段。</span>
          <el-button type="primary" :loading="asking" :disabled="!qaQuestion.trim()" @click="askDocumentQuestion">
            开始提问
          </el-button>
        </div>
      </div>

      <div v-if="qaResult" class="qa-result">
        <div class="qa-result-top">
          <strong>当前回答</strong>
          <el-tag :type="qaResult.can_answer ? 'success' : 'warning'" size="small">
            {{ qaResult.can_answer ? '可回答' : '无法确认' }}
          </el-tag>
          <el-tag size="small" type="info">置信度 {{ Math.round((qaResult.confidence || 0) * 100) }}%</el-tag>
          <el-tag v-if="qaResult.feedback_value" size="small" :type="feedbackTagType(qaResult.feedback_value)">
            {{ feedbackValueText(qaResult.feedback_value) }}
          </el-tag>
        </div>
        <div class="rich-text">{{ qaResult.answer || '暂无回答' }}</div>

        <div class="qa-feedback-actions">
          <template v-if="qaResult.qa_record_id">
            <el-button
              size="small"
              :type="qaResult.feedback_value === 'positive' ? 'success' : 'default'"
              :loading="submittingFeedback"
              @click="submitPositiveFeedback"
            >
              有帮助
            </el-button>
            <el-button
              size="small"
              :type="qaResult.feedback_value === 'negative' ? 'danger' : 'default'"
              :loading="submittingFeedback"
              @click="openNegativeFeedback"
            >
              有问题
            </el-button>
          </template>
        </div>

        <div v-if="negativeFeedbackVisible" class="qa-feedback-form">
          <el-select v-model="feedbackForm.feedback_reason" placeholder="选择问题类型" style="width: 180px">
            <el-option v-for="item in feedbackReasonOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-input
            v-model="feedbackForm.feedback_note"
            type="textarea"
            :rows="2"
            :maxlength="300"
            show-word-limit
            placeholder="补充说明问题"
          />
          <div class="qa-feedback-actions">
            <el-button size="small" @click="cancelNegativeFeedback">取消</el-button>
            <el-button size="small" type="danger" :loading="submittingFeedback" @click="submitNegativeFeedback">
              提交反馈
            </el-button>
          </div>
        </div>

        <div v-if="qaResult.citations?.length" class="qa-citations">
          <div class="panel-title">引用来源</div>
          <div class="reference-list">
            <div v-for="(item, index) in qaResult.citations" :key="`qa-ref-${index}`" class="reference-item citation-item">
              <div class="reference-label">
                <el-tag size="small" type="primary">片段 {{ (item.chunk_index ?? index) + 1 }}</el-tag>
                <strong>{{ item.section_title || '未标注章节' }}</strong>
                <span class="stack-foot" v-if="item.page_number">第 {{ item.page_number }} 页</span>
              </div>
              <blockquote>{{ item.source_text || '暂无引用原文' }}</blockquote>
            </div>
          </div>
        </div>
      </div>
    </el-card>

    <el-card v-if="docId" class="panel-card">
      <template #header>
        <div class="card-header-inline">
          <span>关联 Agent 执行</span>
          <el-tag type="warning" size="small">{{ relatedAgentRuns.length }}</el-tag>
        </div>
      </template>
      <div v-if="relatedAgentRuns.length" class="stack-list">
        <div v-for="item in relatedAgentRuns" :key="`doc-agent-${item.id}`" class="stack-item">
          <div class="stack-top">
            <strong>#{{ item.id }} {{ item.goal }}</strong>
            <StatusTag kind="agent" :status="item.status" size="small" />
          </div>
          <div class="stack-foot">
            <span>{{ item.created_at }}</span>
            <span>步数：{{ item.total_steps || 0 }}</span>
          </div>
          <el-button size="small" text type="primary" @click="openAgentRun(item.id)">查看执行</el-button>
        </div>
      </div>
      <el-empty v-else description="暂无关联 Agent 执行记录" />
    </el-card>

    <DocumentJobsPanel :parse-jobs="parseJobs" @retry="retryParse" @refresh="fetchParseJobs" />

    <DocumentVersionsPanel v-if="docId" :versions="versions" @refresh="fetchVersions" />

    <DocumentQaHistoryPanel v-if="docId" :records="qaRecords" @refresh="fetchQaRecords" />

    <div v-if="analysis" class="content-section-head">
      <div>
        <h4>分析结果</h4>
      </div>
      <span>摘要、风险、待办、条款与结构化字段统一查看</span>
    </div>

    <DocumentAnalysisPanels v-if="analysis" :analysis="analysis" :doc-title="docMeta?.title" :doc-id-label="String(docId || '')" />

    <div v-if="compareResult" class="content-section-head">
      <div>
        <h4>多文档对比</h4>
      </div>
      <span>用于校验条款差异、风险偏差和动作建议</span>
    </div>

    <el-card v-if="compareResult" class="compare-result-card">
      <template #header>
        <div class="card-header-inline">
          <span>多文档对比结果</span>
          <el-tag type="warning" size="small">{{ compareResult.documents.length }} 份文档</el-tag>
        </div>
      </template>

      <div class="compare-hero">
        <div class="compare-hero-copy">
          <span class="summary-label">整体结论</span>
          <strong>{{ compareResult.documents.map((item) => item.title).join(' / ') }}</strong>
          <p>{{ compareResult.comparison.overview || '暂无结论' }}</p>
        </div>
        <div class="compare-hero-metrics">
          <div class="compare-metric">
            <span>共同点</span>
            <strong>{{ (compareResult.comparison.common_points || []).length }}</strong>
          </div>
          <div class="compare-metric">
            <span>风险差异</span>
            <strong>{{ (compareResult.comparison.risk_delta || []).length }}</strong>
          </div>
          <div class="compare-metric">
            <span>差异项</span>
            <strong>{{ (compareResult.comparison.differences || []).length }}</strong>
          </div>
          <div class="compare-metric">
            <span>建议动作</span>
            <strong>{{ (compareResult.comparison.action_suggestions || []).length }}</strong>
          </div>
          <div class="compare-metric conflict-metric">
            <span>证据充分的冲突</span>
            <strong>{{ compareResult.comparison.conflict_analysis?.confirmed_conflict_count || 0 }}</strong>
          </div>
        </div>
      </div>

      <div class="compare-overview">
        <h4>整体结论</h4>
        <p>{{ compareResult.comparison.overview || '暂无结论' }}</p>
      </div>

      <div class="compare-grid">
        <el-card class="mini-card conflict-card" shadow="never">
          <template #header>
            <div class="card-header-inline">
              <span>事实冲突核对</span>
              <el-tag type="danger" size="small">{{ compareResult.comparison.conflict_analysis?.conflicts?.length || 0 }} 条</el-tag>
            </div>
          </template>
          <div v-if="(compareResult.comparison.conflict_analysis?.conflicts || []).length" class="stack-list">
            <div v-for="(item, index) in compareResult.comparison.conflict_analysis.conflicts" :key="`conflict-${index}`" class="stack-item conflict-item">
              <div class="stack-top">
                <strong>{{ item.field_label }}：{{ item.field }}</strong>
                <el-tag :type="item.evidence_complete ? severityTagType(item.severity) : 'warning'" size="small">
                  {{ item.evidence_complete ? severityText(item.severity) : '待补证据' }}
                </el-tag>
              </div>
              <div class="conflict-source"><b>{{ item.source_a.document_title }}</b><span>{{ item.source_a.value }}</span><small>{{ item.source_a.source_text || '未定位原文' }}</small><small v-if="item.source_a.page_number || item.source_a.section_title">{{ item.source_a.page_number ? `第 ${item.source_a.page_number} 页` : '' }}{{ item.source_a.section_title ? ` ${item.source_a.section_title}` : '' }}</small></div>
              <div class="conflict-source"><b>{{ item.source_b.document_title }}</b><span>{{ item.source_b.value }}</span><small>{{ item.source_b.source_text || '未定位原文' }}</small><small v-if="item.source_b.page_number || item.source_b.section_title">{{ item.source_b.page_number ? `第 ${item.source_b.page_number} 页` : '' }}{{ item.source_b.section_title ? ` ${item.source_b.section_title}` : '' }}</small></div>
              <p>建议：{{ item.recommended_action }}</p>
            </div>
          </div>
          <div v-if="confirmedConflictCount" class="card-actions conflict-actions">
            <el-button type="danger" plain :loading="conflictSuggestionLoading" @click="createConflictSuggestions">生成 {{ confirmedConflictCount }} 项风险任务建议</el-button>
          </div>
          <div v-if="conflictCases.length" class="conflict-case-list">
            <div v-for="item in conflictCases" :key="`conflict-case-${item.id}`" class="conflict-case-row">
              <div><strong>案例 #{{ item.id }}</strong><span>{{ item.conflict?.field_label }}：{{ item.conflict?.field }}</span></div>
              <el-tag size="small" :type="conflictCaseTag(item.status)">{{ conflictCaseText(item.status) }}</el-tag>
              <el-button v-if="item.status === 'pending_confirmation'" size="small" type="danger" plain @click="confirmConflictTask(item)">确认并创建任务</el-button>
              <el-button v-else-if="item.task_id" size="small" text type="primary" @click="openTask(item.task_id)">查看任务 #{{ item.task_id }}</el-button>
            </div>
          </div>
          <el-empty v-else description="未发现可证实的日期、金额或负责人冲突" />
        </el-card>

        <el-card class="mini-card" shadow="never">
          <template #header>共同点</template>
          <ul class="simple-list">
            <li v-for="(item, index) in compareResult.comparison.common_points || []" :key="`common-${index}`">
              {{ item }}
            </li>
          </ul>
          <el-empty v-if="!(compareResult.comparison.common_points || []).length" description="暂无共同点" />
        </el-card>

        <el-card class="mini-card" shadow="never">
          <template #header>风险差异</template>
          <div v-if="(compareResult.comparison.risk_delta || []).length" class="stack-list">
            <div v-for="(item, index) in compareResult.comparison.risk_delta" :key="`risk-delta-${index}`" class="stack-item risk-item">
              <div class="stack-top">
                <strong>{{ item.title || `风险差异 ${index + 1}` }}</strong>
                <el-tag v-if="item.severity" :type="severityTagType(item.severity)">{{ severityText(item.severity) }}</el-tag>
              </div>
              <p>{{ item.detail || '暂无说明' }}</p>
            </div>
          </div>
          <el-empty v-if="!(compareResult.comparison.risk_delta || []).length" description="暂无风险差异" />
        </el-card>
      </div>

      <el-card class="mini-card" shadow="never">
        <template #header>文档概览</template>
        <el-table :data="compareResult.summary_cards || []" border size="small">
          <el-table-column prop="title" label="文档" min-width="180" />
          <el-table-column prop="risk_count" label="风险数" width="90" />
          <el-table-column prop="todo_count" label="待办数" width="90" />
          <el-table-column prop="reference_count" label="引用数" width="90" />
        </el-table>
      </el-card>

      <div class="compare-grid">
        <el-card class="mini-card" shadow="never">
          <template #header>差异项</template>
          <div v-if="(compareResult.comparison.differences || []).length" class="stack-list">
            <div v-for="(item, index) in compareResult.comparison.differences" :key="`diff-${index}`" class="stack-item">
              <div class="stack-top">
                <strong>{{ item.title || `差异 ${index + 1}` }}</strong>
              </div>
              <p>{{ item.detail || '暂无说明' }}</p>
            </div>
          </div>
          <el-empty v-else description="暂无明显差异" />
        </el-card>

        <el-card class="mini-card" shadow="never">
          <template #header>建议动作</template>
          <ul class="simple-list">
            <li
              v-for="(item, index) in compareResult.comparison.action_suggestions || []"
              :key="`suggest-${index}`"
            >
              {{ item }}
            </li>
          </ul>
          <el-empty v-if="!(compareResult.comparison.action_suggestions || []).length" description="暂无建议动作" />
        </el-card>
      </div>
    </el-card>

    <div v-if="createdTasks.length" class="content-section-head">
      <div>
        <h4>任务产出</h4>
      </div>
      <span>从文档待办直接生成任务并进入任务中心</span>
    </div>

    <el-card v-if="createdTasks.length" class="task-card">
      <template #header>
        <div class="card-header-inline">
          <span>已创建任务</span>
          <el-tag type="success" size="small">{{ createdTasks.length }}</el-tag>
          <el-button size="small" text type="primary" @click="openDocumentTasks">查看文档任务</el-button>
        </div>
      </template>
      <el-table :data="createdTasks" border size="small">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="title" label="标题" min-width="180" />
        <el-table-column prop="assignee" label="负责人" width="120" />
        <el-table-column prop="priority" label="优先级" width="100" />
        <el-table-column prop="status" label="状态" width="110" />
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-button text type="primary" @click="openTask(row.id)">查看任务</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElOption, ElSelect } from 'element-plus/es/components/select/index'
import { ElSpace } from 'element-plus/es/components/space/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/space/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import StatusTag from '../StatusTag.vue'
import DocumentAnalysisPanels from './DocumentAnalysisPanels.vue'
import DocumentJobsPanel from './DocumentJobsPanel.vue'
import DocumentVersionsPanel from './DocumentVersionsPanel.vue'
import DocumentQaHistoryPanel from './DocumentQaHistoryPanel.vue'
import {
  documentConflictCaseTag as conflictCaseTag,
  documentConflictCaseText as conflictCaseText,
  documentSeverityTagType as severityTagType,
  documentSeverityText as severityText,
} from '../../utils/workspacePresentation'
import { useDocuments } from '../../composables/useDocuments'

const {
  docId, docMeta, loading, creatingTasks, analysisTask, analysisTaskMessage,
  qaQuestion, qaResult, asking, submittingFeedback, negativeFeedbackVisible, feedbackForm,
  feedbackReasonOptions, feedbackValueText, feedbackTagType,
  qaRecords, parseJobs, versions, relatedAgentRuns, analysis, compareResult,
  confirmedConflictCount, conflictSuggestionLoading, conflictCases, createdTasks,
  openAgentDemo, runAnalysis, createTasks, retryParse, fetchParseJobs, fetchVersions, fetchQaRecords,
  askDocumentQuestion, submitPositiveFeedback, openNegativeFeedback, cancelNegativeFeedback, submitNegativeFeedback,
  createConflictSuggestions, confirmConflictTask, openAgentRun, openTask, openDocumentTasks,
} = useDocuments()
</script>

<style scoped>
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
.main-column {
  display: grid;
  gap: var(--space-6);
}
.workspace-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-4);
}
.workspace-tile {
  padding: var(--space-5) var(--space-5);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-light);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
  display: grid;
  gap: var(--space-1);
  transition: all var(--transition-fast);
}
.workspace-tile:hover {
  box-shadow: var(--shadow-card-hover);
  border-color: var(--color-border-hover);
}
.workspace-tile span {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.workspace-tile strong {
  color: var(--color-text);
  font-size: var(--text-lg);
}
.workspace-tile p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
  font-size: var(--text-sm);
}
.async-status {
  margin-top: var(--space-3);
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}
.qa-compose,
.qa-result,
.qa-feedback-form,
.qa-citations {
  display: grid;
  gap: var(--space-3);
}
.qa-compose-actions,
.qa-result-top,
.qa-feedback-actions {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}
.qa-feedback-form {
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: var(--color-warning-light);
}
.panel-title {
  font-weight: 700;
  color: var(--color-text);
}
.stack-list,
.reference-list {
  display: grid;
  gap: var(--space-3);
}
.stack-item,
.reference-item {
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-bg-alt);
  border: 1px solid var(--color-border-light);
  transition: all var(--transition-fast);
}
.stack-item:hover,
.reference-item:hover {
  border-color: var(--color-primary-subtle);
}
.stack-top,
.stack-foot,
.reference-label {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}
.stack-item p,
.compare-overview p {
  margin: var(--space-2) 0;
  color: var(--color-text);
  line-height: 1.7;
}
.stack-foot {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}
.reference-item blockquote {
  margin: var(--space-3) 0 0;
  padding: 0 0 0 var(--space-3);
  border-left: 3px solid var(--color-border);
  color: var(--color-text);
  line-height: 1.7;
}
.citation-item {
  background: var(--color-primary-light);
}
.rich-text {
  white-space: pre-wrap;
  line-height: 1.8;
  color: var(--color-text);
}
.content-section-head {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
  padding: 0 4px;
}
.content-section-head h4 {
  margin: 0;
  font-size: var(--text-xl);
  color: var(--color-text);
}
.content-section-head span {
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.panel-card,
.task-card,
.compare-result-card,
.mini-card {
  border-radius: var(--radius-lg);
}
.compare-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-6);
}
.compare-overview {
  margin-bottom: var(--space-5);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-bg-alt);
}
.compare-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(320px, 0.9fr);
  gap: var(--space-4);
  padding: var(--space-5);
  border-radius: var(--radius-xl);
  border: 1px solid var(--color-border-light);
  background: var(--color-surface);
  margin-bottom: var(--space-5);
}
.compare-hero-copy {
  display: grid;
  gap: var(--space-2);
}
.compare-hero-copy strong {
  color: var(--color-text);
  font-size: var(--text-xl);
}
.compare-hero-copy p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}
.summary-label {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.compare-hero-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
}
.compare-metric {
  padding: var(--space-4) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  border: 1px solid var(--color-border-light);
  display: grid;
  gap: var(--space-1);
}
.compare-metric span {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.compare-metric strong {
  color: var(--color-text);
  font-size: var(--text-xl);
}
.conflict-metric { border-color: var(--el-color-danger-light-7); }
.conflict-card { grid-column: span 2; }
.conflict-item { border-left: 3px solid var(--el-color-danger); }
.conflict-source { display: grid; grid-template-columns: minmax(120px, .8fr) minmax(110px, .55fr); gap: var(--space-1) var(--space-2); padding: var(--space-2) 0; border-top: 1px solid var(--color-border-light); font-size: var(--text-sm); }
.conflict-source b { color: var(--color-text); }.conflict-source span { color: var(--el-color-danger); font-weight: 700; }.conflict-source small { grid-column: 1 / -1; color: var(--color-text-secondary); line-height: 1.55; }
.conflict-actions { justify-content: flex-start; margin-top: var(--space-4); }
.conflict-case-list { display: grid; gap: var(--space-2); margin-top: var(--space-4); }
.conflict-case-row { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; padding: var(--space-3); background: var(--color-bg-alt); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); }
.conflict-case-row > div { display: grid; gap: 2px; flex: 1; min-width: 170px; }.conflict-case-row span { color: var(--color-text-secondary); font-size: var(--text-sm); }
.compare-overview h4 {
  margin: 0 0 var(--space-2);
}
.simple-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: var(--space-2);
  color: var(--color-text);
}
@media (max-width: 1100px) {
  .workspace-strip,
  .compare-hero,
  .compare-grid {
    grid-template-columns: 1fr;
  }
  .conflict-card { grid-column: auto; }
}
</style>
