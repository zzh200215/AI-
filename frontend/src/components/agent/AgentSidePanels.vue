<template>
  <div>
    <el-card class="history-card">
      <template #header>
        <div class="card-header-inline">
          <div>
            <span>待审批操作</span>
          </div>
          <el-button size="small" @click="fetchApprovals">刷新</el-button>
        </div>
      </template>
      <div v-if="approvals.length" class="stack-list">
        <div v-for="item in approvals" :key="`approval-${item.id}`" class="stack-item">
          <div class="stack-top">
            <strong>{{ toolLabel(item.tool_name) }}</strong>
            <el-tag size="small" type="warning">{{ item.risk_level }}</el-tag>
            <el-tag size="small" :type="item.status === 'pending' ? 'warning' : item.status === 'approved' ? 'success' : 'danger'">
              {{ item.status === 'pending' ? '待审批' : item.status === 'approved' ? '已通过' : '已拒绝' }}
            </el-tag>
          </div>
          <div class="stack-foot">
            <span v-if="item.agent_type">Agent：{{ item.agent_type }}</span>
            <span>{{ item.created_at }}</span>
          </div>
          <el-input type="textarea" :rows="3" :model-value="formatJson(item.input_params)" readonly />
          <div class="timeline-status" v-if="item.status === 'pending'">
            <el-button size="small" type="success" @click="decideApproval(item, true)">通过</el-button>
            <el-button size="small" type="danger" plain @click="decideApproval(item, false)">拒绝</el-button>
          </div>
          <div v-else-if="item.decision_note" class="stack-foot">备注：{{ item.decision_note }}</div>
        </div>
      </div>
      <el-empty v-else description="暂无待审批操作" />
    </el-card>

    <el-card class="history-card">
      <template #header>
        <div class="card-header-inline">
          <div>
            <span>运行历史</span>
          </div>
          <el-button size="small" @click="fetchHistory">刷新</el-button>
        </div>
      </template>
      <div v-if="historyError" class="history-error" role="alert">
        <span>{{ historyError.message || '历史记录加载失败' }}</span>
        <el-button size="small" type="primary" plain @click="fetchHistory">重试</el-button>
      </div>
      <el-table v-else :data="history" v-loading="historyLoading" border size="small">
        <el-table-column prop="id" label="运行 ID" width="90" />
        <el-table-column prop="goal" label="执行目标" min-width="260" show-overflow-tooltip />
        <el-table-column prop="status" label="运行状态" width="100">
          <template #default="{ row }">
            <StatusTag kind="agent" :status="row.status" size="small" />
          </template>
        </el-table-column>
        <el-table-column prop="total_steps" label="步数" width="80" />
        <el-table-column prop="final_answer" label="结果摘要" min-width="240" show-overflow-tooltip />
        <el-table-column prop="created_at" label="开始时间" width="180" />
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button size="small" type="primary" text @click="viewRun(row)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        background
        layout="total, prev, pager, next"
        :current-page="historyPage"
        :page-size="historyPageSize"
        :total="historyTotal"
        class="app-pagination-end"
        @current-change="handleHistoryPageChange"
      />
    </el-card>
  </div>
</template>

<script setup>
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import StatusTag from '../StatusTag.vue'
import { formatJson, toolLabel } from '../../utils/workspacePresentation'
import { useAgentWorkbench } from '../../composables/useAgentWorkbench'

const {
  approvals, fetchApprovals, decideApproval,
  history, historyLoading, viewRun, historyPage, historyPageSize, historyTotal,
  fetchHistory, handleHistoryPageChange,
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
.history-card {
  border-radius: var(--radius-lg);
}
.history-card + .history-card {
  margin-top: var(--space-6);
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
.stack-foot,
.timeline-status {
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
</style>
