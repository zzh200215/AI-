<template>
  <div class="system-page">
    <SystemOverviewBar :active-tab="activeTab" />

    <el-tabs v-model="activeTab" class="system-tabs" @tab-change="syncTabQuery">
      <el-tab-pane label="健康检查" name="health">
        <SystemHealthTab />
      </el-tab-pane>
      <!-- lazy：tab 首次激活才挂载加载，避免进入系统中心时 14 个 tab 并发打满后端 -->
      <el-tab-pane label="用户漏斗" name="funnel" lazy>
        <PilotAnalyticsTabs section="funnel" />
      </el-tab-pane>
      <el-tab-pane label="留存与北极星" name="retention" lazy>
        <PilotAnalyticsTabs section="retention" />
      </el-tab-pane>
      <el-tab-pane label="Token 统计" name="tokens" lazy>
        <SystemTokensTab />
      </el-tab-pane>
      <el-tab-pane label="操作日志" name="oplogs" lazy>
        <SystemOplogsTab />
      </el-tab-pane>
      <el-tab-pane label="异常告警" name="alerts" lazy>
        <SystemAlertsTab />
      </el-tab-pane>
      <el-tab-pane label="实验观测" name="experiments" lazy>
        <SystemExperimentsTab />
      </el-tab-pane>
      <el-tab-pane label="反馈闭环" name="feedback" lazy>
        <SystemFeedbackTab />
      </el-tab-pane>
      <el-tab-pane label="工具健康" name="tool_health" lazy>
        <SystemToolHealthTab />
      </el-tab-pane>
      <el-tab-pane label="审批台" name="approvals" lazy>
        <SystemApprovalsTab />
      </el-tab-pane>
      <el-tab-pane label="知识库" name="knowledge" lazy>
        <SystemKnowledgeTab :open-document="openKnowledgeDocument" />
      </el-tab-pane>
      <el-tab-pane label="组织架构" name="orgs" lazy>
        <SystemOrgTab />
      </el-tab-pane>
      <el-tab-pane label="敏感治理" name="sensitivity" lazy>
        <SystemSensitivityTab :open-document="openKnowledgeDocument" />
      </el-tab-pane>
      <el-tab-pane label="任务中心" name="tasks" lazy>
        <SystemTasksTab />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElTabs, ElTabPane } from 'element-plus/es/components/tabs/index'
import 'element-plus/es/components/tab-pane/style/css'
import 'element-plus/es/components/tabs/style/css'
import SystemOverviewBar from '../components/system/SystemOverviewBar.vue'
import SystemHealthTab from '../components/system/SystemHealthTab.vue'
import SystemTokensTab from '../components/system/SystemTokensTab.vue'
import SystemOplogsTab from '../components/system/SystemOplogsTab.vue'
import SystemAlertsTab from '../components/system/SystemAlertsTab.vue'
import SystemExperimentsTab from '../components/system/SystemExperimentsTab.vue'
import SystemFeedbackTab from '../components/system/SystemFeedbackTab.vue'
import SystemToolHealthTab from '../components/system/SystemToolHealthTab.vue'
import SystemApprovalsTab from '../components/system/SystemApprovalsTab.vue'
import SystemKnowledgeTab from '../components/system/SystemKnowledgeTab.vue'
import SystemOrgTab from '../components/system/SystemOrgTab.vue'
import SystemSensitivityTab from '../components/system/SystemSensitivityTab.vue'
import SystemTasksTab from '../components/system/SystemTasksTab.vue'
import PilotAnalyticsTabs from '../components/system/PilotAnalyticsTabs.vue'

const route = useRoute()
const router = useRouter()

const TAB_NAMES = ['health', 'tokens', 'oplogs', 'alerts', 'experiments', 'feedback', 'funnel', 'retention', 'tool_health', 'approvals', 'knowledge', 'orgs', 'sensitivity', 'tasks']

const activeTab = ref(TAB_NAMES.includes(route.query.tab) ? route.query.tab : 'health')

const syncTabQuery = () => {
  router.replace({ query: { ...route.query, tab: activeTab.value } })
}

const openKnowledgeDocument = (row) => {
  router.push({ path: '/documents', query: { documentId: String(row.id) } })
}

watch(
  () => route.query.tab,
  (value, oldValue) => {
    if (value === oldValue) return
    const nextTab = typeof value === 'string' && TAB_NAMES.includes(value) ? value : 'health'
    if (nextTab !== activeTab.value) {
      activeTab.value = nextTab
    }
  }
)
</script>

<style scoped>
.system-page {
  display: grid;
  gap: var(--space-5);
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
}


/* ─── Tabs ─── */
:deep(.system-tabs > .el-tabs__header) {
  margin: 0;
  max-width: 100%;
}
:deep(.system-tabs > .el-tabs__header .el-tabs__nav-wrap) {
  padding: var(--space-1);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  border: 1px solid var(--color-border-light);
  box-shadow: var(--shadow-xs);
  max-width: 100%;
  overflow: hidden;
}
:deep(.system-tabs),
:deep(.system-tabs .el-tabs__content),
:deep(.system-tabs .el-tab-pane),
:deep(.system-tabs .el-card),
:deep(.system-tabs .el-table) {
  min-width: 0;
  max-width: 100%;
}
:deep(.system-tabs .el-row) {
  max-width: 100%;
}
:deep(.system-tabs .el-table__inner-wrapper) {
  max-width: 100%;
}
:deep(.system-tabs > .el-tabs__header .el-tabs__nav) {
  gap: 4px;
}
:deep(.system-tabs > .el-tabs__header .el-tabs__item) {
  height: 34px;
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
}
:deep(.system-tabs > .el-tabs__header .el-tabs__item.is-active) {
  background: var(--color-primary-light);
  color: var(--color-primary);
  font-weight: 700;
}
:deep(.system-tabs > .el-tabs__header .el-tabs__active-bar) {
  display: none;
}
:deep(.system-tabs .el-tab-pane > .el-card:first-child),
:deep(.system-tabs .el-tab-pane > .el-row:first-child) {
  margin-top: var(--space-5);
}
:deep(.system-tabs .el-card) {
  border-radius: var(--radius-lg);
}
:deep(.system-tabs .el-card__header) {
  padding-bottom: var(--space-4);
}
:deep(.system-tabs .el-table th) {
  background: var(--color-bg-alt);
}
</style>
