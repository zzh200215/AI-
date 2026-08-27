<template>
  <div v-if="analysis" class="analysis-section">
    <div class="insight-hero">
      <div class="insight-hero-copy">
        <span class="summary-label">当前摘要</span>
        <strong>{{ docTitle || `文档 ${docIdLabel}` }}</strong>
        <p>{{ analysis.summary || '尚未生成摘要内容。' }}</p>
      </div>
      <div class="insight-hero-metrics">
        <div class="insight-metric">
          <span>风险</span>
          <strong>{{ analysis.risks.length }}</strong>
        </div>
        <div class="insight-metric">
          <span>待办</span>
          <strong>{{ analysis.todos.length }}</strong>
        </div>
        <div class="insight-metric">
          <span>条款</span>
          <strong>{{ analysis.clauses.length }}</strong>
        </div>
        <div class="insight-metric">
          <span>引用</span>
          <strong>{{ analysis.references.length }}</strong>
        </div>
      </div>
    </div>

    <div class="analysis-grid">
      <el-card class="panel-card panel-summary summary-card-surface">
        <template #header>摘要</template>
        <div class="rich-text">{{ analysis.summary || '暂无摘要' }}</div>
      </el-card>

      <el-card class="panel-card analysis-card">
        <template #header>
          <div class="card-header-inline">
            <span>风险点</span>
            <el-tag size="small">{{ analysis.risks.length }}</el-tag>
          </div>
        </template>
        <div v-if="analysis.risks.length" class="stack-list">
          <div v-for="(item, index) in analysis.risks" :key="`risk-${index}`" class="stack-item risk-item">
            <div class="stack-top">
              <strong>{{ item.title || `风险 ${index + 1}` }}</strong>
              <el-tag :type="severityTagType(item.severity)">{{ severityText(item.severity) }}</el-tag>
            </div>
            <p>{{ item.description || '暂无说明' }}</p>
            <div class="stack-foot">{{ item.suggestion || '暂无建议动作' }}</div>
          </div>
        </div>
        <el-empty v-else description="未识别到明显风险" />
      </el-card>

      <el-card class="panel-card analysis-card">
        <template #header>
          <div class="card-header-inline">
            <span>待办事项</span>
            <el-tag size="small">{{ analysis.todos.length }}</el-tag>
          </div>
        </template>
        <div v-if="analysis.todos.length" class="stack-list">
          <div v-for="(item, index) in analysis.todos" :key="`todo-${index}`" class="stack-item todo-item">
            <div class="stack-top">
              <strong>{{ item.title || `待办 ${index + 1}` }}</strong>
              <el-tag :type="severityTagType(item.priority)">{{ severityText(item.priority) }}</el-tag>
            </div>
            <p>{{ item.description || '暂无描述' }}</p>
            <div class="stack-foot">
              <span>负责人：{{ item.assignee || '待确认' }}</span>
              <span>截止：{{ item.due_date || '待确认' }}</span>
            </div>
          </div>
        </div>
        <el-empty v-else description="未识别到可执行待办" />
      </el-card>

      <el-card class="panel-card analysis-card">
        <template #header>
          <div class="card-header-inline">
            <span>关键条款</span>
            <el-tag size="small">{{ analysis.clauses.length }}</el-tag>
          </div>
        </template>
        <div v-if="analysis.clauses.length" class="stack-list">
          <div v-for="(item, index) in analysis.clauses" :key="`clause-${index}`" class="stack-item clause-item">
            <div class="stack-top">
              <strong>{{ item.title || `条款 ${index + 1}` }}</strong>
              <el-tag>{{ item.category || '未分类' }}</el-tag>
            </div>
            <p>{{ item.content || '暂无内容' }}</p>
            <div class="stack-foot">重要程度：{{ severityText(item.importance) }}</div>
          </div>
        </div>
        <el-empty v-else description="未识别到关键条款" />
      </el-card>

      <el-card class="panel-card panel-fields analysis-card">
        <template #header>
          <div class="card-header-inline">
            <span>结构化字段</span>
            <el-tag size="small">{{ structuredFieldCount(analysis.structured_fields) }}</el-tag>
          </div>
        </template>
        <div class="structured-grid">
          <div class="structured-block">
            <div class="panel-title">日期</div>
            <div v-if="analysis.structured_fields.dates.length" class="stack-list">
              <div v-for="(item, index) in analysis.structured_fields.dates" :key="`date-${index}`" class="stack-item">
                <div class="stack-top">
                  <strong>{{ item.value }}</strong>
                  <el-tag size="small" type="info">{{ item.normalized_date || '未标准化' }}</el-tag>
                </div>
                <p>{{ item.description || '未说明日期含义' }}</p>
              </div>
            </div>
            <el-empty v-else description="未识别到日期字段" />
          </div>

          <div class="structured-block">
            <div class="panel-title">金额</div>
            <div v-if="analysis.structured_fields.amounts.length" class="stack-list">
              <div v-for="(item, index) in analysis.structured_fields.amounts" :key="`amount-${index}`" class="stack-item">
                <div class="stack-top">
                  <strong>{{ item.value }}</strong>
                  <el-tag size="small" type="warning">{{ item.currency || '未标币种' }}</el-tag>
                </div>
                <p>{{ item.description || '未说明金额含义' }}</p>
                <div class="stack-foot">标准值：{{ item.amount || item.value }}</div>
              </div>
            </div>
            <el-empty v-else description="未识别到金额字段" />
          </div>

          <div class="structured-block">
            <div class="panel-title">责任人</div>
            <div v-if="analysis.structured_fields.owners.length" class="stack-list">
              <div v-for="(item, index) in analysis.structured_fields.owners" :key="`owner-${index}`" class="stack-item">
                <div class="stack-top">
                  <strong>{{ item.name || '未命名责任人' }}</strong>
                  <el-tag size="small">{{ item.role || '未标角色' }}</el-tag>
                </div>
                <p>{{ item.responsibility || '未说明负责事项' }}</p>
              </div>
            </div>
            <el-empty v-else description="未识别到责任人字段" />
          </div>

          <div class="structured-block">
            <div class="panel-title">风险条款</div>
            <div v-if="analysis.structured_fields.risk_clauses.length" class="stack-list">
              <div v-for="(item, index) in analysis.structured_fields.risk_clauses" :key="`risk-clause-${index}`" class="stack-item risk-item">
                <div class="stack-top">
                  <strong>{{ item.title || `风险条款 ${index + 1}` }}</strong>
                  <el-tag :type="severityTagType(item.severity)">{{ severityText(item.severity) }}</el-tag>
                </div>
                <p>{{ item.description || '暂无说明' }}</p>
                <div class="stack-foot">{{ item.suggestion || '暂无建议动作' }}</div>
              </div>
            </div>
            <el-empty v-else description="未识别到风险条款" />
          </div>
        </div>
      </el-card>

      <el-card class="panel-card panel-reference analysis-card">
        <template #header>
          <div class="card-header-inline">
            <span>引用片段</span>
            <el-tag size="small">{{ analysis.references.length }}</el-tag>
          </div>
        </template>
        <div v-if="analysis.references.length" class="reference-list">
          <div v-for="(item, index) in analysis.references" :key="`ref-${index}`" class="reference-item">
            <div class="reference-label">
              <el-tag size="small" :type="referenceTagType(item.source_type)">
                {{ referenceTypeText(item.source_type) }}
              </el-tag>
              <strong>{{ item.label }}</strong>
            </div>
            <blockquote>{{ item.quote }}</blockquote>
          </div>
        </div>
        <el-empty v-else description="暂无引用片段" />
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ElCard } from 'element-plus/es/components/card/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/tag/style/css'
import {
  documentReferenceTagType as referenceTagType,
  documentReferenceTypeText as referenceTypeText,
  documentSeverityTagType as severityTagType,
  documentSeverityText as severityText,
} from '../../utils/workspacePresentation'

