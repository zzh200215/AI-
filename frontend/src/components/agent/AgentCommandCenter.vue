<template>
  <div>
    <div class="command-grid">
      <el-card class="input-card">
      <template #header>一句话目标</template>
      <el-input
        v-model="goal"
        type="textarea"
        :rows="4"
        placeholder="例如：总结文档 1，并提取风险点；再生成一份审查意见"
      />
      <div class="input-actions">
        <div class="meta-text">文档分析、检索和草稿生成会直接继续；创建任务、批量落待办及敏感数据查询会在执行到该步骤时请求确认。</div>
        <div class="action-row">
          <el-tag v-if="demoContext.documentId" size="small" type="warning">
            演示文档 {{ demoContext.documentId }}{{ demoContext.documentTitle ? ` · ${demoContext.documentTitle}` : '' }}
          </el-tag>
          <span class="meta-text">最大步数</span>
          <el-input-number v-model="maxSteps" :min="2" :max="10" size="small" />
          <el-button :loading="previewLoading" @click="previewPlan">预览计划</el-button>
          <el-button type="primary" :loading="loading" @click="run">直接执行</el-button>
        </div>
      </div>
      </el-card>

      <el-card v-if="demoPreset" class="demo-card">
        <template #header>
          <div class="card-header-inline">
            <div>
              <span>{{ demoPreset.title }}</span>
            </div>
            <el-tag size="small" type="warning">标准演示链路</el-tag>
          </div>
        </template>
        <div class="demo-grid">
          <div class="demo-block">
            <div class="panel-title">演示说明</div>
            <div class="panel-content">{{ demoPreset.description }}</div>
          </div>
          <div class="demo-block">
            <div class="panel-title">当前文档</div>
            <div class="panel-content">
              {{ demoPreset.documentId ? `文档 ${demoPreset.documentId}` : '未指定文档' }}
              <span v-if="demoPreset.documentTitle"> · {{ demoPreset.documentTitle }}</span>
            </div>
          </div>
          <div class="demo-block">
            <div class="panel-title">预期工具路径</div>
            <ul class="simple-list">
              <li v-for="(item, index) in demoPreset.expectedFlow" :key="`demo-flow-${index}`">{{ item }}</li>
            </ul>
          </div>
          <div class="demo-block">
            <div class="panel-title">演示检查点</div>
            <ul class="simple-list">
              <li v-for="(item, index) in demoPreset.successCriteria" :key="`demo-success-${index}`">{{ item }}</li>
            </ul>
          </div>
        </div>
        <div class="risk-list">
          <div class="panel-title">执行前建议</div>
          <ul class="simple-list">
            <li v-for="(item, index) in demoPreset.prepChecklist" :key="`demo-prep-${index}`">{{ item }}</li>
          </ul>
        </div>
      </el-card>
    </div>

    <el-card v-if="planPreview" class="preview-card">
      <template #header>
        <div class="card-header-inline">
          <span>执行计划预览</span>
          <el-tag :type="planPreview.can_execute ? 'success' : 'warning'" size="small">
            {{ planPreview.can_execute ? '可执行' : '需补充信息' }}
          </el-tag>
        </div>
      </template>

      <div class="preview-summary">
        <div class="panel-title">计划概述</div>
        <div class="panel-content">{{ planPreview.summary }}</div>
      </div>

      <div class="preview-metrics">
        <div class="metric-item">
          <span class="metric-label">预计步数</span>
          <strong>{{ planPreview.estimated_steps }}</strong>
        </div>
        <div class="metric-item">
          <span class="metric-label">涉及工具</span>
          <strong>{{ planPreview.steps.length }}</strong>
        </div>
      </div>

      <div class="timeline" style="margin-top: 16px">
        <div v-for="step in planPreview.steps" :key="`preview-${step.step}`" class="timeline-item">
          <div class="timeline-marker pending">{{ step.step }}</div>
          <div class="timeline-body">
            <div class="timeline-top">
              <strong>{{ toolLabel(step.tool_name) }}</strong>
            </div>
            <div class="panel-content">{{ step.purpose }}</div>
            <div v-if="Object.keys(step.action_input_preview || {}).length" class="detail-block" style="margin-top: 12px">
              <div class="detail-label">预估参数</div>
              <el-input type="textarea" :rows="3" :model-value="formatJson(step.action_input_preview)" readonly />
            </div>
          </div>
        </div>
      </div>

      <div v-if="planPreview.risks?.length" class="risk-list">
        <div class="panel-title">风险与缺口</div>
        <ul class="simple-list">
          <li v-for="(risk, index) in planPreview.risks" :key="`risk-${index}`">{{ risk }}</li>
        </ul>
      </div>

      <div class="preview-actions">
        <el-button type="primary" :loading="loading" :disabled="!planPreview.can_execute" @click="executeRun">立即执行</el-button>
      </div>
    </el-card>

    <el-dialog v-model="sensitiveApprovalVisible" title="确认敏感操作" width="560px" class="agent-dialog" append-to-body :close-on-click-modal="false">
      <template v-if="activeApproval">
        <div class="preview-summary">
          <div class="panel-title">即将执行</div>
          <div class="panel-content">{{ toolLabel(activeApproval.tool_name) }}</div>
        </div>
        <div class="risk-list">
          <div class="panel-title">操作说明</div>
          <div class="panel-content">该步骤会创建内部任务、批量落待办或读取敏感数据。确认后，Agent 将从当前步骤继续执行。</div>
        </div>
        <div class="detail-block">
          <div class="detail-label">执行参数</div>
          <el-input type="textarea" :rows="5" :model-value="formatJson(activeApproval.input_params)" readonly />
        </div>
      </template>
      <template #footer>
        <el-button :disabled="loading" @click="decideApproval(activeApproval, false)">拒绝操作</el-button>
        <el-button type="primary" :loading="loading" @click="decideApproval(activeApproval, true)">确认并继续</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElInputNumber } from 'element-plus/es/components/input-number/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/tag/style/css'
import { formatJson, toolLabel } from '../../utils/workspacePresentation'
import { useAgentWorkbench } from '../../composables/useAgentWorkbench'

const {
  goal, maxSteps, loading, previewLoading, demoContext, demoPreset,
  planPreview, sensitiveApprovalVisible, activeApproval,
  previewPlan, run, executeRun, decideApproval,
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
.command-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) minmax(320px, 0.9fr);
  gap: var(--space-6);
  align-items: start;
}
.input-card,
.preview-card,
.demo-card {
  border-radius: var(--radius-lg);
}
.input-actions {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
  align-items: center;
  margin-top: var(--space-5);
}
.action-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.meta-text {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}
.preview-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-4);
}
.metric-item {
  background: #ffffff;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  box-shadow: var(--shadow-xs);
  padding: var(--space-4) var(--space-4);
}
.metric-label {
  display: block;
  margin-bottom: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
}
.preview-summary,
.preview-actions,
.risk-list {
  margin-top: var(--space-4);
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
.timeline-marker.pending {
  background: var(--color-accent);
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
.timeline-top {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}
.demo-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}
.demo-block {
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
.detail-block + .detail-block {
  margin-top: var(--space-4);
}
.detail-block {
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  background: var(--color-surface);
  padding: var(--space-3);
}
.agent-dialog :deep(.el-dialog) {
  border-radius: var(--radius-xl);
}
@media (max-width: 1024px) {
  .input-actions,
  .command-grid,
  .demo-grid {
    grid-template-columns: 1fr;
    display: grid;
  }
  .action-row {
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}
</style>
