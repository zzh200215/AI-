<template>
  <div>
    <div v-if="runResult" class="summary-grid">
      <el-card class="summary-card">
        <template #header>
          <div class="card-header-inline">
            <span>执行结果</span>
            <StatusTag kind="agent" :status="runResult.status" />
          </div>
        </template>
        <div class="run-hero">
          <div class="run-hero-copy">
            <span class="metric-label">执行目标</span>
            <strong>{{ runResult.goal || goal || '未记录目标' }}</strong>
            <p>{{ finalAnswer || runResult.error || '等待执行结果返回。' }}</p>
          </div>
        </div>
        <div class="summary-metrics">
          <div class="metric-item">
            <span class="metric-label">运行 ID</span>
            <strong>{{ runResult.id || runResult.run_id }}</strong>
          </div>
          <div class="metric-item">
            <span class="metric-label">总步数</span>
            <strong>{{ runResult.total_steps || logs.length || 0 }}</strong>
          </div>
          <div class="metric-item">
            <span class="metric-label">失败原因</span>
            <strong>{{ runResult.failure_reason || '无' }}</strong>
          </div>
        </div>
        <div v-if="finalAnswer" class="answer-panel">
          <div class="panel-title">最终答复</div>
          <div class="panel-content">{{ finalAnswer }}</div>
        </div>
        <div v-if="runResult.error" class="error-panel">
          <div class="panel-title">系统错误</div>
          <div class="panel-content">{{ runResult.error }}</div>
        </div>
        <div v-if="['running', 'cancelling', 'awaiting_approval'].includes(runResult.status)" class="card-actions">
          <el-button type="danger" plain :loading="cancelling" @click="cancelCurrentRun">取消执行</el-button>
        </div>
      </el-card>

      <el-card class="summary-card" v-if="logs.length">
        <template #header>步骤时间线</template>
        <div class="timeline">
          <div v-for="log in logs" :key="log.id" class="timeline-item">
            <div class="timeline-marker" :class="log.status">{{ log.step }}</div>
            <div class="timeline-body">
              <div class="timeline-top">
                <strong>{{ toolLabel(log.tool_name) }}</strong>
                <span class="timeline-meta">{{ actionTypeText(log.action_type) }} · {{ log.duration_ms || 0 }}ms</span>
              </div>
              <div class="timeline-status">
                <el-tag size="small" :type="log.status === 'success' ? 'success' : log.status === 'pending' ? 'warning' : 'danger'">
                  {{ log.status === 'success' ? '成功' : log.status === 'pending' ? '执行中' : '失败' }}
                </el-tag>
                <span v-if="log.error" class="error-inline">{{ log.error }}</span>
              </div>
            </div>
          </div>
        </div>
      </el-card>
    </div>

    <el-card v-if="supervisorPlan.workers?.length" class="details-card supervisor-plan-card">
      <template #header>
        <div class="card-header-inline">
          <div>
            <span>Worker 编排</span>
          </div>
          <el-tag size="small" :type="supervisorPlan.plan_source === 'llm' ? 'success' : 'warning'">
            {{ supervisorPlan.plan_source === 'llm' ? '模型计划' : '规则回退' }}
          </el-tag>
          <el-tag v-if="supervisorPlan.execution_mode === 'parallel_read_only'" size="small" type="primary">只读并行</el-tag>
        </div>
      </template>
      <div class="supervisor-summary">
        <div>
          <span>意图</span>
          <strong>{{ supervisorPlan.intent || '未识别' }}</strong>
        </div>
        <div>
          <span>风险等级</span>
          <el-tag size="small" :type="supervisorPlan.risk_level === 'high' ? 'danger' : supervisorPlan.risk_level === 'medium' ? 'warning' : 'success'">
            {{ supervisorPlan.risk_level || 'low' }}
          </el-tag>
        </div>
        <div>
          <span>预期产物</span>
          <strong>{{ (supervisorPlan.expected_artifacts || []).join(' · ') || '无' }}</strong>
        </div>
      </div>
      <div class="worker-route" aria-label="Worker 执行顺序">
        <template v-for="(worker, index) in supervisorPlan.workers" :key="`worker-route-${worker}-${index}`">
          <div class="worker-node">
            <span>{{ index + 1 }}</span>
            <strong>{{ workerLabel(worker) }}</strong>
          </div>
          <span v-if="index < supervisorPlan.workers.length - 1" class="worker-route-arrow" aria-hidden="true">{{ supervisorPlan.execution_mode === 'parallel_read_only' ? '+' : '->' }}</span>
        </template>
      </div>
      <div v-if="supervisorPlan.handoffs?.length" class="handoff-list">
        <div v-for="(handoff, index) in supervisorPlan.handoffs" :key="`handoff-${index}`" class="handoff-row">
          <span>{{ workerLabel(handoff.from_worker) }}</span>
          <span class="worker-route-arrow">-></span>
          <strong>{{ workerLabel(handoff.to_worker) }}</strong>
          <span>{{ handoff.completion_summary }}</span>
        </div>
      </div>
      <div v-if="supervisorPlan.aggregation" class="aggregation-panel">
        <strong>{{ supervisorPlan.aggregation.conclusion }}</strong>
        <ul v-if="supervisorPlan.aggregation.findings?.length" class="simple-list">
          <li v-for="(item, index) in supervisorPlan.aggregation.findings" :key="`aggregation-${index}`">{{ item.title }}<span v-if="item.evidence">：{{ item.evidence }}</span></li>
        </ul>
      </div>
      <div v-if="supervisorPlan.execution_mode === 'parallel_read_only'" class="stack-foot">仅文档等白名单只读能力可并发执行；写入、草稿和敏感查询仍按审批串行执行。</div>
      <div v-if="supervisorPlan.fallback_reason" class="stack-foot">回退原因：{{ supervisorPlan.fallback_reason }}</div>
    </el-card>

    <el-card v-if="hasArtifacts" class="details-card">
      <template #header>
        <div class="card-header-inline">
          <span>本次产出对象</span>
          <el-tag size="small" type="success">执行闭环</el-tag>
        </div>
      </template>
      <div class="artifact-summary">
        <div class="artifact-summary-item">
          <span>文档</span>
          <strong>{{ artifactGroups.documents.length }}</strong>
        </div>
        <div class="artifact-summary-item">
          <span>任务</span>
          <strong>{{ artifactGroups.tasks.length }}</strong>
        </div>
      </div>
      <div class="artifact-grid">
        <div v-if="artifactGroups.documents.length" class="artifact-block">
          <div class="panel-title">文档结果</div>
          <div v-for="item in artifactGroups.documents" :key="`doc-${item.document_id}`" class="stack-item">
            <div class="stack-top">
              <strong>文档 {{ item.document_id }}</strong>
              <el-tag size="small">{{ toolLabel(item.tool_name) }}</el-tag>
            </div>
            <p v-if="item.summary">{{ item.summary }}</p>
            <div class="stack-foot">
              <span v-if="item.risk_count">风险数：{{ item.risk_count }}</span>
              <span v-if="item.chunk_count">命中片段：{{ item.chunk_count }}</span>
            </div>
            <el-button v-if="item.document_id" text type="primary" @click="openDocument(item.document_id)">查看文档</el-button>
          </div>
        </div>

        <div v-if="artifactGroups.tasks.length" class="artifact-block">
          <div class="panel-title">创建任务</div>
          <div v-for="item in artifactGroups.tasks" :key="`task-${item.task_id}`" class="stack-item">
            <div class="stack-top">
              <strong>{{ item.title || `任务 ${item.task_id}` }}</strong>
              <el-tag size="small" type="primary">{{ item.status || 'todo' }}</el-tag>
            </div>
            <div class="stack-foot">
              <span v-if="item.assignee">负责人：{{ item.assignee }}</span>
              <span v-if="item.priority">优先级：{{ item.priority }}</span>
            </div>
            <el-button v-if="item.task_id" text type="primary" @click="openTask(item.task_id)">查看任务</el-button>
          </div>
        </div>
      </div>
    </el-card>

    <el-card v-if="logs.length" class="details-card">
      <template #header>
        <div class="card-header-inline">
          <span>执行详情</span>
          <el-tag size="small">{{ logs.length }} 步</el-tag>
        </div>
      </template>
      <div class="app-section-intro compact-intro">
        <strong>步骤级执行回放</strong>
        <span>展开可查看每一步的决策思路、工具参数、执行观察和原始决策载荷。</span>
      </div>
      <el-collapse>
        <el-collapse-item v-for="log in logs" :key="log.id" :name="log.id">
          <template #title>
            <div class="collapse-title">
              <el-tag size="small" :type="log.status === 'success' ? 'success' : 'danger'">Step {{ log.step }}</el-tag>
              <strong>{{ toolLabel(log.tool_name) }}</strong>
              <span class="timeline-meta">{{ actionTypeText(log.action_type) }}</span>
            </div>
          </template>

          <div class="detail-block" v-if="log.thought">
            <div class="detail-label">决策思路</div>
            <div class="detail-value">{{ log.thought }}</div>
          </div>

          <div class="detail-block" v-if="log.error">
            <div class="detail-label">失败原因</div>
            <div class="detail-value error-text">{{ log.error }}</div>
          </div>

          <div class="detail-block">
            <div class="detail-label">输入参数</div>
            <el-input type="textarea" :rows="4" :model-value="formatJson(log.input_params)" readonly />
          </div>

          <div class="detail-block" v-if="log.observation">
            <div class="detail-label">执行观察</div>
            <el-input type="textarea" :rows="5" :model-value="formatJson(log.observation)" readonly />
          </div>

          <div class="detail-block" v-if="log.raw_decision">
            <div class="detail-label">原始决策</div>
            <el-input type="textarea" :rows="5" :model-value="formatJson(log.raw_decision)" readonly />
          </div>
        </el-collapse-item>
      </el-collapse>
    </el-card>
  </div>