defineProps({
  analysis: { type: Object, default: null },
  docTitle: { type: String, default: '' },
  docIdLabel: { type: String, default: '' },
})

const structuredFieldCount = (value) => {
  if (!value) return 0
  return ['dates', 'amounts', 'owners', 'risk_clauses'].reduce((total, key) => total + ((value[key] || []).length), 0)
}
</script>

<style scoped>
.analysis-section {
  display: grid;
  gap: var(--space-6);
}
.insight-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(320px, 0.9fr);
  gap: var(--space-4);
  padding: var(--space-5);
  border-radius: var(--radius-xl);
  border: 1px solid var(--color-border-light);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
}
.insight-hero-copy {
  display: grid;
  gap: var(--space-2);
}
.summary-label,
.insight-metric span {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.insight-hero-copy strong {
  color: var(--color-text);
  font-size: var(--text-xl);
}
.insight-hero-copy p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}
.insight-hero-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
}
.insight-metric {
  padding: var(--space-4) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  border: 1px solid var(--color-border-light);
  display: grid;
  gap: var(--space-1);
}
.insight-metric strong {
  color: var(--color-text);
  font-size: var(--text-xl);
}
.analysis-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-6);
}
.summary-card-surface,
.analysis-card {
  overflow: hidden;
}
.analysis-card :deep(.el-card__body) {
  display: grid;
  gap: var(--space-3);
}
.panel-summary,
.panel-fields,
.panel-reference {
  grid-column: 1 / -1;
}
.rich-text {
  white-space: pre-wrap;
  line-height: 1.8;
  color: var(--color-text);
}
.stack-list,
.reference-list {
  display: grid;
  gap: var(--space-3);
}
.structured-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}
.structured-block {
  display: grid;
  gap: var(--space-3);
}
.panel-title {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  font-weight: 600;
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
.risk-item {
  border-left: 4px solid var(--color-accent);
}
.todo-item {
  border-left: 4px solid var(--color-primary);
}
.clause-item {
  border-left: 4px solid var(--color-success);
}
.stack-top,
.stack-foot,
.reference-label {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}
.stack-item p {
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
</style>