</template>

<script setup>
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCollapse, ElCollapseItem } from 'element-plus/es/components/collapse/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/collapse/style/css'
import 'element-plus/es/components/collapse-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/tag/style/css'
import StatusTag from '../StatusTag.vue'
import { actionTypeText, formatJson, toolLabel, workerLabel } from '../../utils/workspacePresentation'
import { useAgentWorkbench } from '../../composables/useAgentWorkbench'

const {
  runResult, logs, finalAnswer, goal, cancelling,
  supervisorPlan, hasArtifacts, artifactGroups,
  cancelCurrentRun, openDocument, openTask,
} = useAgentWorkbench()
</script>

<style scoped>
.section-eyebrow {
  margin-bottom: 4px;
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-muted);
}
.card-header-inline {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  align-items: center;
}
.summary-grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: var(--space-6);
}
.summary-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
}
.run-hero {
  margin-bottom: var(--space-4);
  padding: var(--space-5);
  border-radius: var(--radius-xl);
  background: var(--color-surface);
  border: 1px solid var(--color-border-light);
  box-shadow: var(--shadow-xs);
}
.run-hero-copy {
  display: grid;
  gap: var(--space-2);
}
.run-hero-copy strong {
  color: var(--color-text);
  font-size: var(--text-xl);
}
.run-hero-copy p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}
.metric-label {
  display: block;
  margin-bottom: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
}
.metric-item {
  background: #ffffff;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  box-shadow: var(--shadow-xs);
  padding: var(--space-4) var(--space-4);
}
.answer-panel,
.error-panel {
  margin-top: var(--space-4);
  padding: var(--space-4);
  border-radius: var(--radius-md);
}
.answer-panel {
  background: var(--color-success-light);
}
.error-panel {
  background: var(--color-danger-light);
}
.panel-title,
.detail-label {
  margin-bottom: var(--space-2);
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-text);
}
.panel-content,
.detail-value {
  white-space: pre-wrap;
  line-height: 1.7;
  color: var(--color-text);
}
.timeline {
  display: grid;
  gap: var(--space-4);
}
.timeline-item {
  display: grid;
  grid-template-columns: 40px 1fr;
  gap: var(--space-3);
  align-items: flex-start;
}
.timeline-marker {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-full);
  display: grid;
  place-items: center;
  color: #fff;
  font-weight: 700;
  font-size: var(--text-sm);
  background: var(--color-text-muted);
  transition: all var(--transition-fast);
}
.timeline-marker.success {
  background: var(--color-success);
}
.timeline-marker.pending {
  background: var(--color-accent);
}
.timeline-marker.error {
  background: var(--color-danger);
}
.timeline-body {
  padding: var(--space-3) var(--space-3);
  border-radius: var(--radius-md);
  background: #ffffff;
  border: 1px solid var(--color-border-light);
}
.timeline-body:hover {
  background: var(--color-surface);
  border-color: var(--color-border-hover);
  box-shadow: var(--shadow-sm);
}
.timeline-top,
.timeline-status,
.collapse-title {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}
.timeline-meta {
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
}
.artifact-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}
.artifact-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}
.artifact-summary-item {
  padding: var(--space-4) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  background: #ffffff;
  display: grid;
  gap: var(--space-1);
}
.artifact-summary-item span {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.artifact-summary-item strong {
  color: var(--color-text);
  font-size: var(--text-2xl);
  font-weight: 600;
}
.details-card,
.summary-card {
  border-radius: var(--radius-lg);
}
.supervisor-plan-card {
  border-top: 3px solid var(--color-primary);
}
.supervisor-summary {
  display: grid;
  grid-template-columns: minmax(180px, 2fr) minmax(120px, 1fr) minmax(160px, 1fr);
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}
.supervisor-summary > div {
  display: grid;
  gap: var(--space-1);
  padding: var(--space-3);
  border: 1px solid var(--color-border-light);
  background: var(--color-bg-alt);
  border-radius: var(--radius-md);
}
.supervisor-summary span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.supervisor-summary strong {
  color: var(--color-text);
  line-height: 1.5;
}
.worker-route {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  overflow-x: auto;
  padding: var(--space-2) 0;
}
.worker-node {
  min-width: 156px;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--color-border-light);
  background: #ffffff;
  border-radius: var(--radius-md);
}
.worker-node > span {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  color: #ffffff;
  background: var(--color-primary);
  border-radius: var(--radius-full);
  font-size: var(--text-xs);
  font-weight: 700;
}
.worker-route-arrow {
  flex: 0 0 auto;
  color: var(--color-text-muted);
  font-family: monospace;
}
.handoff-list {
  display: grid;
  gap: var(--space-2);
  margin-top: var(--space-4);
}
.handoff-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  padding: var(--space-3);
  border-left: 3px solid var(--color-primary-subtle);
  background: var(--color-bg-alt);
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}
.handoff-row strong {
  color: var(--color-text);
}
.aggregation-panel {
  display: grid;
  gap: var(--space-3);
  margin-top: var(--space-4);
  padding: var(--space-3);
  border-left: 3px solid var(--color-primary);
  background: var(--color-bg-alt);
  color: var(--color-text);
}
.compact-intro {
  margin-bottom: var(--space-4);
}
.artifact-block {
  display: grid;
  gap: var(--space-3);
}
.simple-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: var(--space-2);
  color: var(--color-text);
}
.stack-list {
  display: grid;
  gap: var(--space-3);
}
.stack-item {
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-bg-alt);
  border: 1px solid var(--color-border-light);
  transition: all var(--transition-fast);
}
.stack-item:hover {
  border-color: var(--color-primary-subtle);
  background: var(--color-surface);
}
.stack-top,
.stack-foot {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}
.stack-item p,
.stack-foot {
  color: var(--color-text-secondary);
  line-height: 1.7;
}
.error-inline,
.error-text {
  color: var(--color-danger);
}
.detail-block + .detail-block {
  margin-top: var(--space-4);
}
.detail-block {
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  background: var(--color-surface);
  padding: var(--space-3);
}
@media (max-width: 1024px) {
  .summary-grid,
  .artifact-summary,
  .artifact-grid {
    grid-template-columns: 1fr;
    display: grid;
  }
  .summary-metrics {
    grid-template-columns: 1fr;
  }
  .supervisor-summary {
    grid-template-columns: 1fr;
  }
}
</style>
